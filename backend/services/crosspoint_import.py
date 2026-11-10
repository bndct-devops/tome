"""Import a CrossPoint Sync account export (experimental).

A CrossInk reader hands each reading session to one sync server and then
deletes it, so a reader who synced to CrossPoint Sync before switching to Tome
has all earlier reading only on that server. CrossPoint Sync exports a whole
account as one JSON file (its Settings > Account migration > Export account
data); this module reads that file into the signed-in Tome user's account.

The file (crosspoint-sync ``docs/account-migration.md`` and
``src/models/account-data.ts``)::

    {"format": "crosspoint-sync-account", "version": 1,
     "exported_at": "<ISO time>",
     "profile": {"username", "display_name", "auto_pause_days", "public_profile"},
     "data": {"<table>": [<row>, ...], ...}}

Each section is a table of the reference server's database with its
server-local columns (``user_id``, ``change_seq``, ``server_change_seq``,
``revision``) left out; a row holds every other column. Timestamps are Unix
seconds (``nowSeconds()``); ``finished_date``/``start_date`` are calendar dates
stored as Unix seconds at UTC midnight (migration 0017); ``percentage`` is
0..1. A document is the reader's hash of a file; ``document_aliases`` records
``alias -> document`` for two hashes the user merged into one book (0007).

Everything goes through the code the live reader sync uses, so an imported
session is exactly what the reader would have uploaded:

- documents: an existing ``KOSyncDocumentMap`` wins, then Tome's own file
  hashes (``_find_or_hash_link``), then the exported title/author/file name
  through ``kosync_books.store_metadata`` + ``resolve`` (a library book or a
  file-less book). Every hash of one merged book ends on the same Tome book.
- sessions: ``kosync_stats.parse_session`` + ``stage_sessions``, receipts keyed
  by the reader's own session id, so a second import or a later re-send by the
  reader adds nothing.
- progress and finished state: ``book_progress.apply_progress_to_status``
  (monotonic), then a finish the export records, with its date. Nothing is
  lowered or un-finished.
- clippings: ``kosync_clippings.parse_item`` + ``store``; only ids Tome does
  not have yet.
- daily seconds: ``kosync_stats.merge_daily`` (larger value per day); device
  totals: only for devices Tome has no snapshot of.

Preview and import are the same run: the preview runs it inside a savepoint
and rolls it back, so its report is what the import would do. The import
commits the savepoint, all or nothing.
"""
from __future__ import annotations

import json
import logging
import math
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any, Callable, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.models.book import BOOK_NO_FILE, Book
from backend.models.kosync import (
    KOSyncClipping,
    KOSyncSessionReceipt,
    KOSyncDeviceStats,
    KOSyncDocumentMap,
    KOSyncDocumentMeta,
)
from backend.models.user import User
from backend.models.user_book_status import UserBookStatus
from backend.services import kosync_books, kosync_clippings, kosync_stats
from backend.services.kosync_stats import Rejected

logger = logging.getLogger(__name__)

FORMAT = "crosspoint-sync-account"
VERSION = 1
# The exporter's own upload limit is 50 MiB, but the whole file is parsed in
# memory and a 47 MiB export peaked near 1 GiB on the server; 20 MiB keeps a
# small box safe and is still far above a reader's lifetime of sessions.
MAX_BYTES = 20 * 1024 * 1024
MAX_RECORDS = 200_000             # likewise
MAX_ERRORS = 10                   # problems listed in one refusal
EXAMPLES = 5                      # examples kept per skip reason
FINISHED_AT = 0.98                # crosspoint-sync src/models/activity.ts FINISHED_AT
EPOCH_2000 = 946684800

TEXT, INT, REAL = "text", "int", "real"

# Columns Tome reads, per section: name -> (type, required). Types and
# nullability from the reference server's migrations (see the docstring).
SCHEMA: dict[str, dict[str, tuple[str, bool]]] = {
    "documents": {                          # 0001 + 0002/0011/0017/0019/0026/0043
        "document": (TEXT, True), "title": (TEXT, False), "author": (TEXT, False),
        "filename": (TEXT, False), "isbn": (TEXT, False), "status": (TEXT, False),
        "status_at": (INT, False), "start_date": (INT, False), "finished_date": (INT, False),
        "pause_reason": (TEXT, False), "updated_at": (INT, False),
    },
    "document_aliases": {                   # 0007, 0016
        "alias": (TEXT, True), "document": (TEXT, True), "created_at": (INT, False),
        "merge_stats": (INT, False),
    },
    "progress": {                           # 0001
        "document": (TEXT, True), "device_id": (TEXT, True), "device": (TEXT, False),
        "percentage": (REAL, True), "progress": (TEXT, False), "position": (TEXT, False),
        "updated_at": (INT, True),
    },
    "progress_log": {                       # 0012
        "document": (TEXT, True), "device_id": (TEXT, False), "percentage": (REAL, True),
        "at": (INT, True),
    },
    "clippings": {                          # 0001, 0010, 0047
        "document": (TEXT, True), "id": (TEXT, True), "spine_index": (INT, False),
        "start_page": (INT, False), "end_page": (INT, False), "page_count": (INT, False),
        "start_word": (INT, False), "end_word": (INT, False), "word_count": (INT, False),
        "paragraph_index": (INT, False), "chapter_title": (TEXT, False), "text": (TEXT, False),
        "note": (TEXT, False), "color": (TEXT, False), "created_at": (INT, False),
        "deleted": (INT, False), "updated_at": (INT, False), "layout_signature": (INT, False),
        "start_offset": (INT, False), "end_offset": (INT, False),
    },
    "stats_device_global": {                # 0001
        "device_id": (TEXT, True), "device": (TEXT, False), "payload": (TEXT, True),
        "updated_at": (INT, False),
    },
    "stats_device_book": {                  # 0001
        "device_id": (TEXT, True), "document": (TEXT, True), "payload": (TEXT, True),
        "updated_at": (INT, False),
    },
    "stats_device_day": {                   # 0014
        "device_id": (TEXT, True), "date": (TEXT, True), "seconds": (INT, True),
        "updated_at": (INT, False),
    },
    "reading_sessions": {                   # 0030
        "device_id": (TEXT, True), "session_id": (TEXT, True), "document": (TEXT, True),
        "started_at": (INT, True), "ended_at": (INT, True), "seconds": (INT, True),
        "utc_offset_minutes": (INT, False), "created_at": (INT, False),
    },
}

# Columns the reference server exports that Tome does not use: not reported
# as unknown.
KNOWN_UNUSED: dict[str, set[str]] = {
    "documents": {
        "filesize", "cover_url", "cover_checked_at", "page_count", "hc_id", "hc_slug",
        "moods", "genres", "content_warnings", "rating", "series", "series_position",
        "hc_series_id", "release_year", "hc_checked_at", "description", "meta_manual",
        "genres_manual", "moods_manual", "content_warnings_manual", "description_manual",
        "series_manual", "release_year_manual", "language", "user_rating", "pages_checked_at",
    },
}

# Sections the reference server exports that Tome leaves out, and why.
NOT_IMPORTED: dict[str, str] = {
    "bookmarks": "Tome keeps no reader bookmarks",
    "progress_samples": "position samples only translate percentages for other services",
    "stats_device_book_day": "Tome does not store per-book daily counters (the live sync drops them too)",
    "reading_goals": "set reading goals in Tome itself",
}
# Read only as evidence for what Tome imports.
READ_ONLY: dict[str, str] = {
    "progress_log": "progress history: used for finish dates, not stored",
}

