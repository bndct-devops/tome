"""Notification channels cannot be used to probe the server's network.

An admin's channel may point anywhere (a LAN ntfy is normal). Anyone else's
must resolve to public addresses, at save time and at send time, unless the
host is in TOME_WEBHOOK_ALLOWED_HOSTS. No channel follows a redirect, and a
non-admin's Test button never shows the upstream error.
"""
import threading
import urllib.error
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from sqlalchemy.orm import Session, sessionmaker
from starlette.testclient import TestClient

import backend.services.outbound_notifications as outbound
from backend.core.config import settings
from backend.core.database import get_db
from backend.core.security import create_access_token, hash_password
from backend.models.notification import Notification, NotificationChannel
from backend.models.user import User
from backend.services.safe_fetch import UnsafeURLError


def _public_dns(monkeypatch, ip="93.184.216.34"):
    monkeypatch.setattr("backend.services.safe_fetch.socket.getaddrinfo",
                        lambda host, port: [(0, 0, 0, "", (ip, 0))])


@pytest.fixture()
def member(db: Session):
    u = User(username="reedmember", email="reed@example.org",
             hashed_password=hash_password("memberpass1"), is_active=True,
             is_admin=False, role="member", must_change_password=False)
    db.add(u)
    db.flush()
    return u


@pytest.fixture()
def member_client(db: Session, member):
    from backend.main import create_app

    app = create_app()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        c.headers["Authorization"] = f"Bearer {create_access_token(member.id)}"
        yield c
    app.dependency_overrides.clear()


# ── save time ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8080/hook",
    "http://localhost/hook",
    "http://192.168.1.20/topic",
    "http://169.254.169.254/latest/meta-data/",
    "http://[::1]/x",
])
def test_member_cannot_save_a_local_address(member_client, url):
    r = member_client.post("/api/notification-channels", json={"kind": "webhook", "url": url})
    assert r.status_code == 422
    assert "public" in r.json()["detail"]


def test_member_can_save_a_public_address(member_client, monkeypatch):
    _public_dns(monkeypatch)
    r = member_client.post("/api/notification-channels",
                           json={"kind": "ntfy", "url": "https://ntfy.example.org/topic"})
    assert r.status_code == 201


def test_member_can_save_an_allowed_host(member_client, monkeypatch):
    monkeypatch.setattr(settings, "webhook_allowed_hosts", " ntfy.lan , 192.168.1.20 ")
    for url in ("http://ntfy.lan/topic", "http://192.168.1.20:8090/topic"):
        r = member_client.post("/api/notification-channels", json={"kind": "ntfy", "url": url})
        assert r.status_code == 201, url


def test_admin_can_save_a_local_address(client):
    r = client.post("/api/notification-channels",
                    json={"kind": "ntfy", "url": "http://192.168.1.20/topic"})
    assert r.status_code == 201


# ── send time ─────────────────────────────────────────────────────────────────

class _Hook(BaseHTTPRequestHandler):
    hits: list[str] = []
    redirect_to: str | None = None

    def do_POST(self):  # noqa: N802
        type(self).hits.append(self.path)
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if type(self).redirect_to and self.path == "/start":
            self.send_response(302)
            self.send_header("Location", type(self).redirect_to)
        else:
            self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *a):
        pass


@pytest.fixture()
def hook_server():
    _Hook.hits = []
    _Hook.redirect_to = None
    srv = HTTPServer(("127.0.0.1", 0), _Hook)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv.server_address[1]
    srv.shutdown()


PAYLOAD = {"kind": "test", "title": "T", "body": "B", "link": None}


def test_restricted_send_rechecks_the_address(hook_server):
    # A channel saved while its name resolved publicly, now pointing inside.
    with pytest.raises(UnsafeURLError):
        outbound.deliver("webhook", f"http://127.0.0.1:{hook_server}/x", None, PAYLOAD,
                         restricted=True)
    assert _Hook.hits == []


def test_admin_send_reaches_a_local_address(hook_server):
    outbound.deliver("webhook", f"http://127.0.0.1:{hook_server}/x", None, PAYLOAD)
    assert _Hook.hits == ["/x"]


def test_restricted_send_connects_to_the_checked_address(hook_server, monkeypatch):
    # The name is resolved once, by the check; the connection goes to that
    # address (here the local test server), never to a second lookup.
    monkeypatch.setattr(outbound, "validate_public_url",
                        lambda url: ("rebind.invalid", "127.0.0.1"))
    outbound.deliver("webhook", f"http://rebind.invalid:{hook_server}/pinned", None, PAYLOAD,
                     restricted=True)
    assert _Hook.hits == ["/pinned"]


