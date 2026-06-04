"""Tests for OIDC / SSO (backend/services/oidc.py + backend/api/oidc.py).

The resolver is driven by plain claims dicts so none of this needs a live IdP.
The one HTTP callback test mocks the Authlib client's token exchange.
"""
import pytest

from backend.core.config import settings
from backend.core.security import hash_password
from backend.models.user import User, UserPermission
from backend.services import oidc as svc
from backend.services.oidc import (
    AlreadyLinked,
    Inactive,
    MissingSubject,
    NoSuchUser,
    NotAllowed,
    link_oidc_to_user,
    map_role,
    resolve_user_from_claims,
)

ISSUER = "https://idp.example.test"


@pytest.fixture()
def oidc_cfg(monkeypatch):
    """Enable + configure OIDC with sensible group mappings for a test."""
    monkeypatch.setattr(settings, "oidc_enabled", True)
    monkeypatch.setattr(settings, "oidc_issuer", ISSUER)
    monkeypatch.setattr(settings, "oidc_client_id", "tome")
    monkeypatch.setattr(settings, "oidc_client_secret", "secret")
    monkeypatch.setattr(settings, "oidc_auto_create", True)
    monkeypatch.setattr(settings, "oidc_role_sync", "login")
    monkeypatch.setattr(settings, "oidc_groups_claim", "groups")
    monkeypatch.setattr(settings, "oidc_admin_group", "tome-admins")
    monkeypatch.setattr(settings, "oidc_member_group", "tome-members")
    monkeypatch.setattr(settings, "oidc_guest_group", None)
    monkeypatch.setattr(settings, "oidc_default_role", "guest")
    monkeypatch.setattr(settings, "oidc_allowed_group", None)
    return settings


def _claims(sub="sub-1", email="alice@example.test", verified=True, groups=None, **extra):
    c = {"sub": sub, "iss": ISSUER, "email": email, "email_verified": verified}
    if groups is not None:
        c["groups"] = groups
    c.update(extra)
    return c


# ── map_role ──────────────────────────────────────────────────────────────────

def test_map_role_matrix(oidc_cfg):
    assert map_role(["tome-admins"]) == "admin"
    assert map_role(["tome-members"]) == "member"
    assert map_role(["something-else"]) == "guest"   # default
    assert map_role([]) == "guest"                    # default


# ── Provisioning ────────────────────────────────────────────────────────────────

def test_auto_provision_new_user(db, oidc_cfg):
    user = resolve_user_from_claims(db, _claims(groups=["tome-members"], preferred_username="alice"))
    assert user.id is not None
    assert user.auth_source == "oidc"
    assert user.role == "member"
    assert user.is_admin is False
    assert user.oidc_sub == "sub-1"
    assert user.oidc_issuer == ISSUER
    # mirrors create_user: a permission row exists
    assert db.query(UserPermission).filter_by(user_id=user.id).first() is not None


def test_auto_provision_admin_group_grants_admin(db, oidc_cfg):
    user = resolve_user_from_claims(db, _claims(groups=["tome-admins"]))
    assert user.role == "admin"
    assert user.is_admin is True


def test_auto_create_disabled_denies_unknown(db, oidc_cfg, monkeypatch):
    monkeypatch.setattr(settings, "oidc_auto_create", False)
    with pytest.raises(NoSuchUser):
        resolve_user_from_claims(db, _claims())


def test_missing_email_provisions_by_sub(db, oidc_cfg):
    user = resolve_user_from_claims(db, _claims(email=None, verified=False, sub="no-email", preferred_username="bob"))
    assert user.auth_source == "oidc"
    assert user.username == "bob"
    assert user.email.endswith("@oidc.local")


def test_provision_adopts_idp_email_even_if_unverified(db, oidc_cfg):
    # New account, email present but not asserted verified, and not taken →
    # adopt the real IdP email (verification only gates *linking*, not provisioning).
    user = resolve_user_from_claims(
        db, _claims(sub="fresh", email="fresh@example.test", verified=False, groups=["tome-members"])
    )
    assert user.auth_source == "oidc"
    assert user.email == "fresh@example.test"


