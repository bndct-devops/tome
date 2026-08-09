"""Tome ↔ Hardcover sync of ratings, reading progress, and Want to Read.

Direction: ratings/progress/reading/read are ONE-WAY Tome → Hardcover. The
Want to Read shelf is the single bidirectional piece — Tome pushes
``want_to_read`` as status 1 and pulls the shelf back each cycle (see
``pull_want_to_read``): entries land on unread/missing Tome rows, and removing
a shelf entry on Hardcover reverts a previously-agreed ``want_to_read`` row.

Design (docs/plans/hardcover-sync-plan.md):

- Per-user opt-in: the user links their PERSONAL hardcover.app API token in
  Settings (stored Fernet-encrypted; see backend/core/crypto.py) and can pause
  pushes with a separate toggle. The server-wide TOME_HARDCOVER_TOKEN is
  metadata-fetch-only and never used here.
- The worker is a stateless reconciler: a row needs sync iff its current
  rating/status/progress differ from the ``hardcover_synced_*`` snapshot on
  UserBookStatus. No dirty flags — a crashed cycle loses nothing, and the
  first-link backfill is just a big diff.
- Matching is book-level (``Book.hardcover_*``): ISBN exact lookup first (one
  query returns book id + edition id + pages), then a title+author search
  gated by a strict similarity guard — a wrong match writes reading data onto
  a stranger's book on a public profile, so we refuse rather than guess.
- The pinned EDITION is language-aware and audio-averse: an ISBN that names a
  translation's or the audiobook's edition (publishers share catalogues across
  both) still matches the book, but the edition is re-picked to agree with the
  Tome book's language and to have pages.
- Progress is page-based on Hardcover: ``round(pct × edition pages)``. Books
  whose matched edition has no page count get status-only sync.
- Rate limit: Hardcover allows 60 req/min. All users share one worker queue
  with ≥1.1 s spacing and a per-cycle request cap, so a first-link backlog
  spreads out instead of hammering a beta API.
- Beta-API posture: every response is checked structurally; anything odd is a
  per-row error with exponential backoff (in-memory next-attempt map), never an
  exception out of the worker. A 401 marks the token expired (they reset every
  Jan 1), notifies the user once, and pauses their sync until re-linked.

Nothing is ever deleted or cleared on Hardcover: rating clears and unread/
shelved statuses are not propagated.
"""
import asyncio
import difflib
import logging
import time
from datetime import datetime
from typing import Optional

import httpx
from fastapi import HTTPException
from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.core.crypto import decrypt_secret
from backend.models.book import Book
from backend.models.notification import Notification
from backend.models.user import User
from backend.models.user_book_status import UserBookStatus
from backend.models.wish import Wish

logger = logging.getLogger(__name__)

HARDCOVER_URL = "https://api.hardcover.app/v1/graphql"

# Hardcover user_book status ids. unread/shelved are deliberately unmapped —
# they are never pushed. want_to_read maps to Hardcover's "Want to Read" shelf
# and is the one status that also syncs BACK from Hardcover (see pull_want_to_read).
STATUS_ID = {"want_to_read": 1, "reading": 2, "read": 3}

MIN_REQUEST_SPACING = 1.1      # seconds between requests, all users combined (60/min limit)
MAX_REQUESTS_PER_CYCLE = 300   # cap so a 2000-book backlog spreads over cycles
MAX_ROW_FAILS = 10             # park a row after this many consecutive failures
NUDGE_DEBOUNCE = 30            # seconds of quiet after a rating nudge before syncing

# Similarity floor for the search fallback (mirrors metadata_rank's top tier).
TITLE_SIM_MIN = 0.85
AUTHOR_SIM_MIN = 0.7

# Hardcover reading_format_id for audiobook editions. Audio editions carry
# ISBNs too (Podium!) and have no page counts — never pin one.
AUDIO_FORMAT_ID = 2

# Marks wishes CREATED BY the shelf pull — the only wishes the shelf mirror is
# allowed to dismiss/reopen. Deliberately distinct from source="hardcover",
# which user-created wishes (metadata search) and series follows also carry:
# filtering the mirror on that dismissed every open follow on first sync
# (their "series:<id>" source_ids can never appear in a shelf of book ids).
SHELF_WISH_SOURCE = "hardcover_shelf"
SHELF_WISH_NOTE = "Added from your Hardcover Want to Read shelf"

# ── GraphQL documents ─────────────────────────────────────────────────────────
# Pinned to the beta schema as of 2026-07. Responses are parsed defensively;
# schema drift surfaces as a per-row error, not a crash.

Q_ME = "query { me { id username } }"

Q_EDITION_BY_ISBN = """
query EditionByIsbn($isbn: String!) {
    editions(where: {_or: [{isbn_13: {_eq: $isbn}}, {isbn_10: {_eq: $isbn}}]}, limit: 1) {
        id
        title
        pages
        book_id
        reading_format_id
        language { code2 }
        book { id title pages slug }
    }
}
"""

M_DELETE_USER_BOOK = """
mutation DeleteUserBook($id: Int!) {
    delete_user_book(id: $id) { id }
}
"""

Q_READS = """
query Reads($ubId: Int!) {
    user_book_reads(where: {user_book_id: {_eq: $ubId}}, order_by: {id: desc}, limit: 1) {
        id
    }
}
"""

Q_SEARCH = """
query SearchBook($q: String!) {
    search(query: $q, query_type: "Book", per_page: 5) {
        results
    }
}
"""

Q_BOOK_EDITION = """
query BookEdition($id: Int!) {
    books(where: {id: {_eq: $id}}, limit: 1) {
        id
        pages
        slug
        editions(limit: 30, order_by: {users_count: desc}) {
            id
            pages
            users_count
            reading_format_id
            language { code2 }
        }
    }
}
"""

Q_WTR_SHELF = """
query WantToReadShelf($uid: Int!, $offset: Int!) {
    user_books(where: {user_id: {_eq: $uid}, status_id: {_eq: 1}},
               order_by: {id: asc}, limit: 100, offset: $offset) {
        id
        book_id
    }
}
"""

Q_BOOKS_BY_IDS = """
query BooksByIds($ids: [Int!]!) {
    books(where: {id: {_in: $ids}}) {
        id
        title
        slug
        image { url }
        contributions { author { name } }
    }
}
"""

Q_USER_BOOK = """
query UserBook($uid: Int!, $bid: Int!) {
    user_books(where: {user_id: {_eq: $uid}, book_id: {_eq: $bid}}, limit: 1) {
        id
        status_id
        user_book_reads(order_by: {id: desc}, limit: 1) { id }
    }
}
"""

