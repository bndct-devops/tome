"""Outbound notification fanout — ntfy / Gotify / plain webhook.

The in-app bell only fires when the user visits the web UI; users who run
ntfy/Gotify want the same events pushed the moment they happen. Zero changes
at the six Notification creation sites: an ORM event collects freshly
inserted notifications on the session, and after the transaction COMMITS the
payloads are handed to a daemon thread that loads the owners' enabled
channels and POSTs to each (5s timeout, best-effort, no retries — a missed
push is not worth blocking or crashing anything).

TOME_OUTBOUND_NOTIFY=false is the operator kill-switch; per-user channels
also have their own enabled flag.
"""
from __future__ import annotations

import json
import logging
import threading
import urllib.parse
import urllib.request
from typing import Any

from sqlalchemy import event
from sqlalchemy.orm import Session, object_session

from backend.core.config import settings
from backend.models.notification import Notification, NotificationChannel

log = logging.getLogger(__name__)

_PENDING_KEY = "pending_outbound_notifications"


@event.listens_for(Notification, "after_insert")
def _collect_inserted(mapper, connection, target: Notification) -> None:  # noqa: ARG001
    sess = object_session(target)
    if sess is None:
        return
    sess.info.setdefault(_PENDING_KEY, []).append({
        "user_id": target.user_id,
        "kind": target.kind,
        "title": target.title,
        "body": target.body,
        "link": target.link,
    })


@event.listens_for(Session, "after_commit")
def _dispatch_after_commit(session: Session) -> None:
    pending = session.info.pop(_PENDING_KEY, None)
    if not pending or not settings.outbound_notify:
        return
    _spawn(pending)


def _spawn(payloads: list[dict]) -> None:
    """Hand off to a daemon thread. Split out so tests can run it inline."""
    threading.Thread(target=_send_all, args=(payloads,), daemon=True).start()


def _session_factory() -> Session:
    """Fresh session for the worker thread (injectable for tests, which run
    on an in-memory engine the app-level SessionLocal knows nothing about)."""
    from backend.core.database import SessionLocal

    return SessionLocal()


def _send_all(payloads: list[dict]) -> None:
    db = _session_factory()
    try:
        user_ids = {p["user_id"] for p in payloads}
        channels = (
            db.query(NotificationChannel)
            .filter(NotificationChannel.user_id.in_(user_ids),
                    NotificationChannel.enabled == True)  # noqa: E712
            .all()
        )
        by_user: dict[int, list[NotificationChannel]] = {}
        for c in channels:
            by_user.setdefault(c.user_id, []).append(c)
        for p in payloads:
            for c in by_user.get(p["user_id"], []):
                try:
                    deliver(c.kind, c.url, c.token, p)
                except Exception as exc:  # noqa: BLE001 — best-effort by design
                    log.info("outbound notify: %s -> %s failed: %s", p["kind"], c.kind, exc)
    finally:
        db.close()


def deliver(kind: str, url: str, token: str | None, payload: dict[str, Any]) -> None:
    """Send one notification to one channel. Raises on failure (caller logs;
    the Settings test button surfaces the error to the user)."""
    title = payload.get("title") or "Tome"
    body = payload.get("body") or ""
    link = payload.get("link")
    if kind == "ntfy":
        headers = {"Title": title.encode("ascii", "replace").decode()}
        if link:
            headers["Click"] = _absolute(link)
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(url, data=body.encode(), headers=headers, method="POST")
    elif kind == "gotify":
        msg_url = url.rstrip("/") + "/message?token=" + urllib.parse.quote(token or "")
        req = urllib.request.Request(
            msg_url,
            data=json.dumps({"title": title, "message": body or title, "priority": 5}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
    elif kind == "webhook":
        req = urllib.request.Request(
            url,
            data=json.dumps({
                "event": payload.get("kind"),
                "title": title,
                "body": body,
                "link": _absolute(link) if link else None,
            }).encode(),
            headers={"Content-Type": "application/json", "User-Agent": "tome-notify"},
            method="POST",
        )
    else:
        raise ValueError(f"unknown channel kind: {kind}")
    with urllib.request.urlopen(req, timeout=5):
        pass


def _absolute(link: str) -> str:
    """In-app links are site-relative; prefix the public origin when known."""
    if link.startswith("http://") or link.startswith("https://"):
        return link
    base = (settings.public_url or "").rstrip("/")
    return f"{base}{link}" if base else link

