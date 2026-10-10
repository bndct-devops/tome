"""AI provider plumbing: status, keys, instance settings, key resolution,
the usage ledger and error mapping. The provider is always the FakeProvider."""
import json
from datetime import datetime

import pytest
from fastapi import Depends
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from backend.core.config import settings
from backend.core.database import get_db
from backend.core.security import create_access_token, get_current_user, hash_password
from backend.models.ai_usage import AIUsage
from backend.models.audit_log import AuditLog
from backend.models.user import User
from backend.services import ai
from backend.services.ai import settings as ai_settings, usage
from backend.services.ai.provider import AIProviderError
from tests.ai_fake import FakeProvider

GOOD_KEY = "sk-ant-test-0000000000000000k3Fq"
OTHER_KEY = "sk-ant-test-1111111111111111Zz99"


def _make_user(db: Session, username: str, role: str) -> tuple[User, dict]:
    user = User(username=username, email=f"{username}@example.com",
                hashed_password=hash_password("pw123456"), is_active=True,
                is_admin=(role == "admin"), role=role, must_change_password=False)
    db.add(user)
    db.flush()
    return user, {"Authorization": f"Bearer {create_access_token(subject=user.id)}"}


@pytest.fixture()
def fake() -> FakeProvider:
    f = FakeProvider()
    ai.set_provider_override(f)
    return f


@pytest.fixture()
def member(db: Session):
    return _make_user(db, "aimember", "member")


@pytest.fixture()
def guest(db: Session):
    return _make_user(db, "aiguest", "guest")


def _audit_rows(db: Session, action: str) -> list[AuditLog]:
    return db.query(AuditLog).filter(AuditLog.action == action).all()


# ── status ───────────────────────────────────────────────────────────────────

