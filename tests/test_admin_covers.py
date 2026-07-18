"""Tests for GET /api/admin/covers/audit."""
from PIL import Image
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from backend.core.config import settings
from backend.core.security import create_access_token, hash_password
from backend.models.user import User, UserPermission


def _write_cover(name: str, width: int, height: int) -> str:
    settings.covers_dir.mkdir(parents=True, exist_ok=True)
    path = settings.covers_dir / name
    Image.new("RGB", (width, height), "white").save(path, "JPEG")
    return name


def test_audit_flags_missing_and_low_res(client: TestClient, db: Session, make_book):
    good = make_book(title="Good Cover")
    good.cover_path = _write_cover("good.jpg", 600, 900)
    small = make_book(title="Tiny Cover")
    small.cover_path = _write_cover("small.jpg", 128, 190)
    bare = make_book(title="No Cover")
    bare.cover_path = None
    ghost = make_book(title="Ghost Cover")
    ghost.cover_path = "does-not-exist.jpg"
    db.flush()

    r = client.get("/api/admin/covers/audit")
    assert r.status_code == 200
    data = r.json()
    flagged = {b["title"]: b for b in data["books"]}
    assert "Good Cover" not in flagged
    assert flagged["Tiny Cover"]["reason"] == "low_res"
    assert flagged["Tiny Cover"]["width"] == 128
    assert flagged["No Cover"]["reason"] == "missing"
    assert flagged["Ghost Cover"]["reason"] == "missing"
    assert data["scanned"] >= 4


def test_audit_admin_only(client: TestClient, db: Session):
    member = User(username="cov_member", email="cov_member@example.com",
                  hashed_password=hash_password("pass1234"), is_active=True,
                  is_admin=False, role="member", must_change_password=False)
    db.add(member)
    db.flush()
    db.add(UserPermission(user_id=member.id))
    db.flush()
    token = create_access_token(subject=member.id)
    r = client.get("/api/admin/covers/audit", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403
