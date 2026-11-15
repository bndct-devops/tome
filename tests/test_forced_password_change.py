"""An account with a temporary password can only change it.

Until the password is changed the API answers GET /auth/me, PUT
/auth/me/password and POST /auth/logout, and refuses everything else through
every way of signing in.
"""
import base64

import pytest
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from backend.core.database import get_db
from backend.core.security import create_access_token, hash_password
from backend.models.user import User

TEMP = "temporary-pass1"


@pytest.fixture()
def app_client(db: Session):
    from backend.main import create_app

    app = create_app()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def fresh(db: Session):
    u = User(username="newreader", email="newreader@example.org",
             hashed_password=hash_password(TEMP), is_active=True,
             is_admin=False, role="member", must_change_password=True)
    db.add(u)
    db.flush()
    return u


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_login_works_and_me_answers(app_client, fresh):
    r = app_client.post("/api/auth/login", json={"username": "newreader", "password": TEMP})
    assert r.status_code == 200
    tok = r.json()["access_token"]
    me = app_client.get("/api/auth/me", headers=_auth(tok))
    assert me.status_code == 200
    assert me.json()["must_change_password"] is True


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/books"),
    ("GET", "/api/libraries"),
    ("PUT", "/api/auth/me"),
    ("GET", "/api/auth/me/kosync"),
    ("POST", "/api/tokens"),
    ("GET", "/api/notifications"),
])
def test_everything_else_is_refused(app_client, fresh, method, path):
    r = app_client.request(method, path, headers=_auth(create_access_token(fresh.id)), json={})
    assert r.status_code == 403
    assert "temporary password" in r.json()["detail"]


def test_api_token_is_refused(app_client, db, fresh):
    import hashlib
    from backend.models.api_token import ApiToken
    plain = "tome_forcedchangetesttoken"
    db.add(ApiToken(user_id=fresh.id, name="t", prefix=plain[:8],
                    token_hash=hashlib.sha256(plain.encode()).hexdigest()))
    db.flush()
    assert app_client.get("/api/books", headers=_auth(plain)).status_code == 403
    assert app_client.get("/api/auth/me", headers=_auth(plain)).status_code == 200


def test_query_token_reader_route_is_refused(app_client, fresh):
    tok = create_access_token(fresh.id)
    r = app_client.get(f"/api/books/1/pages/0?token={tok}")
    assert r.status_code == 403


def test_opds_is_refused(app_client, fresh):
    basic = base64.b64encode(f"newreader:{TEMP}".encode()).decode()
    r = app_client.get("/opds/", headers={"Authorization": f"Basic {basic}"})
    assert r.status_code == 403


def test_plugin_key_is_refused(app_client, db, fresh):
    from backend.models.tome_sync import ApiKey
    plain = "plugin-key-for-forced-change"
    db.add(ApiKey(user_id=fresh.id, key_hash=ApiKey.hash_key(plain), label="kindle"))
    db.flush()
    r = app_client.get("/api/tome-sync/stats/watermark", headers=_auth(plain))
    assert r.status_code == 403


def test_kosync_is_refused(app_client, db, fresh):
    from backend.models.kosync import KOSyncUser
    db.add(KOSyncUser(username="newreader-ko", userkey="0" * 32, user_id=fresh.id))
    db.flush()
    r = app_client.get("/api/v1/users/auth",
                       headers={"x-auth-user": "newreader-ko", "x-auth-key": "0" * 32})
    assert r.status_code == 403


def test_change_password_then_everything_works(app_client, db, fresh):
    tok = app_client.post("/api/auth/login",
                          json={"username": "newreader", "password": TEMP}).json()["access_token"]
    assert app_client.get("/api/books", headers=_auth(tok)).status_code == 403
    r = app_client.put("/api/auth/me/password", headers=_auth(tok),
                       json={"current_password": TEMP, "new_password": "my-own-pass-2"})
    assert r.status_code == 204
    assert app_client.get("/api/auth/me", headers=_auth(tok)).json()["must_change_password"] is False
    assert app_client.get("/api/books", headers=_auth(tok)).status_code == 200


def test_logout_is_allowed(app_client, fresh):
    tok = app_client.post("/api/auth/login",
                          json={"username": "newreader", "password": TEMP}).json()["access_token"]
    assert app_client.post("/api/auth/logout", headers=_auth(tok)).status_code == 204
