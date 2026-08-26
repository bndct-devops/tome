"""Per-user content restrictions: excluded tags + download limits (GH #190).

Feature A — excluded tags: a non-admin user with excluded tags must never see
(or download) a book carrying any of them, case-insensitively, across every
surface that routes through ``book_visibility_filter`` (books list, single
book, OPDS). The restriction is account-wide: it hides even the user's own
uploads.

Feature B — download limits: ``User.download_limit`` (NULL = unlimited,
0 = disabled, N = files per UTC day) enforced on all four download paths —
single file, bulk ZIP, OPDS, and TomeSync (the path the rejected external PR
missed). Admins are never limited and never counted. The deprecated
``UserPermission.can_download`` flag stays unenforced on purpose.
"""
import json

import pytest
from fastapi import HTTPException

from backend.core.permissions import book_visibility_filter, excluded_tags_for
from backend.core.security import get_current_user, get_current_user_basic, hash_password
from backend.models.book import Book
from backend.models.download_event import DownloadEvent
from backend.models.user import User
from backend.services.download_quota import (
    downloads_used_today,
    enforce_download_limit,
    record_download,
)

_counter = 0


def _make_member(db, *, excluded_tags=None, download_limit=None) -> User:
    global _counter
    _counter += 1
    u = User(
        username=f"member{_counter}",
        email=f"member{_counter}@example.com",
        hashed_password=hash_password("password123"),
        is_active=True,
        is_admin=False,
        role="member",
        must_change_password=False,
        excluded_tags=json.dumps(excluded_tags) if excluded_tags else None,
        download_limit=download_limit,
    )
    db.add(u)
    db.flush()
    return u


def _as_user(client, user: User):
    """Make every JWT- and Basic-authed endpoint act as `user`."""
    client.app.dependency_overrides[get_current_user] = lambda: user
    client.app.dependency_overrides[get_current_user_basic] = lambda: user


def _as_tomesync_user(client, user: User):
    from backend.api.tome_sync import _get_api_key_user
    client.app.dependency_overrides[_get_api_key_user] = lambda: user


@pytest.fixture(autouse=True)
def _clear_overrides(client):
    yield
    from backend.api.tome_sync import _get_api_key_user
    client.app.dependency_overrides.pop(get_current_user, None)
    client.app.dependency_overrides.pop(get_current_user_basic, None)
    client.app.dependency_overrides.pop(_get_api_key_user, None)


# ── Feature A: excluded tags ─────────────────────────────────────────────────

def test_excluded_tags_parsing_and_admin_exemption(db, admin_user):
    u = _make_member(db, excluded_tags=[" Adult ", "성인", "", "adult"])
    assert excluded_tags_for(u) == ["adult", "성인", "adult"] or set(excluded_tags_for(u)) == {"adult", "성인"}
    admin, _ = admin_user
    admin.excluded_tags = json.dumps(["whatever"])
    assert excluded_tags_for(admin) == []
    # Corrupt JSON never breaks the filter
    u.excluded_tags = "not json"
    assert excluded_tags_for(u) == []


def test_visibility_filter_hides_excluded_tag(db, make_book):
    clean = make_book(title="Clean Book", tags=["fantasy"])
    adult = make_book(title="Adult Book", tags=["성인"])
    u = _make_member(db, excluded_tags=["성인"])
    ids = {b.id for b in db.query(Book).filter(book_visibility_filter(db, u)).all()}
    assert clean.id in ids
    assert adult.id not in ids


def test_tag_matching_is_case_insensitive(db, make_book):
    tagged = make_book(title="Tagged Book", tags=["adult"])
    u = _make_member(db, excluded_tags=["Adult"])
    ids = {b.id for b in db.query(Book).filter(book_visibility_filter(db, u)).all()}
    assert tagged.id not in ids


def test_restriction_hides_even_own_upload(db, make_book):
    u = _make_member(db, excluded_tags=["mature"])
    own = make_book(title="Own Upload", tags=["mature"])
    own.added_by = u.id
    db.flush()
    ids = {b.id for b in db.query(Book).filter(book_visibility_filter(db, u)).all()}
    assert own.id not in ids


def test_admin_still_sees_excluded_books(db, make_book, admin_user):
    adult = make_book(title="Adult Book", tags=["성인"])
    admin, _ = admin_user
    filt = book_visibility_filter(db, admin)
    ids = {b.id for b in db.query(Book).filter(filt).all()}
    assert adult.id in ids


def test_books_list_and_single_book_endpoints_hide_excluded(client, db, make_book):
    make_book(title="Visible Book", tags=["fantasy"])
    hidden = make_book(title="Hidden Book", tags=["성인"])
    u = _make_member(db, excluded_tags=["성인"])
    _as_user(client, u)

    resp = client.get("/api/books")
    assert resp.status_code == 200
    data = resp.json()
    titles = [b["title"] for b in (data["books"] if isinstance(data, dict) else data)]
    assert "Visible Book" in titles
    assert "Hidden Book" not in titles

    assert client.get(f"/api/books/{hidden.id}").status_code == 404


def test_opds_search_hides_excluded(client, db, make_book):
    make_book(title="Findable Clean", tags=["fantasy"])
    make_book(title="Findable Hidden", tags=["성인"])
    u = _make_member(db, excluded_tags=["성인"])
    _as_user(client, u)

    resp = client.get("/opds/search/results?q=Findable")
    assert resp.status_code == 200
    assert "Findable Clean" in resp.text
    assert "Findable Hidden" not in resp.text


# ── Feature B: download limits (service level) ───────────────────────────────

def test_unlimited_by_default_and_uncounted(db):
    u = _make_member(db)  # download_limit = None
    enforce_download_limit(db, u, count=10_000)  # no raise
    record_download(db, u, 1)  # no-op while unlimited
    assert downloads_used_today(db, u.id) == 0