SECTIONS = tuple(SCHEMA) + tuple(NOT_IMPORTED)

# Progress rows a service connection wrote, not a reader: CrossPoint Sync's
# fan-in stores a connector's position under ``device_id`` = the connector id
# (src/connectors/fanin.ts upsertProgress, ids from src/connectors/registry.ts).
# A reader's device id is its own hex id. A connector added after this list
# was written would be taken for a reader.
SERVICE_DEVICE_IDS = frozenset({
    "kosync", "hardcover", "readwise", "readwise-reader", "bookfusion", "audiobookshelf",
    "jellyfin", "bookorbit", "connector:bookorbit", "spotify", "seekquel", "readest",
    "microblog", "kindle",
})
# progress_log rows backfilled from position samples (migration 0013): source not recorded.
HISTORY_DEVICE_ID = "history"


_HASH = re.compile(r"^[0-9a-fA-F]{32}$")


def _reader_hash(document: str) -> bool:
    """A reader's file hash, as the live sync knows documents. CrossPoint Sync
    also has documents of its own (``goodreads.<id>``, ``storygraph.<hash>``
    from its tracker import, src/models/tracker-import.ts), which are not files
    on a reader."""
    return bool(_HASH.match(document))


def _json_object(text: str) -> Optional[dict[str, Any]]:
    """A JSON object from a payload column, or None (also for nesting too deep
    to read)."""
    try:
        value = json.loads(text)
    except (ValueError, RecursionError):
        return None
    return value if isinstance(value, dict) else None


class Refused(Exception):
    """The file cannot be imported. Nothing was written."""

    def __init__(self, detail: str, status: int = 400) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status = status


# ── reading the file ─────────────────────────────────────────────────────────

@dataclass
class Archive:
    exported_at: Optional[str]
    source_username: Optional[str]
    size: int
    sections: dict[str, list[dict[str, Any]]]
    counts: dict[str, int]
    missing_sections: list[str]
    unknown_sections: dict[str, int]
    unknown_columns: dict[str, list[str]]


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true/false"
    if isinstance(value, int):
        return "a whole number"
    if isinstance(value, float):
        return "a decimal number"
    if isinstance(value, str):
        return "text"
    if isinstance(value, list):
        return "a list"
    return "an object"


def _fits(value: Any, kind: str) -> bool:
    if kind == TEXT:
        return isinstance(value, str)
    if isinstance(value, bool):
        return False
    if kind == INT:
        # JSON from JavaScript writes 3.0 as 3; a whole float is still accepted.
        return isinstance(value, int) or (isinstance(value, float) and value.is_integer())
    return isinstance(value, (int, float)) and math.isfinite(value)


_EXPECTED = {TEXT: "text", INT: "a whole number", REAL: "a number"}


def _refuse_constant(name: str) -> Any:
    raise ValueError(f"{name} is not valid JSON")


def parse(raw: bytes) -> Archive:
    """Read and check the file. Raises ``Refused`` saying what is wrong and where."""
    if len(raw) > MAX_BYTES:
        raise Refused(
            f"The file is {len(raw) / 1048576:.1f} MiB; the limit is {MAX_BYTES // 1048576} MiB "
            "(CrossPoint Sync's own export limit).", status=413)
    if not raw.strip():
        raise Refused("The file is empty.")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise Refused(f"The file is not UTF-8 text (bad byte at offset {exc.start}). "
                      "Choose the .json file CrossPoint Sync exported.") from None
    try:
        doc = json.loads(text, parse_constant=_refuse_constant)
    except json.JSONDecodeError as exc:
        hint = (" The file looks cut off (it does not end with \"}\"); export it again."
                if not text.rstrip().endswith("}") else "")
        raise Refused(f"The file is not valid JSON ({exc.msg}; line {exc.lineno}, column {exc.colno}).{hint}") from None
    except (ValueError, RecursionError) as exc:
        raise Refused(f"The file is not valid JSON: {exc}.") from None

    if not isinstance(doc, dict):
        raise Refused("The file is not a CrossPoint Sync account export: it holds "
                      f"{_type_name(doc)} where an object is expected.")
    fmt = doc.get("format")
    if fmt != FORMAT:
        raise Refused(f"The file is not a CrossPoint Sync account export: its \"format\" is "
                      f"{json.dumps(fmt)[:60]}, expected \"{FORMAT}\".")
    version = doc.get("version")
    if version != VERSION:
        raise Refused(f"This export has version {json.dumps(version)[:20]}; Tome reads version {VERSION}. "
                      "Tell the Tome developers which CrossPoint Sync version made it.")
    data = doc.get("data")
    if not isinstance(data, dict):
        raise Refused(f"The export has no \"data\" object (found {_type_name(data)}).")

    profile = doc.get("profile")
    username = profile.get("username") if isinstance(profile, dict) else None
    exported_at = doc.get("exported_at")

    errors: list[str] = []

    def problem(message: str) -> None:
        if len(errors) < MAX_ERRORS:
            errors.append(message)

    sections: dict[str, list[dict[str, Any]]] = {}
    counts: dict[str, int] = {}
    missing: list[str] = []
    unknown_sections: dict[str, int] = {}
    unknown_columns: dict[str, set[str]] = defaultdict(set)
    total = 0
    for name, rows in data.items():
        if name not in SECTIONS:
            unknown_sections[str(name)[:64]] = len(rows) if isinstance(rows, list) else 0
            continue
        if not isinstance(rows, list):
            problem(f"Section \"{name}\" should be a list of rows, found {_type_name(rows)}.")
            continue
        total += len(rows)
        counts[name] = len(rows)
        if name not in SCHEMA:
            continue   # not imported, only counted
        columns = SCHEMA[name]
        known = set(columns) | KNOWN_UNUSED.get(name, set())
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                problem(f"Section \"{name}\", row {index}: expected an object, found {_type_name(row)}.")
                continue
            for key in row:
                if key not in known:
                    unknown_columns[name].add(str(key)[:64])
            for column, (kind, required) in columns.items():
                value = row.get(column)
                if value is None:
                    if required:
                        problem(f"Section \"{name}\", row {index}, field \"{column}\": missing.")
                    continue
                if not _fits(value, kind):
                    problem(f"Section \"{name}\", row {index}, field \"{column}\": expected "
                            f"{_EXPECTED[kind]}, found {_type_name(value)}.")
                elif kind == INT and isinstance(value, float):
                    row[column] = int(value)
        sections[name] = rows
    if total > MAX_RECORDS:
        raise Refused(f"The export holds {total:,} records; the limit is {MAX_RECORDS:,} "
                      "(CrossPoint Sync's own export limit).", status=413)
    if errors:
        more = " (and more)" if len(errors) == MAX_ERRORS else ""
        raise Refused("The export does not have the expected shape, nothing was imported: "
                      + " ".join(errors) + more)
    for name in SCHEMA:
        if name not in sections:
            sections[name] = []
            if name not in counts:
                missing.append(name)

    # Aliases must end somewhere: a cycle has no book to land on.
    aliases = {r["alias"]: r["document"] for r in sections["document_aliases"]}
    for start in aliases:
        seen = {start}
        cur = aliases[start]
        while cur in aliases:
            if cur in seen:
                raise Refused(f"Section \"document_aliases\": the aliases of document {start} form a cycle.")
            seen.add(cur)
            cur = aliases[cur]

    return Archive(
        exported_at=exported_at[:40] if isinstance(exported_at, str) else None,
        source_username=username[:64] if isinstance(username, str) else None,
        size=len(raw),
        sections=sections,
        counts=counts,
        missing_sections=missing,
        unknown_sections=unknown_sections,
        unknown_columns={k: sorted(v) for k, v in unknown_columns.items()},
    )


