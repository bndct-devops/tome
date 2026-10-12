"""The Companion overview (Settings > AI > Companion): one row per series.

``overview(db, user)`` builds ``GET /api/ai/companion/overview``: per series
key (``digest.series_key``; a standalone book is its own row) the volumes
with their chapter count, stored digests, failed placeholders, recap state
and last digest time, the glossary counts (live entities by kind), the last
merge and the last job, this month's spend and the viewer's active job,
``anchor_book_id``: the volume the row's actions start on (the viewer's own
highest engaged volume, else for an admin the highest digested one), and
``in_scope``: the viewer has read, is reading or holds a position on a volume
of the series (always true for a member, whose rows are exactly those).
A series where no volume has chapter text (``book_chapters``) gets no row:
nothing in it can be read.

Who sees what:
- a member: series with a volume they marked read or reading, or hold a
  position in (``UserBookStatus`` with progress, ``TomeSyncPosition``), with
  the volumes they can see; jobs and spend are their own; ``readers`` empty.
- an admin: every series with any stored digest; jobs and spend of every
  user; ``readers`` = the users with a read status or position on a volume.

Cheap by design: a fixed number of grouped queries over the books, digests,
entities, jobs and usage rows, no chapter text, no model call. The recap
state goes through ``volume_recap.state`` only for a volume that has a
whole-book ``ai_recaps`` row at all (else it is ``missing``, or ``none``
without chapters). At most ``MAX_SERIES`` rows, most recent activity first.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Iterable, Optional

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from backend.core.permissions import book_visibility_filter, is_admin
from backend.models.ai_chapter_digest import AIChapterDigest
from backend.models.ai_entity import AIEntity
from backend.models.ai_job import AIJob, LIVE_STATUSES
from backend.models.ai_recap import AIRecap
from backend.models.ai_usage import AIUsage
from backend.models.audit_log import AuditLog
from backend.models.book import Book, BookChapter
from backend.models.tome_sync import TomeSyncPosition
from backend.models.user import User
from backend.models.user_book_status import UserBookStatus
from backend.services.ai import digest, digest_job, volume_recap
from backend.services.ai.usage import parse_month

MAX_SERIES = 200
REREAD_WINDOW_SECONDS = 5     # a job created this soon after an ``ai.reread`` audit row is that re-read

KIND_READ, KIND_MERGE, KIND_REREAD, KIND_WRITE_ALL = "read", "merge", "reread", "write_all"


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() + "Z" if dt is not None else None


def _sort_index(b: Book) -> tuple[float, int]:
    return (b.series_index if b.series_index is not None else float("-inf"), b.id)


@dataclass
class _Row:
    key: str
    books: list[Book] = field(default_factory=list)


def _engaged(db: Session, user_id: Optional[int]) -> dict[int, set[int]]:
    """book_id -> user ids with a read/reading status, progress or a synced
    position on it (one user's when ``user_id`` is set)."""
    out: dict[int, set[int]] = defaultdict(set)
    q = db.query(UserBookStatus.user_id, UserBookStatus.book_id).filter(or_(
        UserBookStatus.status.in_(("read", "reading")),
        UserBookStatus.progress_pct > 0,
        UserBookStatus.cfi.isnot(None),
    ))
    p = db.query(TomeSyncPosition.user_id, TomeSyncPosition.book_id)
    if user_id is not None:
        q = q.filter(UserBookStatus.user_id == user_id)
        p = p.filter(TomeSyncPosition.user_id == user_id)
    for uid, bid in [*q.all(), *p.all()]:
        out[bid].add(uid)
    return out


def job_kind(snap: dict[str, Any], rereads: Iterable[tuple[int, int, datetime]]) -> str:
    """What started a job, from its row: ``reread`` when the row's ``kind``
    says so (Read again enqueues with kind "reread"), or, for rows from
    before that, when an ``ai.reread`` audit row of the same user and book
    was written at most ``REREAD_WINDOW_SECONDS`` before it was created;
    ``write_all`` when it carries the volume-recaps step; ``merge`` when it
    reads nothing and only merges; else ``read``."""
    if snap.get("kind") == KIND_REREAD:
        return KIND_REREAD
    created = datetime.fromisoformat(snap["created_at"].rstrip("Z")) if snap.get("created_at") else None
    if created is not None:
        for uid, bid, at in rereads:
            if (uid == snap["user_id"] and bid == snap["book_id"]
                    and timedelta(0) <= created - at <= timedelta(seconds=REREAD_WINDOW_SECONDS)):
                return KIND_REREAD
    names = [f.get("name") for f in snap.get("followups") or [] if isinstance(f, dict)]
    if digest_job.FOLLOWUP_VOLUME_RECAPS in names:
        return KIND_WRITE_ALL
    if names == [digest_job.FOLLOWUP_RECONCILE] and not snap.get("upto_chapter") and not snap.get("total"):
        return KIND_MERGE
    return KIND_READ


def _merge_result(snap: dict[str, Any]) -> Optional[dict[str, Any]]:
    for f in snap.get("followups") or []:
        if isinstance(f, dict) and f.get("name") == digest_job.FOLLOWUP_RECONCILE and f.get("status") == "done":
            return {"finished_at": snap.get("finished_at"),
                    "candidates": int(f.get("candidates") or 0),
                    "merged": int(f.get("merged") or 0)}
    return None


def _snap_time(snap: dict[str, Any]) -> str:
    return snap.get("finished_at") or snap.get("created_at") or ""


def overview(db: Session, user: User) -> dict[str, Any]:
    admin = is_admin(user)

    # ── the books, grouped by series key ──
    bq = db.query(Book).filter(Book.status == "active")
    if not admin:
        bq = bq.filter(book_visibility_filter(db, user))
    books = bq.all()
    by_key: dict[str, _Row] = {}
    key_of: dict[int, str] = {}
    for b in books:
        k = digest.series_key(b)
        by_key.setdefault(k, _Row(key=k)).books.append(b)
        key_of[b.id] = k

    engaged = _engaged(db, None if admin else user.id)
    own = {bid for bid, uids in engaged.items() if user.id in uids}
    digest_ids = {bid for (bid,) in db.query(AIChapterDigest.book_id).distinct()}

    if admin:
        keys = {key_of[bid] for bid in digest_ids if bid in key_of}
    else:
        keys = {key_of[bid] for bid in engaged if bid in key_of}
    if not keys:
        return {"series": []}
    ids = [b.id for k in keys for b in by_key[k].books]

    # ── per-book figures ──
    chapters = dict(db.query(BookChapter.book_id, func.count(BookChapter.id))
                    .filter(BookChapter.book_id.in_(ids)).group_by(BookChapter.book_id).all())
    dig: dict[int, dict[str, Any]] = {}
    for bid, model, n, last in (db.query(AIChapterDigest.book_id,
                                         AIChapterDigest.model == digest.FAILED_MODEL,
                                         func.count(AIChapterDigest.id),
                                         func.max(AIChapterDigest.created_at))
                                .filter(AIChapterDigest.book_id.in_(ids))
                                .group_by(AIChapterDigest.book_id,
                                          AIChapterDigest.model == digest.FAILED_MODEL)):
        d = dig.setdefault(bid, {"digested": 0, "failed": 0, "last": None})
        d["failed" if model else "digested"] += n
        if last is not None and (d["last"] is None or last > d["last"]):
            d["last"] = last
    recap_rows = {(bid, idx) for bid, idx in db.query(AIRecap.book_id, AIRecap.chapter_idx)
                  .filter(AIRecap.book_id.in_(ids)).distinct()}

    entity_counts: dict[str, dict[str, int]] = defaultdict(lambda: {"person": 0, "place": 0, "term": 0})
    for k, kind, n in (db.query(AIEntity.series_key, AIEntity.kind, func.count(AIEntity.id))
                       .filter(AIEntity.series_key.in_(list(keys)), AIEntity.merged_into.is_(None))
                       .group_by(AIEntity.series_key, AIEntity.kind)):
        if kind in entity_counts[k]:
            entity_counts[k][kind] = n

    # ── jobs (rows, live values win) ──
    jq = db.query(AIJob).filter(AIJob.book_id.in_(ids))
    if not admin:
        jq = jq.filter(AIJob.user_id == user.id)
    snaps = {r.id: digest_job.row_snapshot(r) for r in jq.all()}
    live = {s["id"]: s for s in digest_job.running(None if admin else user.id) if s["book_id"] in key_of}
    snaps.update(live)
    jobs_by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for s in snaps.values():
        k = key_of.get(s["book_id"])
        if k in keys:
            jobs_by_key[k].append(s)
    rereads = [(uid, rid, at) for uid, rid, at in db.query(
        AuditLog.user_id, AuditLog.resource_id, AuditLog.created_at)
        .filter(AuditLog.action == "ai.reread", AuditLog.resource_id.in_(ids))]

    # ── this month's spend ──
    _label, start, end = parse_month(datetime.utcnow().strftime("%Y-%m"))
    job_key = {jid: key_of.get(s["book_id"]) for jid, s in snaps.items()}
    uq = db.query(AIUsage.book_id, AIUsage.job_id, AIUsage.cost_usd).filter(
        AIUsage.created_at >= start, AIUsage.created_at < end,
        or_(AIUsage.book_id.in_(ids), AIUsage.job_id.in_(list(job_key) or [""])))
    if not admin:
        uq = uq.filter(AIUsage.user_id == user.id)
    spend: dict[str, float] = defaultdict(float)
    for bid, jid, cost in uq:
        k = job_key.get(jid) if jid else None
        if k is None and bid is not None:
            k = key_of.get(bid)
        if k in keys:
            spend[k] += cost or 0.0

    # ── readers (admins) ──
    usernames: dict[int, str] = {}
    if admin:
        uids = {u for bid in ids for u in engaged.get(bid, ())}
        if uids:
            usernames = dict(db.query(User.id, User.username).filter(User.id.in_(uids)))

    rows: list[tuple[str, dict[str, Any]]] = []
    for k in keys:
        vols = sorted(by_key[k].books, key=_sort_index)
        if not any(chapters.get(v.id, 0) for v in vols):
            continue  # no chapter text anywhere: nothing to read or show
        volumes: list[dict[str, Any]] = []
        for v in vols:
            n = chapters.get(v.id, 0)
            d = dig.get(v.id, {"digested": 0, "failed": 0, "last": None})
            if not n:
                rstate = "none"
            elif (v.id, n) not in recap_rows:
                rstate = volume_recap.MISSING
            else:
                rstate = volume_recap.item(volume_recap.state(db, user, v))["status"]
            volumes.append({
                "book_id": v.id, "title": v.title, "series_index": v.series_index,
                "chapters": n, "digested": d["digested"], "failed": d["failed"],
                "recap": rstate, "last_digest_at": _iso(d["last"]),
            })

        mine = [v for v in vols if v.id in own]
        anchor_pool = mine or (admin and [v for v in vols if v.id in digest_ids]) or vols
        anchor = max(anchor_pool, key=_sort_index)

        readers: list[dict[str, Any]] = []
        if admin:
            rid = sorted({u for v in vols for u in engaged.get(v.id, ())},
                         key=lambda u: (usernames.get(u) or "").lower())
            readers = [{"id": u, "username": usernames.get(u)} for u in rid if u in usernames]

        jobs = sorted(jobs_by_key.get(k, []), key=_snap_time, reverse=True)
        finished = [s for s in jobs if s["status"] not in LIVE_STATUSES]
        last_job = None
        if finished:
            s = finished[0]
            last_job = {"id": s["id"], "kind": job_kind(s, rereads), "status": s["status"],
                        "finished_at": s.get("finished_at"),
                        "cost_usd": round(float(s.get("cost_usd") or 0.0), 6)}
        last_merge = next((m for m in (_merge_result(s) for s in finished) if m is not None), None)
        active = [s for s in jobs if s["id"] in live and s["user_id"] == user.id]
        active_job = active[0] if active else None

        first = vols[0]
        authors = [v.author for v in vols if v.author]
        author = max(set(authors), key=authors.count) if authors else None
        counts = entity_counts.get(k, {"person": 0, "place": 0, "term": 0})
        row = {
            "key": k,
            "name": first.series if first.series else first.title,
            "author": author,
            "readers": readers,
            "volumes": volumes,
            "people": counts["person"], "places": counts["place"], "terms": counts["term"],
            "last_merge": last_merge,
            "last_job": last_job,
            "spend_month_usd": round(spend.get(k, 0.0), 6),
            "active_job": active_job,
            "anchor_book_id": anchor.id,
            "in_scope": bool(mine),
        }
        activity = max([_snap_time(s) for s in jobs[:1]]
                       + [vol["last_digest_at"] or "" for vol in volumes] + [""])
        rows.append((activity, row))

    # most recent activity first, rows without any after (by name)
    with_act = sorted((r for r in rows if r[0]), key=lambda r: r[0], reverse=True)
    without = sorted((r for r in rows if not r[0]), key=lambda r: (r[1]["name"] or "").lower())
    return {"series": [r for _a, r in (with_act + without)[:MAX_SERIES]]}
