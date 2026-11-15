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

A channel URL is fetched from the server, so it could probe the internal
network. An admin's channel may point anywhere (a LAN ntfy is a normal
setup). Anyone else's must resolve to public addresses only, checked when the
channel is saved and again on every send, unless its host is listed in
TOME_WEBHOOK_ALLOWED_HOSTS; the send then connects to the address that was
checked. Redirects are never followed, for any channel.
"""
from __future__ import annotations

import http.client
import json
import logging
import socket
import threading
import urllib.parse
import urllib.request
from typing import Any

from sqlalchemy import event
from sqlalchemy.orm import Session, object_session

from backend.core.config import settings
from backend.models.notification import Notification, NotificationChannel
from backend.services.safe_fetch import validate_public_url

log = logging.getLogger(__name__)

_PENDING_KEY = "pending_outbound_notifications"

# The bell notice that tells a user one of their channels is refused. It is
# never pushed outward itself, or it would go straight back to that channel.
CHANNEL_BLOCKED_KIND = "channel_blocked"


@event.listens_for(Notification, "after_insert")
def _collect_inserted(mapper, connection, target: Notification) -> None:  # noqa: ARG001
    sess = object_session(target)
    if sess is None or target.kind == CHANNEL_BLOCKED_KIND:
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
        from backend.core.permissions import is_admin
        from backend.models.user import User

        user_ids = {p["user_id"] for p in payloads}
        channels = (
            db.query(NotificationChannel)
            .filter(NotificationChannel.user_id.in_(user_ids),
                    NotificationChannel.enabled == True)  # noqa: E712
            .all()
        )
        admins = {
            u.id for u in db.query(User).filter(User.id.in_(user_ids)).all() if is_admin(u)
        }
        by_user: dict[int, list[NotificationChannel]] = {}
        for c in channels:
            by_user.setdefault(c.user_id, []).append(c)
        for p in payloads:
            for c in by_user.get(p["user_id"], []):
                try:
                    deliver(c.kind, c.url, c.token, p, restricted=c.user_id not in admins)
                except Exception as exc:  # noqa: BLE001 — best-effort by design
                    log.info("outbound notify: %s -> %s failed: %s", p["kind"], c.kind, exc)
                    if c.user_id not in admins and channel_blocked(c.url):
                        _note_blocked(db, c)
    finally:
        db.close()


_KIND_NAME = {"ntfy": "ntfy", "gotify": "Gotify", "webhook": "webhook"}


def _note_blocked(db: Session, channel: NotificationChannel) -> None:
    """Tell the owner in the bell, once per channel address, that this
    channel is refused by the address rule. Without it they would only notice
    that the pushes stopped."""
    body = f"{channel.url} is not a public internet address."
    try:
        seen = (
            db.query(Notification.id)
            .filter(Notification.user_id == channel.user_id,
                    Notification.kind == CHANNEL_BLOCKED_KIND,
                    Notification.body == body)
            .first()
        )
        if seen:
            return
        name = _KIND_NAME.get(channel.kind, channel.kind)
        db.add(Notification(
            user_id=channel.user_id, kind=CHANNEL_BLOCKED_KIND,
            title=f"Your {name} channel is no longer sent",
            body=body, link="/settings#notifications",
        ))
        db.commit()
    except Exception:  # noqa: BLE001 — a notice must never break the fan-out
        db.rollback()


def allowed_hosts() -> set[str]:
    """Hosts from TOME_WEBHOOK_ALLOWED_HOSTS (lower-case, no brackets)."""
    raw = settings.webhook_allowed_hosts or ""
    return {h.strip().strip("[]").lower() for h in raw.split(",") if h.strip()}


def check_channel_url(url: str) -> str | None:
    """For a non-admin's channel: the public address to connect to, or None
    when the host is on the operator's allow list. Raises UnsafeURLError when
    the URL is not allowed."""
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    if host and host in allowed_hosts():
        return None
    _host, ip = validate_public_url(url)
    return ip


NOT_PUBLIC_DETAIL = (
    "This is not a public internet address. "
    "An admin can allow a local host with TOME_WEBHOOK_ALLOWED_HOSTS."
)


def channel_blocked(url: str) -> bool:
    """True when a non-admin's channel would be refused at send time because
    its host is, or resolves to, a non-public address. A name that cannot be
    resolved right now is not reported as blocked: that is a different
    failure, and it may pass."""
    from backend.services.safe_fetch import _is_public_ip

    host = (urllib.parse.urlparse(url).hostname or "").lower()
    if not host or host in allowed_hosts():
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    return any(not _is_public_ip(info[4][0]) for info in infos)


def deliver(
    kind: str,
    url: str,
    token: str | None,
    payload: dict[str, Any],
    *,
    restricted: bool = False,
) -> None:
    """Send one notification to one channel. Raises on failure (caller logs;
    the Settings test button surfaces the error). `restricted` (a channel
    that does not belong to an admin) checks the address before sending and
    connects to the address it checked."""
    pinned_ip = check_channel_url(url) if restricted else None
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
    with _open(req, timeout=5, pinned_ip=pinned_ip):
        pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A 3xx is an error, never followed: a redirect would take the request
    to an address that was not checked."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ARG002
        return None


def _pinned(conn_cls, ip: str):
    class _Pinned(conn_cls):
        # Connect to the checked address; TLS still verifies the URL's host
        # name, because http.client uses self.host for SNI and the check.
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._create_connection = (
                lambda address, *a, **kw: socket.create_connection((ip, address[1]), *a, **kw)
            )

    return _Pinned


class _PinnedHTTPHandler(urllib.request.HTTPHandler):
    def __init__(self, ip: str):
        super().__init__()
        self._ip = ip

    def http_open(self, req):
        return self.do_open(_pinned(http.client.HTTPConnection, self._ip), req)


class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, ip: str):
        super().__init__()
        self._ip = ip

    def https_open(self, req):
        return self.do_open(_pinned(http.client.HTTPSConnection, self._ip), req,
                            context=self._context)


def _open(req: urllib.request.Request, *, timeout: float, pinned_ip: str | None):
    handlers: list[urllib.request.BaseHandler] = [_NoRedirect()]
    if pinned_ip and not _proxied(req.full_url):
        handlers += [_PinnedHTTPHandler(pinned_ip), _PinnedHTTPSHandler(pinned_ip)]
    return urllib.request.build_opener(*handlers).open(req, timeout=timeout)


def _absolute(link: str) -> str:
    """In-app links are site-relative; prefix the public origin when known."""
    if link.startswith("http://") or link.startswith("https://"):
        return link
    base = (settings.public_url or "").rstrip("/")
    return f"{base}{link}" if base else link



def _proxied(url: str) -> bool:
    """True when an environment proxy carries this request (the proxy then
    connects, so there is no address of ours to pin)."""
    parts = urllib.parse.urlparse(url)
    proxies = urllib.request.getproxies()
    return parts.scheme in proxies and not urllib.request.proxy_bypass(parts.hostname or "")
