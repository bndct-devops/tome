"""Per-user content restrictions: excluded tags + download limits (GH #190).

Feature A: a non-admin user with an excluded tag must never see (or download)
books carrying that tag. Feature B: a per-user daily download cap — disabled,
N-per-day, or unlimited — enforced across the download endpoints; admins are
always unlimited.
"""
import pytest
from fastapi import HTTPException

from backend.core.permissions import book_visibility_filter, excluded_tags_for
from backend.core.security import hash_password
from backend.models.book import Book
from backend.models.user import User, UserPermission
from backend.services.downloads_quota import enforce_download, record_download, used_today

_n = 0


def _make_user(db, *, excluded_tags=None, download_limit=None, can_download=True):
    global _n
    _n += 1
    u = User(
        username=f"restricted{_n}",
        email=f"restricted{_n}@example.com",
        hashed_password=hash_password("password123"),
        is_active=True,
        is_admin=False,
        role="member",
        must_change_password=False,
    )
    db.add(u)
    db.flush()
    db.add(UserPermission(
        user_id=u.id,
        can_download=can_download,
        excluded_tags=excluded_tags,
        download_limit=download_limit,
    ))
    db.flush()
    db.refresh(u)
    return u


def test_excluded_tags_parsing(db):
    u = _make_user(db, excluded_tags=" 성인 , 19 ,")
    assert excluded_tags_for(u) == ["성인", "19"]
    assert excluded_tags_for(_make_user(db)) == []


def test_visibility_filter_hides_excluded_tag(db, make_book):
    clean = make_book(title="Clean Book", tags=["general"])
    adult = make_book(title="Adult Book", tags=["성인"])
    u = _make_user(db, excluded_tags="성인")

    ids = {b.id for b in db.query(Book).filter(book_visibility_filter(db, u)).all()}
    assert clean.id in ids
    assert adult.id not in ids


def test_admin_sees_excluded_tag(db, make_book, admin_user):
    """The filter early-returns True for admins, so restrictions never apply."""
    adult = make_book(title="Adult Book", tags=["성인"])
    admin, _ = admin_user
    # admin visibility filter is `True` → the row is returned
    ids = {b.id for b in db.query(Book).filter(book_visibility_filter(db, admin)).all()}
    assert adult.id in ids


def test_download_disabled_by_flag(db):
    with pytest.raises(HTTPException) as exc:
        enforce_download(db, _make_user(db, can_download=False))
    assert exc.value.status_code == 403


def test_download_disabled_by_zero_limit(db):
    with pytest.raises(HTTPException) as exc:
        enforce_download(db, _make_user(db, download_limit=0))
    assert exc.value.status_code == 403


def test_daily_download_limit(db):
    u = _make_user(db, download_limit=2)
    enforce_download(db, u)                 # 0 used → ok
    record_download(db, u, 1)
    record_download(db, u, 2)
    db.flush()
    assert used_today(db, u.id) == 2
    with pytest.raises(HTTPException) as exc:
        enforce_download(db, u)             # would be the 3rd
    assert exc.value.status_code == 403


def test_bulk_count_respects_remaining_quota(db):
    u = _make_user(db, download_limit=3)
    record_download(db, u, 1)
    db.flush()
    enforce_download(db, u, count=2)        # 1 used + 2 = 3 ≤ 3 → ok
    with pytest.raises(HTTPException):
        enforce_download(db, u, count=3)    # 1 + 3 = 4 > 3


def test_admin_is_unlimited_and_uncounted(db, admin_user):
    admin, _ = admin_user
    enforce_download(db, admin, count=10_000)   # no raise
    record_download(db, admin, 1)               # no-op for admins
    db.flush()
    assert used_today(db, admin.id) == 0