M_INSERT_USER_BOOK = """
mutation InsertUserBook($object: UserBookCreateInput!) {
    insert_user_book(object: $object) { id error }
}
"""

M_UPDATE_USER_BOOK = """
mutation UpdateUserBook($id: Int!, $object: UserBookUpdateInput!) {
    update_user_book(id: $id, object: $object) { id error }
}
"""

M_INSERT_READ = """
mutation InsertRead($ubId: Int!, $read: DatesReadInput!) {
    insert_user_book_read(user_book_id: $ubId, user_book_read: $read) { id error }
}
"""

M_UPDATE_READ = """
mutation UpdateRead($id: Int!, $object: DatesReadInput!) {
    update_user_book_read(id: $id, object: $object) { id error }
}
"""


# ── Errors ────────────────────────────────────────────────────────────────────

class HardcoverAuthError(Exception):
    """Token rejected (401/403) — expired (Jan-1 reset) or revoked."""


class HardcoverRateLimited(Exception):
    """429 — abort the cycle; the reconciler picks up next cycle."""


class HardcoverAPIError(Exception):
    """Any other failure talking to Hardcover (per-row, backoff-able)."""


# ── Throttle (module-level: one budget for the whole process) ─────────────────

_throttle_lock = asyncio.Lock()
_last_request_ts = 0.0


async def _gql(client: httpx.AsyncClient, token: str, query: str,
               variables: Optional[dict] = None) -> dict:
    """One throttled GraphQL call. Returns the ``data`` dict."""
    global _last_request_ts
    async with _throttle_lock:
        wait = _last_request_ts + MIN_REQUEST_SPACING - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        _last_request_ts = time.monotonic()
    try:
        resp = await client.post(
            HARDCOVER_URL,
            json={"query": query, "variables": variables or {}},
            # The stored token INCLUDES the "Bearer " prefix — Hardcover
            # requires it (verified live; a bare token gets "Malformed
            # Authorization header"). The link endpoint normalizes pasted
            # tokens to this form.
            headers={"authorization": token},
        )
    except httpx.HTTPError as exc:
        raise HardcoverAPIError(f"network: {exc}") from exc
    if resp.status_code == 429:
        raise HardcoverRateLimited()
    if resp.status_code in (401, 403):
        raise HardcoverAuthError()
    if resp.status_code >= 400:
        raise HardcoverAPIError(f"HTTP {resp.status_code}")
    body = resp.json()
    if body.get("errors"):
        msg = body["errors"][0].get("message", "GraphQL error")
        # Hasura reports auth problems as 200 + errors too.
        if "JWT" in msg or "auth" in msg.lower():
            raise HardcoverAuthError()
        raise HardcoverAPIError(msg)
    return body.get("data") or {}


# ── Token / identity ──────────────────────────────────────────────────────────

def user_token(user: User) -> Optional[str]:
    return decrypt_secret(user.hardcover_token)


async def verify_token(token: str) -> dict:
    """Validate a token and return {'id', 'username'}. Raises on failure."""
    async with httpx.AsyncClient(timeout=15) as client:
        data = await _gql(client, token, Q_ME)
    me = data.get("me")
    if isinstance(me, list):           # Hasura session queries return a list
        me = me[0] if me else None
    if not isinstance(me, dict) or me.get("id") is None:
        raise HardcoverAPIError("me query returned no user")
    return {"id": int(me["id"]), "username": me.get("username") or ""}


# ── ISBN helpers ──────────────────────────────────────────────────────────────

def normalize_isbn(raw: Optional[str]) -> Optional[str]:
    """Strip separators; return a 10/13-char ISBN or None."""
    if not raw:
        return None
    cleaned = "".join(ch for ch in raw if ch.isdigit() or ch in "xX")
    return cleaned.upper() if len(cleaned) in (10, 13) else None


def isbn10_to_13(isbn10: str) -> Optional[str]:
    if len(isbn10) != 10:
        return None
    core = "978" + isbn10[:9]
    if not core.isdigit():
        return None
    check = (10 - sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(core)) % 10) % 10
    return core + str(check)


def isbn_variants(raw: Optional[str]) -> list[str]:
    """The stored ISBN plus its 13-digit form when the stored one is a 10."""
    isbn = normalize_isbn(raw)
    if not isbn:
        return []
    variants = [isbn]
    if len(isbn) == 10:
        thirteen = isbn10_to_13(isbn)
        if thirteen:
            variants.append(thirteen)
    return variants


# ── Matching ──────────────────────────────────────────────────────────────────

