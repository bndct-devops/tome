"""Opt-in telemetry: the report carries nothing identifying, and nothing may be
sent without an explicit, current grant."""
import json
from datetime import datetime

from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from backend.core.security import create_access_token, hash_password
from backend.models.user import User, UserPermission
from backend.models.tome_sync import ReadingSession
from backend.services import telemetry


def test_report_has_no_titles_names_or_hosts(client: TestClient, make_book, admin_user, db: Session):
    user, _ = admin_user
    book = make_book(title="A Very Identifiable Title", author="Unique Authorname")
    db.add(ReadingSession(user_id=user.id, book_id=book.id, started_at=datetime.utcnow(),
                          ended_at=datetime.utcnow(), duration_seconds=600, pages_turned=12, device="My Kindle"))
    db.flush()

    r = client.get("/api/admin/telemetry")
    assert r.status_code == 200, r.text
    body = json.dumps(r.json()["report"])
    for forbidden in (book.title, "Unique Authorname", user.username, "My Kindle"):
        assert forbidden not in body
    assert "@" not in body
    report = r.json()["report"]
    assert report["schema"] == telemetry.REPORT_SCHEMA
    assert set(report) == {
        "schema", "instance", "version", "plugin_build", "platform", "docker", "install_age",
        "users", "readers_30d", "books", "libraries", "book_types", "formats", "languages",
        "reading_30d", "highlights", "features_30d",
    }
    assert report["features_30d"]["koreader_sync"] is True
    assert report["readers_30d"] == "1"


def test_counts_are_buckets():
    assert telemetry.bucket(0) == "0"
    assert telemetry.bucket(1) == "1"
    assert telemetry.bucket(2) == "2"
    assert telemetry.bucket(7) == "6-10"
    assert telemetry.bucket(1234) == "1001-2500"
    assert telemetry.bucket(99999) == "10000+"


def test_nothing_may_be_sent_without_a_grant(client: TestClient, db: Session):
    assert telemetry.consent_state(db)["state"] == "unset"
    assert telemetry.may_send(db) is False

    r = client.post("/api/admin/telemetry/consent", json={"decision": "declined"})
    assert r.status_code == 200 and r.json()["consent"]["state"] == "declined"
    assert telemetry.may_send(db) is False

    r = client.post("/api/admin/telemetry/consent", json={"decision": "granted"})
    assert r.status_code == 200 and r.json()["consent"]["state"] == "granted"
    assert telemetry.may_send(db) is True

    # The report changed shape after consent: sending pauses.
    telemetry._set(db, telemetry.KEY_CONSENT_SCHEMA, "0")
    db.commit()
    assert telemetry.consent_state(db)["stale"] is True
    assert telemetry.may_send(db) is False


def test_env_var_pins_it_off(client: TestClient, db: Session, monkeypatch):
    monkeypatch.setattr(telemetry.settings, "telemetry", False)
    assert telemetry.consent_state(db)["state"] == "env_off"
    assert client.post("/api/admin/telemetry/consent", json={"decision": "granted"}).status_code == 409
    assert telemetry.may_send(db) is False


def test_instance_id_is_stable_and_random(client: TestClient, db: Session):
    a = client.get("/api/admin/telemetry").json()["report"]["instance"]
    b = client.get("/api/admin/telemetry").json()["report"]["instance"]
    assert a == b and len(a) == 36


def test_consent_is_admin_only(client: TestClient, db: Session):
    member = User(username="tele_member", email="tele_member@example.com",
                  hashed_password=hash_password("pass1234"), is_active=True,
                  is_admin=False, role="member", must_change_password=False)
    db.add(member)
    db.flush()
    db.add(UserPermission(user_id=member.id))
    db.flush()
    token = create_access_token(subject=member.id)
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/admin/telemetry", headers=headers).status_code == 403
    assert client.post("/api/admin/telemetry/consent", json={"decision": "granted"}, headers=headers).status_code == 403


def test_sender_respects_consent_and_due_date(client: TestClient, db: Session, monkeypatch):
    sent: list[bytes] = []

    class _Resp:
        status = 204
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(req, timeout=0):
        sent.append(req.data)
        return _Resp()

    monkeypatch.setattr(telemetry.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(telemetry.settings, "telemetry_url", "http://pulse.test/v1/report")

    assert telemetry.send_if_due(db) == "skipped" and sent == []
    telemetry.record_consent(db, "declined")
    assert telemetry.send_if_due(db) == "skipped" and sent == []

    telemetry.record_consent(db, "granted")
    assert telemetry.send_if_due(db) == "sent"
    assert len(sent) == 1
    body = json.loads(sent[0])
    assert body["schema"] == telemetry.REPORT_SCHEMA and "instance" in body
    assert telemetry.send_if_due(db) == "not_due"          # a month has not passed
    state = telemetry.consent_state(db)
    assert state["last_sent"] and state["next_due"] > state["last_sent"]

    telemetry.record_consent(db, "declined")
    assert telemetry.send_if_due(db, force=True) == "skipped" and len(sent) == 1


def test_sender_failure_keeps_the_due_date(client: TestClient, db: Session, monkeypatch):
    def boom(req, timeout=0):
        raise OSError("no route")
    monkeypatch.setattr(telemetry.urllib.request, "urlopen", boom)
    telemetry.record_consent(db, "granted")
    assert telemetry.send_if_due(db) == "failed"
    assert telemetry.consent_state(db)["last_sent"] is None
