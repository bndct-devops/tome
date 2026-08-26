from __future__ import annotations

from datetime import datetime
from typing import List, TYPE_CHECKING
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.database import Base

if TYPE_CHECKING:
    from backend.models.wish import Wish
    from backend.models.notification import Notification
    from backend.models.api_token import ApiToken


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="guest")
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Auth provenance. "local" = username/password (default); "oidc" = provisioned
    # or linked via SSO. OIDC role-sync only ever touches "oidc" users, so a local
    # admin is always a break-glass login regardless of IdP state.
    auth_source: Mapped[str] = mapped_column(String(16), nullable=False, default="local")
    oidc_sub: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    oidc_issuer: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Hardcover sync (opt-in, per-user). The token is the user's personal
    # hardcover.app API token — it must be replayable against their GraphQL API,
    # so it is stored Fernet-encrypted with a key derived from TOME_SECRET_KEY
    # (see backend/core/crypto.py), never hashed. Independent of the server-wide
    # read-only TOME_HARDCOVER_TOKEN used for metadata fetch. Hardcover tokens
    # expire every January 1 — token_status flips to "expired" on a 401 and the
    # user is notified to re-link.
    hardcover_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    hardcover_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hardcover_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    hardcover_token_status: Mapped[str | None] = mapped_column(String(16), nullable=True)  # ok | expired | invalid
    hardcover_linked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Linking and syncing are separate opt-ins: a linked user can pause pushes.
    hardcover_sync_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Content restrictions (issue #190), admin-set. Lives on User, not the
    # deprecated UserPermission table. Admin accounts are never restricted.
    # excluded_tags: JSON list of tag names — books carrying any of them
    # (case-insensitive) are invisible to this user (see
    # backend/core/permissions.py:book_visibility_filter).
    # download_limit: NULL = unlimited, 0 = downloads disabled, N = at most
    # N files per UTC day, enforced on every download path (single / bulk /
    # OPDS / TomeSync) via backend/services/download_quota.py.
    excluded_tags: Mapped[str | None] = mapped_column(Text, nullable=True)
    download_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    permissions: Mapped["UserPermission"] = relationship(
        "UserPermission", back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    api_tokens: Mapped[List["ApiToken"]] = relationship(
        "ApiToken", back_populates="user", cascade="all, delete-orphan"
    )
    wishes: Mapped[List["Wish"]] = relationship(
        "Wish", foreign_keys="Wish.user_id", back_populates="user", cascade="all, delete-orphan"
    )
    notifications: Mapped[List["Notification"]] = relationship(
        "Notification", back_populates="user", cascade="all, delete-orphan"
    )

    @property
    def oidc_linked(self) -> bool:
        """True when an IdP identity is attached (SSO login resolves here)."""
        return self.oidc_sub is not None


class UserPermission(Base):
    __tablename__ = "user_permissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, unique=True)

    # Content permissions
    can_upload: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    can_download: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    can_edit_metadata: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    can_delete_books: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Library / organisation permissions
    can_manage_libraries: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    can_manage_tags: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    can_manage_series: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Admin permissions
    can_manage_users: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    can_approve_bindery: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    can_view_stats: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Feature access
    can_use_opds: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    can_use_kosync: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    can_share: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    can_bulk_operations: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    user: Mapped["User"] = relationship("User", back_populates="permissions")