def _sim(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def _norm_title(t: str) -> str:
    """Normalize for comparison: lowercase, drop commas/periods, squeeze spaces
    (so "Black Summoner, Vol. 2" ≈ "Black Summoner Vol 2")."""
    t = t.lower().replace(",", " ").replace(".", " ").replace(":", " ")
    return " ".join(t.split())


def _vol_in_title(title: str) -> Optional[float]:
    """Volume number carried by a candidate title ("…, Vol. 2", "… Volume 3",
    "… v04", "… #5"), or None."""
    import re
    m = (re.search(r"vol(?:ume)?\.?\s*(\d+(?:\.\d+)?)", title, re.IGNORECASE)
         or re.search(r"\bv(\d{1,3})\b", title, re.IGNORECASE)
         or re.search(r"#\s*(\d+(?:\.\d+)?)", title))
    return float(m.group(1)) if m else None


def _split_authors(author: Optional[str]) -> list[str]:
    """Tome stores multi-author books as one string ("A and B", "A & B",
    "A, B"); candidate author_names are individual. Compare part-wise."""
    if not author:
        return []
    import re
    parts = re.split(r"\s+(?:and|&)\s+|\s*[,;]\s*", author)
    return [p.strip() for p in parts if p.strip()]


def _fmt_index(idx: float) -> str:
    return str(int(idx)) if float(idx).is_integer() else str(idx)


def _is_manga_title(title: str) -> bool:
    return "(manga)" in title.lower() or "manga vol" in title.lower()


_LANG_NAMES = {
    "english": "en", "german": "de", "deutsch": "de", "japanese": "ja",
    "french": "fr", "spanish": "es", "italian": "it", "chinese": "zh",
    "korean": "ko", "portuguese": "pt",
}


def _lang2(raw: Optional[str]) -> Optional[str]:
    """Tome's Book.language down to a 2-letter code ('en-US' → 'en',
    'English' → 'en'), or None when unknown."""
    if not raw:
        return None
    s = raw.strip().lower()
    if s in _LANG_NAMES:
        return _LANG_NAMES[s]
    return s[:2] if len(s) >= 2 else None


def _edition_lang(ed: dict) -> Optional[str]:
    code = ((ed.get("language") or {}).get("code2") or "").strip().lower()
    return code or None


def _edition_acceptable(ed: dict, want_lang: Optional[str]) -> bool:
    """Is this specific edition safe to pin? Audio editions never are (no page
    counts, and the profile shows the audiobook cover); a known language must
    not contradict the Tome book's known language."""
    if ed.get("reading_format_id") == AUDIO_FORMAT_ID:
        return False
    ed_lang = _edition_lang(ed)
    return want_lang is None or ed_lang is None or ed_lang == want_lang


def _pick_edition(editions: list[dict], want_lang: Optional[str]) -> Optional[dict]:
    """Choose the edition to pin on a matched Hardcover book: language match
    first (unknown language beats a contradicting one), then non-audio, then
    the edition the community actually uses, then one with page data."""
    def score(ed: dict) -> tuple:
        ed_lang = _edition_lang(ed)
        if want_lang and ed_lang == want_lang:
            lang_rank = 2
        elif ed_lang is None or want_lang is None:
            lang_rank = 1
        else:
            lang_rank = 0
        non_audio = 0 if ed.get("reading_format_id") == AUDIO_FORMAT_ID else 1
        return (lang_rank, non_audio, ed.get("users_count") or 0,
                1 if ed.get("pages") else 0)
    candidates = [e for e in editions if e.get("id") is not None]
    return max(candidates, key=score) if candidates else None


def _isbn_hit_plausible(book, hc_title: str) -> bool:
    """Sanity-check an ISBN edition hit against what we think the book is.

    ISBN is the strongest identity signal, so this stays permissive — it only
    rejects contradictions: a manga/novel format mismatch, a volume number in
    the catalogue title that disagrees with ours, or a title with essentially
    no resemblance (ISBN pointing at an unrelated book)."""
    if not hc_title:
        return True  # nothing to check against
    tome_is_manga = bool(book.book_type and book.book_type.slug == "manga")
    if _is_manga_title(hc_title) != tome_is_manga:
        return False
    want_vol = book.series_index if book.series else None
    cand_vol = _vol_in_title(hc_title)
    if want_vol is not None and cand_vol is not None and cand_vol != want_vol:
        return False
    if want_vol is None and cand_vol is not None:
        return False  # standalone book, numbered catalogue title
    references = [book.title]
    if book.series:
        references.append(book.series)
        if want_vol is not None:
            references.append(f"{book.series} Vol. {_fmt_index(want_vol)}")
    best = max(_sim(_norm_title(hc_title), _norm_title(r)) for r in references)
    return best >= 0.5


async def _adopt_best_edition(client: httpx.AsyncClient, token: str, book: Book) -> bool:
    """Fetch the matched Hardcover book's editions and pin the best one
    (language-aware, audio-averse). Returns False when nothing usable came
    back so the caller can keep its own fallback. Auth/rate-limit errors
    propagate; a plain API error is just 'no better edition'."""
    try:
        data = await _gql(client, token, Q_BOOK_EDITION, {"id": book.hardcover_book_id})
    except HardcoverAPIError:
        return False
    books = data.get("books") or []
    if not books:
        return False
    b = books[0]
    if not book.hardcover_slug:
        book.hardcover_slug = b.get("slug")
    ed = _pick_edition(b.get("editions") or [], _lang2(book.language))
    if ed is None:
        book.hardcover_edition_id = None
        book.hardcover_pages = b.get("pages")
        return True
    book.hardcover_edition_id = ed.get("id")
    book.hardcover_pages = ed.get("pages") or b.get("pages")
    return True


async def match_book(client: httpx.AsyncClient, token: str, book: Book) -> bool:
    """Resolve a Tome book to a Hardcover book/edition, in place (no commit).

    Sets hardcover_book_id/edition_id/pages/match_method/matched_at. A failed
    match records method='none' + matched_at so it is not retried until the
    book's metadata changes (matched_at < updated_at).
    """
    book.hardcover_matched_at = datetime.utcnow()

    # 1. ISBN exact — one query yields book id, edition id, and pages. The hit
    # still has to pass a volume/format sanity check against the edition's own
    # title: stored ISBNs can be for the wrong edition (legacy auto-apply
    # damage — the reason /scribe audit editions exists) and catalogue mappings
    # can be off; observed live, a vol-2 ISBN resolved to "Volume 10". A
    # failing hit falls through to the guarded search instead of corrupting a
    # neighbouring volume's record.
    for isbn in isbn_variants(book.isbn):
        data = await _gql(client, token, Q_EDITION_BY_ISBN, {"isbn": isbn})
        editions = data.get("editions") or []
        if editions:
            ed = editions[0]
            hc_title = (ed.get("book") or {}).get("title") or ed.get("title") or ""
            if not _isbn_hit_plausible(book, hc_title):
                logger.info(
                    "Hardcover: ISBN %s resolves to %r, which fails the volume/format "
                    "check for %r — falling back to search", isbn, hc_title, book.title)
                continue
            book.hardcover_book_id = ed.get("book_id") or (ed.get("book") or {}).get("id")
            book.hardcover_slug = (ed.get("book") or {}).get("slug")
            book.hardcover_match_method = "isbn13" if len(isbn) == 13 else "isbn10"
            # The ISBN nails the BOOK, but its own edition may be one we must
            # not pin: publishers reuse a catalogue across translations and
            # audio (observed live: Podium ISBNs on English books resolving to
            # the German-translation edition — the user's profile then shows
            # the German cover), so a wrong-language or audio edition falls
            # back to a language-aware pick among the book's editions.
            if _edition_acceptable(ed, _lang2(book.language)):
                book.hardcover_edition_id = ed.get("id")
                book.hardcover_pages = ed.get("pages") or (ed.get("book") or {}).get("pages")
            elif not await _adopt_best_edition(client, token, book):
                logger.info(
                    "Hardcover: ISBN %s edition %s is wrong-language/audio for %r "
                    "and no better edition could be fetched — pinning it anyway",
                    isbn, ed.get("id"), book.title)
                book.hardcover_edition_id = ed.get("id")
                book.hardcover_pages = ed.get("pages") or (ed.get("book") or {}).get("pages")
            return True

    # 2. Title+author search, strictly guarded. Refuse rather than guess.
    #
    # Volume-awareness (verified against the live catalogue): Hardcover keeps
    # per-volume records ("Black Summoner, Vol. 2") while Tome LN/manga volumes
    # often all share the bare series title — searching the bare title would
    # collapse every volume onto one Hardcover book and corrupt its progress.
    # So when the book has a series index, the query and the expected title
    # carry the volume, and a candidate's own volume marker must agree.
    want_vol = book.series_index if book.series else None
    if book.series and want_vol is not None:
        expected = f"{book.series} Vol. {_fmt_index(want_vol)}"
    else:
        expected = book.title
    primary_author = (_split_authors(book.author) or [None])[0]

    def _with_author(q: str) -> str:
        return q if not primary_author else f"{q} {primary_author}"

    queries = [_with_author(expected)]
    if want_vol == 1:
        # First volumes often live under the bare series title (no volume
        # marker) and do NOT surface for "… Vol. 1" queries — search both
        # forms and let the guards + ranking pick (observed live).
        queries.append(_with_author(book.series))

    tome_is_manga = bool(book.book_type and book.book_type.slug == "manga")
    author_parts = _split_authors(book.author)

    hits, seen_ids = [], set()
    for q in queries:
        data = await _gql(client, token, Q_SEARCH, {"q": q})
        for hit in ((data.get("search") or {}).get("results") or {}).get("hits") or []:
            hid = (hit.get("document") or {}).get("id")
            if hid is not None and hid not in seen_ids:
                seen_ids.add(hid)
                hits.append(hit)
    best = None
    for hit in hits:
        doc = hit.get("document") or {}
        cand_title = doc.get("title") or ""
        if not cand_title or doc.get("id") is None:
            continue
        # Format guard: never cross the manga/novel line — the variants score
        # deceptively high on title similarity ("X (Manga) Vol 2" vs "X Vol 2").
        if _is_manga_title(cand_title) != tome_is_manga:
            continue
        # Volume guard: the number is authoritative in both directions. An
        # unmarked candidate is acceptable only for volume 1 (series-titled
        # first-volume records) or for standalone books.
        cand_vol = _vol_in_title(cand_title)
        if want_vol is not None:
            if cand_vol is not None and cand_vol != want_vol:
                continue
            if cand_vol is None and want_vol != 1:
                continue
        elif cand_vol is not None:
            continue  # standalone book must not match a numbered volume
        title_sim = max(
            _sim(_norm_title(cand_title), _norm_title(expected)),
            _sim(_norm_title(cand_title), _norm_title(book.title)),
        )
        if title_sim < TITLE_SIM_MIN:
            continue
        names = doc.get("author_names") or []
        if author_parts and names:
            pair_sim = max(
                (_sim(n, p) for n in names for p in author_parts), default=0.0
            )
            if pair_sim < AUTHOR_SIM_MIN:
                continue
        # Everything past the guards is plausibly the right book — prefer the
        # record the community actually uses over user-created stubs (observed
        # live: a coverless "Black Summoner -, Vol. 1" stub outscoring the real
        # record on title similarity alone). Popularity first, similarity as
        # the tiebreak.
        rank = (doc.get("users_count") or 0, title_sim)
        if best is None or rank > best[0]:
            best = (rank, doc)
    if best:
        doc = best[1]
        hc_id = int(doc["id"])
        book.hardcover_book_id = hc_id
        book.hardcover_slug = doc.get("slug")
        book.hardcover_match_method = "search"
        # Second query picks the edition to pin (language-aware, audio-averse).
        await _adopt_best_edition(client, token, book)
        return True

    book.hardcover_match_method = "none"
    return False


async def search_candidates(token: str, q: str, limit: int = 8) -> list[dict]:
    """Raw (unguarded) Hardcover book search for the manual match picker —
    these results go to human eyes, so no similarity filtering. Same pattern
    as the wishlist follow's series search."""
    async with httpx.AsyncClient(timeout=15) as client:
        data = await _gql(client, token, Q_SEARCH, {"q": q})
    hits = ((data.get("search") or {}).get("results") or {}).get("hits") or []
    out = []
    for hit in hits[:limit]:
        doc = hit.get("document") or {}
        if doc.get("id") is None:
            continue
        out.append({
            "hardcover_book_id": int(doc["id"]),
            "title": doc.get("title") or "",
            "authors": doc.get("author_names") or [],
            "slug": doc.get("slug"),
            "users_count": doc.get("users_count") or 0,
            "cover_url": (doc.get("image") or {}).get("url"),
            "series": (doc.get("series_names") or [None])[0],
        })
    return out


async def resolve_manual_match(token: str, book, hardcover_book_id: int) -> None:
    """Pin a user-chosen Hardcover record onto a Tome book (no commit). Fetches
    the representative edition + pages + slug; method 'manual' is never
    auto-cleared by Sync-now or metadata edits."""
    async with httpx.AsyncClient(timeout=15) as client:
        data = await _gql(client, token, Q_BOOK_EDITION, {"id": hardcover_book_id})
    books = data.get("books") or []
    if not books:
        raise HardcoverAPIError(f"Hardcover book {hardcover_book_id} not found")
    b = books[0]
    book.hardcover_book_id = hardcover_book_id
    book.hardcover_slug = b.get("slug")
    ed = _pick_edition(b.get("editions") or [], _lang2(book.language))
    book.hardcover_edition_id = ed.get("id") if ed else None
    book.hardcover_pages = (ed.get("pages") if ed else None) or b.get("pages")
    book.hardcover_match_method = "manual"
    book.hardcover_matched_at = datetime.utcnow()


async def delete_user_book(client: httpx.AsyncClient, token: str, user_book_id: int) -> bool:
    """Best-effort removal of a user_book entry WE created (wrong-match repair).
    The one deliberate exception to 'never delete on Hardcover' — it only ever
    targets an id we stored after our own insert."""
    try:
        await _gql(client, token, M_DELETE_USER_BOOK, {"id": user_book_id})
        return True
    except (HardcoverAPIError, HardcoverAuthError, HardcoverRateLimited):
        logger.info("Hardcover: could not delete user_book %s (already gone?)", user_book_id)
        return False


# ── Reconciler ────────────────────────────────────────────────────────────────

def needs_sync(row: UserBookStatus) -> bool:
    """Does this row differ from its last-pushed snapshot?

    Rating clears (rating=None after a synced value) are NOT propagated;
    unread/shelved statuses are never pushed. Progress is FORWARD-ONLY:
    Tome internally tracks positions last-write-wins (downward included, a
    deliberate re-read affordance), but mirroring a regression to a public
    profile has no upside — and it would let a stale device's wake-push
    rewind the user's Hardcover progress too.
    """
    if row.rating is not None and row.rating != row.hardcover_synced_rating:
        return True
    if row.status in STATUS_ID:
        if row.status != row.hardcover_synced_status:
            return True
        # Progress only matters for books actually being read: a want_to_read
        # row may carry leftover sample progress, which must not create a
        # read-row on Hardcover's Want to Read shelf.
        if row.status != "want_to_read":
            pct = row.progress_pct or 0.0
            if pct - (row.hardcover_synced_pct or 0.0) >= 0.01:
                return True
    return False


def _progress_pages(row: UserBookStatus, pages: Optional[int]) -> Optional[int]:
    if not pages or pages <= 0:
        return None
    pct = 1.0 if row.status == "read" else (row.progress_pct or 0.0)
    return max(0, min(pages, round(pct * pages)))


def _mutation_result(data: dict, key: str) -> dict:
    """Pull {id, error} out of a mutation response, tolerating shape drift."""
    result = data.get(key)
    if not isinstance(result, dict):
        raise HardcoverAPIError(f"{key}: unexpected response shape")
    if result.get("error"):
        raise HardcoverAPIError(f"{key}: {result['error']}")
    return result


async def _push_row(client: httpx.AsyncClient, token: str, user: User,
                    row: UserBookStatus, book: Book) -> None:
    """Push one row's diff to Hardcover. Raises on failure; caller records it."""
    status_id = STATUS_ID.get(row.status)

    # Ensure the Hardcover user_book row exists and we hold its id.
    if row.hardcover_user_book_id is None:
        data = await _gql(client, token, Q_USER_BOOK,
                          {"uid": user.hardcover_user_id, "bid": book.hardcover_book_id})
        existing = data.get("user_books") or []
        if existing:
            row.hardcover_user_book_id = existing[0]["id"]
            reads = existing[0].get("user_book_reads") or []
            if reads:
                row.hardcover_read_id = reads[0]["id"]
        else:
            obj: dict = {"book_id": book.hardcover_book_id}
            if status_id is not None:
                obj["status_id"] = status_id
            if book.hardcover_edition_id:
                obj["edition_id"] = book.hardcover_edition_id
            data = await _gql(client, token, M_INSERT_USER_BOOK, {"object": obj})
            result = _mutation_result(data, "insert_user_book")
            new_id = result.get("id") or (result.get("user_book") or {}).get("id")
            if new_id is None:
                raise HardcoverAPIError("insert_user_book returned no id")
            row.hardcover_user_book_id = int(new_id)
            row.hardcover_synced_status = row.status if status_id else None

    # Rating and/or status in one mutation.
    update_obj: dict = {}
    if row.rating is not None and row.rating != row.hardcover_synced_rating:
        update_obj["rating"] = float(row.rating)
    if status_id is not None and row.status != row.hardcover_synced_status:
        update_obj["status_id"] = status_id
    if update_obj:
        data = await _gql(client, token, M_UPDATE_USER_BOOK,
                          {"id": row.hardcover_user_book_id, "object": update_obj})
        _mutation_result(data, "update_user_book")
        if "rating" in update_obj:
            row.hardcover_synced_rating = row.rating
        if "status_id" in update_obj:
            row.hardcover_synced_status = row.status

    # Progress (page-based, forward-only — see needs_sync). No pages on the
    # edition → status-only sync. Skipped entirely for want_to_read: queued
    # books get a shelf entry, never a read-row.
    if status_id is not None and row.status != "want_to_read":
        pages = _progress_pages(row, book.hardcover_pages)
        pct = 1.0 if row.status == "read" else (row.progress_pct or 0.0)
        if pages is not None and pct - (row.hardcover_synced_pct or 0.0) >= 0.01:
            read_obj: dict = {"progress_pages": pages}
            if book.hardcover_edition_id:
                read_obj["edition_id"] = book.hardcover_edition_id
            if row.status == "read" and row.finished_at:
                read_obj["finished_at"] = row.finished_at.strftime("%Y-%m-%d")
            if row.hardcover_read_id is None:
                # insert_user_book auto-creates an initial read row (observed
                # live) — adopt it rather than inserting a duplicate.
                data = await _gql(client, token, Q_READS,
                                  {"ubId": row.hardcover_user_book_id})
                reads = data.get("user_book_reads") or []
                if reads:
                    row.hardcover_read_id = int(reads[0]["id"])
            if row.hardcover_read_id is None:
                data = await _gql(client, token, M_INSERT_READ,
                                  {"ubId": row.hardcover_user_book_id, "read": read_obj})
                result = _mutation_result(data, "insert_user_book_read")
                if result.get("id") is not None:
                    row.hardcover_read_id = int(result["id"])
            else:
                data = await _gql(client, token, M_UPDATE_READ,
                                  {"id": row.hardcover_read_id, "object": read_obj})
                _mutation_result(data, "update_user_book_read")
            row.hardcover_synced_pct = pct
        elif pages is None:
            # Status-only book (no page data): status went out above; snapshot
            # the pct anyway so needs_sync stops firing on progress churn.
            row.hardcover_synced_pct = pct

    row.hardcover_synced_at = datetime.utcnow()
    row.hardcover_error = None
    row.hardcover_fail_count = 0


def repair_shelf_sync_collateral(db: Session) -> dict:
    """One-time startup repair for the first shipped 2.2.0 build, whose shelf
    mirror filtered wishes on source="hardcover" without kind — dismissing
    every open series follow (kind="follow") and every user-created wish whose
    metadata happened to come from Hardcover, on the first sync cycle.

    Precisely reversible because the legitimate paths are distinguishable:
    users DELETE their own wishes/follows (rows vanish), and an admin dismiss
    writes a "wishlist.dismissed" audit entry. A dismissed source="hardcover"
    row with no such audit entry can only be the bug's work → reopen it.

    Also reclassifies wishes the shelf sync itself created under the old
    ambiguous source (identified by their fixed note text) to
    SHELF_WISH_SOURCE, keeping their dismiss/reopen mirroring intact.
    Idempotent; safe to run every boot. Commits."""
    from backend.models.audit_log import AuditLog

    reclassified = (
        db.query(Wish)
        .filter(Wish.kind == "wish", Wish.source == "hardcover",
                Wish.note == SHELF_WISH_NOTE)
        .update({"source": SHELF_WISH_SOURCE}, synchronize_session=False)
    )
    admin_dismissed = (
        db.query(AuditLog.resource_id)
        .filter(AuditLog.action == "wishlist.dismissed",
                AuditLog.resource_id.isnot(None))
    )
    reopened = (
        db.query(Wish)
        .filter(Wish.status == "dismissed", Wish.source == "hardcover",
                ~Wish.id.in_(admin_dismissed))
        .update({"status": "open"}, synchronize_session=False)
    )
    db.commit()
    if reclassified or reopened:
        logger.info(
            "Hardcover shelf-sync repair: reopened %d wrongly-dismissed "
            "wishes/follows, reclassified %d shelf-created wishes",
            reopened, reclassified)
    return {"reopened": reopened, "reclassified": reclassified}


def mark_token_expired(db: Session, user: User) -> None:
    """Flip token status and notify once."""
    if user.hardcover_token_status == "expired":
        return
    user.hardcover_token_status = "expired"
    db.add(Notification(
        user_id=user.id,
        kind="hardcover_token_expired",
        title="Hardcover token expired",
        body="Hardcover API tokens reset every January 1. Re-link your account "
             "in Settings to resume syncing ratings and progress.",
        link="/settings",
    ))
    db.commit()


# In-memory retry schedule for failed rows: (user_id, book_id) -> monotonic ts.
# Single-process, resets on restart (harmless — rows just retry sooner).
_next_attempt: dict[tuple[int, int], float] = {}


class _Budget:
    def __init__(self, limit: int):
        self.limit = limit
        self.used = 0

    def spend(self) -> bool:
        self.used += 1
        return self.used <= self.limit

    @property
    def exhausted(self) -> bool:
        return self.used > self.limit


async def sync_user(db: Session, client: httpx.AsyncClient, user: User,
                    budget: _Budget) -> dict:
    """Reconcile one user's diffs. Returns counters."""
    token = user_token(user)
    stats = {"pushed": 0, "failed": 0, "skipped_unmatched": 0}
    if not token:
        return stats

    from sqlalchemy import or_
    rows = (
        db.query(UserBookStatus, Book)
        .join(Book, UserBookStatus.book_id == Book.id)
        .filter(
            UserBookStatus.user_id == user.id,
            Book.status == "active",
            # Cheap prefilter: rows that can never need a sync (unrated +
            # unread/shelved) stay in the DB. needs_sync() in Python remains
            # the authority for everything this overselects.
            or_(
                UserBookStatus.rating.isnot(None),
                UserBookStatus.status.in_(tuple(STATUS_ID)),
            ),
        )
        .all()
    )
    now = time.monotonic()
    for row, book in rows:
        if not needs_sync(row):
            continue
        if row.hardcover_fail_count >= MAX_ROW_FAILS:
            continue  # parked until manual "Sync now" (which resets fail counts)
        key = (user.id, row.book_id)
        if _next_attempt.get(key, 0) > now:
            continue
        # No-request skips must not consume budget.
        if book.hardcover_book_id is None:
            if book.hardcover_match_method == "excluded":
                continue  # user said "never sync this book"
            if (book.hardcover_match_method == "none"
                    and book.hardcover_matched_at
                    and book.hardcover_matched_at >= book.updated_at):
                stats["skipped_unmatched"] += 1
                continue
        if not budget.spend():
            logger.info("Hardcover sync: request budget reached, deferring rest of cycle")
            break

        try:
            # Match lazily; re-try failed matches only after a metadata edit.
            if book.hardcover_book_id is None:
                matched = await match_book(client, token, book)
                db.commit()  # persist the match (or the 'none') immediately
                if not matched:
                    # Re-stamp matched_at AFTER the commit. The ORM flush above
                    # just bumped updated_at (onupdate) PAST the in-flight
                    # matched_at — left alone, the "don't retry until a
                    # metadata edit" guard would never hold and every cycle
                    # would re-match (and re-bill) every unmatched book
                    # forever. Column-level onupdate fires on ANY UPDATE of the
                    # row, so pin updated_at to itself to suppress it.
                    db.query(Book).filter(Book.id == book.id).update(
                        {"hardcover_matched_at": datetime.utcnow(),
                         "updated_at": Book.updated_at},
                        synchronize_session=False,
                    )
                    db.commit()
                    stats["skipped_unmatched"] += 1
                    continue
            await _push_row(client, token, user, row, book)
            db.commit()
            stats["pushed"] += 1
        except HardcoverAuthError:
            db.rollback()
            mark_token_expired(db, user)
            logger.info("Hardcover sync: token expired for user %s", user.username)
            break
        except HardcoverRateLimited:
            db.rollback()
            raise
        except HardcoverAPIError as exc:
            db.rollback()
            row.hardcover_error = str(exc)[:255]
            row.hardcover_fail_count = (row.hardcover_fail_count or 0) + 1
            db.commit()
            # Fresh clock — `now` was captured before the throttled loop and
            # can be minutes stale, which would silently shorten the backoff.
            _next_attempt[key] = time.monotonic() + min(
                86400, 60 * (2 ** row.hardcover_fail_count))
            stats["failed"] += 1
    return stats


async def pull_want_to_read(db: Session, client: httpx.AsyncClient, user: User,
                            budget: _Budget) -> dict:
    """Pull the user's Hardcover Want to Read shelf into Tome.

    The one inbound direction. Rules (docs/plans/want-to-read-plan.md):
    - Shelf entries resolve via ``Book.hardcover_book_id`` — only books Tome
      has already matched apply; the rest are picked up once matched later.
    - Additive apply: a resolved entry only fills a missing status row or
      upgrades an explicit "unread" to ``want_to_read``. Engagement
      (reading/read/shelved) always wins — never downgraded.
    - Echo prevention: applied rows get ``hardcover_synced_status`` stamped so
      the push reconciler sees no diff.
    - Convergence: a Tome row that is ``want_to_read`` AND was last agreed with
      Hardcover (snapshot says want_to_read) but whose entry left the shelf is
      checked individually — gone entirely → revert to unread; merely moved to
      another Hardcover status → leave both sides alone (no fighting).
    - An incomplete shelf fetch (budget/API trouble) aborts the WHOLE pull for
      this user — applying a partial shelf would mass-revert agreed rows.
    """
    stats = {"pulled": 0, "reverted": 0}
    token = user_token(user)
    if not token or user.hardcover_user_id is None:
        return stats

    # 1. Full shelf fetch (paginated). Partial → abort, see docstring.
    shelf: dict[int, int] = {}  # hardcover book_id -> hardcover user_book id
    offset = 0
    while True:
        if not budget.spend():
            logger.info("Hardcover pull: budget exhausted mid-shelf-fetch for %s — "
                        "skipping pull this cycle", user.username)
            return stats
        data = await _gql(client, token, Q_WTR_SHELF,
                          {"uid": user.hardcover_user_id, "offset": offset})
        rows = data.get("user_books")
        if rows is None:
            logger.info("Hardcover pull: unexpected shelf response shape for %s — "
                        "skipping pull this cycle", user.username)
            return stats
        for r in rows:
            if r.get("book_id") is not None and r.get("id") is not None:
                shelf[int(r["book_id"])] = int(r["id"])
        if len(rows) < 100:
            break
        offset += 100

    # 2. Additive apply onto matched books.
    if shelf:
        matched_books = (
            db.query(Book)
            .filter(Book.hardcover_book_id.in_(shelf.keys()), Book.status == "active")
            .all()
        )
        for book in matched_books:
            row = (
                db.query(UserBookStatus)
                .filter_by(user_id=user.id, book_id=book.id)
                .first()
            )
            if row is None:
                db.add(UserBookStatus(
                    user_id=user.id, book_id=book.id, status="want_to_read",
                    hardcover_synced_status="want_to_read",
                    hardcover_user_book_id=shelf[book.hardcover_book_id],
                ))
                stats["pulled"] += 1
            elif row.status == "unread":
                row.status = "want_to_read"
                row.hardcover_synced_status = "want_to_read"
                row.hardcover_user_book_id = shelf[book.hardcover_book_id]
                stats["pulled"] += 1

    # 3. Convergence: previously-agreed rows whose entry left the shelf.
    agreed = (
        db.query(UserBookStatus, Book)
        .join(Book, UserBookStatus.book_id == Book.id)
        .filter(
            UserBookStatus.user_id == user.id,
            UserBookStatus.status == "want_to_read",
            UserBookStatus.hardcover_synced_status == "want_to_read",
            Book.hardcover_book_id.isnot(None),
        )
        .all()
    )
    for row, book in agreed:
        if book.hardcover_book_id in shelf:
            continue
        # Distinguish removed from moved: a targeted lookup per candidate
        # (candidates are rare — only just-unshelved books reach here).
        if not budget.spend():
            break
        data = await _gql(client, token, Q_USER_BOOK,
                          {"uid": user.hardcover_user_id, "bid": book.hardcover_book_id})
        existing = data.get("user_books") or []
        if existing:
            # Moved to another Hardcover status (reading/read/…): leave the
            # Tome row queued and the snapshot intact so nothing re-pushes.
            continue
        row.status = "unread"
        row.hardcover_synced_status = None
        row.hardcover_user_book_id = None
        row.hardcover_read_id = None
        stats["reverted"] += 1

    db.commit()

    # 4. Shelf entries with NO Tome book at all become wishes (full circle:
    # shelve a volume on Hardcover → it lands on the Tome wishlist). Runs after
    # the status commit so an API/schema failure here can never cost applied
    # statuses. Guarded against the owned-but-unmatched case: if the library
    # already has a plausible copy (title/author fuzzy), no wish is created —
    # the book just hasn't been catalogue-matched yet.
    try:
        stats.update(await _wishes_from_shelf(db, client, token, user, shelf, budget))
        db.commit()
    except (HardcoverAPIError, HardcoverRateLimited):
        db.rollback()
        logger.info("Hardcover pull: wish sync skipped for %s (API trouble)", user.username)
    return stats


async def _wishes_from_shelf(db: Session, client: httpx.AsyncClient, token: str,
                             user: User, shelf: dict[int, int],
                             budget: _Budget) -> dict:
    """Create wishes for shelf entries that resolve to no Tome book, and keep
    Hardcover-sourced wishes mirroring the shelf: un-shelving dismisses them,
    re-shelving reopens them. Fulfilled wishes are final either way.

    Consequence: for wishes this sync created, the shelf outranks an in-Tome
    dismissal — an admin-dismissed one reopens next cycle while the book stays
    shelved. Dedup for creation is create_wish's (user, source, source_id)
    uniqueness. Does not commit — the caller owns the transaction.
    """
    from backend.schemas.wish import WishCreate
    from backend.services.wish_matcher import find_matching_books
    from backend.services.wishlist import create_wish

    stats = {"wished": 0, "wish_dismissed": 0, "wish_reopened": 0, "adopted": 0}
    if not settings.wishlist_enabled:
        return stats

    # The shelf is authoritative for the wishes it created — and ONLY those
    # (SHELF_WISH_SOURCE; never follows, never user-created wishes that merely
    # cite Hardcover as their metadata source). Un-shelving dismisses,
    # re-shelving reopens. Both silent — the user made the change themselves on
    # Hardcover; no notification. Fulfilled wishes are final and never
    # reopened (the book is here; a lingering shelf entry changes nothing).
    hc_wishes = (
        db.query(Wish)
        .filter(Wish.user_id == user.id, Wish.kind == "wish",
                Wish.status.in_(("open", "dismissed")),
                Wish.source == SHELF_WISH_SOURCE, Wish.source_id.isnot(None))
        .all()
    )
    shelf_ids = {str(bid) for bid in shelf}
    for wish in hc_wishes:
        if wish.status == "open" and wish.source_id not in shelf_ids:
            wish.status = "dismissed"
            stats["wish_dismissed"] += 1
        elif wish.status == "dismissed" and wish.source_id in shelf_ids:
            wish.status = "open"
            stats["wish_reopened"] += 1

    known_ids = {
        hc_id for (hc_id,) in
        db.query(Book.hardcover_book_id).filter(Book.hardcover_book_id.in_(shelf.keys()))
    }
    existing_wish_ids = {w.source_id for w in hc_wishes} | {
        w.source_id for w in
        db.query(Wish).filter(Wish.user_id == user.id,
                              Wish.source == SHELF_WISH_SOURCE,
                              Wish.status == "fulfilled").all()
    }
    unresolved = [bid for bid in shelf
                  if bid not in known_ids and str(bid) not in existing_wish_ids]
    if not unresolved:
        return stats

    if not budget.spend():
        return stats
    data = await _gql(client, token, Q_BOOKS_BY_IDS, {"ids": unresolved})
    for b in data.get("books") or []:
        title = (b.get("title") or "").strip()
        if not title or b.get("id") is None:
            continue
        authors = [((c.get("author") or {}).get("name") or "").strip()
                   for c in (b.get("contributions") or [])]
        author = next((a for a in authors if a), None)
        # Owned-but-unmatched: a plausible library copy means the book is here
        # already — never wish for it. With exactly ONE candidate the shelf
        # entry itself IS the missing catalogue match (matching is lazy and an
        # unread book never triggers it), so adopt it and queue the book
        # instead of dead-ending. Multiple candidates are too ambiguous to
        # write a book-level match.
        probe = Wish(user_id=user.id, title=title, author=author, kind="wish")
        candidates = find_matching_books(db, probe)
        if candidates:
            cand = candidates[0]
            if (len(candidates) == 1 and cand.hardcover_book_id is None
                    and cand.status == "active"
                    and _isbn_hit_plausible(cand, title)):
                cand.hardcover_book_id = int(b["id"])
                cand.hardcover_slug = b.get("slug")
                cand.hardcover_match_method = "shelf"
                cand.hardcover_matched_at = datetime.utcnow()
                if budget.spend():
                    await _adopt_best_edition(client, token, cand)
                row = (
                    db.query(UserBookStatus)
                    .filter_by(user_id=user.id, book_id=cand.id)
                    .first()
                )
                if row is None:
                    db.add(UserBookStatus(
                        user_id=user.id, book_id=cand.id, status="want_to_read",
                        hardcover_synced_status="want_to_read",
                        hardcover_user_book_id=shelf.get(int(b["id"])),
                    ))
                elif row.status == "unread":
                    row.status = "want_to_read"
                    row.hardcover_synced_status = "want_to_read"
                    row.hardcover_user_book_id = shelf.get(int(b["id"]))
                stats["adopted"] += 1
            else:
                logger.info(
                    "Hardcover pull: shelf entry %r looks already owned "
                    "(unmatched, %d candidates) — no wish created",
                    title, len(candidates))
            continue
        cover = ((b.get("image") or {}).get("url") or "").strip() or None
        try:
            create_wish(db, user, WishCreate(
                title=title, author=author, cover_url=cover,
                source=SHELF_WISH_SOURCE, source_id=str(int(b["id"])),
                note=SHELF_WISH_NOTE,
            ))
            stats["wished"] += 1
        except HTTPException as exc:
            # Cap reached / raced duplicate — skip quietly, retry next cycle.
            logger.info("Hardcover pull: wish for %r not created (%s)", title, exc.detail)
    return stats


async def run_sync_cycle(reason: str = "interval", only_user_id: Optional[int] = None) -> dict:
    """One reconcile pass over all linked, enabled users."""
    from backend.core.database import SessionLocal

    budget = _Budget(MAX_REQUESTS_PER_CYCLE)
    totals = {"users": 0, "pushed": 0, "failed": 0, "skipped_unmatched": 0,
              "pulled": 0, "reverted": 0, "wished": 0, "wish_dismissed": 0,
              "wish_reopened": 0, "adopted": 0, "exhausted": False}
    with SessionLocal() as db:
        q = db.query(User).filter(
            User.hardcover_token.isnot(None),
            User.hardcover_token_status == "ok",
            User.hardcover_sync_enabled.is_(True),
            User.is_active.is_(True),
        )
        if only_user_id is not None:
            q = q.filter(User.id == only_user_id)
        users = q.all()
        if not users:
            return totals
        async with httpx.AsyncClient(timeout=20) as client:
            for user in users:
                totals["users"] += 1
                try:
                    stats = await sync_user(db, client, user, budget)
                except HardcoverRateLimited:
                    logger.warning("Hardcover sync: rate limited — ending cycle early")
                    break
                for k in ("pushed", "failed", "skipped_unmatched"):
                    totals[k] += stats[k]
                # Inbound: Want to Read shelf pull (the one bidirectional piece).
                # Runs after the push pass so freshly-pushed rows already carry
                # their snapshot and read back as agreed, not as new pulls.
                try:
                    pull_stats = await pull_want_to_read(db, client, user, budget)
                except HardcoverRateLimited:
                    db.rollback()
                    logger.warning("Hardcover pull: rate limited — ending cycle early")
                    break
                except HardcoverAuthError:
                    db.rollback()
                    mark_token_expired(db, user)
                    continue
                except HardcoverAPIError as exc:
                    db.rollback()
                    logger.info("Hardcover pull failed for %s: %s", user.username, exc)
                    continue
                for k in ("pulled", "reverted", "wished", "wish_dismissed",
                          "wish_reopened", "adopted"):
                    # A partial-fetch abort returns without the wish counters.
                    totals[k] += pull_stats.get(k, 0)
    totals["exhausted"] = budget.exhausted
    if any(totals[k] for k in ("pushed", "failed", "pulled", "reverted",
                               "wished", "wish_dismissed", "wish_reopened",
                               "adopted")):
        logger.info("Hardcover sync (%s): %s", reason, totals)
    return totals


# ── Manual sync (Settings "Sync now") ─────────────────────────────────────────

_manual_running: set[int] = set()


def is_manual_sync_running(user_id: int) -> bool:
    return user_id in _manual_running


def reset_backoff(user_id: int) -> None:
    for key in [k for k in _next_attempt if k[0] == user_id]:
        _next_attempt.pop(key, None)


def start_manual_sync(user_id: int) -> bool:
    """Fire one background reconcile for a single user. Returns False if one
    is already running for them. Must be called from a running event loop
    (async endpoint)."""
    if user_id in _manual_running:
        return False
    _manual_running.add(user_id)

    async def _run() -> None:
        try:
            # Drain: a large first-link backfill spans several budget-capped
            # cycles; keep going until a cycle completes without exhausting its
            # budget. Terminates because every push advances a snapshot and
            # every failure earns a backoff skip; the iteration cap is a
            # backstop, not the mechanism.
            for _ in range(50):
                totals = await run_sync_cycle("manual", only_user_id=user_id)
                if not totals.get("exhausted"):
                    break
                await asyncio.sleep(5)
        finally:
            _manual_running.discard(user_id)

    asyncio.create_task(_run())
    return True


# ── Worker loop + nudges ──────────────────────────────────────────────────────

_wake_requested_at: float = 0.0
_last_cycle_at: float = 0.0


def nudge() -> None:
    """Request a near-term sync (rating changed / book finished). Called from
    sync request handlers (threadpool) — a bare float assignment is safe."""
    global _wake_requested_at
    _wake_requested_at = time.monotonic()


async def hardcover_sync_loop() -> None:
    """Background task: interval reconcile + debounced nudge wake-ups.

    Drain mode: a cycle that ran out of request budget with work remaining
    (big backfill) resumes on the next 15s tick instead of waiting out the
    full interval."""
    global _last_cycle_at
    _last_cycle_at = time.monotonic()  # don't fire immediately on boot
    drain_pending = False
    while True:
        try:
            await asyncio.sleep(15)
            now = time.monotonic()
            due_interval = now - _last_cycle_at >= settings.hardcover_sync_interval
            due_nudge = (
                _wake_requested_at > _last_cycle_at
                and now - _wake_requested_at >= NUDGE_DEBOUNCE
            )
            if due_interval or due_nudge or drain_pending:
                _last_cycle_at = now
                reason = "drain" if drain_pending and not (due_interval or due_nudge) \
                    else ("nudge" if due_nudge and not due_interval else "interval")
                totals = await run_sync_cycle(reason)
                drain_pending = bool(totals.get("exhausted"))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Unhandled error in Hardcover sync loop")
