"""AI settings: instance switches, per-feature overrides, and key resolution.

Instance settings live in the generic ``instance_settings`` key/value table:

    ai.enabled                  "1" | "0"          default on
    ai.instance_key             Fernet ciphertext  default unset
    ai.share_instance_key       "1" | "0"          default off
    ai.confidence_threshold     float 0..1         default 0.85
    ai.feature.<key>.enabled    "1" | "0"          default on
    ai.feature.<key>.model      model id           default unset (registry default)

Env: TOME_AI_ENABLED=false hides everything regardless of the above;
TOME_ANTHROPIC_API_KEY acts as the instance key when no DB key is stored.

Keys are stored encrypted (backend/core/crypto.py) and never returned: callers
get ``has_key`` and a masked suffix at most.

Resolution for user U: U's own key; else the instance key (DB, then env) if U
is an admin or sharing is on; else nothing. Guests never resolve a key.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.core.crypto import decrypt_secret, encrypt_secret
from backend.core.permissions import is_admin, is_member_or_above
from backend.models.instance_setting import InstanceSetting
from backend.models.user import User
from backend.services.ai import registry

KEY_ENABLED = "ai.enabled"
KEY_INSTANCE_KEY = "ai.instance_key"
KEY_SHARE = "ai.share_instance_key"
KEY_THRESHOLD = "ai.confidence_threshold"

DEFAULT_THRESHOLD = 0.85


def _feature_key(feature: str, field: str) -> str:
    return f"ai.feature.{feature}.{field}"


# ── instance_settings helpers ────────────────────────────────────────────────

def _get(db: Session, key: str) -> str | None:
    row = db.get(InstanceSetting, key)
    return row.value if row else None


def _set(db: Session, key: str, value: str) -> None:
    row = db.get(InstanceSetting, key)
    if row:
        row.value = value
    else:
        db.add(InstanceSetting(key=key, value=value))
    # Sessions run with autoflush off: flush so a read later in the same
    # request (db.get) sees the new row instead of the stale state.
    db.flush()


def _delete(db: Session, key: str) -> None:
    row = db.get(InstanceSetting, key)
    if row:
        db.delete(row)
        db.flush()


def _get_bool(db: Session, key: str, default: bool) -> bool:
    raw = _get(db, key)
    if raw is None:
        return default
    return raw == "1"


def _set_bool(db: Session, key: str, value: bool) -> None:
    _set(db, key, "1" if value else "0")


# ── switches ─────────────────────────────────────────────────────────────────

def env_enabled() -> bool:
    return bool(settings.ai_enabled)


def instance_enabled(db: Session) -> bool:
    return _get_bool(db, KEY_ENABLED, True)


def ai_enabled(db: Session) -> bool:
    """Env switch AND the admin's instance switch."""
    return env_enabled() and instance_enabled(db)


def set_instance_enabled(db: Session, value: bool) -> None:
    _set_bool(db, KEY_ENABLED, value)


def share_instance_key(db: Session) -> bool:
    return _get_bool(db, KEY_SHARE, False)


def set_share_instance_key(db: Session, value: bool) -> None:
    _set_bool(db, KEY_SHARE, value)


def confidence_threshold(db: Session) -> float:
    raw = _get(db, KEY_THRESHOLD)
    if raw is None:
        return DEFAULT_THRESHOLD
    try:
        val = float(raw)
    except ValueError:
        return DEFAULT_THRESHOLD
    return min(1.0, max(0.0, val))


def set_confidence_threshold(db: Session, value: float) -> None:
    _set(db, KEY_THRESHOLD, repr(float(value)))


def feature_enabled(db: Session, feature: str) -> bool:
    return _get_bool(db, _feature_key(feature, "enabled"), True)


def set_feature_enabled(db: Session, feature: str, value: bool) -> None:
    _set_bool(db, _feature_key(feature, "enabled"), value)