def test_status_guest_sees_no_key_and_no_admin_fields(client: TestClient, guest):
    _, h = guest
    r = client.get("/api/ai/status", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["enabled"] is True
    assert body["provider"] == "anthropic"
    assert body["can_use"] is False
    assert body["key_source"] is None
    assert body["instance_key_available"] is False
    assert "instance_key_set" not in body
    assert set(body["features"]) == {"bindery_identify", "fix_book", "series_cleanup"}
    f = body["features"]["fix_book"]
    assert f["default_model"] == "claude-opus-5-5" and f["model"] is None and f["enabled"] is True


def test_status_member_without_key(client: TestClient, member):
    _, h = member
    body = client.get("/api/ai/status", headers=h).json()
    assert body["can_use"] is True
    assert body["has_own_key"] is False and body["own_key_suffix"] is None
    assert body["key_source"] is None
    assert "share_instance_key" not in body


def test_status_admin_has_instance_fields(client: TestClient):
    body = client.get("/api/ai/status").json()
    for field in ("instance_enabled", "instance_key_set", "instance_key_source", "share_instance_key"):
        assert field in body
    assert body["instance_key_set"] is False and body["instance_key_source"] is None
    assert body["confidence_threshold"] == pytest.approx(0.85)


def test_guest_gets_403_everywhere_but_status(client: TestClient, guest, fake):
    _, h = guest
    assert client.put("/api/ai/key", json={"api_key": GOOD_KEY}, headers=h).status_code == 403
    assert client.delete("/api/ai/key", headers=h).status_code == 403
    assert client.get("/api/ai/usage", headers=h).status_code == 403
    assert client.put("/api/ai/instance", json={"enabled": False}, headers=h).status_code == 403
    assert fake.validate_calls == 0


def test_env_switch_hides_everything(client: TestClient, monkeypatch, fake):
    monkeypatch.setattr(settings, "ai_enabled", False)
    body = client.get("/api/ai/status").json()
    assert body["enabled"] is False and body["env_enabled"] is False
    assert client.put("/api/ai/key", json={"api_key": GOOD_KEY}).status_code == 404
    assert client.get("/api/ai/usage").status_code == 404


# ── own key ──────────────────────────────────────────────────────────────────

def test_set_key_validates_encrypts_masks_and_audits(client: TestClient, member, db: Session, fake):
    user, h = member
    r = client.put("/api/ai/key", json={"api_key": f"  {GOOD_KEY}  "}, headers=h)
    assert r.status_code == 200, r.text
    assert GOOD_KEY not in r.text
    body = r.json()
    assert body["has_own_key"] is True
    assert body["own_key_suffix"] == "...k3Fq"
    assert body["key_source"] == "user"
    assert fake.validate_calls == 1

    db.refresh(user)
    assert user.ai_api_key and user.ai_api_key != GOOD_KEY
    assert ai_settings.get_user_key(user) == GOOD_KEY
    assert user.ai_key_set_at is not None

    rows = _audit_rows(db, "ai.key_set")
    assert len(rows) == 1 and rows[0].user_id == user.id
    assert GOOD_KEY not in (rows[0].details or "") and "k3Fq" not in (rows[0].details or "")


def test_bad_key_is_400_and_not_stored(client: TestClient, member, db: Session, fake):
    user, h = member
    fake.valid = False
    r = client.put("/api/ai/key", json={"api_key": GOOD_KEY}, headers=h)
    assert r.status_code == 400
    assert "rejected" in r.json()["detail"]
    db.refresh(user)
    assert user.ai_api_key is None
    assert _audit_rows(db, "ai.key_set") == []


def test_provider_down_during_validation_is_502(client: TestClient, member, fake):
    _, h = member
    fake.validate_key = lambda: (_ for _ in ()).throw(AIProviderError("Could not reach Anthropic."))  # type: ignore[method-assign]
    r = client.put("/api/ai/key", json={"api_key": GOOD_KEY}, headers=h)
    assert r.status_code == 502


def test_remove_key(client: TestClient, member, db: Session, fake):
    user, h = member
    client.put("/api/ai/key", json={"api_key": GOOD_KEY}, headers=h)
    r = client.delete("/api/ai/key", headers=h)
    assert r.status_code == 200
    assert r.json()["has_own_key"] is False
    db.refresh(user)
    assert user.ai_api_key is None and user.ai_key_set_at is None
    assert len(_audit_rows(db, "ai.key_removed")) == 1


# ── instance settings ────────────────────────────────────────────────────────

def test_instance_update_writes_and_audits_field_names_only(client: TestClient, db: Session, fake):
    r = client.put("/api/ai/instance", json={
        "instance_key": OTHER_KEY,
        "share_instance_key": True,
        "confidence_threshold": 0.9,
        "features": {"fix_book": {"enabled": False, "model": "claude-sonnet-5-5"}},
    })
    assert r.status_code == 200, r.text
    assert OTHER_KEY not in r.text
    body = r.json()
    assert body["instance_key_set"] is True and body["instance_key_source"] == "db"
    assert body["instance_key_suffix"] == "...Zz99"
    assert body["share_instance_key"] is True
    assert body["confidence_threshold"] == pytest.approx(0.9)
    assert body["features"]["fix_book"] == {**body["features"]["fix_book"], "enabled": False,
                                            "model": "claude-sonnet-5-5"}
    assert ai_settings.effective_model(db, "fix_book") == "claude-sonnet-5-5"
    assert fake.validate_calls == 1

    rows = _audit_rows(db, "ai.instance_settings_changed")
    assert len(rows) == 1
    details = json.loads(rows[0].details)
    assert set(details["fields"]) == {"instance_key", "share_instance_key", "confidence_threshold",
                                      "features.fix_book.enabled", "features.fix_book.model"}
    assert OTHER_KEY not in rows[0].details and "Zz99" not in rows[0].details

    # Reset the model to the default and clear the key.
    r = client.put("/api/ai/instance", json={"clear_instance_key": True,
                                             "features": {"fix_book": {"model": "default"}}})
    assert r.status_code == 200
    assert r.json()["instance_key_set"] is False
    assert ai_settings.effective_model(db, "fix_book") == "claude-opus-5-5"
    rows = _audit_rows(db, "ai.instance_settings_changed")
    assert set(json.loads(rows[-1].details)["fields"]) == {"instance_key_cleared", "features.fix_book.model"}


def test_instance_update_no_change_writes_no_audit(client: TestClient, db: Session, fake):
    r = client.put("/api/ai/instance", json={"enabled": True, "share_instance_key": False})
    assert r.status_code == 200
    assert _audit_rows(db, "ai.instance_settings_changed") == []


def test_instance_update_validation(client: TestClient, member, fake):
    assert client.put("/api/ai/instance", json={"features": {"nope": {"enabled": False}}}).status_code == 422
    assert client.put("/api/ai/instance", json={"features": {"fix_book": {"model": "gpt-x"}}}).status_code == 422
    assert client.put("/api/ai/instance", json={"confidence_threshold": 1.5}).status_code == 422
    fake.valid = False
    assert client.put("/api/ai/instance", json={"instance_key": OTHER_KEY}).status_code == 400
    _, h = member
    assert client.put("/api/ai/instance", json={"enabled": False}, headers=h).status_code == 403


# ── key resolution ───────────────────────────────────────────────────────────

def test_key_resolution_order(client: TestClient, db: Session, admin_user, member, guest, monkeypatch):
    admin, _ = admin_user
    mem, _ = member
    gst, _ = guest

    # Nothing anywhere: not configured.
    assert ai_settings.resolve_key(db, admin) is None
    assert ai_settings.resolve_key(db, mem) is None

    # Env key: admin gets it as the instance key; a member does not (not shared).
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-env-key-aaaa")
    r = ai_settings.resolve_key(db, admin)
    assert r and r.source == "instance" and r.api_key == "sk-env-key-aaaa"
    assert ai_settings.get_instance_key(db) == ("sk-env-key-aaaa", "env")
    assert ai_settings.resolve_key(db, mem) is None

    # A stored instance key wins over the env key.
    ai_settings.set_instance_key(db, OTHER_KEY)
    assert ai_settings.get_instance_key(db) == (OTHER_KEY, "db")
    assert ai_settings.resolve_key(db, admin).api_key == OTHER_KEY

    # Shared: members fall back to it too. Guests never resolve.
    ai_settings.set_share_instance_key(db, True)
    r = ai_settings.resolve_key(db, mem)
    assert r and r.source == "instance" and r.api_key == OTHER_KEY
    assert ai_settings.resolve_key(db, gst) is None

    # The user's own key always comes first.
    ai_settings.set_user_key(db, mem, GOOD_KEY)
    r = ai_settings.resolve_key(db, mem)
    assert r and r.source == "user" and r.api_key == GOOD_KEY

    # The guest with a stored key still resolves nothing.
    ai_settings.set_user_key(db, gst, GOOD_KEY)
    assert ai_settings.resolve_key(db, gst) is None


def test_status_reports_env_instance_key(client: TestClient, member, db: Session, monkeypatch):
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-env-key-bbbb")
    body = client.get("/api/ai/status").json()
    assert body["instance_key_source"] == "env" and body["key_source"] == "instance"
    assert "sk-env-key-bbbb" not in json.dumps(body)
    _, h = member
    body = client.get("/api/ai/status", headers=h).json()
    assert body["instance_key_available"] is False and body["key_source"] is None
    ai_settings.set_share_instance_key(db, True)
    body = client.get("/api/ai/status", headers=h).json()
    assert body["instance_key_available"] is True and body["key_source"] == "instance"


# ── run_feature, usage ledger, error mapping ─────────────────────────────────

SCHEMA = {"type": "object", "properties": {"greeting": {"type": "string"}},
          "required": ["greeting"], "additionalProperties": False}


def test_run_feature_records_usage_with_cost(db: Session, member, fake):
    user, _ = member
    ai_settings.set_user_key(db, user, GOOD_KEY)
    fake.responses = [{"greeting": "hello"}]
    fake.input_tokens, fake.output_tokens = 2000, 500
    result = ai.run_feature(db, user, "fix_book", system="Say hello.", user_content="hi", schema=SCHEMA)
    assert result.parsed == {"greeting": "hello"}
    assert fake.calls[0]["model"] == "claude-opus-5-5" and fake.calls[0]["effort"] == "high"

    row = db.query(AIUsage).one()
    assert row.user_id == user.id and row.feature == "fix_book" and row.key_source == "user"
    assert row.model == "claude-opus-5-5"
    # 2000 * 4.00/M + 500 * 20.00/M = 0.008 + 0.010
    assert row.cost_usd == pytest.approx(0.018)


def test_run_feature_uses_model_override(db: Session, admin_user, fake):
    admin, _ = admin_user
    ai_settings.set_instance_key(db, OTHER_KEY)
    ai_settings.set_feature_model(db, "series_cleanup", "claude-haiku-5-5")
    ai.run_feature(db, admin, "series_cleanup", system="s", user_content="u")
    assert fake.calls[0]["model"] == "claude-haiku-5-5"
    assert db.query(AIUsage).one().key_source == "instance"


def test_run_feature_not_configured_and_disabled(db: Session, member, fake):
    user, _ = member
    with pytest.raises(ai.AINotConfigured):
        ai.run_feature(db, user, "fix_book", system="s", user_content="u")
    ai_settings.set_user_key(db, user, GOOD_KEY)
    ai_settings.set_feature_enabled(db, "fix_book", False)
    with pytest.raises(ai.AIDisabled):
        ai.run_feature(db, user, "fix_book", system="s", user_content="u")
    ai_settings.set_feature_enabled(db, "fix_book", True)
    ai_settings.set_instance_enabled(db, False)
    with pytest.raises(ai.AIDisabled):
        ai.run_feature(db, user, "fix_book", system="s", user_content="u")
    assert fake.calls == []


def test_cost_table():
    assert usage.cost_usd("claude-haiku-5-5", 1_000_000, 1_000_000) == pytest.approx(0.60)
    assert usage.cost_usd("claude-sonnet-5-5", 0, 0, 1_000_000, 1_000_000) == pytest.approx(2.70)
    # Unknown ids are priced by family, never as free.
    assert usage.cost_usd("claude-opus-9", 1_000_000, 0) == pytest.approx(4.00)


def test_month_summary_endpoint(client: TestClient, db: Session, admin_user, member):
    admin, _ = admin_user
    mem, h = member

    def add(user_id, feature, cost, when):
        db.add(AIUsage(user_id=user_id, feature=feature, model="claude-opus-5-5", key_source="user",
                       input_tokens=100, output_tokens=10, cost_usd=cost, created_at=when))

    add(mem.id, "fix_book", 0.02, datetime(2026, 10, 3))
    add(mem.id, "fix_book", 0.03, datetime(2026, 10, 9))
    add(mem.id, "series_cleanup", 0.10, datetime(2026, 10, 5))
    add(mem.id, "fix_book", 9.99, datetime(2026, 9, 30, 23, 59))  # previous month
    add(admin.id, "bindery_identify", 0.50, datetime(2026, 10, 1))
    db.flush()

    r = client.get("/api/ai/usage?month=2026-10", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["month"] == "2026-10" and body["scope"] == "self"
    assert body["total"]["calls"] == 3
    assert body["total"]["cost_usd"] == pytest.approx(0.15)
    by_f = {x["feature"]: x for x in body["by_feature"]}
    assert by_f["fix_book"]["calls"] == 2 and by_f["fix_book"]["cost_usd"] == pytest.approx(0.05)
    assert by_f["series_cleanup"]["label"] == "Clean up this series"
    assert "by_user" not in body

    assert client.get("/api/ai/usage?month=2026-10&all=true", headers=h).status_code == 403
    assert client.get("/api/ai/usage?month=October", headers=h).status_code == 422

    body = client.get("/api/ai/usage?month=2026-10&all=true").json()
    assert body["scope"] == "all"
    assert body["total"]["cost_usd"] == pytest.approx(0.65)
    by_u = {x["username"]: x for x in body["by_user"]}
    assert by_u["testadmin"]["cost_usd"] == pytest.approx(0.50)
    assert by_u["aimember"]["calls"] == 3


@pytest.fixture()
def run_route(client: TestClient):
    """A throwaway endpoint that calls run_feature, so the app-level AIError
    handler is exercised exactly as phase-2 feature endpoints will be."""
    def _run(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
        r = ai.run_feature(db, user, "fix_book", system="s", user_content="u", schema=SCHEMA)
        return r.parsed

    client.app.add_api_route("/api/ai/_test_run", _run, methods=["POST"])
    return "/api/ai/_test_run"


def test_refusal_maps_to_422_and_is_still_metered(client: TestClient, db: Session, run_route, fake):
    ai_settings.set_instance_key(db, OTHER_KEY)
    fake.refuse = True
    r = client.post(run_route)
    assert r.status_code == 422
    assert r.json()["detail"] == "The model declined this request."
    assert db.query(AIUsage).count() == 1


def test_error_mapping(client: TestClient, db: Session, run_route, fake):
    r = client.post(run_route)
    assert r.status_code == 409  # no key anywhere
    ai_settings.set_instance_key(db, OTHER_KEY)
    fake.error = AIProviderError("Could not reach Anthropic.")
    r = client.post(run_route)
    assert r.status_code == 502 and r.json()["detail"] == "Could not reach Anthropic."
    fake.error = None
    ai_settings.set_feature_enabled(db, "fix_book", False)
    assert client.post(run_route).status_code == 404
    ai_settings.set_feature_enabled(db, "fix_book", True)
    fake.responses = [{"greeting": "hi"}]
    r = client.post(run_route)
    assert r.status_code == 200 and r.json() == {"greeting": "hi"}