# ── the report ───────────────────────────────────────────────────────────────

@dataclass
class Tally:
    """One kind of record: what happened to each, with skip reasons."""
    in_file: int = 0
    counts: dict[str, int] = field(default_factory=dict)
    skipped: dict[str, dict[str, Any]] = field(default_factory=dict)

    def add(self, outcome: str, n: int = 1) -> None:
        self.counts[outcome] = self.counts.get(outcome, 0) + n

    def skip(self, reason: str, example: dict[str, Any]) -> None:
        entry = self.skipped.setdefault(reason, {"reason": reason, "count": 0, "examples": []})
        entry["count"] += 1
        if len(entry["examples"]) < EXAMPLES:
            entry["examples"].append(example)

    def out(self) -> dict[str, Any]:
        return {
            "in_file": self.in_file,
            "counts": dict(self.counts),
            "skipped": sum(e["count"] for e in self.skipped.values()),
            "skip_reasons": sorted(self.skipped.values(), key=lambda e: -e["count"]),
        }


@dataclass
class Family:
    """One book as CrossPoint Sync knew it: a document and its aliases."""
    canonical: str
    members: list[str]
    title: Optional[str] = None
    author: Optional[str] = None
    has_data: bool = False
    outcome: str = ""
    reason: Optional[str] = None
    book_id: Optional[int] = None
    book_title: Optional[str] = None
    book_author: Optional[str] = None
    book_status: Optional[str] = None
    conflict: bool = False
    sessions_in_file: int = 0
    seconds_in_file: int = 0
    sessions_new: int = 0
    seconds_new: int = 0
    clippings_new: int = 0
    status_change: Optional[str] = None
    status_note: Optional[str] = None
    source_note: Optional[str] = None
    reader_pages: Optional[int] = None

    @property
    def reader_members(self) -> list[str]:
        return [d for d in self.members if _reader_hash(d)]


def _hm(seconds: int) -> str:
    minutes = int(seconds) // 60
    if minutes < 60:
        return f"{minutes} min"
    return f"{minutes // 60} h {minutes % 60:02d} min"


def _utc(ts: int) -> datetime:
    return datetime.fromtimestamp(ts, timezone.utc).replace(tzinfo=None)


def _date_noon(ts: int) -> datetime:
    """A calendar date stored as UTC midnight, as noon UTC so it reads as the
    same day in every timezone Tome shows it in."""
    return _utc(ts).replace(hour=12, minute=0, second=0, microsecond=0)


def _sane_ts(value: Any, now_ts: int) -> Optional[int]:
    if isinstance(value, int) and not isinstance(value, bool) and EPOCH_2000 <= value <= now_ts + 2 * 86400:
        return value
    return None


def _clean_title(row: Optional[dict[str, Any]]) -> Optional[str]:
    if not row:
        return None
    title = row.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()[:200]
    name = row.get("filename")
    if isinstance(name, str) and name.strip():
        return (PurePosixPath(name.strip()).stem or name.strip())[:200]
    return None


# ── the run ──────────────────────────────────────────────────────────────────

