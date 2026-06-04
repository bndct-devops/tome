"""OIDC / SSO endpoints.

Flow: GET /api/auth/oidc/login → IdP → GET /api/auth/oidc/callback. The callback
mints Tome's normal JWT and hands it to the frontend in the URL *fragment*
(never the query string, so it can't land in proxy/server logs).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.core.database import get_db
from backend.core.oidc import get_oauth
from backend.core.security import create_access_token, get_current_user
from backend.core.urls import is_secure_origin, public_base_url
from backend.models.user import User
from backend.services.audit import audit
from backend.services.oidc import OIDCError, link_oidc_to_user, resolve_user_from_claims

# Session key carrying "this handshake is an account-link for user N", not a login.
_LINK_SESSION_KEY = "oidc_link_user_id"

log = logging.getLogger(__name__)

router = APIRouter(prefix="/auth/oidc", tags=["oidc"])


def _redirect_uri(request: Request) -> str | None:
    """The exact callback URL handed to the IdP.

    Priority: explicit ``TOME_OIDC_REDIRECT_URL`` → derived public origin. The
    derived origin must be secure (https, or localhost for dev); otherwise we
    refuse rather than emit an ``http`` redirect the IdP won't match (§2.1).
    """
    if settings.oidc_redirect_url:
        return settings.oidc_redirect_url.rstrip("/")
    origin = public_base_url(request)
    if not is_secure_origin(origin):
        log.error(
            "OIDC: cannot derive a secure redirect URL from origin %r. "
            "Set TOME_OIDC_REDIRECT_URL or TOME_PUBLIC_URL to the public https URL.",
            origin,
        )
        return None
    return f"{origin}/api/auth/oidc/callback"


def _error_redirect(reason: str) -> RedirectResponse:
    return RedirectResponse(url=f"/login?sso_error={reason}", status_code=302)


@router.get("/config")
def oidc_config():
    """Public — drives whether the frontend shows the SSO button."""
    return {
        "enabled": settings.oidc_configured,
        "button_label": settings.oidc_button_label,
    }


@router.post("/link/start")
def oidc_link_start(request: Request, current_user: User = Depends(get_current_user)):
    """Authenticated: mark the next OIDC handshake as an account-link for this
    user. The browser then navigates to /login as usual; the session cookie
    carries the intent through to the callback."""
    if not settings.oidc_configured:
        raise HTTPException(status_code=404, detail="SSO is not enabled")
    request.session[_LINK_SESSION_KEY] = current_user.id
    return {"login_url": "/api/auth/oidc/login"}


@router.get("/login")
async def oidc_login(request: Request):
    if not settings.oidc_configured:
        raise HTTPException(status_code=404, detail="SSO is not enabled")
    redirect_uri = _redirect_uri(request)
    if redirect_uri is None:
        return _error_redirect("misconfigured")
    oauth = get_oauth()
    return await oauth.tome_oidc.authorize_redirect(request, redirect_uri)


@router.get("/callback")
async def oidc_callback(request: Request, db: Session = Depends(get_db)):
    if not settings.oidc_configured:
        return _error_redirect("disabled")

    oauth = get_oauth()
    try:
        token = await oauth.tome_oidc.authorize_access_token(request)
    except Exception as exc:  # state/nonce mismatch, exchange failure, etc.
        log.warning("OIDC token exchange failed: %s", exc)
        return _error_redirect("exchange")

    claims = dict(token.get("userinfo") or {})
    if not claims.get("sub"):
        # Fall back to the id_token claims if userinfo wasn't populated.
        try:
            claims = dict(await oauth.tome_oidc.parse_id_token(request, token))
        except Exception as exc:
            log.warning("OIDC id_token parse failed: %s", exc)
            return _error_redirect("claims")

    # Account-link mode: the user was already logged in and chose "Link SSO".
    # Attach the identity to *their* account instead of logging in/provisioning.
    link_user_id = request.session.pop(_LINK_SESSION_KEY, None)
    if link_user_id is not None:
        try:
            link_oidc_to_user(db, int(link_user_id), claims)
        except OIDCError as exc:
            return RedirectResponse(url=f"/settings?sso_link_error={exc.code}", status_code=302)
        except Exception as exc:
            log.exception("OIDC link error: %s", exc)
            return RedirectResponse(url="/settings?sso_link_error=oidc_error", status_code=302)
        return RedirectResponse(url="/settings?sso_linked=1", status_code=302)

    try:
        user = resolve_user_from_claims(db, claims)
    except OIDCError as exc:
        return _error_redirect(exc.code)
    except Exception as exc:  # defensive — never 500 the callback
        log.exception("OIDC resolution error: %s", exc)
        return _error_redirect("oidc_error")

    jwt = create_access_token(user.id)
    audit(db, "auth.oidc_login", user_id=user.id, username=user.username,
          ip=request.client.host if request.client else None)
    return RedirectResponse(
        url=f"{settings.oidc_post_login_redirect}#token={jwt}",
        status_code=302,
    )
