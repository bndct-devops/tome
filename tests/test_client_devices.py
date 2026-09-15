"""Connected devices: a native client that names itself at login gets a
revocable, device-bound JWT and shows up under /auth/devices."""
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.core.security import create_access_token, hash_password
from backend.models.user import User, UserPermission

PHONE = {"name": "Ben's iPhone", "platform": "iOS 26.0 · iPhone16,2", "app_version": "0.1 (1)"}


def _make_member(db: Session, username: str = "member") -> tuple[User, str]:
    user = User(
        username=username,
        email=f"{username}@example.com",
        hashed_password=hash_password("memberpass"),
        is_active=True,
        is_admin=False,
        role="member",
        must_change_password=False,
    )
    db.add(user)
    db.flush()
    db.add(UserPermission(user_id=user.id, can_download=True, can_view_stats=True,
                          can_use_opds=True, can_use_kosync=True))
    db.flush()
    return user, create_access_token(subject=user.id)


def _pair_phone(client: TestClient, device=PHONE) -> str:
    issued = client.post("/api/auth/quick-connect/issue").json()
    body = {"code": issued["code"], "poll_token": issued["poll_token"]}
    if device is not None:
        body["device"] = device
    done = client.post("/api/auth/quick-connect/poll", json=body)
    assert done.status_code == 200 and done.json()["status"] == "authorized"
    return done.json()["access_token"]


def test_poll_with_device_registers_it(client: TestClient):
    phone = _pair_phone(client)

    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {phone}"})
    assert me.status_code == 200

    devices = client.get("/api/auth/devices").json()
    assert len(devices) == 1
    d = devices[0]
    assert d["name"] == PHONE["name"]
    assert d["platform"] == PHONE["platform"]
    assert d["app_version"] == PHONE["app_version"]
    assert d["last_seen_at"] and d["created_at"].endswith("Z")
    assert d["revoked_at"] is None


def test_poll_without_device_registers_nothing(client: TestClient):
    token = _pair_phone(client, device=None)
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    assert client.get("/api/auth/devices").json() == []


def test_revoke_signs_the_phone_out(client: TestClient):
    phone = _pair_phone(client)
    device_id = client.get("/api/auth/devices").json()[0]["id"]

    assert client.delete(f"/api/auth/devices/{device_id}").status_code == 204
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {phone}"}).status_code == 401

    listed = client.get("/api/auth/devices").json()[0]
    assert listed["revoked_at"] is not None
    # revoking twice is fine and does not change the timestamp
    assert client.delete(f"/api/auth/devices/{device_id}").status_code == 204
    assert client.get("/api/auth/devices").json()[0]["revoked_at"] == listed["revoked_at"]


def test_member_scope(client: TestClient, db: Session):
    member, member_token = _make_member(db)
    mh = {"Authorization": f"Bearer {member_token}"}

    # admin (the fixture client) pairs a phone
    _pair_phone(client)
    admin_device = client.get("/api/auth/devices").json()[0]

    # member sees nothing of it, may not list all, may not revoke it
    assert client.get("/api/auth/devices", headers=mh).json() == []
    assert client.get("/api/auth/devices?all=true", headers=mh).status_code == 403
    assert client.delete(f"/api/auth/devices/{admin_device['id']}", headers=mh).status_code == 403

    # member's own phone via password login
    login = client.post("/api/auth/login", json={"username": member.username, "password": "memberpass", "device": {"name": "Member phone"}})
    assert login.status_code == 200
    mine = client.get("/api/auth/devices", headers=mh).json()
    assert [d["name"] for d in mine] == ["Member phone"]

    # admin ?all=true sees both, with owners
    everyone = client.get("/api/auth/devices?all=true").json()
    assert {d["username"] for d in everyone} >= {member.username, admin_device["username"]}

    # admin can revoke the member's phone
    assert client.delete(f"/api/auth/devices/{mine[0]['id']}").status_code == 204
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {login.json()['access_token']}"}).status_code == 401


def test_unknown_device_404(client: TestClient):
    assert client.delete("/api/auth/devices/99999").status_code == 404


def test_device_token_has_no_expiry_web_token_keeps_it(client: TestClient):
    from backend.core.security import decode_claims

    phone = _pair_phone(client)
    claims = decode_claims(phone)
    assert claims["jti"] and "exp" not in claims

    web = client.post("/api/auth/login", json={"username": "testadmin", "password": "adminpass123"})
    web_claims = decode_claims(web.json()["access_token"])
    assert "jti" not in web_claims and "exp" in web_claims
    from backend.core.config import settings
    remaining = web_claims["exp"] - datetime.now(timezone.utc).timestamp()
    assert abs(remaining - settings.jwt_expire_minutes * 60) < 60


def test_device_token_still_valid_past_web_expiry(client: TestClient, monkeypatch):
    from backend.core import security

    phone = _pair_phone(client)
    web = client.post("/api/auth/login", json={"username": "testadmin", "password": "adminpass123"}).json()["access_token"]

    # Jump past the web token's lifetime: jose checks `exp` against datetime.now(UTC).
    future = datetime.now(timezone.utc) + timedelta(minutes=security.settings.jwt_expire_minutes + 60)

    class _FrozenDatetime(datetime):
        @classmethod
        def utcnow(cls):
            return future.replace(tzinfo=None)

        @classmethod
        def now(cls, tz=None):
            return future if tz else future.replace(tzinfo=None)

    import jose.jwt
    monkeypatch.setattr(jose.jwt, "datetime", _FrozenDatetime)

    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {web}"}).status_code == 401
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {phone}"}).status_code == 200

    device_id = client.get("/api/auth/devices", headers={"Authorization": f"Bearer {phone}"}).json()[0]["id"]
    assert client.delete(f"/api/auth/devices/{device_id}", headers={"Authorization": f"Bearer {phone}"}).status_code == 204
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {phone}"}).status_code == 401


def test_password_change_revokes_devices_not_web(client: TestClient, db: Session):
    member, _ = _make_member(db, "pwchanger")
    creds = {"username": member.username, "password": "memberpass"}
    phone = client.post("/api/auth/login", json={**creds, "device": {"name": "Phone"}}).json()["access_token"]
    tablet = client.post("/api/auth/login", json={**creds, "device": {"name": "Tablet"}}).json()["access_token"]
    web = client.post("/api/auth/login", json=creds).json()["access_token"]
    # another user's device is untouched
    admin_phone = _pair_phone(client)

    r = client.put("/api/auth/me/password", json={"current_password": "memberpass", "new_password": "newpass123"},
                   headers={"Authorization": f"Bearer {web}"})
    assert r.status_code == 204

    for token in (phone, tablet):
        assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {web}"}).status_code == 200
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {admin_phone}"}).status_code == 200
    mine = client.get("/api/auth/devices", headers={"Authorization": f"Bearer {web}"}).json()
    assert len(mine) == 2 and all(d["revoked_at"] for d in mine)


def test_wrong_current_password_revokes_nothing(client: TestClient):
    phone = _pair_phone(client)
    r = client.put("/api/auth/me/password", json={"current_password": "nope", "new_password": "newpass123"})
    assert r.status_code == 400
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {phone}"}).status_code == 200