def _run(db: Session, user: User, archive: Archive, now_ts: int) -> dict[str, Any]:
    S = archive.sections
    uid = user.id

    alias_of = {r["alias"]: r["document"] for r in S["document_aliases"]}
    merge_stats = {r["alias"]: (r.get("merge_stats") if r.get("merge_stats") is not None else 1)
                   for r in S["document_aliases"]}

    def canonical(doc: str) -> str:
        while doc in alias_of:
            doc = alias_of[doc]
        return doc

    families: dict[str, Family] = {}

    def family(doc: str) -> Family:
        canon = canonical(doc)
        fam = families.get(canon)
        if fam is None:
            fam = families[canon] = Family(canonical=canon, members=[canon])
        if doc not in fam.members:
            fam.members.append(doc)
        return fam

    for alias in alias_of:
        family(alias)
    doc_rows: dict[str, dict[str, Any]] = {}
    for row in S["documents"]:
        doc_rows[row["document"]] = row
        family(row["document"])
    for name in ("progress", "progress_log", "reading_sessions", "clippings", "stats_device_book"):
        for row in S[name]:
            family(row["document"])

    # ── what the file holds per book ──
    pct: dict[str, float] = defaultdict(float)
    pct_at: dict[str, int] = {}
    pct_src: dict[str, tuple[str, Optional[str]]] = {}
    device_names: dict[str, str] = {}
    progress_tally = Tally(in_file=len(S["progress"]))
    for i, row in enumerate(S["progress"]):
        fam = family(row["document"])
        value = float(row["percentage"])
        if not 0 <= value <= 1:
            progress_tally.skip("percentage outside 0..1", {"row": i, "document": row["document"]})
            continue
        if isinstance(row.get("device"), str) and row["device"].strip():
            device_names.setdefault(row["device_id"], row["device"].strip()[:255])
        if value > pct[fam.canonical]:
            pct[fam.canonical] = value
            at = _sane_ts(row["updated_at"], now_ts)
            if at is not None:
                pct_at[fam.canonical] = at
            else:
                pct_at.pop(fam.canonical, None)
            pct_src[fam.canonical] = (row["device_id"], row.get("device") if isinstance(row.get("device"), str) else None)
        if value > 0 and row["device_id"] not in SERVICE_DEVICE_IDS:
            fam.has_data = True   # a reader's position; a service's alone is not reading data

    log_finish: dict[str, int] = {}
    log_src: dict[str, str] = {}
    by_doc: dict[str, list[tuple[int, float, str]]] = defaultdict(list)
    unusable_log_times = 0
    for row in S["progress_log"]:
        at = _sane_ts(row["at"], now_ts)
        if at is None:
            unusable_log_times += 1
            continue
        by_doc[family(row["document"]).canonical].append(
            (at, float(row["percentage"]), str(row.get("device_id") or "")))
    for canon, points in by_doc.items():
        best = 0.0
        for at, value, source in sorted(points):
            best = max(best, value)
            if best >= FINISHED_AT:
                log_finish[canon] = at
                log_src[canon] = source
                break

    device_finish: dict[str, int] = {}
    device_completed: set[str] = set()
    unreadable_book_stats = 0
    for row in S["stats_device_book"]:
        fam = family(row["document"])
        if row["document"] != fam.canonical and not merge_stats.get(row["document"], 1):
            continue   # merged "kept apart": its stats do not count for the book
        snap = _json_object(row["payload"])
        if snap is None:
            unreadable_book_stats += 1
            continue
        fam.has_data = True   # the reader's own counter for this book
        if snap.get("completed") is True:
            device_completed.add(fam.canonical)
            fin = _sane_ts(snap.get("finished_date"), now_ts)
            if fin and fin > device_finish.get(fam.canonical, 0):
                device_finish[fam.canonical] = fin

    for row in S["clippings"]:
        if not row.get("deleted"):
            family(row["document"]).has_data = True

    for row in S["reading_sessions"]:
        fam = family(row["document"])
        fam.has_data = True
        fam.sessions_in_file += 1
        fam.seconds_in_file += max(0, int(row["seconds"]))

    for fam in families.values():
        # A status or a date alone is not reading data: CrossPoint Sync's
        # tracker import sets them for whole Goodreads shelves.
        members = fam.reader_members or fam.members
        lead = next((doc_rows[d] for d in members if d in doc_rows and _clean_title(doc_rows[d])), None) \
            or doc_rows.get(fam.canonical)
        fam.title = _clean_title(lead)
        author = (lead or {}).get("author")
        fam.author = author.strip()[:200] if isinstance(author, str) and author.strip() else None

    # ── 1. books ──
    books = Tally(in_file=len(families))
    own_file_less_before = {
        bid for (bid,) in db.query(Book.id).filter(Book.status == BOOK_NO_FILE, Book.added_by == uid)
    }
    from backend.api.kosync import _find_or_hash_link
    from backend.services.kosync_stats import attach_sessions

    resolver: dict[str, Optional[int]] = {}
    ordered = sorted(families.values(), key=lambda f: ((f.title or "").casefold(), f.canonical))
    extra = Tally()
    candidates = kosync_books.library_candidates(db, user)
    for fam in ordered:
        example = {"document": fam.canonical[:64], "title": fam.title}
        if not fam.reader_members:
            fam.outcome = "not_reader_file"
            books.skip("not a reader file (tracker import)", example)
            continue
        if not fam.has_data:
            fam.outcome = "no_data"
            books.skip("no reading recorded for this file", example)
            continue
        maps = {
            m.document: m for m in db.query(KOSyncDocumentMap).filter(
                KOSyncDocumentMap.tome_user_id == uid, KOSyncDocumentMap.document.in_(fam.reader_members))
        }
        lead_map = maps.get(fam.canonical) or next((maps[d] for d in fam.reader_members if d in maps), None)
        if lead_map is not None:
            fam.outcome = "already_linked"
        else:
            for doc in fam.reader_members:
                lead_map = _find_or_hash_link(db, uid, doc, commit=False)
                if lead_map is not None:
                    maps[doc] = lead_map
                    fam.outcome = "hash_link"
                    break

        # What the reader said about the file, for documents Tome has not
        # been told about yet (a newer description from the live sync wins).
        metas = {
            m.document: m for m in db.query(KOSyncDocumentMeta).filter(
                KOSyncDocumentMeta.user_id == uid, KOSyncDocumentMeta.document.in_(fam.reader_members))
        }
        for doc in fam.reader_members:
            row = doc_rows.get(doc)
            if doc in metas or row is None:
                continue
            meta = kosync_books.store_metadata(db, uid, doc, {
                "filename": row.get("filename"), "title": row.get("title"),
                "authors": row.get("author"), "isbn": row.get("isbn"),
            })
            if meta is not None:
                metas[doc] = meta
                extra.add("descriptions")
        if metas:
            db.flush()

        if lead_map is None:
            lead_doc = next((d for d in fam.reader_members if d in metas and kosync_books.display_title(metas[d])), None)
            if lead_doc is None:
                fam.outcome = "unlinked"
                fam.reason = "the export has no title or file name for this file"
            else:
                meta = metas[lead_doc]
                lead_map = kosync_books.resolve(db, user, lead_doc, meta, commit=False, candidates=candidates)
                if lead_map is None:
                    fam.outcome = "unlinked"
                    fam.reason = (
                        "you deleted the book Tome made for this file earlier" if meta.ignored
                        else "it matches several books in your library; link it by hand on the book page"
                    )
                else:
                    maps[lead_doc] = lead_map
                    book = db.get(Book, lead_map.book_id)
                    if book.status == BOOK_NO_FILE and book.id not in own_file_less_before:
                        fam.outcome = "created"
                    elif book.status == BOOK_NO_FILE:
                        fam.outcome = "file_less_existing"
                    else:
                        fam.outcome = "matched"

        if lead_map is None:
            books.skip(f"left unlinked: {fam.reason}", example)
            continue

        fam.book_id = lead_map.book_id
        for doc in fam.reader_members:
            if doc not in maps:
                maps[doc] = KOSyncDocumentMap(tome_user_id=uid, document=doc, book_id=fam.book_id)
                db.add(maps[doc])
                if fam.outcome == "already_linked":
                    fam.outcome = "already_linked_more"
        db.flush()
        if len({m.book_id for m in maps.values()}) > 1:
            fam.conflict = True
        for doc, doc_map in maps.items():
            resolver[doc.lower()] = doc_map.book_id
            # Sessions the live sync stored before this document had a book.
            attached = attach_sessions(db, uid, doc, doc_map.book_id)
            if attached:
                extra.add("earlier_sessions_linked", attached)
        book = db.get(Book, fam.book_id)
        fam.book_title, fam.book_author, fam.book_status = book.title, book.author, book.status
        books.add(fam.outcome)

    # ── 2. device totals (before sessions: a session is named after its device) ──
    devices = Tally(in_file=len(S["stats_device_global"]))
    now = datetime.utcnow()
    for i, row in enumerate(S["stats_device_global"]):
        device_id = row["device_id"]
        example = {"row": i, "device_id": device_id[:64]}
        if not kosync_stats.valid_device_id(device_id):
            devices.skip("device id Tome cannot store (1-64 letters, digits, - or _)", example)
            continue
        payload = _json_object(row["payload"])
        if payload is None:
            devices.skip("totals are not readable JSON", example)
            continue
        name = row.get("device").strip()[:255] if isinstance(row.get("device"), str) and row["device"].strip() else None
        existing = db.query(KOSyncDeviceStats).filter(
            KOSyncDeviceStats.user_id == uid, KOSyncDeviceStats.device_id == device_id).first()
        if existing is not None:
            if not existing.device and name:
                existing.device = name
                extra.add("device_names")
            devices.add("already")   # the reader's own newer snapshot stays
            continue
        try:
            snapshot = kosync_stats.snapshot_json({"device_id": device_id, "device": row.get("device") or "", **payload})
        except Rejected:
            devices.skip("totals larger than Tome stores", example)
            continue
        db.add(KOSyncDeviceStats(user_id=uid, device_id=device_id, device=name, snapshot=snapshot, updated_at=now))
        devices.add("created")
        if name:
            device_names[device_id] = name
    db.flush()

    # ── 3. daily counters ──
    daily = Tally(in_file=len(S["stats_device_day"]))
    per_device: dict[str, dict[str, int]] = defaultdict(dict)
    for i, row in enumerate(S["stats_device_day"]):
        example = {"row": i, "device_id": row["device_id"][:64], "date": str(row["date"])[:10]}
        if not kosync_stats.valid_device_id(row["device_id"]):
            daily.skip("device id Tome cannot store (1-64 letters, digits, - or _)", example)
            continue
        try:
            key, seconds = kosync_stats.parse_day({"date": row["date"], "seconds": row["seconds"]})
        except Rejected as exc:
            daily.skip(exc.detail.lower(), example)
            continue
        days = per_device[row["device_id"]]
        days[key] = max(days.get(key, 0), seconds)
    for device_id, days in per_device.items():
        for outcome in kosync_stats.merge_daily(db, uid, device_id, days, now).values():
            daily.add(outcome)
    db.flush()

    # ── 4. reading sessions ──
    sessions = Tally(in_file=len(S["reading_sessions"]))
    seen_ids: set[str] = set()
    added_seconds = 0
    first_start: Optional[int] = None
    last_end: Optional[int] = None

    def resolve_book(doc: str) -> Optional[int]:
        return resolver.get(doc.lower())

    reasons = {
        "Invalid session_id": "session id Tome cannot store",
        "Invalid document": "document is not a 32-character hash",
        "Invalid started_at": "start time before 2000 or not a time",
        "Invalid ended_at": "ends before it starts",
        "Invalid seconds": "shorter than 10 seconds of reading",
        "Session duration does not fit its time span": "reading time longer than the session, or a session over 7 days",
        "Session ends in the future": "ends in the future (reader clock was wrong)",
    }
    pending: list[tuple[int, dict[str, Any], Family, dict[str, Any], Any]] = []
    for i, row in enumerate(S["reading_sessions"]):
        fam = family(row["document"])
        example = {"row": i, "session_id": str(row["session_id"])[:64], "document": row["document"][:64],
                   "title": fam.title}
        if not kosync_stats.valid_device_id(row["device_id"]):
            sessions.skip("device id Tome cannot store (1-64 letters, digits, - or _)", example)
            continue
        if row["session_id"] in seen_ids:
            sessions.skip("the same session id appears twice in the file", example)
            continue
        try:
            item = kosync_stats.parse_session({
                "session_id": row["session_id"], "document": row["document"],
                "started_at": row["started_at"], "ended_at": row["ended_at"], "seconds": row["seconds"],
            }, now_ts)
        except Rejected as exc:
            sessions.skip(reasons.get(exc.detail, exc.detail), example)
            continue
        seen_ids.add(row["session_id"])
        pending.append((i, row, fam, example, item))

    # What Tome already has under these ids, in one query per 500 instead of
    # one per session. A receipt for a different session is skipped here, so
    # ``stage_sessions`` (which would refuse the whole batch) never sees it.
    known: dict[str, Optional[KOSyncSessionReceipt]] = {item.session_id: None for *_, item in pending}
    ids = list(known)
    for start in range(0, len(ids), 500):
        for receipt in db.query(KOSyncSessionReceipt).filter(
                KOSyncSessionReceipt.user_id == uid, KOSyncSessionReceipt.session_uuid.in_(ids[start:start + 500])):
            known[receipt.session_uuid] = receipt
    by_device: dict[str, list[tuple[int, dict[str, Any], Family, dict[str, Any], Any]]] = defaultdict(list)
    for entry in pending:
        item = entry[4]
        receipt = known[item.session_id]
        if receipt is not None and (receipt.document, receipt.started_at) != (item.document, item.started_at):
            sessions.skip("Tome already has a different session under this id", entry[3])
            continue
        by_device[entry[1]["device_id"]].append(entry)
    for device_id, entries in by_device.items():
        outcomes = kosync_stats.stage_sessions(
            db, uid, device_id, [e[4] for e in entries], resolve_book,
            fallback_name=device_names.get(device_id), known_receipts=known,
        )
        for (i, row, fam, example, item), (outcome, added) in zip(entries, outcomes):
            sessions.add(outcome)
            if outcome == "created":
                fam.sessions_new += 1
                first_start = row["started_at"] if first_start is None else min(first_start, row["started_at"])
                last_end = row["ended_at"] if last_end is None else max(last_end, row["ended_at"])
            fam.seconds_new += added
            added_seconds += added
    db.flush()

    nudge_hardcover = False
    # ── 5. progress and read status ──
    # The export is history. Where Tome already has a book as reading, read
    # or shelved, Tome's row wins (a re-read, a book set aside since): its
    # status and progress stay, and only a missing finish date of a read book
    # is filled in. Missing, unread and want-to-read rows take the export's
    # status, progress and finish. A row this import itself wrote (two files
    # of one book) goes on taking the export's.
    from backend.services.book_progress import apply_progress_to_status
    statuses = Tally()
    written: set[int] = set()
    for fam in ordered:
        if fam.book_id is None:
            continue
        info = doc_rows.get(fam.canonical) or next((doc_rows[d] for d in fam.reader_members if d in doc_rows), {})
        status = info.get("status")
        manual = _sane_ts(info.get("finished_date"), now_ts)
        dated = manual or (device_finish.get(fam.canonical) if fam.canonical in device_completed else None)
        logged = log_finish.get(fam.canonical)
        finish_dt: Optional[datetime] = None
        finish_from_log = False
        if dated:
            finish_dt = _date_noon(dated)
        elif logged:
            finish_dt = _utc(logged)
            finish_from_log = True
        finished = False
        if status is None:
            finished = finish_dt is not None or fam.canonical in device_completed
        elif status == "finished":
            finished = True
            status_at = _sane_ts(info.get("status_at"), now_ts)
            if finish_dt is None and status_at:
                finish_dt = _utc(status_at)
        best = pct.get(fam.canonical, 0.0)
        pos_dt = _utc(pct_at[fam.canonical]) if fam.canonical in pct_at else None
        if not finished and best <= 0 and status not in ("dnf", "paused"):
            continue
        statuses.in_file += 1

        row = db.query(UserBookStatus).filter(
            UserBookStatus.user_id == uid, UserBookStatus.book_id == fam.book_id).first()
        before = (row.status, row.progress_pct, row.finished_at) if row is not None else None

        if row is not None and row.status in ("reading", "read", "shelved") and fam.book_id not in written:
            if row.status == "read" and row.finished_at is None and finished and (finish_dt or pos_dt):
                row.finished_at = finish_dt or pos_dt
                db.flush()
                statuses.add("finish_dated")
                fam.status_change = f"finish date filled in: {row.finished_at.date().isoformat()}"
                continue
            statuses.add("kept")
            fam.status_change = f"status kept: Tome has {row.status} at {round((row.progress_pct or 0) * 100)}%"
            continue

        undated = False
        if best > 0:
            row = apply_progress_to_status(db, user_id=uid, book_id=fam.book_id, pct=best,
                                           monotonic=True, status_row=row, nudge=False)
        if finished:
            if row is None:
                row = UserBookStatus(user_id=uid, book_id=fam.book_id, status="unread")
                db.add(row)
            if before is None or before[0] != "read":
                row.status = "read"
                row.progress_pct = 1.0
                row.finished_at = finish_dt or pos_dt or datetime.utcnow()
                undated = finish_dt is None and pos_dt is None
            elif row.finished_at is None and (finish_dt or pos_dt):
                row.finished_at = finish_dt or pos_dt
        elif row is not None and row.status == "read" and (before is None or before[0] != "read"):
            # Finished by progress alone (99% or more): date it by the position.
            if pos_dt is not None:
                row.finished_at = pos_dt
            else:
                undated = True   # apply_progress_to_status dated it now
        elif status == "dnf" or (status == "paused" and info.get("pause_reason") == "manual"):
            if before is None or before[0] in ("unread", "want_to_read"):
                if row is None:
                    row = UserBookStatus(user_id=uid, book_id=fam.book_id, status="shelved")
                    db.add(row)
                else:
                    row.status = "shelved"
                fam.status_note = "did not finish" if status == "dnf" else "paused"

        db.flush()   # two files of one Tome book share this row
        after = (row.status, row.progress_pct, row.finished_at) if row is not None else None
        if after != before:
            written.add(fam.book_id)
        # Say so when what Tome applies came from a service connection.
        if after != before and after is not None:
            newly_read = after[0] == "read" and (before is None or before[0] != "read")
            src = pct_src.get(fam.canonical)
            if newly_read and finished and status != "finished" and not dated and finish_from_log:
                log_source = log_src.get(fam.canonical, "")
                if log_source in SERVICE_DEVICE_IDS:
                    fam.source_note = f'the finish comes from the progress history of the service "{log_source}", not a reader'
                elif log_source == HISTORY_DEVICE_ID:
                    fam.source_note = 'the finish comes from progress history whose source CrossPoint Sync did not record ("history")'
            # Finished by CrossPoint Sync's own record (status, a date, the
            # reader's counter or the progress history), not by the position.
            recorded_finish = finished and (status == "finished" or bool(dated) or finish_from_log
                                            or fam.canonical in device_completed)
            moved = after[1] != (before[1] if before else None)
            if fam.source_note is None and src is not None and src[0] in SERVICE_DEVICE_IDS and best > 0 and (
                    (newly_read and not recorded_finish) or (not newly_read and moved)):
                label = f'"{src[0]}"' + (f" ({src[1]})" if src[1] else "")
                fam.source_note = f"the furthest position ({round(best * 100)}%) comes from the service {label}, not a reader"
        if after == before:
            statuses.add("unchanged")
            if before is not None and before[0] == "read":
                fam.status_change = "already finished in Tome"
            continue
        was = before[0] if before else "unread"
        if after[0] == "read" and was != "read":
            statuses.add("finished")
            nudge_hardcover = True
            if undated:
                fam.status_change = "marked finished; the file has no date for it, so it is dated at the import"
            else:
                fam.status_change = "marked finished" + (f" on {after[2].date().isoformat()}" if after[2] else "")
        elif after[0] == "shelved" and was != "shelved":
            statuses.add("shelved")
            fam.status_change = f"set aside ({fam.status_note} in CrossPoint Sync)"
        elif after[0] == "read" and after[2] != (before[2] if before else None):
            statuses.add("finish_dated")
            fam.status_change = f"finish date filled in: {after[2].date().isoformat()}"
        else:
            statuses.add("progress")
            old = round((before[1] or 0) * 100) if before else 0
            fam.status_change = f"progress {old}% -> {round((after[1] or 0) * 100)}%"
    db.flush()

    # ── 5b. per-book page counters (the reader's lifetime snapshot per book) ──
    book_pages = Tally(in_file=len(S["stats_device_book"]))
    snap_items: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    snap_sent: dict[str, dict[str, datetime]] = defaultdict(dict)
    snap_rows: dict[tuple[str, str], int] = {}
    for i, row in enumerate(S["stats_device_book"]):
        doc = row["document"]
        fam = family(doc)
        example = {"row": i, "device_id": row["device_id"][:64], "document": doc[:64], "title": fam.title}
        if not kosync_stats.valid_device_id(row["device_id"]):
            book_pages.skip("device id Tome cannot store (1-64 letters, digits, - or _)", example)
            continue
        if not kosync_clippings.valid_document(doc):
            book_pages.skip("document is not a 32-character hash", example)
            continue
        if doc != fam.canonical and not merge_stats.get(doc, 1):
            book_pages.skip("merged in CrossPoint Sync with its stats kept apart", example)
            continue
        payload = _json_object(row["payload"])
        if payload is None:
            book_pages.skip("totals are not readable JSON", example)
            continue
        key = doc.lower()
        snap_items[row["device_id"]][key] = {"document": key, **{k: v for k, v in payload.items() if k != "document"}}
        if _sane_ts(row.get("updated_at"), now_ts):
            snap_sent[row["device_id"]][key] = _utc(row["updated_at"])
        snap_rows[(row["device_id"], key)] = i
    for device_id, items in snap_items.items():
        outcomes = kosync_stats.stage_books(db, uid, device_id, items, sent_at=snap_sent[device_id])
        for outcome in outcomes.values():
            book_pages.add({"kept_newer": "already", "unchanged": "already"}.get(outcome, outcome))
    db.flush()
    pages_by_book = kosync_stats.reader_pages(
        db, uid, sorted({f.book_id for f in families.values() if f.book_id is not None}))
    for fam in families.values():
        if fam.book_id is not None and fam.book_id in pages_by_book:
            fam.reader_pages = pages_by_book[fam.book_id].pages

    # ── 6. clippings ──
    clippings = Tally(in_file=len(S["clippings"]))
    chosen: dict[tuple[str, str], tuple[int, dict[str, Any]]] = {}
    for i, row in enumerate(S["clippings"]):
        fam = family(row["document"])
        key = (fam.canonical, row["id"])
        rank = (1 if row.get("deleted") else 0, row.get("updated_at") or 0, row["document"] == fam.canonical)
        held = chosen.get(key)
        if held is None:
            chosen[key] = (i, row)
            continue
        hrow = held[1]
        hrank = (1 if hrow.get("deleted") else 0, hrow.get("updated_at") or 0, hrow["document"] == fam.canonical)
        if rank > hrank:
            chosen[key] = (i, row)
        clippings.add("merged_copy")   # an older copy of the same clipping under a merged hash
    for (canon, clip_id), (i, row) in sorted(chosen.items(), key=lambda kv: kv[1][0]):
        fam = families[canon]
        doc = row["document"]
        example = {"row": i, "document": doc[:64], "title": fam.title, "clipping_id": str(clip_id)[:64]}
        if not kosync_clippings.valid_document(doc):
            clippings.skip("document is not a 32-character hash", example)
            continue
        family_docs = [d.lower() for d in fam.members if kosync_clippings.valid_document(d)]
        if db.query(KOSyncClipping.id).filter(
                KOSyncClipping.user_id == uid, KOSyncClipping.document.in_(family_docs),
                KOSyncClipping.clip_id == clip_id).first():
            clippings.add("already")
            continue
        if row.get("deleted"):
            raw = {"id": clip_id, "deleted": 1}
        else:
            raw = {
                "id": clip_id, "spine": row.get("spine_index") or 0,
                "start_page": row.get("start_page") or 0, "end_page": row.get("end_page") or 0,
                "pages": row["page_count"] if row.get("page_count") is not None else 1,
                "start_word": row.get("start_word") or 0, "end_word": row.get("end_word") or 0,
                "words": row.get("word_count") or 0, "para": row.get("paragraph_index"),
                "chapter": row.get("chapter_title") or "", "text": row.get("text"),
                "created_at": row.get("created_at") or 0,
                "layout_signature": row.get("layout_signature") or 0,
                "start_offset": row.get("start_offset"), "end_offset": row.get("end_offset"),
            }
            if row.get("note") is not None:
                raw["note"] = row["note"]
            if row.get("color") is not None:
                raw["color"] = row["color"]
        try:
            item = kosync_clippings.parse_item(raw)
        except Rejected as exc:
            reason = {
                "Invalid clipping text": "no text, or text longer than 4096 bytes",
                "Invalid clipping id": "clipping id Tome cannot store",
                "Invalid clipping offsets": "start and end offsets do not fit",
            }.get(exc.detail, "a position value outside what the reader can read back")
            clippings.skip(reason, example)
            continue
        kosync_clippings.store(db, uid, doc, [item], now_ts, commit=False)
        clippings.add("deleted" if row.get("deleted") else "created")
        if not row.get("deleted"):
            fam.clippings_new += 1
    db.flush()

    report = _report(archive, families, ordered, books, sessions, statuses, clippings, daily, devices,
                     progress_tally, added_seconds, first_start, last_end, unreadable_book_stats)
    report["book_pages"] = book_pages.out()
    report["also_written"] = extra.out()
    report["_nudge_hardcover"] = nudge_hardcover
    if unusable_log_times:
        report["notes"].append(f"{unusable_log_times} progress history rows have a time before 2000 or in the "
                               "future and were not used")
    if extra.counts:
        report["nothing_new"] = False
    if book_pages.counts.get("created") or book_pages.counts.get("replaced"):
        report["nothing_new"] = False
    return report


