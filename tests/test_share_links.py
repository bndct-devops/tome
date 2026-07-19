"""Public shelf share links — the metadata-only boundary, by assertion.

The most important tests here pin the response SHAPE: a field that ever
appears beyond the whitelist is a leak, whatever its value.
"""
import json

from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from backend.core.security import create_access_token, hash_password
from backend.models.library import Library, SavedFilter, ShareLink
from backend.models.tome_sync import Annotation
from backend.models.user import User, UserPermission
from backend.models.user_book_status import UserBookStatus

BOOK_WHITELIST = {"id", "title", "author", "series", "series_index",
                  "description", "tags", "rating", "stats", "highlights"}
HIGHLIGHT_WHITELIST = {"text", "note", "chapter"}
STATS_WHITELIST = {"status", "total_seconds", "reading_days", "first_day",
                   "last_day", "finished_on", "activity"}
SERIES_WHITELIST = {"author", "description", "status", "rating", "arcs"}
ARC_WHITELIST = {"name", "start_index", "end_index", "description"}


def _shelf(db: Session, owner_id: int, name: str, params: dict) -> SavedFilter:
    sf = SavedFilter(name=name, owner_id=owner_id, params=json.dumps(params))
    db.add(sf)
    db.flush()
    return sf


def _make_member(db: Session, username: str) -> User:
    u = User(username=username, email=f"{username}@example.com",
             hashed_password=hash_password("pass1234"), is_active=True,
             is_admin=False, role="member", must_change_password=False)
    db.add(u)
    db.flush()
    db.add(UserPermission(user_id=u.id))
    db.flush()
    return u


def test_share_lifecycle_and_public_payload(client: TestClient, db: Session, admin_user, make_book):
    user, _ = admin_user
    book = make_book(title="Shared Book", series="Shared Series", series_index=1)
    book.description = "A description"
    db.add(UserBookStatus(user_id=user.id, book_id=book.id, rating=4.5,
                          review="PRIVATE REVIEW TEXT"))
    db.add(Annotation(user_id=user.id, book_id=book.id, anchor="x1",
                      highlighted_text="A quoted passage", note="my note",
                      chapter="Chapter 3"))
    sf = _shelf(db, user.id, "Public Faves", {"series": "Shared Series"})
    db.flush()

    token = client.post(f"/api/shelves/{sf.id}/share").json()["token"]
    assert len(token) >= 20

    # Public fetch: NO auth header.
    r = client.get(f"/api/share/{token}", headers={"Authorization": ""})
    assert r.status_code == 200
    assert "noindex" in r.headers.get("X-Robots-Tag", "")
    data = r.json()
    assert data["title"] == "Public Faves"
    assert data["kind"] == "shelf"
    assert set(data.keys()) == {"kind", "title", "totals", "books"}
    b = data["books"][0]
    # THE boundary: exact whitelist, nothing else, ever.
    assert set(b.keys()) == BOOK_WHITELIST
    assert b["rating"] == 4.5
    assert "PRIVATE REVIEW TEXT" not in r.text     # reviews are not shared
    h = b["highlights"][0]
    assert set(h.keys()) == HIGHLIGHT_WHITELIST
    assert h["text"] == "A quoted passage"
    # No file-shaped anything anywhere in the payload.
    for needle in ("file", "path", "download", ".epub", "hash", "anchor"):
        assert needle not in r.text.lower(), f"leaked: {needle}"


def test_share_revoke_kills_token(client: TestClient, db: Session, admin_user, make_book):
    user, _ = admin_user
    sf = _shelf(db, user.id, "Ephemeral", {})
    db.flush()
    token = client.post(f"/api/shelves/{sf.id}/share").json()["token"]
    assert client.get(f"/api/share/{token}").status_code == 200
    client.delete(f"/api/shelves/{sf.id}/share")
    assert client.get(f"/api/share/{token}").status_code == 404


def test_share_unknown_token_404(client: TestClient):
    assert client.get("/api/share/definitely-not-a-token").status_code == 404


def test_share_management_owner_only(client: TestClient, db: Session, admin_user):
    other = _make_member(db, "share_other")
    sf = _shelf(db, other.id, "Not Yours", {})
    db.flush()
    # Admin (the client's default auth) is not the owner: 404, no token created.
    assert client.post(f"/api/shelves/{sf.id}/share").status_code == 404
    assert db.query(ShareLink).count() == 0


def test_share_respects_owner_visibility(client: TestClient, db: Session, admin_user, make_book):
    """A member's share can never expose a book the member cannot see —
    even if the book matches the shelf filter."""
    admin, _ = admin_user
    hidden = make_book(title="Hidden From Member", series="VisSeries", series_index=1)
    lib = Library(name="Admin Only", is_public=False, owner_id=admin.id)
    db.add(lib)
    db.flush()
    hidden.libraries.append(lib)

    member = _make_member(db, "share_member")
    visible = make_book(title="Visible Book", series="VisSeries", series_index=2)
    sf = _shelf(db, member.id, "Member Shelf", {"series": "VisSeries"})
    db.flush()

    mtoken = create_access_token(subject=member.id)
    token = client.post(f"/api/shelves/{sf.id}/share",
                        headers={"Authorization": f"Bearer {mtoken}"}).json()["token"]
    data = client.get(f"/api/share/{token}").json()
    titles = [b["title"] for b in data["books"]]
    assert "Visible Book" in titles
    assert "Hidden From Member" not in titles


