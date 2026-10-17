"""Web logins stay signed in until logout: every login is a revocable device
login, and every place that accepts a JWT honours the revocation."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.core.security import create_access_token, decode_claims, hash_password
from backend.models.audit_log import AuditLog
from backend.models.client_device import ClientDevice
from backend.models.user import User, UserPermission
from backend.services.client_devices import browser_label

ADMIN = {"username": "testadmin", "password": "adminpass123"}
SAFARI_MAC = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/18.0 Safari/605.1.15"
)


def _login(client: TestClient, creds=ADMIN, ua: str | None = SAFARI_MAC) -> str:
    headers = {"User-Agent": ua} if ua else {}
    r = client.post("/api/auth/login", json=creds, headers=headers)
    assert r.status_code == 200
    return r.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _member(db: Session, username: str = "reader") -> User:
    user = User(
        username=username, email=f"{username}@example.com",
        hashed_password=hash_password("memberpass"),
        is_active=True, is_admin=False, role="member", must_change_password=False,
    )
    db.add(user)
    db.flush()
    db.add(UserPermission(user_id=user.id, can_download=True))
    db.flush()
    return user


# ── naming ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("ua, expected", [
    (SAFARI_MAC, ("Safari", "macOS")),
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
     "Chrome/130.0.0.0 Safari/537.36", ("Chrome", "Windows")),
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
     "Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0", ("Edge", "Windows")),
    ("Mozilla/5.0 (X11; Linux x86_64; rv:131.0) Gecko/20100101 Firefox/131.0", ("Firefox", "Linux")),
    ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 "
     "(KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1", ("Safari", "iOS")),
    ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 "
     "(KHTML, like Gecko) CriOS/130.0 Mobile/15E148 Safari/604.1", ("Chrome", "iOS")),
    ("Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) "
     "Chrome/130.0.0.0 Mobile Safari/537.36", ("Chrome", "Android")),
    ("Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) "
     "SamsungBrowser/26.0 Chrome/122.0.0.0 Mobile Safari/537.36", ("Samsung Internet", "Android")),
    ("Mozilla/5.0 (X11; CrOS x86_64 14541.0.0) AppleWebKit/537.36 (KHTML, like Gecko) "
     "Chrome/130.0.0.0 Safari/537.36", ("Chrome", "ChromeOS")),
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
     "Chrome/130.0.0.0 Safari/537.36 OPR/115.0.0.0", ("Opera", "Windows")),
    ("curl/8.7.1", ("Browser", None)),
    (None, ("Browser", None)),
    ("", ("Browser", None)),
])
def test_browser_label(ua, expected):
    assert browser_label(ua) == expected


def test_web_login_is_a_named_browser_device(client: TestClient):
    token = _login(client)
    claims = decode_claims(token)
    assert claims["jti"] and "exp" not in claims

    [d] = client.get("/api/auth/devices", headers=_auth(token)).json()
    assert d["kind"] == "web"
    assert (d["name"], d["platform"]) == ("Safari", "macOS")
    assert d["app_version"] is None
    assert d["current"] is True
    assert d["revoked_at"] is None


def test_app_login_keeps_its_own_name(client: TestClient):
    r = client.post("/api/auth/login", json={**ADMIN, "device": {"name": "My iPhone", "platform": "iOS 26"}},
                    headers={"User-Agent": SAFARI_MAC})
    [d] = client.get("/api/auth/devices", headers=_auth(r.json()["access_token"])).json()
    assert (d["kind"], d["name"], d["platform"]) == ("app", "My iPhone", "iOS 26")


def test_blank_device_name_counts_as_a_browser(client: TestClient):
    r = client.post("/api/auth/login", json={**ADMIN, "device": {"name": "   "}})
    [d] = client.get("/api/auth/devices", headers=_auth(r.json()["access_token"])).json()
    assert d["kind"] == "web"


def test_current_flag_marks_only_the_caller(client: TestClient):
    a = _login(client)
    b = _login(client, ua="Mozilla/5.0 (X11; Linux x86_64; rv:131.0) Gecko/20100101 Firefox/131.0")
    listed = client.get("/api/auth/devices", headers=_auth(a)).json()
    assert len(listed) == 2
    assert [d["name"] for d in listed if d["current"]] == ["Safari"]
    listed_b = client.get("/api/auth/devices", headers=_auth(b)).json()
    assert [d["name"] for d in listed_b if d["current"]] == ["Firefox"]
    # The fixture's plain token is no device: nothing is current.
    assert not any(d["current"] for d in client.get("/api/auth/devices").json())


# ── logout ───────────────────────────────────────────────────────────────────

def test_logout_deletes_the_browser_and_kills_the_token(client: TestClient, db: Session):
    token = _login(client)
    other = _login(client)
    assert client.post("/api/auth/logout", headers=_auth(token)).status_code == 204
    assert client.get("/api/auth/me", headers=_auth(token)).status_code == 401
    # The other browser is untouched, and the logged-out one is gone from the list.
    assert client.get("/api/auth/me", headers=_auth(other)).status_code == 200
    assert len(client.get("/api/auth/devices", headers=_auth(other)).json()) == 1
    # Logging out again with the dead token is a 401, not a crash.
    assert client.post("/api/auth/logout", headers=_auth(token)).status_code == 401

    entry = db.query(AuditLog).filter(AuditLog.action == "auth.logout").one()
    assert entry.resource_title == "Safari"


def test_logout_of_an_app_marks_it_revoked(client: TestClient):
    r = client.post("/api/auth/login", json={**ADMIN, "device": {"name": "My iPhone"}})
    token = r.json()["access_token"]
    assert client.post("/api/auth/logout", headers=_auth(token)).status_code == 204
    assert client.get("/api/auth/me", headers=_auth(token)).status_code == 401
    [d] = client.get("/api/auth/devices").json()
    assert d["revoked_at"] is not None


def test_logout_with_a_plain_token_is_a_no_op(client: TestClient, db: Session):
    browser = _login(client)
    # The fixture client carries a plain (pre-upgrade style) expiring token.
    assert client.post("/api/auth/logout").status_code == 204
    assert client.get("/api/auth/me", headers=_auth(browser)).status_code == 200
    assert db.query(AuditLog).filter(AuditLog.action == "auth.logout").count() == 0


def test_revoking_this_browser_from_the_list_signs_it_out(client: TestClient):
    token = _login(client)
    [d] = client.get("/api/auth/devices", headers=_auth(token)).json()
    assert client.delete(f"/api/auth/devices/{d['id']}", headers=_auth(token)).status_code == 204
    assert client.get("/api/auth/me", headers=_auth(token)).status_code == 401


# ── token checks everywhere ──────────────────────────────────────────────────

def test_deactivated_user_is_signed_out(client: TestClient, db: Session):
    user = _member(db)
    token = _login(client, {"username": user.username, "password": "memberpass"})
    assert client.get("/api/auth/me", headers=_auth(token)).status_code == 200
    assert client.put(f"/api/users/{user.id}", json={"is_active": False}).status_code == 200
    assert client.get("/api/auth/me", headers=_auth(token)).status_code == 401


def test_token_of_a_device_owned_by_someone_else_is_rejected(client: TestClient, db: Session):
    user = _member(db)
    token = _login(client)
    jti = decode_claims(token)["jti"]
    forged = create_access_token(user.id, device_token_id=jti)
    assert client.get("/api/auth/me", headers=_auth(forged)).status_code == 401


def test_unknown_jti_is_rejected(client: TestClient):
    from backend.core.security import decode_token
    sub = decode_token(_login(client))
    assert client.get("/api/auth/me", headers=_auth(create_access_token(sub, device_token_id="nope"))).status_code == 401


def test_garbage_and_non_numeric_sub_are_rejected(client: TestClient):
    from jose import jwt
    from backend.core.config import settings
    from backend.core.security import _signing_key
    bad_sub = jwt.encode({"sub": "abc"}, _signing_key(), algorithm=settings.jwt_algorithm)
    for token in ("not-a-jwt", bad_sub):
        assert client.get("/api/auth/me", headers=_auth(token)).status_code == 401


@pytest.fixture()
def reader_files(tmp_path: Path, make_book):
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
    epub = tmp_path / "book.epub"
    epub.write_bytes(b"PK\x05\x06" + b"\x00" * 18)  # empty zip
    import zipfile
    cbz = tmp_path / "comic.cbz"
    with zipfile.ZipFile(cbz, "w") as zf:
        zf.writestr("001.jpg", b"\xff\xd8\xff\xd9")
    return {
        "pdf": make_book(title="Pdf", file_path=str(pdf), file_format="pdf"),
        "epub": make_book(title="Epub", file_path=str(epub), file_format="epub"),
        "cbz": make_book(title="Comic", file_path=str(cbz), file_format="cbz"),
    }


def _reader_urls(books) -> list[str]:
    return [
        f"/api/books/{books['pdf'].id}/read.pdf",
        f"/api/books/{books['cbz'].id}/read.cbz",
        f"/api/books/{books['cbz'].id}/pages/0",
        f"/api/books/{books['epub'].id}/read.epub",
    ]


def test_reader_query_token_routes_accept_a_browser_login(client: TestClient, reader_files):
    token = _login(client)
    for url in _reader_urls(reader_files):
        r = client.get(url, params={"token": token})
        assert r.status_code == 200, (url, r.status_code, r.text[:200])


def test_reader_query_token_routes_reject_a_revoked_login(client: TestClient, reader_files):
    token = _login(client)
    assert client.post("/api/auth/logout", headers=_auth(token)).status_code == 204
    for url in _reader_urls(reader_files):
        assert client.get(url, params={"token": token}).status_code == 401, url


def test_reader_query_token_routes_reject_a_deactivated_user(client: TestClient, db: Session, reader_files):
    user = _member(db)
    token = _login(client, {"username": user.username, "password": "memberpass"})
    user.is_active = False
    db.flush()
    for url in _reader_urls(reader_files):
        assert client.get(url, params={"token": token}).status_code == 401, url


def test_reader_query_token_routes_reject_garbage(client: TestClient, reader_files):
    for url in _reader_urls(reader_files):
        assert client.get(url, params={"token": "nope"}).status_code == 401, url


# ── passwords ────────────────────────────────────────────────────────────────

def test_admin_password_reset_signs_the_user_out_everywhere(client: TestClient, db: Session):
    user = _member(db)
    creds = {"username": user.username, "password": "memberpass"}
    browser = _login(client, creds)
    phone = client.post("/api/auth/login", json={**creds, "device": {"name": "Phone"}}).json()["access_token"]
    admin_browser = _login(client)

    r = client.put(f"/api/users/{user.id}", json={"password": "brandnew123"}, headers=_auth(admin_browser))
    assert r.status_code == 200
    for token in (browser, phone):
        assert client.get("/api/auth/me", headers=_auth(token)).status_code == 401
    assert client.get("/api/auth/me", headers=_auth(admin_browser)).status_code == 200

    entry = db.query(AuditLog).filter(AuditLog.action == "users.updated").one()
    details = json.loads(entry.details)
    assert "password" not in details and "brandnew123" not in entry.details
    assert details["password_changed"] is True and details["devices_revoked"] == 2


def test_admin_resetting_own_password_keeps_this_browser(client: TestClient, db: Session):
    from backend.core.security import decode_token
    mine = _login(client)
    elsewhere = _login(client)
    admin_id = int(decode_token(mine))
    r = client.put(f"/api/users/{admin_id}", json={"password": "adminpass456"}, headers=_auth(mine))
    assert r.status_code == 200
    assert client.get("/api/auth/me", headers=_auth(mine)).status_code == 200
    assert client.get("/api/auth/me", headers=_auth(elsewhere)).status_code == 401


def test_admin_update_without_password_revokes_nothing(client: TestClient, db: Session):
    user = _member(db)
    browser = _login(client, {"username": user.username, "password": "memberpass"})
    assert client.put(f"/api/users/{user.id}", json={"email": "new@example.com"}).status_code == 200
    assert client.get("/api/auth/me", headers=_auth(browser)).status_code == 200
    details = json.loads(db.query(AuditLog).filter(AuditLog.action == "users.updated").one().details)
    assert details == {"email": "new@example.com"}


# ── other ways in ────────────────────────────────────────────────────────────

def test_impersonation_token_still_expires_and_is_no_device(client: TestClient, db: Session):
    user = _member(db)
    r = client.post(f"/api/users/{user.id}/impersonate")
    assert r.status_code == 200
    claims = decode_claims(r.json()["access_token"])
    assert "exp" in claims and "jti" not in claims
    assert db.query(ClientDevice).filter(ClientDevice.user_id == user.id).count() == 0


def test_quick_connect_in_a_browser_is_a_browser_device(client: TestClient):
    issued = client.post("/api/auth/quick-connect/issue").json()
    r = client.post("/api/auth/quick-connect/poll",
                    json={"code": issued["code"], "poll_token": issued["poll_token"]},
                    headers={"User-Agent": SAFARI_MAC})
    token = r.json()["access_token"]
    assert "exp" not in decode_claims(token)
    [d] = client.get("/api/auth/devices", headers=_auth(token)).json()
    assert (d["kind"], d["name"], d["current"]) == ("web", "Safari", True)


def test_oidc_login_is_a_browser_device(client: TestClient, db: Session, monkeypatch):
    from backend.core.config import settings
    for key, value in {
        "oidc_enabled": True, "oidc_issuer": "https://idp.example.test",
        "oidc_client_id": "tome", "oidc_client_secret": "s3cret",
        "oidc_member_group": "tome-members",
    }.items():
        monkeypatch.setattr(settings, key, value)

    class _FakeClient:
        async def authorize_access_token(self, request):
            return {"userinfo": {"sub": "web-1", "email": "web1@example.test",
                                 "email_verified": True, "preferred_username": "web1",
                                 "groups": ["tome-members"], "iss": "https://idp.example.test"}}

    class _FakeOAuth:
        tome_oidc = _FakeClient()

    monkeypatch.setattr("backend.api.oidc.get_oauth", lambda: _FakeOAuth())
    r = client.get("/api/auth/oidc/callback", follow_redirects=False, headers={"User-Agent": SAFARI_MAC})
    assert r.status_code == 302, r.headers.get("location")
    token = r.headers["location"].split("#token=", 1)[1]
    claims = decode_claims(token)
    assert claims["jti"] and "exp" not in claims
    [d] = client.get("/api/auth/devices", headers=_auth(token)).json()
    assert (d["kind"], d["name"]) == ("web", "Safari")
    assert client.post("/api/auth/logout", headers=_auth(token)).status_code == 204
    assert client.get("/api/auth/me", headers=_auth(token)).status_code == 401