OUTCOME_TEXT = {
    "already_linked": "already linked to {book}",
    "already_linked_more": "already linked to {book}; its other hashes now point there too",
    "hash_link": "linked to {book} (a file Tome served)",
    "matched": "linked to library book {book}",
    "file_less_existing": "linked to your earlier book without a file {book}",
    "created": "new book without a file {book}",
}


def _report(archive: Archive, families: dict[str, Family], ordered: list[Family], books: Tally,
            sessions: Tally, statuses: Tally, clippings: Tally, daily: Tally, devices: Tally,
            progress_tally: Tally, added_seconds: int, first_start: Optional[int],
            last_end: Optional[int], unreadable_book_stats: int) -> dict[str, Any]:
    book_rows = []
    for fam in ordered:
        if fam.outcome == "no_data":
            continue
        book_rows.append({
            "document": fam.canonical,
            "documents": fam.members,
            "title": fam.title,
            "author": fam.author,
            "outcome": fam.outcome,
            "reason": fam.reason,
            "book_id": fam.book_id,
            "book_title": fam.book_title,
            "book_author": fam.book_author,
            "file_less": fam.book_status == BOOK_NO_FILE,
            "conflict": fam.conflict,
            "sessions_in_file": fam.sessions_in_file,
            "seconds_in_file": fam.seconds_in_file,
            "sessions_new": fam.sessions_new,
            "seconds_new": fam.seconds_new,
            "clippings_new": fam.clippings_new,
            "status_change": fam.status_change,
            "source_note": fam.source_note,
            "reader_pages": fam.reader_pages,
        })
    not_imported = [
        {"section": name, "rows": archive.counts.get(name, 0), "why": why}
        for name, why in {**READ_ONLY, **NOT_IMPORTED}.items() if archive.counts.get(name)
    ]
    for name, count in sorted(archive.unknown_sections.items()):
        not_imported.append({"section": name, "rows": count, "why": "unknown to this Tome version"})

    new_links = sum(books.counts.get(k, 0) for k in ("hash_link", "matched", "file_less_existing", "created", "already_linked_more"))
    changed = (
        new_links
        + sessions.counts.get("created", 0) + sessions.counts.get("extended", 0)
        + sum(v for k, v in statuses.counts.items() if k not in ("unchanged", "kept"))
        + clippings.counts.get("created", 0) + clippings.counts.get("deleted", 0)
        + daily.counts.get("created", 0) + daily.counts.get("raised", 0)
        + devices.counts.get("created", 0)
    )
    report = {
        "file": {
            "format": FORMAT,
            "version": VERSION,
            "exported_at": archive.exported_at,
            "source_username": archive.source_username,
            "bytes": archive.size,
            "sections": archive.counts,
            "missing_sections": archive.missing_sections,
            "unknown_sections": archive.unknown_sections,
            "unknown_columns": archive.unknown_columns,
        },
        "nothing_new": changed == 0,
        "books": books.out(),
        "sessions": sessions.out(),
        "statuses": statuses.out(),
        "clippings": clippings.out(),
        "daily": daily.out(),
        "devices": devices.out(),
        "progress": progress_tally.out(),
        "seconds_added": added_seconds,
        "first_session": _utc(first_start).isoformat() + "Z" if first_start else None,
        "last_session": _utc(last_end).isoformat() + "Z" if last_end else None,
        "book_list": book_rows,
        "not_imported": not_imported,
        "notes": _notes(archive, unreadable_book_stats),
    }
    return report


