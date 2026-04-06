from datetime import datetime
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.database import Base


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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    permissions: Mapped["UserPermission"] = relationship(
        "UserPermission", back_populates="user", uselist=False, cascade="all, delete-orphan"
    )


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
