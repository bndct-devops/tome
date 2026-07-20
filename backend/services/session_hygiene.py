"""Runaway-session detection (issue #150).

A device left awake without turning pages (reader fell asleep, cover failed
to suspend) books wall-clock time as reading. Plugin build 37 caps idle gaps
at the source, but sessions from older builds — and the ones already in the
DB — still need flagging. These rules are shared by the sync ingest path
(which raises a notification the moment a suspect session arrives) and the
sessions list (which marks suspect rows and proposes a trimmed duration).

A session is suspect when it is implausibly long for one sitting, or when
its average time per page turn says the device mostly sat idle. The
suggested duration re-prices the session's page turns at the user's own
median pace, so the trim keeps the reading and drops the idle tail.
"""
from __future__ import annotations

import logging
import statistics
from typing import Optional

from sqlalchemy.orm import Session

from backend.models.notification import Notification
from backend.models.tome_sync import ReadingSession

log = logging.getLogger(__name__)

SUSPECT_ABS_SECONDS = 6 * 3600   # a single sitting beyond this is flagged outright
SUSPECT_SECS_PER_PAGE = 600      # 10+ min average per page turn means idle time
PACE_MIN, PACE_MAX = 5, 600      # plausible secs/page band for the median
_MEDIAN_SAMPLE = 500             # most recent sessions considered for the median


def is_suspect(duration_seconds: Optional[int], pages_turned: Optional[int]) -> bool:
    if not duration_seconds:
        return False
    if duration_seconds >= SUSPECT_ABS_SECONDS:
        return True
    if pages_turned and pages_turned > 0:
        return duration_seconds / pages_turned > SUSPECT_SECS_PER_PAGE
    return False


def median_secs_per_page(db: Session, user_id: int) -> Optional[float]:
    """The user's typical dwell time per page turn, from their own history.

    Only sessions whose average falls in a plausible band contribute, so the
    runaway sessions being diagnosed can't drag their own baseline up.
    """
    rows = (
        db.query(ReadingSession.duration_seconds, ReadingSession.pages_turned)
        .filter(
            ReadingSession.user_id == user_id,
            ReadingSession.duration_seconds.isnot(None),
            ReadingSession.pages_turned > 0,
        )
        .order_by(ReadingSession.started_at.desc())
        .limit(_MEDIAN_SAMPLE)
        .all()
    )
    paces = [
        d / p
        for d, p in rows
        if d and p and PACE_MIN <= d / p <= PACE_MAX
    ]
    if len(paces) < 3:  # too little history for a meaningful baseline
        return None
    return statistics.median(paces)


def suggested_seconds(
    duration_seconds: Optional[int],
    pages_turned: Optional[int],
    median_pace: Optional[float],
) -> Optional[int]:
    """Plausible real duration for a suspect session, or None when there is
    nothing to base it on. Never suggests more than the recorded duration."""
    if not duration_seconds or not pages_turned or pages_turned <= 0 or not median_pace:
        return None
    estimate = round(pages_turned * median_pace)
    if estimate >= duration_seconds:
        return None
    return max(60, estimate)


def notify_suspect_session(
    db: Session, user_id: int, book_title: str, duration_seconds: int
) -> None:
    """Raise a bell notification for a freshly synced suspect session.

    Deduped on unread title so a morning flush of several runaway sessions
    for the same book doesn't stack identical notices. Caller commits.
    """
    hours = duration_seconds / 3600
    title = f'Unusually long reading session on "{book_title}"'
    exists = (
        db.query(Notification.id)
        .filter(
            Notification.user_id == user_id,
            Notification.kind == "session_suspect",
            Notification.title == title,
            Notification.read.is_(False),
        )
        .first()
    )
    if exists:
        return
    db.add(
        Notification(
            user_id=user_id,
            kind="session_suspect",
            title=title,
            body=(
                f"A {hours:.1f}-hour session was just synced. If the device sat "
                "awake unread, you can trim or delete it under Reading Stats."
            ),
            link="/stats",
        )
    )