def _notes(archive: Archive, unreadable_book_stats: int) -> list[str]:
    notes = []
    if archive.missing_sections:
        notes.append("Sections missing from the file (read as empty): " + ", ".join(archive.missing_sections))
    if unreadable_book_stats:
        notes.append(f"{unreadable_book_stats} per-book device totals were not readable and were not used for finish dates")
    return notes


def _book_label(title: Optional[str], author: Optional[str]) -> str:
    label = f"\"{title}\"" if title else "(no title)"
    return f"{label} by {author}" if author else label


def render_text(report: dict[str, Any], applied: bool) -> str:
    """The report as plain text, for the tester to paste back. English only,
    like every server message. Titles are kept; nothing else from the file."""
    f = report["file"]
    lines = [
        f"Tome {'import' if applied else 'preview'} of a CrossPoint Sync account export"
        + ("" if applied else " (nothing written yet)"),
        f"File: exported {f['exported_at'] or 'at an unknown time'}"
        + (f" by user \"{f['source_username']}\"" if f['source_username'] else "")
        + f", {f['bytes'] / 1048576:.2f} MiB, format {f['format']} v{f['version']}",
        "Sections: " + ", ".join(f"{k} {v}" for k, v in f["sections"].items()),
    ]
    if f["unknown_sections"]:
        lines.append("Unknown sections (ignored): " + ", ".join(f"{k} ({v} row{'' if v == 1 else 's'})" for k, v in f["unknown_sections"].items()))
    if f["unknown_columns"]:
        lines.append("Unknown columns (ignored): " + ", ".join(
            f"{s}.{c}" for s, cols in f["unknown_columns"].items() for c in cols))
    for note in report["notes"]:
        lines.append("Note: " + note)
    lines.append("")
    if report["nothing_new"]:
        lines.append("Nothing new: everything in this file is already in Tome.")
    lines.append("Summary")

    def counts(t: dict[str, Any], labels: list[tuple[str, str]]) -> str:
        parts = [f"{t['counts'][k]} {label}" for k, label in labels if t["counts"].get(k)]
        if t["skipped"]:
            parts.append(f"{t['skipped']} skipped")
        return ", ".join(parts) or "none"

    b = report["books"]
    lines.append(f"  Books: {b['in_file']} in the file: " + counts(b, [
        ("matched", "linked to library books"), ("hash_link", "linked by Tome's own file hash"),
        ("file_less_existing", "linked to earlier books without a file"),
        ("created", "new books without a file"), ("already_linked", "already linked"),
        ("already_linked_more", "already linked (more hashes added)")]))
    s = report["sessions"]
    lines.append(f"  Reading sessions: {s['in_file']} in the file: " + counts(s, [
        ("created", "new"), ("extended", "longer than Tome had"), ("unchanged", "already in Tome"),
        ("deleted_in_tome", "deleted in Tome (left deleted)")]))
    span = ""
    if report["first_session"]:
        span = f", new sessions from {report['first_session'][:10]} to {report['last_session'][:10]}"
    lines.append(f"  Reading time added: {_hm(report['seconds_added'])}{span}")
    st = report["statuses"]
    lines.append(f"  Read status: {st['in_file']} books with progress or a finish: " + counts(st, [
        ("finished", "marked finished"), ("finish_dated", "finish date filled in"),
        ("progress", "progress moved forward"), ("shelved", "set aside"),
        ("kept", "kept as Tome has them"), ("unchanged", "unchanged")]))
    c = report["clippings"]
    lines.append(f"  Clippings: {c['in_file']} in the file: " + counts(c, [
        ("created", "new"), ("deleted", "deletion markers (deleted clippings)"), ("already", "already in Tome"),
        ("merged_copy", "older copies of merged books")]))
    d = report["daily"]
    lines.append(f"  Daily reading counters: {d['in_file']} in the file: " + counts(d, [
        ("created", "new"), ("raised", "raised to the larger value"), ("unchanged", "already in Tome")]))
    dv = report["devices"]
    lines.append(f"  Device totals: {dv['in_file']} in the file: " + counts(dv, [
        ("created", "new"), ("already", "already in Tome (kept)")]))
    bp = report["book_pages"]
    lines.append(f"  Book page counts: {bp['in_file']} reader snapshots in the file: " + counts(bp, [
        ("created", "new"), ("replaced", "newer than Tome's (replaced)"), ("already", "already in Tome or older than Tome's")]))
    aw = report["also_written"]
    if aw["counts"]:
        lines.append("  Also written: " + counts(aw, [
            ("descriptions", "file descriptions (title, author, file name) stored for the live sync"),
            ("earlier_sessions_linked", "earlier sessions from the live sync now linked to their book"),
            ("device_names", "device names filled in")]))
    if report["progress"]["skipped"]:
        lines.append(f"  Positions: {report['progress']['skipped']} skipped")

    skips = [(name, report[key]) for name, key in (
        ("Books", "books"), ("Reading sessions", "sessions"), ("Clippings", "clippings"),
        ("Daily reading counters", "daily"), ("Device totals", "devices"), ("Book page counts", "book_pages"),
        ("Positions", "progress"))]
    if any(t["skipped"] for _, t in skips):
        lines += ["", "Skipped"]
        for name, t in skips:
            for r in t["skip_reasons"]:
                lines.append(f"  {name}: {r['reason']} ({r['count']})")
                for ex in r["examples"]:
                    bits = []
                    for key in ("session_id", "clipping_id", "device_id", "date", "document"):
                        if ex.get(key):
                            bits.append(f"{key.replace('_', ' ')} {ex[key]}")
                    if ex.get("title"):
                        bits.append(f"\"{ex['title']}\"")
                    if "row" in ex:
                        bits.append(f"row {ex['row']}")
                    lines.append("    - " + ", ".join(bits))

    lines += ["", "Books"]
    for row in report["book_list"]:
        label = _book_label(row["title"], row["author"])
        if row["outcome"] == "unlinked":
            what = f"left unlinked: {row['reason']}"
        else:
            book = f"#{row['book_id']} \"{row['book_title']}\""
            what = OUTCOME_TEXT.get(row["outcome"], row["outcome"]).format(book=book)
        line = f"  - {label} [{row['document']}"
        if len(row["documents"]) > 1:
            line += f" + {len(row['documents']) - 1} merged"
        line += f"]: {what}"
        line += f"; {row['sessions_in_file']} sessions, {_hm(row['seconds_in_file'])} in the file"
        if row["sessions_new"] or row["seconds_new"]:
            line += f" ({row['sessions_new']} new, {_hm(row['seconds_new'])} added)"
        if row["clippings_new"]:
            line += f"; {row['clippings_new']} new clipping{'' if row['clippings_new'] == 1 else 's'}"
        if row["status_change"]:
            line += f"; {row['status_change']}"
        if row["source_note"]:
            line += f" ({row['source_note']})"
        if row["reader_pages"]:
            line += f"; pages: {row['reader_pages']} from the reader's counter"
        if row["conflict"]:
            line += "; note: its hashes were already linked to different Tome books, left as they are"
        lines.append(line)
    if not report["book_list"]:
        lines.append("  (none with reading data)")
    if report["not_imported"]:
        lines += ["", "Not imported"]
        for n in report["not_imported"]:
            lines.append(f"  {n['section']} ({n['rows']} row{'' if n['rows'] == 1 else 's'}): {n['why']}")
    lines.append("Also not imported: covers, service connections, profile settings.")
    return "\n".join(lines)