def test_provision_synthesizes_email_when_idp_email_collides(db, oidc_cfg):
    # An unverified email that collides with an existing account must NOT be
    # adopted (and must not raise on the unique constraint) — synthesize instead.
    existing = User(username="taken", email="dup@example.test",
                    hashed_password=hash_password("pw12345678"),
                    is_active=True, role="member", auth_source="local")
    db.add(existing); db.flush()
    user = resolve_user_from_claims(db, _claims(sub="collide", email="dup@example.test", verified=False))
    assert user.id != existing.id
    assert user.auth_source == "oidc"
    assert user.email.endswith("@oidc.local")


# ── Linking ─────────────────────────────────────────────────────────────────────

def test_link_local_user_by_verified_email(db, oidc_cfg):
    local = User(username="alice", email="alice@example.test",
                 hashed_password=hash_password("pw12345678"),
                 is_active=True, role="member", auth_source="local")
    db.add(local); db.flush()

    user = resolve_user_from_claims(db, _claims(groups=["tome-admins"]))
    assert user.id == local.id
    assert user.oidc_sub == "sub-1"
    # break-glass: a LOCAL account is NOT converted and NOT role-synced by the IdP
    assert user.auth_source == "local"
    assert user.role == "member"  # unchanged despite admin group in claims


def test_unverified_email_does_not_hijack_local_account(db, oidc_cfg):
    local = User(username="carol", email="carol@example.test",
                 hashed_password=hash_password("pw12345678"),
                 is_active=True, role="member", auth_source="local")
    db.add(local); db.flush()

    # Same email but NOT verified → must create a new account, never link.
    user = resolve_user_from_claims(db, _claims(sub="sub-evil", email="carol@example.test", verified=False))
    assert user.id != local.id
    assert user.auth_source == "oidc"
    db.refresh(local)
    assert local.oidc_sub is None  # untouched


# ── Role sync ────────────────────────────────────────────────────────────────────

def test_role_sync_login_updates_on_each_login(db, oidc_cfg):
    u = resolve_user_from_claims(db, _claims(groups=["tome-members"]))
    assert u.role == "member"
    # next login, now in admins group
    u2 = resolve_user_from_claims(db, _claims(groups=["tome-admins"]))
    assert u2.id == u.id
    assert u2.role == "admin"
    assert u2.is_admin is True


def test_role_sync_create_does_not_update(db, oidc_cfg, monkeypatch):
    u = resolve_user_from_claims(db, _claims(groups=["tome-members"]))
    assert u.role == "member"
    monkeypatch.setattr(settings, "oidc_role_sync", "create")
    u2 = resolve_user_from_claims(db, _claims(groups=["tome-admins"]))
    assert u2.id == u.id
    assert u2.role == "member"  # set-once: not re-synced


# ── Gates ────────────────────────────────────────────────────────────────────────

def test_allowed_group_required(db, oidc_cfg, monkeypatch):
    monkeypatch.setattr(settings, "oidc_allowed_group", "tome-users")
    with pytest.raises(NotAllowed):
        resolve_user_from_claims(db, _claims(groups=["tome-members"]))
    # present → allowed
    user = resolve_user_from_claims(db, _claims(groups=["tome-users", "tome-members"]))
    assert user.role == "member"


def test_inactive_user_denied(db, oidc_cfg):
    u = User(username="dave", email="dave@example.test",
             hashed_password="x", is_active=False, role="member",
             auth_source="oidc", oidc_sub="sub-dave", oidc_issuer=ISSUER)
    db.add(u); db.flush()
    with pytest.raises(Inactive):
        resolve_user_from_claims(db, _claims(sub="sub-dave", email="dave@example.test"))


def test_missing_sub_raises(db, oidc_cfg):
    with pytest.raises(MissingSubject):
        resolve_user_from_claims(db, {"email": "x@example.test", "email_verified": True})


