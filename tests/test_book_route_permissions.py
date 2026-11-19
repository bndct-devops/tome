"""Every route that takes a book id refuses a book the caller cannot see.

The walk below reads the route list from the app itself, so a route added
later is covered without anyone remembering this file: it either refuses the
hidden book, or it fails here until it is listed with a reason.
"""
import re

import pytest
from sqlalchemy.orm import Session

from backend.core.security import create_access_token, get_current_user_basic, hash_password
from backend.models.book import Book, BookFile
from backend.models.library import Library
from backend.models.tome_sync import ApiKey
from backend.models.user import User

API_KEY = "walk-test-api-key"

# Routes that answer for a hidden book on purpose.
ALLOWED_TO_ANSWER = {
    # Covers are public by design (CLAUDE.md, "Per-user book visibility").
    ("GET", "/api/books/{book_id}/cover"),
    # Removing a book from your own library: nothing to remove, nothing shown.
    ("DELETE", "/api/libraries/{lib_id}/books/{book_id}"),
    # Taking back your own share link must work even after the book was
    # hidden from you; it only ever touches a link you created.
    ("DELETE", "/api/books/{book_id}/share"),
}

# A valid body per route, so the request reaches the permission check instead
# of stopping at validation. A route with a body that is missing here fails
# the walk with a 422.
BODIES = {
    ("PUT", "/api/books/{book_id}"): {"title": "Changed"},
    ("POST", "/api/books/{book_id}/apply-metadata"): {"title": "Changed"},
    ("POST", "/api/books/{book_id}/link-kosync"): {"document": "a" * 32},
    ("PUT", "/api/books/{book_id}/rating"): {"rating": 4},
    ("POST", "/api/books/{book_id}/send"): {"device_id": 1, "file_id": 1},
    ("POST", "/api/books/{book_id}/sessions"): {"duration_minutes": 10},
    ("POST", "/api/books/{book_id}/share"): {},
    ("PUT", "/api/books/{book_id}/status"): {"status": "reading"},
    ("POST", "/api/hardcover/books/{book_id}/match"): {"hardcover_book_id": 1},
    ("POST", "/api/hardcover/books/{book_id}/rematch"): {},
    ("POST", "/api/tome-sync/annotations/{book_id}/sync"): {"annotations": []},
    ("PUT", "/api/tome-sync/position/{book_id}"): {"progress": "x", "percentage": 0.5},
    ("PUT", "/api/tome-sync/rating/{book_id}"): {"rating": 4},
    ("PUT", "/api/tome-sync/status/{book_id}"): {"status": "reading"},
    ("POST", "/api/tome-sync/sweep/{book_id}"): {},
}


def _user(db: Session, name: str, role: str) -> User:
    user = User(
        username=name, email=f"{name}@example.com",
        hashed_password=hash_password("password123"),
        is_active=True, is_admin=False, role=role, must_change_password=False,
    )
    db.add(user)
    db.flush()
    return user


@pytest.fixture()
def world(db: Session, admin_user, client, monkeypatch):
    admin, _ = admin_user
    # Mail "set up" (conftest blocks real sending), so the send route gets as
    # far as looking at the book.
    from backend.core.config import settings
    monkeypatch.setattr(settings, "smtp_host", "smtp.invalid")
    monkeypatch.setattr(settings, "smtp_from", "tome@example.com")
    member = _user(db, "walker", "member")
    db.add(ApiKey(user_id=member.id, key_hash=ApiKey.hash_key(API_KEY), label="walk"))

    private = Library(name="Admin only", is_public=False, owner_id=admin.id)
    own = Library(name="Walker's", is_public=False, owner_id=member.id)
    db.add_all([private, own])
    db.flush()

    hidden = Book(title="Hidden Ledger", author="Nobody", status="active", added_by=admin.id)
    hidden.libraries.append(private)
    db.add(hidden)
    db.flush()
    file = BookFile(book_id=hidden.id, file_path="/library/hidden/ledger.epub",
                    format="epub", file_size=10)
    db.add(file)
    db.flush()

    token = create_access_token(subject=member.id)
    client.app.dependency_overrides[get_current_user_basic] = lambda: member
    return {
        "client": client, "member": member, "token": token,
        "hidden": hidden, "file": file, "own_lib": own,
    }


def _book_routes(app) -> list[tuple[str, str]]:
    found = []
    for path, operations in app.openapi()["paths"].items():
        if "{book_id}" not in path:
            continue
        for method in operations:
            found.append((method.upper(), path))
    return sorted(found)


def _call(world, method: str, path: str):
    values = {
        "book_id": world["hidden"].id,
        "file_id": world["file"].id,
        "lib_id": world["own_lib"].id,
        "user_id": world["member"].id,
        "history_id": 1,
        "page_index": 0,
    }
    url = re.sub(r"\{(\w+)\}", lambda m: str(values[m.group(1)]), path)
    bearer = API_KEY if path.startswith("/api/tome-sync/") else world["token"]
    kwargs: dict = {"headers": {"Authorization": f"Bearer {bearer}"}}
    if "/pages/{page_index}" in path or "/read." in path:
        kwargs["params"] = {"token": world["token"]}
    if path == "/api/books/{book_id}/cover" and method == "POST":
        kwargs["files"] = {"file": ("c.jpg", b"\xff\xd8\xff\xe0" + b"0" * 64, "image/jpeg")}
    elif (method, path) in BODIES:
        kwargs["json"] = BODIES[(method, path)]
    return world["client"].request(method, url, **kwargs)


def test_no_book_route_serves_a_hidden_book(world):
    routes = _book_routes(world["client"].app)
    assert len(routes) > 40, "route discovery broke; the walk would prove nothing"

    wrong = []
    for method, path in routes:
        if (method, path) in ALLOWED_TO_ANSWER:
            continue
        r = _call(world, method, path)
        if r.status_code not in (403, 404):
            wrong.append(f"{method} {path} -> {r.status_code} {r.text[:120]}")
    assert not wrong, "these routes answered for a hidden book:\n" + "\n".join(wrong)


def test_the_walk_reaches_real_handlers(world, db: Session):
    """The same calls against a book the member uploaded do not all bounce,
    so a 404 above means "hidden", not "the test sent nonsense"."""
    own = Book(title="Walker's Own", author="Walker", status="active",
               added_by=world["member"].id)
    db.add(own)
    db.flush()
    world["hidden"], world["file"] = own, BookFile(
        book_id=own.id, file_path="/library/own/own.epub", format="epub", file_size=10)
    db.add(world["file"])
    db.flush()

    answered = 0
    for method, path in _book_routes(world["client"].app):
        if method != "GET" or "download" in path or "/read." in path or "/pages" in path:
            continue
        if _call(world, method, path).status_code == 200:
            answered += 1
    assert answered >= 8


def test_exemptions_still_exist(world):
    routes = set(_book_routes(world["client"].app))
    assert ALLOWED_TO_ANSWER <= routes, "an exempt route is gone; remove it from the list"
    assert set(BODIES) <= routes, "a route with a listed body is gone; remove it from BODIES"