# ── entry point ──────────────────────────────────────────────────────────────

def execute(db: Session, user: User, raw: bytes, *, apply: bool,
            now_ts: Optional[int] = None,
            _after_run: Optional[Callable[[], None]] = None) -> dict[str, Any]:
    """Preview (``apply=False``) or import the file for ``user``.

    Both run the same code inside a savepoint; the preview rolls it back. The
    import commits it with an audit entry, or on any error rolls it back and
    re-raises, leaving the database as it was. Raises ``Refused`` for a file
    that cannot be read.
    """
    import time

    archive = parse(raw)
    now_ts = int(time.time()) if now_ts is None else now_ts
    db.commit()   # settle anything the request had pending before the savepoint

    def _no_commit() -> None:
        raise RuntimeError("commit inside the CrossPoint Sync import")

    savepoint = db.begin_nested()
    db.commit = _no_commit  # type: ignore[method-assign]
    try:
        report = _run(db, user, archive, now_ts)
        if _after_run is not None:
            _after_run()
        if apply:
            from backend.services.audit import audit
            audit(db, "kosync.crosspoint_import", user_id=user.id, username=user.username, commit=False,
                  details={
                      "bytes": archive.size,
                      "books": report["books"]["counts"], "books_skipped": report["books"]["skipped"],
                      "sessions": report["sessions"]["counts"], "sessions_skipped": report["sessions"]["skipped"],
                      "statuses": report["statuses"]["counts"],
                      "clippings": report["clippings"]["counts"],
                      "daily": report["daily"]["counts"], "devices": report["devices"]["counts"],
                      "seconds_added": report["seconds_added"],
                      "book_pages": report["book_pages"]["counts"],
                      "also_written": report["also_written"]["counts"],
                  })
            db.flush()
            savepoint.commit()
        else:
            savepoint.rollback()
    except BaseException:
        if savepoint.is_active:
            savepoint.rollback()
        raise
    finally:
        del db.commit
    # Ends the transaction. After an import it commits; after a preview the
    # savepoint is already rolled back and nothing else is pending (the
    # session was committed before it), so this commits nothing and only
    # releases SQLite's write lock, which ROLLBACK TO a savepoint keeps until
    # the transaction ends. (A commit, not a rollback: it is the same here, and
    # a session joined to an outer transaction, as in the tests, does not
    # propagate it.)
    db.commit()
    nudge = report.pop("_nudge_hardcover", False)
    if apply and nudge:
        from backend.services.book_progress import _nudge_hardcover
        _nudge_hardcover()
    db.expire_all()
    report["applied"] = apply
    report["text"] = render_text(report, apply)
    return report
