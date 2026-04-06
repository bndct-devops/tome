"""Tests for the bindery API endpoints.

Covers: GET /api/bindery/count, GET /api/bindery, POST /api/bindery/preview,
POST /api/bindery/accept, POST /api/bindery/reject, and permission checks.

All filesystem interactions use tmp_path so no real files are touched.
External API calls (fetch_candidates, extract_metadata, sha256_file) are
mocked to keep tests fast and isolated.
"""
import asyncio
from pathlib import Path

import pytest
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from backend.core.security import hash_password, create_access_token
from backend.models.user import User, UserPermission
from backend.models.library import BookType
from backend.services.metadata_fetch import FetchResult, MetadataCandidate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_non_admin(
    db: Session,
    *,
    username: str = "regularuser",
    can_approve_bindery: bool = False,
) -> tuple[User, str]:
    """Insert a plain (non-admin) user and return (user, jwt_token)."""
    user = User(
        username=username,
        email=f"{username}@example.com",
        hashed_password=hash_password("userpass123"),
        is_active=True,
        is_admin=False,
        must_change_password=False,
        role="member" if can_approve_bindery else "guest",
    )
    db.add(user)
    db.flush()

    perms = UserPermission(
        user_id=user.id,
        can_download=True,
        can_view_stats=True,
        can_use_opds=True,
        can_use_kosync=True,
        can_approve_bindery=can_approve_bindery,
    )
    db.add(perms)
    db.flush()

    token = create_access_token(subject=user.id)
    return user, token


# ---------------------------------------------------------------------------
# Shared fixture: bindery_dir + monkeypatched settings
# ---------------------------------------------------------------------------

@pytest.fixture()
def bindery_dir(tmp_path: Path, monkeypatch):
    """Create a temp bindery and library dir, patch settings, and create test files."""
    bindery_path = tmp_path / "bindery"
    library_path = tmp_path / "library"
    bindery_path.mkdir()
    library_path.mkdir()

    monkeypatch.setattr("backend.core.config.settings.incoming_dir", bindery_path)
    monkeypatch.setattr("backend.core.config.settings.library_dir", library_path)

    # Root-level file (no subdirectory)
    (bindery_path / "Faust v01.cbz").write_bytes(b"PK\x03\x04fake")

    # chapters/ sub-directory file
    chapters_dir = bindery_path / "chapters" / "Moby Dick"
    chapters_dir.mkdir(parents=True)
    (chapters_dir / "Moby Dick Chapter 1179 v1179.cbz").write_bytes(b"PK\x03\x04fake")

    # Subdirectory file
    dandadan_dir = bindery_path / "Beowulf"
    dandadan_dir.mkdir()
    (dandadan_dir / "Beowulf v18.cbz").write_bytes(b"PK\x03\x04fake")

    return bindery_path, library_path


# ---------------------------------------------------------------------------
# TestBinderyCount
# ---------------------------------------------------------------------------

class TestBinderyCount:
    def test_count_returns_supported_files(self, client: TestClient, bindery_dir):
        resp = client.get("/api/bindery/count")
        assert resp.status_code == 200
        assert resp.json()["count"] == 3

    def test_count_ignores_hidden_files(self, client: TestClient, bindery_dir):
        bindery_path, _ = bindery_dir
        (bindery_path / ".DS_Store").write_bytes(b"junk")
        hidden_dir = bindery_path / ".hidden"
        hidden_dir.mkdir()
        (hidden_dir / "secret.cbz").write_bytes(b"PK\x03\x04fake")

        resp = client.get("/api/bindery/count")
        assert resp.status_code == 200
        assert resp.json()["count"] == 3

    def test_count_ignores_unsupported_extensions(self, client: TestClient, bindery_dir):
        bindery_path, _ = bindery_dir
        (bindery_path / "readme.txt").write_text("ignore me")

        resp = client.get("/api/bindery/count")
        assert resp.status_code == 200
        assert resp.json()["count"] == 3


# ---------------------------------------------------------------------------
# TestBinderyList
# ---------------------------------------------------------------------------

