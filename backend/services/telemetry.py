"""Opt-in instance telemetry: the report, and the consent that gates it.

Rules this module enforces, in order of importance:

- Nothing is sent without a stored, positive consent row written by an admin.
  An absent row, a declined row and the TOME_TELEMETRY=false env var are all
  the same thing: nothing leaves.
- The preview the admin sees is produced by the same function that would be
  sent. There is no second code path that could drift.
- Consent is tied to REPORT_SCHEMA. Adding a field bumps the version, which
  stops sends until the admin has looked at the new shape and re-enabled.
- The report carries nothing that identifies a person, a book or a server:
  counts are bucketed, feature use is a boolean over the last 30 days, mixes
  are shares, the instance id is a random UUID minted here and related to
  nothing else. The receiving end (tome-pulse) rejects anything outside this
  exact shape.

The sender (``send_if_due``) runs from a daily background loop and once right
after consent. It asks ``may_send`` first, every time.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import random
import urllib.request
import uuid
from datetime import datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from backend import __version__
from backend.core.config import settings
from backend.models.api_token import ApiToken
from backend.models.audit_log import AuditLog
from backend.models.book import Book, BookFile
from backend.models.client_device import ClientDevice
from backend.models.instance_setting import InstanceSetting
from backend.models.library import BookType, Library
from backend.models.opds_pin import OpdsPin
from backend.models.reading_goal import ReadingGoal
from backend.models.send_queue import SendQueueItem
from backend.models.series_meta import Arc
from backend.models.tome_sync import Annotation, ReadingSession
from backend.models.user import User
from backend.models.user_book_status import UserBookStatus
from backend.models.user_series_rating import UserSeriesRating
from backend.models.wish import Wish

# Bump when a field is added, removed or changes meaning. Stored consent
# records the version it was given for; a mismatch pauses sending.
REPORT_SCHEMA = 1

KEY_CONSENT = "telemetry_consent"          # "granted" | "declined"
KEY_CONSENT_AT = "telemetry_consent_at"
KEY_CONSENT_SCHEMA = "telemetry_consent_schema"
KEY_INSTANCE_ID = "telemetry_instance_id"
KEY_LAST_SENT = "telemetry_last_sent"
KEY_NEXT_DUE = "telemetry_next_due"

SEND_EVERY_DAYS = 30
logger = logging.getLogger(__name__)

WINDOW_DAYS = 30
_BUCKETS = [1, 2, 5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000]
# The browser reader reports under these device names; everything else is a device.
_WEB_DEVICES = {"web", "tome web"}


def _get(db: Session, key: str) -> str | None:
    row = db.get(InstanceSetting, key)
    return row.value if row else None


def _set(db: Session, key: str, value: str) -> None:
    row = db.get(InstanceSetting, key)
    if row:
        row.value = value
    else:
        db.add(InstanceSetting(key=key, value=value))


def bucket(n: int | float) -> str:
    """0 -> "0", 1 -> "1", 7 -> "6-10", 1234 -> "1001-2500", above the table -> "10000+"."""
    n = int(n)
    if n <= 0:
        return "0"
    lo = 0
    for hi in _BUCKETS:
        if n <= hi:
            return str(hi) if hi == 1 or lo + 1 == hi else f"{lo + 1}-{hi}"
        lo = hi
    return f"{_BUCKETS[-1]}+"


def _share(pairs: list[tuple[str | None, int]], top: int) -> dict[str, float]:
    """Top-N shares of a count-by-key query, keys lower-cased, unknowns folded."""
    counts: dict[str, int] = {}
    for key, n in pairs:
        k = (key or "unknown").strip().lower()[:24] or "unknown"
        counts[k] = counts.get(k, 0) + int(n)
    total = sum(counts.values())
    if not total:
        return {}
    shares = {k: round(v / total, 2) for k, v in sorted(counts.items(), key=lambda kv: -kv[1])[:top]}
    return {k: v for k, v in shares.items() if v > 0}


def _count(db: Session, model, *filters) -> int:
    return db.query(func.count()).select_from(model).filter(*filters).scalar() or 0


def instance_id(db: Session) -> str:
    """A random id minted once per database. Not derived from anything."""
    existing = _get(db, KEY_INSTANCE_ID)
    if existing:
        return existing
    new = str(uuid.uuid4())
    _set(db, KEY_INSTANCE_ID, new)
    # Committed on first preview so the id the admin sees is the one that
    # would be sent. It is random and local; nothing leaves until consent.
    db.commit()
    return new


def build_report(db: Session) -> dict:
    """The exact payload a send would carry. Also what the admin previews."""
    from backend.api.tome_sync import TOMESYNC_PLUGIN_BUILD

    now = datetime.utcnow()
    since = now - timedelta(days=WINDOW_DAYS)

    first_user = db.query(func.min(User.created_at)).scalar()
    install_months = int((now - first_user).days / 30) if first_user else 0

    sessions = db.query(ReadingSession).filter(ReadingSession.started_at >= since)
    reading = db.query(
        func.count(ReadingSession.id),
        func.coalesce(func.sum(ReadingSession.duration_seconds), 0),
        func.coalesce(func.sum(ReadingSession.pages_turned), 0),
        func.count(func.distinct(ReadingSession.user_id)),
    ).filter(ReadingSession.started_at >= since).one()
    session_count, seconds, pages, readers = (int(x or 0) for x in reading)
    devices_30d = {(d or "").lower() for (d,) in sessions.with_entities(ReadingSession.device).distinct().all()}

    return {
        "schema": REPORT_SCHEMA,
        "instance": instance_id(db),
        "version": __version__,
        "plugin_build": TOMESYNC_PLUGIN_BUILD,
        "platform": f"{platform.system().lower()}/{platform.machine().lower()}",
        "docker": os.path.exists("/.dockerenv"),
        "install_age": bucket(install_months),
        "users": bucket(_count(db, User, User.is_active.is_(True))),
        "readers_30d": bucket(readers),
        "books": bucket(_count(db, Book)),
        "libraries": bucket(_count(db, Library)),
        "book_types": _share(
            db.query(BookType.slug, func.count(Book.id)).join(Book, Book.book_type_id == BookType.id).group_by(BookType.slug).all(), top=8),
        "formats": _share(db.query(BookFile.format, func.count(BookFile.id)).group_by(BookFile.format).all(), top=8),
        "languages": _share(db.query(Book.language, func.count(Book.id)).group_by(Book.language).all(), top=5),
        "reading_30d": {
            "sessions": bucket(session_count),
            "hours": bucket(seconds / 3600),
            "pages": bucket(pages),
            "finished": bucket(_count(db, UserBookStatus, UserBookStatus.finished_at >= since)),
        },
        "highlights": bucket(_count(db, Annotation)),
        "features_30d": {
            "koreader_sync": any(d and d not in _WEB_DEVICES for d in devices_30d),
            "web_reader": any(d in _WEB_DEVICES for d in devices_30d),
            "kindle_sync_code": _count(db, ClientDevice, ClientDevice.last_seen_at >= since) > 0,
            "send_to_device": _count(db, SendQueueItem, SendQueueItem.created_at >= since) > 0,
            "bindery": _count(db, AuditLog, AuditLog.action == "bindery.accepted", AuditLog.created_at >= since) > 0,
            "wishlist": _count(db, Wish) > 0,
            "hardcover": _count(db, User, User.hardcover_token.isnot(None)) > 0,
            "opds": _count(db, OpdsPin) > 0,
            "sso_users": _count(db, User, User.auth_source == "oidc") > 0,
            "api_tokens": _count(db, ApiToken) > 0,
            "reading_goals": _count(db, ReadingGoal) > 0,
            "series_ratings": _count(db, UserSeriesRating) > 0,
            "arcs": _count(db, Arc) > 0,
        },
    }


def consent_state(db: Session) -> dict:
    """Where the instance stands. The frontend renders this in words."""
    if not settings.telemetry:
        return {"state": "env_off", "schema": REPORT_SCHEMA}
    decision = _get(db, KEY_CONSENT)
    if decision not in ("granted", "declined"):
        return {"state": "unset", "schema": REPORT_SCHEMA}
    out = {
        "state": decision,
        "schema": REPORT_SCHEMA,
        "decided_at": _get(db, KEY_CONSENT_AT),
        "last_sent": _get(db, KEY_LAST_SENT),
        "next_due": _get(db, KEY_NEXT_DUE),
    }
    if decision == "granted":
        given_for = int(_get(db, KEY_CONSENT_SCHEMA) or 0)
        # The report changed since consent was given: sending is paused until
        # the admin has seen the new shape.
        out["stale"] = given_for != REPORT_SCHEMA
    return out


def record_consent(db: Session, decision: str) -> dict:
    assert decision in ("granted", "declined")
    _set(db, KEY_CONSENT, decision)
    _set(db, KEY_CONSENT_AT, datetime.utcnow().isoformat(timespec="seconds"))
    if decision == "granted":
        _set(db, KEY_CONSENT_SCHEMA, str(REPORT_SCHEMA))
        # Due now: the first report goes out right after consent.
        _set(db, KEY_NEXT_DUE, datetime.utcnow().isoformat(timespec="seconds"))
        instance_id(db)
    db.commit()
    return consent_state(db)


def may_send(db: Session) -> bool:
    """The one question the sender asks. Every path that is not an explicit,
    current grant answers no."""
    state = consent_state(db)
    return state["state"] == "granted" and not state.get("stale", False)


def report_json(db: Session) -> str:
    return json.dumps(build_report(db), indent=2, sort_keys=False)


def _next_due(after: datetime) -> datetime:
    """A month on, plus up to six hours of jitter so a release day does not make
    every instance report in the same hour."""
    return after + timedelta(days=SEND_EVERY_DAYS, seconds=random.randint(0, 6 * 3600))


def send_if_due(db: Session, *, force: bool = False) -> str:
    """Send one report if consent is current and the month is up.

    Returns a short word for the log: "skipped" (no current consent),
    "not_due", "sent", or "failed". Never raises. A failure leaves the due date
    alone, so the daily loop simply tries again tomorrow.
    """
    if not may_send(db):
        return "skipped"
    now = datetime.utcnow()
    due_raw = _get(db, KEY_NEXT_DUE)
    due = datetime.fromisoformat(due_raw) if due_raw else now
    if not force and now < due:
        return "not_due"

    report = build_report(db)
    body = json.dumps(report, separators=(",", ":"), sort_keys=True).encode()
    digest = hashlib.sha256(body).hexdigest()[:16]
    req = urllib.request.Request(
        settings.telemetry_url, data=body, method="POST",
        headers={"Content-Type": "application/json", "User-Agent": f"tome/{__version__}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            status = resp.status
    except Exception as e:  # network, DNS, 4xx/5xx: all "try again tomorrow"
        logger.warning("Telemetry report not sent (%s); will retry on the next daily check", e)
        return "failed"
    if status not in (200, 204):
        logger.warning("Telemetry endpoint answered %s; will retry on the next daily check", status)
        return "failed"

    _set(db, KEY_LAST_SENT, now.isoformat(timespec="seconds"))
    _set(db, KEY_NEXT_DUE, _next_due(now).isoformat(timespec="seconds"))
    db.commit()
    # The audit log is the admin's own record of what left and when.
    from backend.services.audit import audit
    audit(db, "telemetry.sent", resource_type="instance",
          details={"schema": REPORT_SCHEMA, "sha256": digest, "bytes": len(body)})
    return "sent"