def feature_model_override(db: Session, feature: str) -> str | None:
    raw = _get(db, _feature_key(feature, "model"))
    return raw or None


def set_feature_model(db: Session, feature: str, model: str | None) -> None:
    if model:
        _set(db, _feature_key(feature, "model"), model)
    else:
        _delete(db, _feature_key(feature, "model"))


def effective_model(db: Session, feature: str) -> str:
    f = registry.FEATURES[feature]
    return feature_model_override(db, feature) or f.default_model


# ── keys ─────────────────────────────────────────────────────────────────────

def mask(key: str | None) -> str | None:
    if not key:
        return None
    return "..." + key[-4:]


def get_db_instance_key(db: Session) -> str | None:
    return decrypt_secret(_get(db, KEY_INSTANCE_KEY))


def get_instance_key(db: Session) -> tuple[str | None, str | None]:
    """(plaintext key, source) where source is "db" | "env" | None."""
    db_key = get_db_instance_key(db)
    if db_key:
        return db_key, "db"
    env_key = (settings.anthropic_api_key or "").strip()
    if env_key:
        return env_key, "env"
    return None, None


def set_instance_key(db: Session, key: str | None) -> None:
    if key:
        _set(db, KEY_INSTANCE_KEY, encrypt_secret(key))
    else:
        _delete(db, KEY_INSTANCE_KEY)


def get_user_key(user: User) -> str | None:
    return decrypt_secret(user.ai_api_key)


def set_user_key(db: Session, user: User, key: str | None) -> None:
    if key:
        user.ai_api_key = encrypt_secret(key)
        user.ai_key_set_at = datetime.utcnow()
    else:
        user.ai_api_key = None
        user.ai_key_set_at = None


def can_use_instance_key(db: Session, user: User) -> bool:
    return is_admin(user) or share_instance_key(db)


@dataclass
class ResolvedKey:
    api_key: str
    source: str  # "user" | "instance"


def resolve_key(db: Session, user: User) -> ResolvedKey | None:
    if not is_member_or_above(user):
        return None
    own = get_user_key(user)
    if own:
        return ResolvedKey(own, "user")
    if can_use_instance_key(db, user):
        inst, _src = get_instance_key(db)
        if inst:
            return ResolvedKey(inst, "instance")
    return None


# ── status payload ───────────────────────────────────────────────────────────

def status_payload(db: Session, user: User) -> dict[str, Any]:
    """What GET /api/ai/status returns. Never contains a key."""
    enabled = ai_enabled(db)
    own = get_user_key(user) if is_member_or_above(user) else None
    inst_key, inst_src = get_instance_key(db)
    resolved = resolve_key(db, user)
    features: dict[str, Any] = {}
    for key, f in registry.FEATURES.items():
        features[key] = {
            "label": f.label,
            "description": f.description,
            "enabled": feature_enabled(db, key),
            "model": feature_model_override(db, key),
            "default_model": f.default_model,
        }
    payload: dict[str, Any] = {
        "enabled": enabled,
        "env_enabled": env_enabled(),
        "provider": registry.PROVIDER,
        "provider_label": registry.PROVIDER_LABEL,
        "can_use": is_member_or_above(user),
        "has_own_key": bool(own),
        "own_key_suffix": mask(own),
        "own_key_set_at": user.ai_key_set_at.isoformat() if own and user.ai_key_set_at else None,
        "instance_key_available": bool(inst_key) and can_use_instance_key(db, user) and is_member_or_above(user),
        "key_source": resolved.source if resolved else None,
        "confidence_threshold": confidence_threshold(db),
        "models": list(registry.MODELS),
        "features": features,
    }
    if is_admin(user):
        payload.update({
            "instance_enabled": instance_enabled(db),
            "instance_key_set": bool(inst_key),
            "instance_key_source": inst_src,
            "instance_key_suffix": mask(inst_key),
            "share_instance_key": share_instance_key(db),
        })
    return payload