class TestBinderyList:
    def test_list_returns_all_files_with_metadata(self, client: TestClient, bindery_dir):
        resp = client.get("/api/bindery")
        assert resp.status_code == 200
        items = resp.json()
        assert len(items) == 3
        for item in items:
            for field in ("path", "filename", "format", "content_type", "series", "series_index", "title", "folder"):
                assert field in item

    def test_list_detects_content_type(self, client: TestClient, bindery_dir):
        resp = client.get("/api/bindery")
        assert resp.status_code == 200
        items = resp.json()

        by_filename = {i["filename"]: i for i in items}

        # File inside chapters/ dir → chapter
        assert by_filename["Moby Dick Chapter 1179 v1179.cbz"]["content_type"] == "chapter"

        # Root-level volume → volume
        assert by_filename["Faust v01.cbz"]["content_type"] == "volume"

        # Subdirectory (non-chapters) volume → volume
        assert by_filename["Beowulf v18.cbz"]["content_type"] == "volume"

    def test_list_parses_series(self, client: TestClient, bindery_dir):
        resp = client.get("/api/bindery")
        assert resp.status_code == 200
        items = resp.json()
        by_filename = {i["filename"]: i for i in items}

        gantz = by_filename["Faust v01.cbz"]
        assert gantz["series"] == "Faust"
        assert gantz["series_index"] == 1.0

        one_piece = by_filename["Moby Dick Chapter 1179 v1179.cbz"]
        assert one_piece["series"] == "Moby Dick"
        assert one_piece["series_index"] == 1179.0

    def test_list_includes_folder(self, client: TestClient, bindery_dir):
        resp = client.get("/api/bindery")
        assert resp.status_code == 200
        items = resp.json()
        by_filename = {i["filename"]: i for i in items}

        assert by_filename["Beowulf v18.cbz"]["folder"] == "Beowulf"
        assert by_filename["Moby Dick Chapter 1179 v1179.cbz"]["folder"] == "Moby Dick"
        assert by_filename["Faust v01.cbz"]["folder"] is None

    def test_list_sorted_by_folder_then_filename(self, client: TestClient, bindery_dir):
        resp = client.get("/api/bindery")
        assert resp.status_code == 200
        items = resp.json()

        # Items with folders should appear before items without (folder=None sorts last)
        # folder=None items should be at the end
        folders = [i["folder"] for i in items]
        none_seen = False
        for folder in folders:
            if folder is None:
                none_seen = True
            else:
                # Once we've seen a None, there should be no more non-None
                assert not none_seen, "Ungrouped (None folder) items should come after grouped items"


# ---------------------------------------------------------------------------
# TestBinderyAccept
# ---------------------------------------------------------------------------