def test_groups_claim_accepts_space_separated_string(db, oidc_cfg):
    user = resolve_user_from_claims(db, _claims(groups="tome-admins tome-members"))
    assert user.role == "admin"


# ── Explicit account linking ──────────────────────────────────────────────────────

def test_link_attaches_sub_to_existing_account(db, oidc_cfg):
    local = User(username="owner", email="owner@example.test",
                 hashed_password=hash_password("pw12345678"),
                 is_active=True, role="admin", is_admin=True, auth_source="local")
    db.add(local); db.flush()

    linked = link_oidc_to_user(db, local.id, _claims(sub="link-sub", groups=["tome-admins"]))
    assert linked.id == local.id
    assert linked.oidc_sub == "link-sub"
    assert linked.oidc_issuer == ISSUER
    # link does NOT convert or role-sync a local account
    assert linked.auth_source == "local"
    assert linked.role == "admin"
    assert linked.oidc_linked is True

    # a subsequent SSO LOGIN now resolves to that same account by sub
    same = resolve_user_from_claims(db, _claims(sub="link-sub", groups=["tome_members"]))
    assert same.id == local.id
    # still local → role NOT synced down to member
    assert same.role == "admin"


def test_link_blocked_when_sub_belongs_to_another_account(db, oidc_cfg):
    a = User(username="a", email="a@example.test", hashed_password="x",
             is_active=True, role="member", auth_source="oidc",
             oidc_sub="shared-sub", oidc_issuer=ISSUER)
    b = User(username="b", email="b@example.test", hashed_password=hash_password("pw12345678"),
             is_active=True, role="member", auth_source="local")
    db.add_all([a, b]); db.flush()

    with pytest.raises(AlreadyLinked):
        link_oidc_to_user(db, b.id, _claims(sub="shared-sub"))
    db.refresh(b)
    assert b.oidc_sub is None  # unchanged


# ── Config endpoint ───────────────────────────────────────────────────────────────

def test_config_endpoint_reports_disabled(client, monkeypatch):
    # Endpoint reflects an unconfigured/disabled SSO state.
    monkeypatch.setattr(settings, "oidc_enabled", False)
    monkeypatch.setattr(settings, "oidc_issuer", None)
    r = client.get("/api/auth/oidc/config")
    assert r.status_code == 200
    assert r.json()["enabled"] is False


def test_config_endpoint_reflects_enabled(client, oidc_cfg):
    r = client.get("/api/auth/oidc/config")
    assert r.json()["enabled"] is True


# ── Callback (Authlib mocked) ─────────────────────────────────────────────────────

def test_callback_mints_jwt_and_redirects(client, db, oidc_cfg, monkeypatch):
    """Full callback path with the token exchange mocked → 302 to #token=…"""
    class _FakeClient:
        async def authorize_access_token(self, request):
            return {"userinfo": _claims(sub="cb-1", email="cb@example.test", groups=["tome-members"])}

    class _FakeOAuth:
        tome_oidc = _FakeClient()

    monkeypatch.setattr("backend.api.oidc.get_oauth", lambda: _FakeOAuth())

    r = client.get("/api/auth/oidc/callback", follow_redirects=False)
    assert r.status_code == 302
    loc = r.headers["location"]
    assert loc.startswith("/auth/callback#token=")
    token = loc.split("#token=", 1)[1]
    assert token

    # user was provisioned
    u = db.query(User).filter_by(oidc_sub="cb-1").first()
    assert u is not None and u.role == "member"


def test_callback_denied_redirects_to_login_error(client, db, oidc_cfg, monkeypatch):
    monkeypatch.setattr(settings, "oidc_auto_create", False)

    class _FakeClient:
        async def authorize_access_token(self, request):
            return {"userinfo": _claims(sub="nobody", email="nobody@example.test")}

    class _FakeOAuth:
        tome_oidc = _FakeClient()

    monkeypatch.setattr("backend.api.oidc.get_oauth", lambda: _FakeOAuth())

    r = client.get("/api/auth/oidc/callback", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/login?sso_error=no_account"
