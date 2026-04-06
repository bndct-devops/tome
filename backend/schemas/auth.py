from datetime import datetime
from typing import Optional
from pydantic import BaseModel, field_validator
import re


class LoginRequest(BaseModel):
    username: str  # username or email
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class SetupRequest(BaseModel):
    username: str
    email: str
    password: str

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        if not re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", v):
            raise ValueError("Invalid email address")
        return v.lower()


class PermissionsOut(BaseModel):
    can_upload: bool
    can_download: bool
    can_edit_metadata: bool
    can_delete_books: bool
    can_manage_libraries: bool
    can_manage_tags: bool
    can_manage_series: bool
    can_manage_users: bool
    can_approve_bindery: bool
    can_view_stats: bool
    can_use_opds: bool
    can_use_kosync: bool
    can_share: bool
    can_bulk_operations: bool

    class Config:
        from_attributes = True


class UserOut(BaseModel):
    id: int
    username: str
    email: str
    is_active: bool
    is_admin: bool
    role: str = "guest"
    must_change_password: bool = False
    created_at: datetime
    permissions: Optional[PermissionsOut] = None

    class Config:
        from_attributes = True
