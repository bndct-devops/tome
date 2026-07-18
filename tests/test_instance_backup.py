"""Instance backup/restore service — snapshot, validate, staged apply."""
import sqlite3
import tarfile
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from backend.services.instance_backup import (
    apply_staged_restore_if_present,
    create_backup_tarball,
    staged_path,
    validate_backup,
)


def _mini_db(path: Path, users: int = 2, books: int = 3) -> None:
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE users (id INTEGER PRIMARY KEY)")
    con.execute("CREATE TABLE books (id INTEGER PRIMARY KEY)")
    con.executemany("INSERT INTO users VALUES (?)", [(i,) for i in range(users)])
    con.executemany("INSERT INTO books VALUES (?)", [(i,) for i in range(books)])
    con.commit()
    con.close()


def test_backup_roundtrip(tmp_path: Path):
    db = tmp_path / "tome.db"
    _mini_db(db)
    covers = tmp_path / "covers"
    covers.mkdir()
    (covers / "1.jpg").write_bytes(b"fakejpg")

    tarball = create_backup_tarball(db, covers, "9.9.9")
    try:
        summary = validate_backup(tarball)
        assert summary["version"] == "9.9.9"
        assert summary["users"] == 2
        assert summary["books"] == 3
        with tarfile.open(tarball) as tar:
            assert "covers/1.jpg" in tar.getnames()
    finally:
        tarball.unlink(missing_ok=True)


def test_validate_rejects_garbage(tmp_path: Path):
    bad = tmp_path / "junk.tar.gz"
    bad.write_bytes(b"this is not a tarball")
    with pytest.raises(ValueError):
        validate_backup(bad)


def test_staged_apply_swaps_db_and_keeps_safety_copy(tmp_path: Path):
    data = tmp_path
    db = data / "tome.db"
    covers = data / "covers"
    covers.mkdir()
    _mini_db(db, users=1, books=1)          # the "current" instance

    other = tmp_path / "other.db"
    _mini_db(other, users=5, books=9)       # the backup being restored
    other_covers = tmp_path / "other-covers"
    other_covers.mkdir()
    (other_covers / "9.jpg").write_bytes(b"x")
    tarball = create_backup_tarball(other, other_covers, "1.2.3")
    tarball.rename(staged_path(data))

    applied = apply_staged_restore_if_present(data, db, covers)
    assert applied is True
    con = sqlite3.connect(str(db))
    assert con.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 5
    con.close()
    assert (covers / "9.jpg").is_file()
    assert not staged_path(data).exists()
    assert list(data.glob("tome.db.pre-restore-*")), "safety copy missing"


def test_staged_apply_failure_keeps_current_db(tmp_path: Path):
    data = tmp_path
    db = data / "tome.db"
    _mini_db(db, users=1)
    staged_path(data).write_bytes(b"corrupt garbage")

    applied = apply_staged_restore_if_present(data, db, data / "covers")
    assert applied is False
    con = sqlite3.connect(str(db))
    assert con.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1  # untouched
    con.close()
    assert staged_path(data).with_suffix(".failed").exists()


def test_no_staging_is_noop(tmp_path: Path):
    db = tmp_path / "tome.db"
    _mini_db(db)
    assert apply_staged_restore_if_present(tmp_path, db, tmp_path / "covers") is False


def test_restore_endpoints_admin_only_and_confirm(client: TestClient, db, tmp_path):
    from backend.core.security import create_access_token, hash_password
    from backend.models.user import User, UserPermission

    member = User(username="bk_member", email="bk_member@example.com",
                  hashed_password=hash_password("pass1234"), is_active=True,
                  is_admin=False, role="member", must_change_password=False)
    db.add(member)
    db.flush()
    db.add(UserPermission(user_id=member.id))
    db.flush()
    token = create_access_token(subject=member.id)
    r = client.get("/api/admin/backup/restore", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403

    # Admin without the confirm phrase is rejected before any file handling.
    r = client.post("/api/admin/backup/restore",
                    files={"file": ("b.tar.gz", b"x", "application/gzip")},
                    data={"confirm": "yes please"})
    assert r.status_code == 422
