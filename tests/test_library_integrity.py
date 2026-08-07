"""Tests for the library-integrity cleanup surfaces (issue #165).

Covers:
- POST /books/bulk-delete (server-side bulk delete with per-book permissions)
- GET /books/library-health missing-file detection
- POST /books/remove-missing (drop dead BookFile rows / empty books)
- Orphan-aware duplicate merge (dead file rows are dropped, not reassigned)
- path_exists flag on duplicate group files
"""
from pathlib import Path

from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from backend.core.security import hash_password, create_access_token
from backend.models.book import Book, BookFile
from backend.models.audit_log import AuditLog
from backend.models.user import User


def _make_user(db: Session, username: str, role: str, is_admin: bool = False) -> tuple[User, str]:
    user = User(
        username=username,
        email=f"{username}@example.com",
        hashed_password=hash_password("password123"),
        is_active=True,
        is_admin=is_admin,
        role=role,
        must_change_password=False,
    )
    db.add(user)
    db.flush()
    return user, create_access_token(subject=user.id)


def _hdr(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _real_file(tmp_path: Path, name: str) -> str:
    p = tmp_path / name
    p.write_bytes(b"ebook bytes")
    return str(p)


# ── Bulk delete ───────────────────────────────────────────────────────────────

def test_bulk_delete_admin(client: TestClient, make_book):
    a = make_book(title="Bulk A")
    b = make_book(title="Bulk B")

    resp = client.post("/api/books/bulk-delete", json={"book_ids": [a.id, b.id, 999999]})
    assert resp.status_code == 200
    body = resp.json()
    assert set(body["deleted"]) == {a.id, b.id}
    assert body["errors"] == [{"book_id": 999999, "error": "Book not found"}]

    assert client.get(f"/api/books/{a.id}").status_code == 404
    assert client.get(f"/api/books/{b.id}").status_code == 404


def test_bulk_delete_member_only_own_uploads(client: TestClient, db: Session, make_book):
    member, m_token = _make_user(db, "bulkmember", "member")

    admins_book = make_book(title="Admin Owned")
    own = Book(title="Member Owned", status="active", added_by=member.id)
    db.add(own)
    db.flush()
    db.add(BookFile(book_id=own.id, file_path=f"/library/{own.id}/own.epub",
                    format="epub", file_size=1024))
    db.flush()

    resp = client.post(
        "/api/books/bulk-delete",
        json={"book_ids": [own.id, admins_book.id]},
        headers=_hdr(m_token),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["deleted"] == [own.id]
    assert body["errors"] == [
        {"book_id": admins_book.id, "error": "You can only delete books you uploaded"}
    ]
    # The admin's book survived
    assert client.get(f"/api/books/{admins_book.id}").status_code == 200


def test_bulk_delete_empty_request_rejected(client: TestClient):
    resp = client.post("/api/books/bulk-delete", json={"book_ids": []})
    assert resp.status_code == 400


def test_bulk_delete_over_cap_rejected(client: TestClient):
    resp = client.post("/api/books/bulk-delete", json={"book_ids": list(range(1, 502))})
    assert resp.status_code == 400
    assert "500" in resp.json()["detail"]


def test_bulk_delete_dedupes_repeated_ids(client: TestClient, make_book):
    book = make_book(title="Dedupe Me")
    resp = client.post("/api/books/bulk-delete", json={"book_ids": [book.id, book.id, book.id]})
    assert resp.status_code == 200
    body = resp.json()
    # One deletion, no phantom "not found" errors from the repeats
    assert body["deleted"] == [book.id]
    assert body["errors"] == []


def test_bulk_delete_removes_files_on_disk(client: TestClient, make_book, tmp_path):
    path = _real_file(tmp_path, "on_disk.epub")
    book = make_book(title="On Disk", file_path=path)

    resp = client.post("/api/books/bulk-delete", json={"book_ids": [book.id]})
    assert resp.status_code == 200
    assert resp.json()["deleted"] == [book.id]
    assert not Path(path).exists()


def test_bulk_delete_audited_per_book(client: TestClient, db: Session, make_book):
    a = make_book(title="Audit A")
    b = make_book(title="Audit B")

    client.post("/api/books/bulk-delete", json={"book_ids": [a.id, b.id]})

    deleted_ids = {
        r.resource_id
        for r in db.query(AuditLog).filter(AuditLog.action == "books.deleted").all()
    }
    assert {a.id, b.id} <= deleted_ids


# ── Library health: missing files ─────────────────────────────────────────────

def test_library_health_reports_missing_files(client: TestClient, make_book, tmp_path):
    dead = make_book(title="Ghost Book")  # default path never exists on disk
    alive = make_book(title="Alive Book", file_path=_real_file(tmp_path, "alive.epub"))

    resp = client.get("/api/books/library-health")
    assert resp.status_code == 200
    body = resp.json()

    missing_book_ids = {m["book_id"] for m in body["missing"]}
    assert dead.id in missing_book_ids
    assert alive.id not in missing_book_ids
    assert body["missing_count"] == len(body["missing"])

    entry = next(m for m in body["missing"] if m["book_id"] == dead.id)
    assert entry["title"] == "Ghost Book"
    assert entry["book_file_count"] == 1
    # A missing file must not additionally show up as misplaced
    assert dead.id not in {i["book_id"] for i in body["issues"]}


def test_remove_missing_deletes_dead_rows_and_empty_books(
    client: TestClient, db: Session, make_book, tmp_path
):
    # Book whose only file is dead — the whole record should go
    ghost = make_book(title="Full Ghost")
    ghost_file_id = ghost.files[0].id

    # Book with one dead and one real file — only the dead row should go
    partial = make_book(title="Partial", file_path=_real_file(tmp_path, "partial.epub"))
    dead_row = BookFile(book_id=partial.id, file_path=f"/library/{partial.id}/gone.cbz",
                        format="cbz", file_size=2048)
    db.add(dead_row)
    db.flush()

    resp = client.post(
        "/api/books/remove-missing",
        json={"file_ids": [ghost_file_id, dead_row.id]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["removed_file_rows"] == 1
    assert body["removed_books"] == [{"book_id": ghost.id, "title": "Full Ghost"}]
    assert body["skipped"] == []

    assert client.get(f"/api/books/{ghost.id}").status_code == 404
    partial_resp = client.get(f"/api/books/{partial.id}")
    assert partial_resp.status_code == 200
    assert len(partial_resp.json()["files"]) == 1


def test_remove_missing_refuses_files_that_exist(
    client: TestClient, make_book, tmp_path
):
    path = _real_file(tmp_path, "still_here.epub")
    book = make_book(title="Still Here", file_path=path)
    file_id = book.files[0].id

    resp = client.post("/api/books/remove-missing", json={"file_ids": [file_id]})
    assert resp.status_code == 200
    body = resp.json()
    assert body["removed_file_rows"] == 0
    assert body["removed_books"] == []
    assert body["skipped"] == [{"file_id": file_id, "error": "file exists on disk"}]
    assert Path(path).exists()
    assert client.get(f"/api/books/{book.id}").status_code == 200


def test_remove_missing_empty_request_rejected(client: TestClient):
    resp = client.post("/api/books/remove-missing", json={"file_ids": []})
    assert resp.status_code == 400


def test_remove_missing_audited(client: TestClient, db: Session, make_book):
    ghost = make_book(title="Audited Ghost")
    resp = client.post("/api/books/remove-missing", json={"file_ids": [ghost.files[0].id]})
    assert resp.status_code == 200

    row = (
        db.query(AuditLog)
        .filter(AuditLog.action == "books.missing_removed")
        .order_by(AuditLog.id.desc())
        .first()
    )
    assert row is not None
    import json
    assert json.loads(row.details) == {"file_rows": 0, "books_removed": 1}


def test_remove_missing_requires_admin(client: TestClient, db: Session, make_book):
    _member, m_token = _make_user(db, "rmmember", "member")
    book = make_book(title="Member Cannot Remove")
    resp = client.post(
        "/api/books/remove-missing",
        json={"file_ids": [book.files[0].id]},
        headers=_hdr(m_token),
    )
    assert resp.status_code == 403


# ── /books/ids (select-all across the whole filtered set) ────────────────────

def test_book_ids_respects_filters(client: TestClient, make_book):
    a = make_book(title="Ids Series A1", series="Ids Series", series_index=1.0)
    b = make_book(title="Ids Series A2", series="Ids Series", series_index=2.0)
    other = make_book(title="Ids Other", series="Different Series")

    resp = client.get("/api/books/ids", params={"series": "Ids Series"})
    assert resp.status_code == 200
    ids = resp.json()["ids"]
    assert set(ids) == {a.id, b.id}
    assert other.id not in ids


def test_book_ids_respects_visibility(client: TestClient, db: Session, make_book):
    """A member's own unfiled upload must not leak into another member's ids."""
    member1, _ = _make_user(db, "idsmember1", "member")
    _member2, m2_token = _make_user(db, "idsmember2", "member")

    admin_book = make_book(title="Ids Admin Book")  # unfiled admin upload → public
    private = Book(title="Ids Private Upload", status="active", added_by=member1.id)
    db.add(private)
    db.flush()
    db.add(BookFile(book_id=private.id, file_path=f"/library/{private.id}/p.epub",
                    format="epub", file_size=1024))
    db.flush()

    resp = client.get("/api/books/ids", headers=_hdr(m2_token))
    assert resp.status_code == 200
    ids = set(resp.json()["ids"])
    assert admin_book.id in ids
    assert private.id not in ids


# ── Orphan-aware merge ────────────────────────────────────────────────────────

def test_merge_drops_dead_file_rows(client: TestClient, make_book, tmp_path):
    keep = make_book(title="Keeper", file_path=_real_file(tmp_path, "keeper.epub"),
                     content_hash="feedface" * 8)
    remove = make_book(title="Dead Copy", content_hash="feedface" * 8)

    resp = client.post(
        "/api/admin/duplicates/merge",
        json={"keep_id": keep.id, "remove_ids": [remove.id]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["merged"] == 1
    assert body["dropped_missing_files"] == 1

    kept = client.get(f"/api/books/{keep.id}").json()
    assert len(kept["files"]) == 1  # the dead row was NOT carried over


def test_merge_still_transfers_real_files(client: TestClient, make_book, tmp_path):
    keep = make_book(title="Keeper 2", file_path=_real_file(tmp_path, "keeper2.epub"),
                     content_hash="cafebabe" * 8)
    remove = make_book(title="Real Copy", file_path=_real_file(tmp_path, "copy2.epub"),
                       content_hash="cafebabe" * 8)

    resp = client.post(
        "/api/admin/duplicates/merge",
        json={"keep_id": keep.id, "remove_ids": [remove.id]},
    )
    assert resp.status_code == 200
    assert resp.json()["dropped_missing_files"] == 0

    kept = client.get(f"/api/books/{keep.id}").json()
    assert len(kept["files"]) == 2


# ── path_exists flag on duplicate groups ──────────────────────────────────────

def test_duplicates_expose_path_exists(client: TestClient, make_book, tmp_path):
    alive = make_book(title="Alive Twin", file_path=_real_file(tmp_path, "twin.epub"),
                      content_hash="0badf00d" * 8)
    dead = make_book(title="Dead Twin", content_hash="0badf00d" * 8)

    resp = client.get("/api/admin/duplicates")
    assert resp.status_code == 200

    group = next(
        g for g in resp.json()["groups"]
        if g["match_reason"] == "content_hash"
        and {b["id"] for b in g["books"]} == {alive.id, dead.id}
    )
    by_id = {b["id"]: b for b in group["books"]}
    assert by_id[alive.id]["files"][0]["path_exists"] is True
    assert by_id[dead.id]["files"][0]["path_exists"] is False