def test_allowed_host_skips_the_check(hook_server, monkeypatch):
    monkeypatch.setattr(settings, "webhook_allowed_hosts", "127.0.0.1")
    outbound.deliver("webhook", f"http://127.0.0.1:{hook_server}/ok", None, PAYLOAD,
                     restricted=True)
    assert _Hook.hits == ["/ok"]


@pytest.mark.parametrize("restricted", [False, True])
def test_redirects_are_not_followed(hook_server, monkeypatch, restricted):
    monkeypatch.setattr(settings, "webhook_allowed_hosts", "127.0.0.1")
    _Hook.redirect_to = f"http://127.0.0.1:{hook_server}/internal"
    with pytest.raises(urllib.error.HTTPError) as e:
        outbound.deliver("webhook", f"http://127.0.0.1:{hook_server}/start", None, PAYLOAD,
                         restricted=restricted)
    assert e.value.code == 302
    assert _Hook.hits == ["/start"]


def test_fanout_restricts_non_admin_channels(client, db: Session, admin_user, member, monkeypatch):
    admin, _ = admin_user
    calls = []
    monkeypatch.setattr(outbound, "_spawn", lambda payloads: outbound._send_all(payloads))
    test_factory = sessionmaker(bind=db.get_bind())
    monkeypatch.setattr(outbound, "_session_factory", lambda: test_factory())
    monkeypatch.setattr(outbound, "deliver",
                        lambda kind, url, token, payload, *, restricted=False:
                        calls.append((url, restricted)))
    db.add(NotificationChannel(user_id=admin.id, kind="ntfy", url="http://a.lan/t", enabled=True))
    db.add(NotificationChannel(user_id=member.id, kind="ntfy", url="http://m.lan/t", enabled=True))
    db.commit()
    db.add(Notification(user_id=admin.id, kind="k", title="one"))
    db.add(Notification(user_id=member.id, kind="k", title="two"))
    db.commit()
    assert sorted(calls) == [("http://a.lan/t", False), ("http://m.lan/t", True)]


# ── the Test button ───────────────────────────────────────────────────────────

def test_member_test_button_hides_the_upstream_error(member_client, db: Session, member, monkeypatch):
    _public_dns(monkeypatch)
    ch = member_client.post("/api/notification-channels",
                            json={"kind": "webhook", "url": "https://hook.example.org/x"}).json()

    def boom(*a, **kw):
        raise urllib.error.URLError("[Errno 61] Connection refused on 10.0.0.5:22")

    monkeypatch.setattr(outbound, "deliver", boom)
    r = member_client.post(f"/api/notification-channels/{ch['id']}/test").json()
    assert r == {"ok": False, "error": "The notification could not be delivered."}


def test_admin_test_button_keeps_the_detail(client, monkeypatch):
    ch = client.post("/api/notification-channels",
                     json={"kind": "webhook", "url": "http://10.0.0.5/x"}).json()

    def boom(*a, **kw):
        raise urllib.error.URLError("Connection refused")

    monkeypatch.setattr(outbound, "deliver", boom)
    r = client.post(f"/api/notification-channels/{ch['id']}/test").json()
    assert r["ok"] is False
    assert "Connection refused" in r["error"]


# ── a channel saved before the rule ───────────────────────────────────────────

def _legacy_channel(db: Session, user: User, url: str) -> NotificationChannel:
    c = NotificationChannel(user_id=user.id, kind="ntfy", url=url, enabled=True)
    db.add(c)
    db.flush()
    return c


def test_member_list_marks_a_local_channel_as_blocked(member_client, db: Session, member, monkeypatch):
    _legacy_channel(db, member, "http://192.168.1.20:8080/tome")
    _public_dns(monkeypatch)
    member_client.post("/api/notification-channels",
                       json={"kind": "ntfy", "url": "https://ntfy.example.org/t"})
    monkeypatch.undo()
    monkeypatch.setattr(
        "backend.services.safe_fetch.socket.getaddrinfo",
        lambda host, port: [(0, 0, 0, "", ("192.168.1.20" if host[0].isdigit() else "93.184.216.34", 0))],
    )
    rows = member_client.get("/api/notification-channels").json()
    assert [(r["url"], r["blocked"]) for r in rows] == [
        ("http://192.168.1.20:8080/tome", True),
        ("https://ntfy.example.org/t", False),
    ]


