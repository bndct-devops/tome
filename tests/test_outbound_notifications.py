"""Outbound notification fanout (ntfy/Gotify/webhook) + channel CRUD."""
import json

from sqlalchemy.orm import Session
from starlette.testclient import TestClient

import backend.services.outbound_notifications as outbound
from backend.models.notification import Notification, NotificationChannel


def test_channel_crud_and_token_hidden(client: TestClient):
    r = client.post("/api/notification-channels",
                    json={"kind": "ntfy", "url": "https://ntfy.sh/my-topic", "token": "secret"})
    assert r.status_code == 201
    ch = r.json()
    assert ch["has_token"] is True
    assert "secret" not in json.dumps(ch)

    rows = client.get("/api/notification-channels").json()
    assert len(rows) == 1

    assert client.post(f"/api/notification-channels/{ch['id']}/toggle").json()["enabled"] is False
    assert client.delete(f"/api/notification-channels/{ch['id']}").json()["ok"] is True
    assert client.get("/api/notification-channels").json() == []


def test_channel_rejects_bad_input(client: TestClient):
    assert client.post("/api/notification-channels",
                       json={"kind": "carrier-pigeon", "url": "https://x"}).status_code == 422
    assert client.post("/api/notification-channels",
                       json={"kind": "ntfy", "url": "ftp://x"}).status_code == 422


def test_fanout_on_commit(client: TestClient, db: Session, admin_user, monkeypatch):
    user, _ = admin_user
    sent: list[tuple] = []
    monkeypatch.setattr(outbound, "_spawn", lambda payloads: outbound._send_all(payloads))
    # The worker normally opens its own app-level session; give it a fresh
    # session on the test engine instead (same rule as production: never the
    # session that is mid-commit).
    from sqlalchemy.orm import sessionmaker
    test_factory = sessionmaker(bind=db.get_bind())
    monkeypatch.setattr(outbound, "_session_factory", lambda: test_factory())
    monkeypatch.setattr(outbound, "deliver",
                        lambda kind, url, token, payload: sent.append((kind, url, payload["title"])))

    db.add(NotificationChannel(user_id=user.id, kind="ntfy",
                               url="https://ntfy.sh/topic", enabled=True))
    db.add(NotificationChannel(user_id=user.id, kind="webhook",
                               url="https://hook.example", enabled=False))  # disabled: skipped
    db.commit()

    db.add(Notification(user_id=user.id, kind="wish_fulfilled",
                        title="Your wish arrived", body="Vol 12 is in the library"))
    db.commit()

    assert sent == [("ntfy", "https://ntfy.sh/topic", "Your wish arrived")]


def test_fanout_respects_kill_switch(client: TestClient, db: Session, admin_user, monkeypatch):
    from backend.core.config import settings

    user, _ = admin_user
    called = []
    monkeypatch.setattr(outbound, "_spawn", lambda payloads: called.append(payloads))
    monkeypatch.setattr(settings, "outbound_notify", False)

    db.add(Notification(user_id=user.id, kind="test", title="quiet"))
    db.commit()
    assert called == []


def test_deliver_builds_correct_requests(monkeypatch):
    captured = []

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        captured.append(req)
        return FakeResp()

    monkeypatch.setattr(outbound.urllib.request, "urlopen", fake_urlopen)

    outbound.deliver("ntfy", "https://ntfy.sh/topic", "tok",
                     {"kind": "k", "title": "Title", "body": "Body", "link": "/books/1"})
    outbound.deliver("gotify", "https://gotify.example", "apptok",
                     {"kind": "k", "title": "Title", "body": "Body", "link": None})
    outbound.deliver("webhook", "https://hook.example/x", None,
                     {"kind": "wish_fulfilled", "title": "T", "body": "B", "link": "/wishlist"})

    ntfy, gotify, hook = captured
    assert ntfy.get_header("Title") == "Title"
    assert ntfy.get_header("Authorization") == "Bearer tok"
    assert ntfy.data == b"Body"
    assert "/message?token=apptok" in gotify.full_url
    assert json.loads(gotify.data)["title"] == "Title"
    body = json.loads(hook.data)
    assert body["event"] == "wish_fulfilled"
    assert body["link"] == "/wishlist"  # no public_url configured -> relative
