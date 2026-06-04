"""Authlib OIDC client registry.

Built lazily on first use so a bad/unreachable issuer never breaks app startup —
the registry is only touched when a user actually initiates an SSO login. The
``OAuth`` instance uses Starlette's request session for transient state/nonce/PKCE
(see SessionMiddleware in ``create_app``).
"""
from __future__ import annotations

from authlib.integrations.starlette_client import OAuth

from backend.core.config import settings

_oauth: OAuth | None = None

# Registered provider name; referenced as ``oauth.tome_oidc``.
PROVIDER = "tome_oidc"


def get_oauth() -> OAuth:
    """Return the process-wide OAuth registry, registering the provider once."""
    global _oauth
    if _oauth is None:
        oauth = OAuth()
        oauth.register(
            name=PROVIDER,
            server_metadata_url=f"{settings.oidc_issuer.rstrip('/')}/.well-known/openid-configuration",
            client_id=settings.oidc_client_id,
            client_secret=settings.oidc_client_secret,
            client_kwargs={
                "scope": settings.oidc_scopes,
                "code_challenge_method": "S256",  # PKCE
            },
        )
        _oauth = oauth
    return _oauth


def reset_oauth() -> None:
    """Drop the cached registry (tests / config reload)."""
    global _oauth
    _oauth = None
