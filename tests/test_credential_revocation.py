"""A password change can revoke the other credentials; API tokens cannot mint
new credentials; impersonation tokens are short-lived."""
import base64
import hashlib
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.core.security import decode_claims
from backend.models.api_token import ApiToken
from backend.models.audit_log import AuditLog
from backend.models.kosync import KOSyncUser
from backend.models.opds_pin import OpdsPin
from backend.models.tome_sync import ApiKey

PW = "adminpass123"


def _basic(user: str, pw: str) -> dict:
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()}


def _setup_credentials(client: TestClient) -> dict:
    api_token = client.post("/api/tokens/", json={"name": "Script"}).json()["token"]
    plugin_key = client.post("/api/plugin/api-keys", json={"label": "Kindle"}).json()["key"]
    pin = client.post("/api/opds-pins", json={"label": "Kobo"}).json()["pin"]
    assert client.post("/api/auth/me/kosync", json={"password": "syncpass"}).status_code == 201
    return {"api_token": api_token, "plugin_key": plugin_key, "pin": pin,
            "kosync_key": hashlib.md5(b"syncpass").hexdigest()}


def _works(client: TestClient, creds: dict) -> dict:
    saved = client.headers.pop("Authorization")
    try:
        return {
            "api_token": client.get("/api/auth/me", headers={"Authorization": f"Bearer {creds['api_token']}"}).status_code == 200,
            "plugin_key": client.get("/api/plugin/main-impl.lua", headers={"Authorization": f"Bearer {creds['plugin_key']}"}).status_code == 200,
            "pin": client.get("/opds/", headers=_basic("testadmin", creds["pin"])).status_code == 200,
            "kosync": client.get("/api/v1/users/auth", headers={"x-auth-user": "testadmin", "x-auth-key": creds["kosync_key"]}).status_code == 200,
        }
    finally:
        client.headers["Authorization"] = saved


def test_password_change_keeps_credentials_by_default(client: TestClient):
    creds = _setup_credentials(client)
    r = client.put("/api/auth/me/password", json={"current_password": PW, "new_password": "brandnewpass1"})
    assert r.status_code == 204
    assert all(_works(client, creds).values())


def test_password_change_can_revoke_every_credential(client: TestClient, db: Session, admin_user):
    user, _ = admin_user
    creds = _setup_credentials(client)
    assert all(_works(client, creds).values())
    r = client.put("/api/auth/me/password", json={
        "current_password": PW, "new_password": "brandnewpass1", "revoke_credentials": True,
    })
    assert r.status_code == 204
    assert _works(client, creds) == {"api_token": False, "plugin_key": False, "pin": False, "kosync": False}

    assert db.query(ApiToken).filter(ApiToken.user_id == user.id, ApiToken.revoked_at.is_(None)).count() == 0
    assert db.query(ApiKey).filter(ApiKey.user_id == user.id).count() == 0
    assert db.query(OpdsPin).filter(OpdsPin.user_id == user.id).count() == 0
    assert db.query(KOSyncUser).filter(KOSyncUser.user_id == user.id).one().userkey == ""

    entries = db.query(AuditLog).filter(AuditLog.action == "auth.password_changed").all()
    assert len(entries) == 1
    details = entries[0].details if isinstance(entries[0].details, dict) else __import__("json").loads(entries[0].details)
    assert details["api_tokens_revoked"] == 1
    assert details["plugin_keys_revoked"] == 1
    assert details["opds_pins_revoked"] == 1
    assert details["kosync_keys_revoked"] == 1

    # The KOSync link can be set up again from Settings.
    assert client.post("/api/auth/me/kosync", json={"password": "again"}).status_code == 201


def test_api_token_cannot_mint_credentials(client: TestClient):
    token = client.post("/api/tokens/", json={"name": "Script"}).json()["token"]
    bearer = {"Authorization": f"Bearer {token}"}
    calls = [
        ("post", "/api/tokens/", {"name": "Another"}),
        ("post", "/api/plugin/api-keys", {"label": "x"}),
        ("get", "/api/plugin/koreader", None),
        ("post", "/api/opds-pins", {"label": "x"}),
        ("post", "/api/auth/quick-connect/issue", None),
        ("post", "/api/auth/quick-connect/authorize", {"code": "ABCDEF"}),
        ("post", "/api/auth/me/kosync", {"password": "x"}),
    ]
    for method, path, body in calls:
        kwargs = {"headers": bearer}
        if body is not None:
            kwargs["json"] = body
        r = getattr(client, method)(path, **kwargs)
        assert r.status_code == 403, (path, r.status_code, r.text)
        assert "API tokens cannot" in r.json()["detail"], path
    # The same token still reads and writes ordinary data.
    assert client.get("/api/books", headers=bearer).status_code == 200


def test_login_session_still_mints_and_plugin_self_update_still_works(client: TestClient):
    # The client fixture is signed in with a login token.
    assert client.get("/api/plugin/koreader").status_code == 200
    key = client.post("/api/plugin/api-keys", json={"label": "Kindle"}).json()["key"]
    r = client.get("/api/plugin/main-impl.lua", headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 200
    assert client.post("/api/auth/quick-connect/issue").status_code == 200


def test_impersonation_token_lasts_one_hour(client: TestClient, db: Session):
    from backend.core.security import hash_password
    from backend.models.user import User
    target = User(username="someone", email="someone@example.com",
                  hashed_password=hash_password("whatever1"), is_active=True,
                  is_admin=False, role="member", must_change_password=False)
    db.add(target)
    db.flush()
    r = client.post(f"/api/users/{target.id}/impersonate")
    assert r.status_code == 200
    claims = decode_claims(r.json()["access_token"])
    left = claims["exp"] - datetime.now(timezone.utc).timestamp()
    assert 55 * 60 < left <= 60 * 60