def test_allowed_host_is_not_marked_blocked(member_client, db: Session, member, monkeypatch):
    _legacy_channel(db, member, "http://192.168.1.20:8080/tome")
    monkeypatch.setattr(settings, "webhook_allowed_hosts", "192.168.1.20")
    assert member_client.get("/api/notification-channels").json()[0]["blocked"] is False


def test_unresolvable_host_is_not_marked_blocked(member_client, db: Session, member, monkeypatch):
    _legacy_channel(db, member, "https://gone.example.org/t")

    def no_dns(host, port):
        raise OSError("no such host")

    monkeypatch.setattr("backend.services.safe_fetch.socket.getaddrinfo", no_dns)
    assert member_client.get("/api/notification-channels").json()[0]["blocked"] is False


def test_admin_list_never_marks_blocked(client, db: Session, admin_user):
    admin, token = admin_user
    _legacy_channel(db, admin, "http://192.168.1.20:8080/tome")
    rows = client.get("/api/notification-channels",
                      headers={"Authorization": f"Bearer {token}"}).json()
    assert rows[0]["blocked"] is False


def test_member_test_button_names_the_address_rule(member_client, db: Session, member):
    c = _legacy_channel(db, member, "http://192.168.1.20:8080/tome")
    r = member_client.post(f"/api/notification-channels/{c.id}/test").json()
    assert r == {"ok": False, "error": outbound.NOT_PUBLIC_DETAIL}


# ── the bell notice for a blocked channel ─────────────────────────────────────

def _inline_fanout(db: Session, monkeypatch):
    monkeypatch.setattr(outbound, "_spawn", lambda payloads: outbound._send_all(payloads))
    test_factory = sessionmaker(bind=db.get_bind())
    monkeypatch.setattr(outbound, "_session_factory", lambda: test_factory())


def _blocked_notices(db: Session, user: User) -> list[Notification]:
    db.expire_all()
    return (db.query(Notification)
            .filter_by(user_id=user.id, kind=outbound.CHANNEL_BLOCKED_KIND).all())


def test_blocked_send_leaves_one_bell_notice(client, db: Session, member, monkeypatch):
    _inline_fanout(db, monkeypatch)
    db.add(NotificationChannel(user_id=member.id, kind="ntfy",
                               url="http://192.168.1.20:8080/tome", enabled=True))
    db.commit()
    db.add(Notification(user_id=member.id, kind="k", title="one"))
    db.commit()
    db.add(Notification(user_id=member.id, kind="k", title="two"))
    db.commit()
    notices = _blocked_notices(db, member)
    assert len(notices) == 1
    assert notices[0].title == "Your ntfy channel is no longer sent"
    assert "192.168.1.20" in notices[0].body
    assert notices[0].link == "/settings#notifications"


def test_bell_notice_is_not_pushed_to_channels(client, db: Session, member, monkeypatch):
    _inline_fanout(db, monkeypatch)
    sent = []
    monkeypatch.setattr(outbound, "deliver",
                        lambda kind, url, token, payload, *, restricted=False:
                        sent.append(payload["kind"]))
    _public_dns(monkeypatch)
    db.add(NotificationChannel(user_id=member.id, kind="ntfy",
                               url="https://ntfy.example.org/t", enabled=True))
    db.commit()
    db.add(Notification(user_id=member.id, kind=outbound.CHANNEL_BLOCKED_KIND, title="x"))
    db.add(Notification(user_id=member.id, kind="k", title="one"))
    db.commit()
    assert sent == ["k"]


def test_other_failures_leave_no_bell_notice(client, db: Session, member, monkeypatch):
    _inline_fanout(db, monkeypatch)
    _public_dns(monkeypatch)

    def down(*a, **kw):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(outbound, "deliver", down)
    db.add(NotificationChannel(user_id=member.id, kind="ntfy",
                               url="https://ntfy.example.org/t", enabled=True))
    db.commit()
    db.add(Notification(user_id=member.id, kind="k", title="one"))
    db.commit()
    assert _blocked_notices(db, member) == []


def test_admin_channel_never_gets_the_notice(client, db: Session, admin_user, monkeypatch):
    admin, _ = admin_user
    _inline_fanout(db, monkeypatch)

    def down(*a, **kw):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(outbound, "deliver", down)
    db.add(NotificationChannel(user_id=admin.id, kind="ntfy",
                               url="http://192.168.1.20:8080/tome", enabled=True))
    db.commit()
    db.add(Notification(user_id=admin.id, kind="k", title="one"))
    db.commit()
    assert _blocked_notices(db, admin) == []
