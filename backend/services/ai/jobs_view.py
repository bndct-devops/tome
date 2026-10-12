"""The AI Activity view: live jobs, then history grouped by book with spend.

``list_jobs`` reads the ``ai_jobs`` rows of the last ``days`` days (live
values from memory for jobs still running) and the usage rows that belong to
no job (recap, Recall, fixes) with a ``book_id``, and groups both by book. A
book's ``cost_usd``/``calls`` are its jobs' plus those direct calls; a series
job's spend counts under the book it was started from (the volumes it read
are on its usage rows). ``chapters_digested`` sums the jobs' chapters read
without failure.

Deleting a book cascades its ``ai_jobs`` rows, but the usage rows stay (their
``book_id`` set to NULL). Usage rows naming a job whose row is gone are
grouped under one ``book_id: None`` group ("Deleted book"), so that spend
never drops out of the view.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.models.ai_job import AIJob
from backend.models.ai_usage import AIUsage
from backend.models.book import Book
from backend.models.user import User
from backend.services.ai import digest_job


def _parse(iso: str | None) -> datetime | None:
    if not iso:
        return None
    return datetime.fromisoformat(iso.rstrip("Z"))


def _job_entry(snap: dict[str, Any], now: datetime) -> dict[str, Any]:
    started = _parse(snap.get("started_at"))
    finished = _parse(snap.get("finished_at"))
    actual = None
    if started is not None:
        actual = max(0.0, ((finished or now) - started).total_seconds())
    return {
        "id": snap["id"], "user_id": snap["user_id"], "scope": snap["scope"],
        "status": snap["status"], "total": snap["total"], "done": snap["done"],
        "failed": snap["failed"], "calls": snap["calls"], "cost_usd": snap["cost_usd"],
        "est_cost_usd": snap.get("est_cost_usd"), "est_seconds": snap.get("est_seconds"),
        "actual_seconds": None if actual is None else round(actual, 1),
        "created_at": snap["created_at"], "finished_at": snap["finished_at"],
        "stop_reason": snap["stop_reason"],
        "step": snap.get("step"), "step_done": snap.get("step_done", 0),
        "step_total": snap.get("step_total", 0),
    }


def list_jobs(db: Session, *, user_id: int | None, days: int = 30,
              with_usernames: bool = False) -> dict[str, Any]:
    """``{running: [snapshot + book_title], books: [{book_id, title, series,
    series_index, cost_usd, calls, chapters_digested, last_activity, jobs:
    [...]}]}``; one user's when ``user_id`` is set, else everyone's."""
    now = datetime.utcnow()
    since = now - timedelta(days=max(1, days))
    live = {s["id"]: s for s in digest_job.running(user_id)}

    q = db.query(AIJob).filter(AIJob.created_at >= since)
    if user_id is not None:
        q = q.filter(AIJob.user_id == user_id)
    snaps = {r.id: digest_job.row_snapshot(r) for r in q.all()}
    snaps.update(live)   # live values win; a live job without a row still shows

    books: dict[int | None, dict[str, Any]] = {}

    def group(book_id: int | None) -> dict[str, Any]:
        return books.setdefault(book_id, {"book_id": book_id, "cost_usd": 0.0, "calls": 0,
                                          "chapters_digested": 0, "last": None, "jobs": []})

    def touch(g: dict[str, Any], when: datetime | None) -> None:
        if when is not None and (g["last"] is None or when > g["last"]):
            g["last"] = when

    for snap in snaps.values():
        g = group(snap["book_id"])
        entry = _job_entry(snap, now)
        g["jobs"].append(entry)
        g["cost_usd"] += snap["cost_usd"] or 0.0
        g["calls"] += snap["calls"] or 0
        g["chapters_digested"] += max(0, (snap["done"] or 0) - (snap["failed"] or 0))
        touch(g, _parse(snap["finished_at"]) or _parse(snap["started_at"]) or _parse(snap["created_at"]))
        if snap["id"] in live:
            touch(g, now)

    uq = (
        db.query(AIUsage.book_id, func.count(AIUsage.id), func.sum(AIUsage.cost_usd),
                 func.max(AIUsage.created_at))
        .filter(AIUsage.job_id.is_(None), AIUsage.book_id.isnot(None), AIUsage.created_at >= since)
    )
    if user_id is not None:
        uq = uq.filter(AIUsage.user_id == user_id)
    for book_id, calls, cost, last in uq.group_by(AIUsage.book_id).all():
        g = group(book_id)
        g["calls"] += int(calls or 0)
        g["cost_usd"] += float(cost or 0.0)
        touch(g, last)

    # Calls of jobs whose row went with a deleted book.
    oq = (
        db.query(func.count(AIUsage.id), func.sum(AIUsage.cost_usd), func.max(AIUsage.created_at))
        .outerjoin(AIJob, AIJob.id == AIUsage.job_id)
        .filter(AIUsage.job_id.isnot(None), AIJob.id.is_(None), AIUsage.created_at >= since)
    )
    if user_id is not None:
        oq = oq.filter(AIUsage.user_id == user_id)
    calls, cost, last = oq.one()
    if calls:
        g = group(None)
        g["calls"] += int(calls)
        g["cost_usd"] += float(cost or 0.0)
        touch(g, last)

    book_ids = [i for i in books if i is not None]
    meta = {b.id: b for b in db.query(Book).filter(Book.id.in_(book_ids or [0])).all()}
    usernames: dict[int, str] = {}
    if with_usernames:
        ids = {s["user_id"] for s in snaps.values()}
        usernames = {u.id: u.username for u in db.query(User).filter(User.id.in_(list(ids) or [0]))}

    out_books = []
    for g in sorted(books.values(), key=lambda g: g["last"] or datetime.min, reverse=True):
        b = meta.get(g["book_id"])
        g["jobs"].sort(key=lambda j: j["created_at"] or "", reverse=True)
        if with_usernames:
            for j in g["jobs"]:
                j["username"] = usernames.get(j["user_id"])
        out_books.append({
            "book_id": g["book_id"],
            "title": b.title if b else None,
            "series": b.series if b else None,
            "series_index": b.series_index if b else None,
            "cost_usd": round(g["cost_usd"], 4),
            "calls": g["calls"],
            "chapters_digested": g["chapters_digested"],
            "last_activity": g["last"].isoformat() + "Z" if g["last"] else None,
            "jobs": g["jobs"],
        })

    running = []
    for snap in live.values():
        b = meta.get(snap["book_id"]) or db.get(Book, snap["book_id"])
        item = {**snap, "book_title": b.title if b else None}
        if with_usernames:
            item["username"] = usernames.get(snap["user_id"])
        running.append(item)
    return {"days": days, "running": running, "books": out_books}


def job_calls(db: Session, job_id: str) -> list[dict[str, Any]]:
    """The usage rows of one job, oldest first."""
    rows = (
        db.query(AIUsage)
        .filter(AIUsage.job_id == job_id)
        .order_by(AIUsage.created_at.asc(), AIUsage.id.asc())
        .all()
    )
    return [{
        "id": r.id,
        "created_at": r.created_at.isoformat() + "Z" if r.created_at else None,
        "model": r.model, "feature": r.feature, "book_id": r.book_id,
        "input_tokens": r.input_tokens, "output_tokens": r.output_tokens,
        "cache_read_tokens": r.cache_read_tokens, "cache_write_tokens": r.cache_write_tokens,
        "duration_ms": r.duration_ms, "cost_usd": round(r.cost_usd, 6),
    } for r in rows]
