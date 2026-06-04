"""OIDC identity resolution + role mapping.

Pure-ish and unit-testable: ``resolve_user_from_claims`` takes a plain claims
dict (so tests never need a live IdP) and returns the Tome ``User`` to mint a JWT
for, applying provisioning / linking / role-sync / access gates per config.

Invariants:
- Match by ``(oidc_sub, oidc_issuer)`` first; fall back to email only when the
  IdP asserts ``email_verified`` (never hijack a local account on an unverified
  email).
- **Break-glass:** OIDC role-sync only ever mutates ``auth_source='oidc'`` users.
  A local account linked by email keeps its role/admin under Tome's control, so a
  misconfigured IdP can't lock the operator out.
"""
from __future__ import annotations

import re
import secrets

import bcrypt
from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.models.user import User, UserPermission
from backend.services.audit import audit


class OIDCError(Exception):
    """Base — login denied. ``code`` becomes the ?sso_error= value."""
    code = "oidc_error"


class NotAllowed(OIDCError):
    code = "not_allowed"


class NoSuchUser(OIDCError):
    code = "no_account"


class Inactive(OIDCError):
    code = "inactive"


class MissingSubject(OIDCError):
    code = "oidc_error"


class AlreadyLinked(OIDCError):
    """The IdP identity is already attached to a different Tome account."""
    code = "already_linked"


def _unusable_password() -> str:
    """A bcrypt hash of random bytes — no one can present a matching password."""
    return bcrypt.hashpw(secrets.token_urlsafe(32).encode(), bcrypt.gensalt()).decode()


def _extract_groups(claims: dict) -> list[str]:
    """Normalize the groups claim to a list of strings.

    Tolerates: missing, a JSON list, or a space/comma-separated string.
    """
    raw = claims.get(settings.oidc_groups_claim)
    if raw is None:
        return []
    if isinstance(raw, str):
        return [g for g in re.split(r"[,\s]+", raw.strip()) if g]
    if isinstance(raw, (list, tuple)):
        return [str(g) for g in raw if str(g)]
    return []


def map_role(groups: list[str]) -> str:
    """Map IdP groups → Tome role, falling through to the configured default."""
    s = settings
    if s.oidc_admin_group and s.oidc_admin_group in groups:
        return "admin"
    if s.oidc_member_group and s.oidc_member_group in groups:
        return "member"
    if s.oidc_guest_group and s.oidc_guest_group in groups:
        return "guest"
    return s.oidc_default_role


def _unique_username(db: Session, base: str | None) -> str:
    """Derive a unique, sanitized username from an IdP-supplied candidate."""
    candidate = re.sub(r"[^a-zA-Z0-9_.-]", "", (base or "").strip()) or "ssouser"
    candidate = candidate[:40]
    if not db.query(User).filter(User.username == candidate).first():
        return candidate
    i = 2
    while db.query(User).filter(User.username == f"{candidate}{i}").first():
        i += 1
    return f"{candidate}{i}"


def resolve_user_from_claims(db: Session, claims: dict) -> User:
    """Resolve (find / link / provision) the Tome user for a set of OIDC claims.

    Commits and returns the user, or raises an ``OIDCError`` subclass on denial.
    """
    s = settings
    sub = claims.get("sub")
    if not sub:
        raise MissingSubject("id_token missing 'sub'")
    issuer = claims.get("iss") or s.oidc_issuer
    groups = _extract_groups(claims)

    # Access gate — must hold the allowed group (when configured) to log in at all.
    if s.oidc_allowed_group and s.oidc_allowed_group not in groups:
        audit(db, "auth.oidc_denied", username=str(claims.get("preferred_username") or sub),
              details={"reason": "not in allowed group"})
        raise NotAllowed("not in allowed group")

    email = claims.get("email")
    email_verified = bool(claims.get("email_verified"))

    # 1. Match by stable (sub, issuer).
    user = (
        db.query(User)
        .filter(User.oidc_sub == sub, User.oidc_issuer == issuer)
        .first()
    )

    # 2. Else link to an existing account by VERIFIED email only.
    if user is None and email and email_verified:
        existing = db.query(User).filter(User.email == email).first()
        if existing is not None:
            existing.oidc_sub = sub
            existing.oidc_issuer = issuer
            # Note: a local account keeps auth_source='local' (break-glass).
            user = existing
            audit(db, "auth.oidc_linked", user_id=user.id, username=user.username,
                  details={"auth_source": user.auth_source})

    # 3. Else provision a new SSO user, or deny.
    if user is None:
        if not s.oidc_auto_create:
            audit(db, "auth.oidc_denied", username=str(claims.get("preferred_username") or sub),
                  details={"reason": "auto-create disabled, no matching account"})
            raise NoSuchUser("no matching Tome account and auto-create is off")
        username = _unique_username(
            db, claims.get("preferred_username") or (email.split("@")[0] if email else sub)
        )
        role = map_role(groups)
        # Adopt the IdP email for this NEW account whenever it's present and not
        # already taken by another account. Verification matters for *linking* to
        # an existing local account (step 2, where hijack risk lives) — not for a
        # fresh account's own email field. email is NOT NULL + unique, so fall back
        # to a unique synthetic address when it's missing or would collide.
        email_taken = bool(email and db.query(User).filter(User.email == email).first())
        account_email = email if (email and not email_taken) else None
        synth_email = account_email or f"{username}@oidc.local"
        user = User(
            username=username,
            email=synth_email,
            hashed_password=_unusable_password(),
            is_active=True,
            is_admin=(role == "admin"),
            role=role,
            auth_source="oidc",
            oidc_sub=sub,
            oidc_issuer=issuer,
            must_change_password=False,
        )
        db.add(user)
        db.flush()
        db.add(UserPermission(user_id=user.id))
        audit(db, "auth.oidc_provisioned", user_id=user.id, username=user.username,
              details={"role": role})

    if not user.is_active:
        raise Inactive("account disabled")

    # Role-sync — IdP is truth on every login, but ONLY for SSO accounts.
    if user.auth_source == "oidc" and s.oidc_role_sync == "login":
        new_role = map_role(groups)
        user.role = new_role
        user.is_admin = (new_role == "admin")

    db.commit()
    db.refresh(user)
    return user


def link_oidc_to_user(db: Session, user_id: int, claims: dict) -> User:
    """Attach an IdP identity to an already-authenticated Tome account.

    Used by the explicit "Link SSO" flow: the user has proven ownership of both
    sides (logged into Tome + authenticated at the IdP), so this needs no
    email_verified check and never role-syncs — it only stamps the sub/issuer
    onto their account so future SSO logins resolve to it. A `local` account
    stays `local` (keeps password login alongside SSO).
    """
    sub = claims.get("sub")
    if not sub:
        raise MissingSubject("id_token missing 'sub'")
    issuer = claims.get("iss") or settings.oidc_issuer

    user = db.get(User, user_id)
    if user is None:
        raise OIDCError("account no longer exists")

    # The identity must not already belong to a different account.
    other = (
        db.query(User)
        .filter(User.oidc_sub == sub, User.oidc_issuer == issuer)
        .first()
    )
    if other is not None and other.id != user.id:
        raise AlreadyLinked("this SSO identity is already linked to another account")

    user.oidc_sub = sub
    user.oidc_issuer = issuer
    db.commit()
    db.refresh(user)
    audit(db, "auth.oidc_linked", user_id=user.id, username=user.username,
          details={"mode": "explicit"})
    return user