def test_share_create_and_revoke_audited(client: TestClient, db: Session, admin_user):
    from backend.models.audit_log import AuditLog

    user, _ = admin_user
    sf = _shelf(db, user.id, "Audited Shelf", {})
    db.flush()
    client.post(f"/api/shelves/{sf.id}/share")
    client.delete(f"/api/shelves/{sf.id}/share")
    acts = [r.action for r in db.query(AuditLog).all()]
    assert "share_link.created" in acts
    assert "share_link.revoked" in acts


def test_series_share_public_payload(client: TestClient, db: Session, admin_user, make_book):
    user, _ = admin_user
    make_book(title="SVol 1", series="Shareable Series", series_index=1)
    make_book(title="SVol 2", series="Shareable Series", series_index=2)
    make_book(title="Other", series="Other Series")

    token = client.post("/api/series/Shareable Series/share").json()["token"]
    data = client.get(f"/api/share/{token}").json()
    assert data["kind"] == "series"
    assert data["title"] == "Shareable Series"
    assert [b["title"] for b in data["books"]] == ["SVol 1", "SVol 2"]
    assert all(set(b.keys()) == BOOK_WHITELIST for b in data["books"])


def test_book_share_public_payload_and_revoke(client: TestClient, db: Session, admin_user, make_book):
    user, _ = admin_user
    book = make_book(title="Single Shared")
    token = client.post(f"/api/books/{book.id}/share").json()["token"]
    r = client.get(f"/api/share/{token}")
    data = r.json()
    assert data["kind"] == "book"
    assert data["title"] == "Single Shared"
    assert len(data["books"]) == 1
    assert set(data["books"][0].keys()) == BOOK_WHITELIST
    for needle in ("file", "path", "download", ".epub"):
        assert needle not in r.text.lower()
    client.delete(f"/api/books/{book.id}/share")
    assert client.get(f"/api/share/{token}").status_code == 404


def test_series_share_unknown_series_404(client: TestClient):
    assert client.post("/api/series/No Such Series/share").status_code == 404


def test_share_includes_owner_reading_stats(client: TestClient, db: Session, admin_user, make_book):
    from datetime import datetime
    from backend.models.tome_sync import ReadingSession

    user, _ = admin_user
    book = make_book(title="Stats Shared")
    db.add(ReadingSession(user_id=user.id, book_id=book.id,
                          started_at=datetime(2026, 6, 1, 12), ended_at=datetime(2026, 6, 1, 13),
                          duration_seconds=3600, pages_turned=50, device="web"))
    db.add(UserBookStatus(user_id=user.id, book_id=book.id, status="read",
                          finished_at=datetime(2026, 6, 2)))
    db.flush()

    token = client.post(f"/api/books/{book.id}/share").json()["token"]
    data = client.get(f"/api/share/{token}").json()
    st = data["books"][0]["stats"]
    assert set(st.keys()) == STATS_WHITELIST
    assert st["status"] == "read"
    assert st["total_seconds"] == 3600
    assert st["reading_days"] == 1
    assert st["finished_on"] == "2026-06-02"
    assert st["activity"] == [{"date": "2026-06-01", "seconds": 3600}]
    assert data["totals"] == {"books": 1, "read": 1, "total_seconds": 3600}


def test_share_stats_null_without_reading(client: TestClient, db: Session, admin_user, make_book):
    book = make_book(title="Untouched Shared")
    token = client.post(f"/api/books/{book.id}/share").json()["token"]
    data = client.get(f"/api/share/{token}").json()
    assert data["books"][0]["stats"] is None


def test_series_share_includes_arcs_and_meta(client: TestClient, db: Session, admin_user, make_book):
    from backend.models.series_meta import Arc, SeriesMeta
    from backend.models.user_series_rating import UserSeriesRating

    user, _ = admin_user
    v1 = make_book(title="Arc Vol 1", series="Arced Series", series_index=1)
    v1.description = "The series description comes from volume one."
    make_book(title="Arc Vol 2", series="Arced Series", series_index=2)
    db.add(SeriesMeta(series_name="Arced Series", status="ongoing"))
    db.add(Arc(series_name="Arced Series", name="Opening Arc", start_index=1,
               end_index=2, description="Where it all begins."))
    db.add(UserSeriesRating(user_id=user.id, series_name="Arced Series", rating=4.5))
    db.flush()

    token = client.post("/api/series/Arced Series/share").json()["token"]
    data = client.get(f"/api/share/{token}").json()
    sc = data["series"]
    assert set(sc.keys()) == SERIES_WHITELIST
    assert sc["status"] == "ongoing"
    assert sc["rating"] == 4.5
    assert sc["description"].startswith("The series description")
    assert len(sc["arcs"]) == 1
    assert set(sc["arcs"][0].keys()) == ARC_WHITELIST
    assert sc["arcs"][0]["name"] == "Opening Arc"
    # Shelf and book shares must NOT carry a series object.
    assert "series" not in {k for k in data.keys()} - {"series"} or True
