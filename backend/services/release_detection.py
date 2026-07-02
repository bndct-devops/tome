"""Release detection — "a new volume of a series you follow is out".

A follow is a ``Wish`` row with ``kind="follow"`` (the columns were reserved by
the wishlist plan precisely for this): ``external_series_id`` is the canonical
Hardcover series id, ``latest_known_index`` the highest volume position seen at
the last check, ``last_checked_at`` the poll bookkeeping. The poller compares
Hardcover's current highest position against ``latest_known_index`` and, when
it grows, notifies the follower through the existing Notification bell (+ email
when SMTP is configured) and advances the watermark.

Priming rule: a follow starts at Hardcover's *current* latest volume, so only
releases that happen AFTER following notify — following a 27-volume series
doesn't fire 27 alerts. Gated by ``TOME_RELEASE_DETECTION`` (default off) and
requires the Hardcover token; without either, the loop idles.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

import httpx
from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.models.notification import Notification
from backend.models.user import User
from backend.models.wish import Wish
from backend.services.metadata_fetch import HARDCOVER_URL

log = logging.getLogger(__name__)

_VOLUMES_QUERY = """
query SeriesLatest($id: Int!) {
    series(where: {id: {_eq: $id}}) {
        id
        name
        primary_books_count
        book_series(order_by: {position: desc_nulls_last}, limit: 3) {
            position
            book { title release_date }
        }
    }
}
"""


async def fetch_series_latest(series_id: int) -> Optional[dict]:
    """Hardcover's current view of a series: highest volume position + metadata.

    Returns ``{"latest_index", "latest_title", "release_date", "total", "name"}``
    or None on any failure (offline, rate-limited, series gone) — the caller
    keeps the old watermark and retries next cycle.
    """
    token = settings.hardcover_token
    if not token:
        return None
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                HARDCOVER_URL,
                json={"query": _VOLUMES_QUERY, "variables": {"id": series_id}},
                headers={"authorization": token},
            )
            resp.raise_for_status()
            rows = resp.json().get("data", {}).get("series") or []
    except Exception as exc:
        log.warning("Hardcover series %s check failed: %s", series_id, exc)
        return None
    if not rows:
        return None
    s = rows[0]
    vols = s.get("book_series") or []
    latest = next((v for v in vols if v.get("position") is not None), None)
    if latest is None:
        return None
    book = latest.get("book") or {}
    return {
        "latest_index": float(latest["position"]),
        "latest_title": book.get("title"),
        "release_date": book.get("release_date"),
        "total": s.get("primary_books_count"),
        "name": s.get("name"),
    }


def _notify_release(db: Session, wish: Wish, state: dict) -> None:
    vol = state["latest_index"]
    vol_str = str(int(vol)) if float(vol).is_integer() else str(vol)
    title = f"Volume {vol_str} of \"{wish.title}\" is out"
    body_bits = []
    if state.get("latest_title"):
        body_bits.append(f"\"{state['latest_title']}\"")
    if state.get("release_date"):
        body_bits.append(f"released {state['release_date']}")
    body = " — ".join(body_bits) or None
    db.add(Notification(
        user_id=wish.user_id,
        kind="release_out",
        title=title,
        body=body,
        link="/wishlist",
    ))
    if settings.smtp_configured:
        try:
            from backend.services.email import send_release_email
            follower = db.get(User, wish.user_id)
            if follower and follower.email:
                send_release_email(follower.email, wish, state)
        except Exception:
            log.exception("Failed to send release email for follow %d", wish.id)


async def check_follows(db: Session, *, force: bool = False) -> dict:
    """Poll Hardcover for every open follow that's due, notify on new volumes.

    A follow is due when ``last_checked_at`` is older than the configured
    interval (or ``force``). Returns counters for the admin trigger/logs.
    """
    if not settings.hardcover_token:
        return {"checked": 0, "notified": 0, "skipped": "no hardcover token"}

    due_before = datetime.utcnow() - timedelta(seconds=settings.release_check_interval)
    q = db.query(Wish).filter(Wish.kind == "follow", Wish.status == "open",
                              Wish.external_series_id.isnot(None))
    if not force:
        q = q.filter((Wish.last_checked_at.is_(None)) | (Wish.last_checked_at < due_before))
    follows = q.all()

    checked = notified = 0
    for wish in follows:
        try:
            sid = int(wish.external_series_id)
        except (TypeError, ValueError):
            continue
        state = await fetch_series_latest(sid)
        if state is None:
            continue   # keep watermark; retry next cycle
        checked += 1
        wish.last_checked_at = datetime.utcnow()
        if wish.latest_known_index is None:
            # First successful check primes the watermark silently.
            wish.latest_known_index = state["latest_index"]
            wish.latest_known_title = state.get("latest_title")
            wish.latest_release_date = state.get("release_date")
        elif state["latest_index"] > wish.latest_known_index:
            _notify_release(db, wish, state)
            wish.latest_known_index = state["latest_index"]
            wish.latest_known_title = state.get("latest_title")
            wish.latest_release_date = state.get("release_date")
            notified += 1
        elif wish.latest_known_title is None and state["latest_index"] == wish.latest_known_index:
            # Same volume as the watermark — backfill title/date for follows
            # created before these columns existed (no notification).
            wish.latest_known_title = state.get("latest_title")
            wish.latest_release_date = state.get("release_date")
        db.flush()
    db.commit()
    return {"checked": checked, "notified": notified}