class TestBinderyAccept:
    def test_accept_creates_book_and_moves_file(
        self, client: TestClient, bindery_dir, db: Session, monkeypatch
    ):
        bindery_path, library_path = bindery_dir
        monkeypatch.setattr(
            "backend.api.bindery.extract_metadata",
            lambda *a, **kw: {"title": "Faust v1"},
        )
        monkeypatch.setattr(
            "backend.api.bindery.sha256_file",
            lambda *a: "fakehash123",
        )

        resp = client.post(
            "/api/bindery/accept",
            json={
                "files": [
                    {
                        "path": "Faust v01.cbz",
                        "title": "Faust",
                        "author": "Johann Goethe",
                        "series": "Faust",
                        "series_index": 1.0,
                        "content_type": "volume",
                    }
                ]
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["accepted"]) == 1
        assert body["errors"] == []

        accepted = body["accepted"][0]
        assert "book_id" in accepted
        assert accepted["title"] == "Faust"

        # Original file should be gone
        assert not (bindery_path / "Faust v01.cbz").exists()

        # Book record should exist in DB
        from backend.models.book import Book, BookFile
        book = db.get(Book, accepted["book_id"])
        assert book is not None
        assert book.title == "Faust"
        assert book.author == "Johann Goethe"
        assert book.series == "Faust"
        assert book.series_index == 1.0

        # BookFile should exist and point inside library_dir
        book_files = db.query(BookFile).filter(BookFile.book_id == book.id).all()
        assert len(book_files) == 1
        bf = book_files[0]
        assert bf.file_path.startswith(str(library_path))
        assert Path(bf.file_path).exists()

    def test_accept_with_tags(
        self, client: TestClient, bindery_dir, db: Session, monkeypatch
    ):
        monkeypatch.setattr(
            "backend.api.bindery.extract_metadata",
            lambda *a, **kw: {"title": "Faust v1"},
        )
        monkeypatch.setattr(
            "backend.api.bindery.sha256_file",
            lambda *a: "fakehash456",
        )

        resp = client.post(
            "/api/bindery/accept",
            json={
                "files": [
                    {
                        "path": "Faust v01.cbz",
                        "title": "Faust",
                        "tags": ["manga", "seinen"],
                    }
                ]
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["accepted"]) == 1

        from backend.models.book import BookTag
        book_id = body["accepted"][0]["book_id"]
        tags = db.query(BookTag).filter(BookTag.book_id == book_id).all()
        tag_names = {t.tag for t in tags}
        assert "manga" in tag_names
        assert "seinen" in tag_names

    def test_accept_with_book_type_id(
        self, client: TestClient, bindery_dir, db: Session, monkeypatch
    ):
        monkeypatch.setattr(
            "backend.api.bindery.extract_metadata",
            lambda *a, **kw: {"title": "Faust v1"},
        )
        monkeypatch.setattr(
            "backend.api.bindery.sha256_file",
            lambda *a: "fakehash789",
        )

        # Seed a BookType
        bt = BookType(slug="manga", label="Manga", icon="BookOpen", color="blue", sort_order=0)
        db.add(bt)
        db.flush()

        resp = client.post(
            "/api/bindery/accept",
            json={
                "files": [
                    {
                        "path": "Faust v01.cbz",
                        "title": "Faust",
                        "book_type_id": bt.id,
                    }
                ]
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["accepted"]) == 1

        from backend.models.book import Book
        book_id = body["accepted"][0]["book_id"]
        book = db.get(Book, book_id)
        assert book.book_type_id == bt.id

    def test_accept_nonexistent_file_returns_error(
        self, client: TestClient, bindery_dir, monkeypatch
    ):
        monkeypatch.setattr(
            "backend.api.bindery.extract_metadata",
            lambda *a, **kw: {},
        )
        monkeypatch.setattr(
            "backend.api.bindery.sha256_file",
            lambda *a: "fakehash",
        )

        resp = client.post(
            "/api/bindery/accept",
            json={
                "files": [
                    {
                        "path": "does_not_exist.cbz",
                        "title": "Missing File",
                    }
                ]
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["accepted"] == []
        assert len(body["errors"]) == 1
        assert body["errors"][0]["path"] == "does_not_exist.cbz"

    def test_accept_cleans_up_empty_dirs(
        self, client: TestClient, bindery_dir, monkeypatch
    ):
        bindery_path, _ = bindery_dir
        monkeypatch.setattr(
            "backend.api.bindery.extract_metadata",
            lambda *a, **kw: {"title": "Beowulf v18"},
        )
        monkeypatch.setattr(
            "backend.api.bindery.sha256_file",
            lambda *a: "fakehashdd",
        )

        resp = client.post(
            "/api/bindery/accept",
            json={
                "files": [
                    {
                        "path": "Beowulf/Beowulf v18.cbz",
                        "title": "Beowulf",
                        "series": "Beowulf",
                        "series_index": 18.0,
                    }
                ]
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["accepted"]) == 1

        # Beowulf/ directory should be gone (was empty after file moved)
        assert not (bindery_path / "Beowulf").exists()

        # chapters/ should still exist (it's protected)
        assert (bindery_path / "chapters").exists()

    def test_accept_multiple_files(
        self, client: TestClient, bindery_dir, monkeypatch
    ):
        bindery_path, _ = bindery_dir
        call_count = [0]

        def mock_extract(*a, **kw):
            call_count[0] += 1
            return {"title": f"Book {call_count[0]}"}

        hash_count = [0]

        def mock_hash(*a):
            hash_count[0] += 1
            return f"fakehash{hash_count[0]}"

        monkeypatch.setattr("backend.api.bindery.extract_metadata", mock_extract)
        monkeypatch.setattr("backend.api.bindery.sha256_file", mock_hash)

        resp = client.post(
            "/api/bindery/accept",
            json={
                "files": [
                    {
                        "path": "Faust v01.cbz",
                        "title": "Faust",
                    },
                    {
                        "path": "Beowulf/Beowulf v18.cbz",
                        "title": "Beowulf",
                    },
                ]
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["accepted"]) == 2
        assert body["errors"] == []

        # Both original files should be gone
        assert not (bindery_path / "Faust v01.cbz").exists()
        assert not (bindery_path / "Beowulf" / "Beowulf v18.cbz").exists()


# ---------------------------------------------------------------------------
# TestBinderyReject
# ---------------------------------------------------------------------------

class TestBinderyReject:
    def test_reject_deletes_file(self, client: TestClient, bindery_dir):
        bindery_path, _ = bindery_dir

        resp = client.post(
            "/api/bindery/reject",
            json={"paths": ["Faust v01.cbz"]},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["rejected"] == 1
        assert body["errors"] == []

        assert not (bindery_path / "Faust v01.cbz").exists()

    def test_reject_nonexistent_file(self, client: TestClient, bindery_dir):
        resp = client.post(
            "/api/bindery/reject",
            json={"paths": ["ghost.cbz"]},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["rejected"] == 0
        assert len(body["errors"]) == 1

    def test_reject_cleans_up_empty_dirs(self, client: TestClient, bindery_dir):
        bindery_path, _ = bindery_dir

        resp = client.post(
            "/api/bindery/reject",
            json={"paths": ["Beowulf/Beowulf v18.cbz"]},
        )
        assert resp.status_code == 200
        assert resp.json()["rejected"] == 1

        # Empty Beowulf/ directory should be removed
        assert not (bindery_path / "Beowulf").exists()


# ---------------------------------------------------------------------------
# TestBinderySecurity
# ---------------------------------------------------------------------------

class TestBinderySecurity:
    def test_path_traversal_rejected(self, client: TestClient, bindery_dir):
        resp = client.post(
            "/api/bindery/reject",
            json={"paths": ["../../etc/passwd"]},
        )
        assert resp.status_code == 400

    def test_path_traversal_rejected_accept(self, client: TestClient, bindery_dir):
        resp = client.post(
            "/api/bindery/accept",
            json={
                "files": [
                    {
                        "path": "../../etc/passwd",
                        "title": "Evil",
                    }
                ]
            },
        )
        assert resp.status_code == 400

    def test_non_admin_without_permission_gets_403(
        self, client: TestClient, db: Session, bindery_dir
    ):
        _user, token = _make_non_admin(db, username="noperm", can_approve_bindery=False)

        resp = client.get(
            "/api/bindery/count",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403

    def test_non_admin_with_permission_succeeds(
        self, client: TestClient, db: Session, bindery_dir
    ):
        _user, token = _make_non_admin(db, username="hasperm", can_approve_bindery=True)

        resp = client.get(
            "/api/bindery/count",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# TestBinderyPreview
# ---------------------------------------------------------------------------

class TestBinderyPreview:
    def test_preview_returns_file_metadata_and_candidates(
        self, client: TestClient, bindery_dir, monkeypatch
    ):
        monkeypatch.setattr(
            "backend.api.bindery.extract_metadata",
            lambda *a, **kw: {"title": "Faust", "author": "Johann Goethe"},
        )

        candidates = [
            MetadataCandidate(
                source="google_books",
                source_id="abc123",
                title="Faust Vol 1",
                author="Johann Goethe",
            ),
            MetadataCandidate(
                source="open_library",
                source_id="OL123W",
                title="Faust Vol 1",
                author="Johann Goethe",
            ),
        ]

        async def mock_fetch(**kwargs):
            return FetchResult(candidates=candidates, query_used="Faust 1")

        monkeypatch.setattr("backend.api.bindery.fetch_candidates", mock_fetch)

        resp = client.post(
            "/api/bindery/preview",
            json={"path": "Faust v01.cbz"},
        )
        assert resp.status_code == 200
        body = resp.json()

        assert "file_metadata" in body
        assert "candidates" in body
        assert "query_used" in body

        assert len(body["candidates"]) == 2
        assert body["query_used"] == "Faust 1"
        assert body["file_metadata"]["title"] == "Faust"

    def test_preview_passes_query_override(
        self, client: TestClient, bindery_dir, monkeypatch
    ):
        monkeypatch.setattr(
            "backend.api.bindery.extract_metadata",
            lambda *a, **kw: {"title": "Faust"},
        )

        received_kwargs: dict = {}

        async def mock_fetch(**kwargs):
            received_kwargs.update(kwargs)
            return FetchResult(candidates=[], query_used=kwargs.get("query_override", ""))

        monkeypatch.setattr("backend.api.bindery.fetch_candidates", mock_fetch)

        resp = client.post(
            "/api/bindery/preview",
            json={"path": "Faust v01.cbz", "query": "custom search"},
        )
        assert resp.status_code == 200
        assert received_kwargs.get("query_override") == "custom search"

    def test_preview_nonexistent_file_returns_404(
        self, client: TestClient, bindery_dir, monkeypatch
    ):
        monkeypatch.setattr(
            "backend.api.bindery.extract_metadata",
            lambda *a, **kw: {},
        )

        async def mock_fetch(**kwargs):
            return FetchResult(candidates=[], query_used="")

        monkeypatch.setattr("backend.api.bindery.fetch_candidates", mock_fetch)

        resp = client.post(
            "/api/bindery/preview",
            json={"path": "not_a_real_file.cbz"},
        )
        assert resp.status_code == 404