def test_limit_zero_disables_downloads(db):
    u = _make_member(db, download_limit=0)
    with pytest.raises(HTTPException) as exc:
        enforce_download_limit(db, u)
    assert exc.value.status_code == 403


def test_daily_limit_counts_and_blocks(db):
    u = _make_member(db, download_limit=2)
    enforce_download_limit(db, u)
    record_download(db, u, 1)
    record_download(db, u, 2)
    assert downloads_used_today(db, u.id) == 2
    with pytest.raises(HTTPException) as exc:
        enforce_download_limit(db, u)  # would be the 3rd today
    assert exc.value.status_code == 403


def test_bulk_count_checked_against_remaining_quota(db):
    u = _make_member(db, download_limit=3)
    record_download(db, u, 1)
    enforce_download_limit(db, u, count=2)  # 1 + 2 = 3 ≤ 3
    with pytest.raises(HTTPException):
        enforce_download_limit(db, u, count=3)  # 1 + 3 = 4 > 3


def test_admin_never_limited_or_counted(db, admin_user):
    admin, _ = admin_user
    admin.download_limit = 0  # even a nonsense value on an admin row is ignored
    enforce_download_limit(db, admin, count=500)
    record_download(db, admin, 1)
    assert downloads_used_today(db, admin.id) == 0


# ── Feature B: all four download paths enforce ───────────────────────────────

def test_single_download_endpoint_blocked(client, db, make_book):
    book = make_book(title="Blocked Single")
    u = _make_member(db, download_limit=0)
    _as_user(client, u)
    resp = client.get(f"/api/books/{book.id}/download/{book.files[0].id}")
    assert resp.status_code == 403
    assert "disabled" in resp.json()["detail"]


def test_bulk_download_endpoint_blocked(client, db, make_book, tmp_path):
    p = tmp_path / "bulk.epub"
    p.write_bytes(b"not a real epub")
    book = make_book(title="Blocked Bulk", file_path=str(p))
    u = _make_member(db, download_limit=0)
    _as_user(client, u)
    resp = client.post("/api/downloads", json={"book_ids": [book.id]})
    assert resp.status_code == 403


def test_opds_download_endpoint_blocked(client, db, make_book, tmp_path):
    p = tmp_path / "opds.epub"
    p.write_bytes(b"not a real epub")
    book = make_book(title="Blocked OPDS", file_path=str(p))
    u = _make_member(db, download_limit=0)
    _as_user(client, u)
    resp = client.get(f"/opds/download/{book.id}/{book.files[0].id}")
    assert resp.status_code == 403


def test_tomesync_download_endpoint_blocked(client, db, make_book):
    """The path the rejected external PR (#195) forgot — KOReader plugin
    downloads must respect the limit too."""
    book = make_book(title="Blocked TomeSync")
    u = _make_member(db, download_limit=0)
    _as_tomesync_user(client, u)
    resp = client.get(
        f"/api/tome-sync/download/{book.id}/{book.files[0].id}",
        headers={"Authorization": "Bearer tome_dummy"},
    )
    assert resp.status_code == 403


def test_end_to_end_limit_one_then_blocked(client, db, make_book, tmp_path):
    """A real served file counts, and the next request is refused."""
    p = tmp_path / "real.epub"
    p.write_bytes(b"garbage bytes, bake falls back to raw file")
    book = make_book(title="Countable Book", file_path=str(p))
    u = _make_member(db, download_limit=1)
    _as_user(client, u)

    first = client.get(f"/api/books/{book.id}/download/{book.files[0].id}")
    assert first.status_code == 200
    assert db.query(DownloadEvent).filter_by(user_id=u.id).count() == 1

    second = client.get(f"/api/books/{book.id}/download/{book.files[0].id}")
    assert second.status_code == 403
    assert "limit" in second.json()["detail"].lower()


# ── Admin restrictions endpoint ──────────────────────────────────────────────

def test_set_restrictions_persists_and_normalises(client, db):
    u = _make_member(db)
    resp = client.put(f"/api/users/{u.id}/restrictions", json={
        "excluded_tags": ["  Adult ", "adult", "성인", ""],
        "download_limit": 5,
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["excluded_tags"] == ["Adult", "성인"]  # trimmed, deduped case-insensitively
    assert body["download_limit"] == 5

    listed = {x["id"]: x for x in client.get("/api/users").json()}
    assert listed[u.id]["excluded_tags"] == ["Adult", "성인"]
    assert listed[u.id]["download_limit"] == 5


def test_set_restrictions_clears(client, db):
    u = _make_member(db, excluded_tags=["성인"], download_limit=3)
    resp = client.put(f"/api/users/{u.id}/restrictions", json={
        "excluded_tags": [],
        "download_limit": None,
    })
    assert resp.status_code == 200
    db.refresh(u)
    assert u.excluded_tags is None
    assert u.download_limit is None


def test_set_restrictions_rejects_admin_target(client, db, admin_user):
    admin, _ = admin_user
    resp = client.put(f"/api/users/{admin.id}/restrictions", json={"excluded_tags": ["x"]})
    assert resp.status_code == 400


def test_set_restrictions_rejects_negative_limit(client, db):
    u = _make_member(db)
    resp = client.put(f"/api/users/{u.id}/restrictions", json={"download_limit": -1})
    assert resp.status_code == 400


def test_set_restrictions_requires_admin(client, db):
    u = _make_member(db)
    other = _make_member(db)
    _as_user(client, u)
    resp = client.put(f"/api/users/{other.id}/restrictions", json={"download_limit": 0})
    assert resp.status_code == 403
