"""Clean up this series (POST /api/ai/series/{name}/cleanup and .../apply).
The provider is always the FakeProvider; titles are neutral and made up."""
import json

import pytest
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from backend.core.security import create_access_token, hash_password
from backend.models.audit_log import AuditLog
from backend.models.book import Book
from backend.models.library import Library
from backend.models.series_meta import Arc, SeriesMeta
from backend.models.user import User
from backend.services import ai
from backend.services.ai import series_cleanup, settings as ai_settings
from tests.ai_fake import FakeProvider

KEY = "sk-ant-test-series-0000000000000Ef56"
SERIES = "Harbour Tales"


def _make_user(db: Session, username: str, role: str) -> tuple[User, dict]:
    user = User(username=username, email=f"{username}@example.com",
                hashed_password=hash_password("pw123456"), is_active=True,
                is_admin=(role == "admin"), role=role, must_change_password=False)
    db.add(user)
    db.flush()
    return user, {"Authorization": f"Bearer {create_access_token(subject=user.id)}"}


@pytest.fixture()
def admin_with_key(db: Session, admin_user) -> User:
    user, _ = admin_user
    ai_settings.set_user_key(db, user, KEY)
    db.flush()
    return user


@pytest.fixture()
def series_books(make_book) -> list[Book]:
    """Three volumes; the third is mis-numbered as 2 and its title drifts."""
    return [
        make_book(title="Harbour Tales, Vol. 1: The Fog", series=SERIES, series_index=1.0,
                  file_path="/library/ht/Harbour Tales v01.epub"),
        make_book(title="Harbour Tales, Vol. 2: The Ferry", series=SERIES, series_index=2.0,
                  file_path="/library/ht/Harbour Tales v02.epub"),
        make_book(title="harbour tales 3 the lighthouse", series=SERIES, series_index=2.0,
                  file_path="/library/ht/Harbour Tales v03.epub"),
    ]


def _answer(books: list[Book], **overrides) -> dict:
    base = {
        "series_name": None,
        "series_name_evidence": "The name is fine.",
        "books": [{
            "book_id": books[2].id,
            "title": "Harbour Tales, Vol. 3: The Lighthouse",
            "series_index": 3,
            "evidence": "The file name says v03 and the other titles follow one pattern.",
        }],
        "status": "finished",
        "status_evidence": "The series ended with its third volume.",
        "arcs": [
            {"name": "Fog Season", "start_index": 1, "end_index": 2, "description": None},
            {"name": "Lighthouse", "start_index": 3, "end_index": 3, "description": "The last volume."},
        ],
        "arcs_evidence": "Two story arcs as published.",
        "confidence": 0.92,
        "evidence": "File names and titles agree.",
    }
    base.update(overrides)
    return base


def _audits(db: Session) -> list[dict]:
    rows = db.query(AuditLog).filter(AuditLog.action == "ai.series_cleanup_applied").all()
    return [json.loads(r.details) for r in rows]


# ── proposal ─────────────────────────────────────────────────────────────────

def test_proposal_shape(client: TestClient, db: Session, admin_with_key, series_books):
    db.add(Arc(series_name=SERIES, name="Old Arc", start_index=1, end_index=3))
    db.flush()
    fake = FakeProvider(responses=[_answer(series_books, series_name="Harbour Tales")])
    ai.set_provider_override(fake)

    r = client.post(f"/api/ai/series/{SERIES}/cleanup")
    assert r.status_code == 200, r.text
    data = r.json()

    assert data["series"] == SERIES
    assert data["book_count"] == 3
    # Restating the current name is no change.
    assert data["series_name"] == {"current": SERIES, "proposed": None,
                                   "evidence": "The name is fine."}
    assert len(data["books"]) == 1
    row = data["books"][0]
    assert row["book_id"] == series_books[2].id
    assert row["current"] == {"title": "harbour tales 3 the lighthouse", "series_index": 2.0}
    assert row["proposed"] == {"title": "Harbour Tales, Vol. 3: The Lighthouse", "series_index": 3.0}
    assert row["evidence"].startswith("The file name")
    assert row["editable"] is True
    assert data["status"] == {"current": "unknown", "proposed": "finished",
                              "evidence": "The series ended with its third volume."}
    assert data["arcs"]["current"] == [{"name": "Old Arc", "start_index": 1.0, "end_index": 3.0,
                                        "description": None}]
    assert [a["name"] for a in data["arcs"]["proposed"]] == ["Fog Season", "Lighthouse"]
    assert data["confidence"] == 0.92
    assert data["threshold"] == pytest.approx(0.85)
    assert data["permissions"] == {"series_name": True, "status": True, "arcs": True}

    # The model saw every book, the stored arcs and the schema.
    call = fake.calls[0]
    assert call["feature"] == "series_cleanup"
    assert call["schema"] == series_cleanup.build_schema()
    text = call["user_content"][0]["text"]
    payload = json.loads(text.split("\n\n", 1)[1])
    assert [b["book_id"] for b in payload["books"]] == [b.id for b in series_books]
    assert payload["books"][2]["files"] == ["Harbour Tales v03.epub"]
    assert payload["arcs"][0]["name"] == "Old Arc"


def test_proposal_drops_overlapping_arcs_and_unknown_books(client: TestClient, admin_with_key,
                                                           series_books):
    answer = _answer(series_books, arcs=[
        {"name": "A", "start_index": 1, "end_index": 2, "description": None},
        {"name": "B", "start_index": 2, "end_index": 3, "description": None},
    ], status="cancelled")
    answer["books"].append({"book_id": 999999, "title": "Elsewhere", "series_index": 9,
                            "evidence": "x"})
    answer["books"].append({"book_id": series_books[0].id, "title": series_books[0].title,
                            "series_index": 1, "evidence": "restated"})
    ai.set_provider_override(FakeProvider(responses=[answer]))

    data = client.post(f"/api/ai/series/{SERIES}/cleanup").json()
    assert [r["book_id"] for r in data["books"]] == [series_books[2].id]
    assert data["arcs"]["proposed"] is None
    assert "overlap" in data["arcs"]["evidence"]
    assert data["status"]["proposed"] is None   # not a known status


@pytest.mark.parametrize("bad_status", [["finished"], {"x": 1}, 3])
def test_non_string_status_is_dropped_not_a_500(client: TestClient, admin_with_key,
                                                series_books, bad_status):
    ai.set_provider_override(FakeProvider(responses=[_answer(series_books, status=bad_status)]))
    r = client.post(f"/api/ai/series/{SERIES}/cleanup")
    assert r.status_code == 200, r.text
    assert r.json()["status"]["proposed"] is None


def test_unknown_series_is_404(client: TestClient, admin_with_key):
    fake = FakeProvider(responses=[{}])
    ai.set_provider_override(fake)
    assert client.post("/api/ai/series/Nowhere/cleanup").status_code == 404
    assert client.post("/api/ai/series/__unserialized__/cleanup").status_code == 404
    assert fake.calls == []


def test_without_key_is_409(client: TestClient, series_books):
    fake = FakeProvider(responses=[{}])
    ai.set_provider_override(fake)
    assert client.post(f"/api/ai/series/{SERIES}/cleanup").status_code == 409
    assert fake.calls == []


def test_feature_off_is_404(client: TestClient, db: Session, admin_with_key, series_books):
    ai_settings.set_feature_enabled(db, "series_cleanup", False)
    db.flush()
    ai.set_provider_override(FakeProvider(responses=[{}]))
    assert client.post(f"/api/ai/series/{SERIES}/cleanup").status_code == 404
    r = client.post(f"/api/ai/series/{SERIES}/cleanup/apply", json={"status": "finished"})
    assert r.status_code == 404


def test_refusal_is_422(client: TestClient, admin_with_key, series_books):
    ai.set_provider_override(FakeProvider(refuse=True))
    assert client.post(f"/api/ai/series/{SERIES}/cleanup").status_code == 422


def test_guest_forbidden(client: TestClient, db: Session, series_books):
    _, headers = _make_user(db, "seriesguest", "guest")
    fake = FakeProvider(responses=[{}])
    ai.set_provider_override(fake)
    assert client.post(f"/api/ai/series/{SERIES}/cleanup", headers=headers).status_code == 403
    r = client.post(f"/api/ai/series/{SERIES}/cleanup/apply", headers=headers,
                    json={"status": "finished"})
    assert r.status_code == 403
    assert fake.calls == []


def test_hidden_books_excluded(client: TestClient, db: Session, admin_user, series_books):
    admin, _ = admin_user
    private = Library(name="Admin Private", is_public=False, owner_id=admin.id)
    db.add(private)
    db.flush()
    private.books.append(series_books[2])
    db.flush()

    member, headers = _make_user(db, "seriesmember", "member")
    ai_settings.set_user_key(db, member, KEY)
    db.flush()
    fake = FakeProvider(responses=[_answer(series_books)])
    ai.set_provider_override(fake)

    r = client.post(f"/api/ai/series/{SERIES}/cleanup", headers=headers)
    assert r.status_code == 200, r.text
    data = r.json()
    payload = json.loads(fake.calls[0]["user_content"][0]["text"].split("\n\n", 1)[1])
    assert series_books[2].id not in [b["book_id"] for b in payload["books"]]
    assert data["book_count"] == 2
    assert data["books"] == []    # the hidden book's row is dropped
    # Members may not rename a series of others' books, nor set status/arcs.
    assert data["permissions"] == {"series_name": False, "status": False, "arcs": False}

    # And the apply cannot reach the hidden book either.
    r = client.post(f"/api/ai/series/{SERIES}/cleanup/apply", headers=headers,
                    json={"books": [{"book_id": series_books[2].id, "series_index": 3}]})
    assert r.status_code == 404
    db.refresh(series_books[2])
    assert series_books[2].series_index == 2.0


# ── apply ────────────────────────────────────────────────────────────────────

def test_apply_subset(client: TestClient, db: Session, series_books):
    """Book fix and status checked; arcs and the other row left out."""
    db.add(Arc(series_name=SERIES, name="Old Arc", start_index=1, end_index=3))
    db.flush()
    r = client.post(f"/api/ai/series/{SERIES}/cleanup/apply", json={
        "books": [{"book_id": series_books[2].id,
                   "title": "Harbour Tales, Vol. 3: The Lighthouse", "series_index": 3}],
        "status": "finished",
    })
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["series_name"] == SERIES
    assert data["books_updated"] == 1
    assert data["status"] == "finished"

    db.expire_all()
    third = db.get(Book, series_books[2].id)
    assert third.title == "Harbour Tales, Vol. 3: The Lighthouse"
    assert third.series_index == 3.0
    assert db.get(Book, series_books[1].id).series_index == 2.0
    assert db.query(SeriesMeta).filter_by(series_name=SERIES).one().status == "finished"
    assert [a.name for a in db.query(Arc).filter_by(series_name=SERIES)] == ["Old Arc"]

    assert _audits(db) == [{"renamed_to": None, "books_updated": 1, "books_moved": 0,
                            "status_changed": True, "arcs_set": None}]


def test_apply_arcs_replaces_the_list(client: TestClient, db: Session, series_books):
    db.add(Arc(series_name=SERIES, name="Old Arc", start_index=1, end_index=3))
    db.flush()
    r = client.post(f"/api/ai/series/{SERIES}/cleanup/apply", json={"arcs": [
        {"name": "Fog Season", "start_index": 1, "end_index": 2},
        {"name": "Lighthouse", "start_index": 3, "end_index": 3, "description": "The last volume."},
    ]})
    assert r.status_code == 200, r.text
    assert [a["name"] for a in r.json()["arcs"]] == ["Fog Season", "Lighthouse"]
    db.expire_all()
    names = sorted(a.name for a in db.query(Arc).filter_by(series_name=SERIES))
    assert names == ["Fog Season", "Lighthouse"]
    assert _audits(db)[0]["arcs_set"] == 2


def test_apply_overlapping_arcs_is_400_and_writes_nothing(client: TestClient, db: Session,
                                                          series_books):
    r = client.post(f"/api/ai/series/{SERIES}/cleanup/apply", json={
        "books": [{"book_id": series_books[2].id, "series_index": 3}],
        "arcs": [
            {"name": "A", "start_index": 1, "end_index": 2},
            {"name": "B", "start_index": 2, "end_index": 3},
        ],
    })
    assert r.status_code == 400
    assert "overlap" in r.json()["detail"]
    db.expire_all()
    assert db.get(Book, series_books[2].id).series_index == 2.0
    assert db.query(Arc).filter_by(series_name=SERIES).count() == 0
    assert _audits(db) == []


def test_apply_backwards_arc_is_400(client: TestClient, series_books):
    r = client.post(f"/api/ai/series/{SERIES}/cleanup/apply", json={
        "arcs": [{"name": "A", "start_index": 3, "end_index": 1}],
    })
    assert r.status_code == 400


def test_rename_updates_every_book_and_carries_meta(client: TestClient, db: Session,
                                                    series_books, make_book):
    db.add(SeriesMeta(series_name=SERIES, status="ongoing"))
    db.add(Arc(series_name=SERIES, name="Fog Season", start_index=1, end_index=2))
    other = make_book(title="Unrelated", series="Other Series", series_index=1.0)
    db.flush()

    r = client.post(f"/api/ai/series/{SERIES}/cleanup/apply",
                    json={"series_name": "The Harbour Tales"})
    assert r.status_code == 200, r.text
    assert r.json()["series_name"] == "The Harbour Tales"
    assert r.json()["books_moved"] == 3

    db.expire_all()
    assert {db.get(Book, b.id).series for b in series_books} == {"The Harbour Tales"}
    assert db.get(Book, other.id).series == "Other Series"
    assert db.query(SeriesMeta).filter_by(series_name="The Harbour Tales").one().status == "ongoing"
    assert db.query(Arc).filter_by(series_name="The Harbour Tales").count() == 1
    assert db.query(Arc).filter_by(series_name=SERIES).count() == 0

    # The series list follows the rename.
    names = [s["name"] for s in client.get("/api/books/series").json()]
    assert "The Harbour Tales" in names and SERIES not in names
    assert _audits(db)[0]["renamed_to"] == "The Harbour Tales"


def test_rename_with_status_targets_new_name(client: TestClient, db: Session, series_books):
    r = client.post(f"/api/ai/series/{SERIES}/cleanup/apply",
                    json={"series_name": "The Harbour Tales", "status": "hiatus"})
    assert r.status_code == 200, r.text
    db.expire_all()
    assert db.query(SeriesMeta).filter_by(series_name="The Harbour Tales").one().status == "hiatus"


def test_rename_into_existing_series_keeps_target_meta(client: TestClient, db: Session,
                                                     series_books, make_book):
    """Merging into a series that already has its own status and arcs keeps
    the target's rows; the source's rows stay under the old name."""
    db.add(SeriesMeta(series_name=SERIES, status="ongoing"))
    db.add(Arc(series_name=SERIES, name="Fog Season", start_index=1, end_index=2))
    db.add(SeriesMeta(series_name="Other", status="finished"))
    db.add(Arc(series_name="Other", name="Target Arc", start_index=1, end_index=5))
    make_book(title="Existing", series="Other", series_index=4.0)
    db.flush()

    r = client.post(f"/api/ai/series/{SERIES}/cleanup/apply", json={"series_name": "Other"})
    assert r.status_code == 200, r.text
    assert r.json()["books_moved"] == 3

    db.expire_all()
    assert {db.get(Book, b.id).series for b in series_books} == {"Other"}
    assert db.query(SeriesMeta).filter_by(series_name="Other").one().status == "finished"
    assert [a.name for a in db.query(Arc).filter_by(series_name="Other")] == ["Target Arc"]
    assert db.query(SeriesMeta).filter_by(series_name=SERIES).one().status == "ongoing"
    assert [a.name for a in db.query(Arc).filter_by(series_name=SERIES)] == ["Fog Season"]


def test_rename_into_existing_series_with_status_sets_target(client: TestClient, db: Session,
                                                             series_books, make_book):
    db.add(SeriesMeta(series_name=SERIES, status="ongoing"))
    db.add(SeriesMeta(series_name="Other", status="finished"))
    make_book(title="Existing", series="Other", series_index=4.0)
    db.flush()

    r = client.post(f"/api/ai/series/{SERIES}/cleanup/apply",
                    json={"series_name": "Other", "status": "hiatus"})
    assert r.status_code == 200, r.text
    db.expire_all()
    assert db.query(SeriesMeta).filter_by(series_name="Other").one().status == "hiatus"
    assert db.query(SeriesMeta).filter_by(series_name=SERIES).one().status == "ongoing"


def test_nothing_to_apply_is_422(client: TestClient, series_books):
    r = client.post(f"/api/ai/series/{SERIES}/cleanup/apply", json={})
    assert r.status_code == 422


def test_member_cannot_set_status_or_edit_others_books(client: TestClient, db: Session,
                                                       series_books):
    _, headers = _make_user(db, "seriesmember2", "member")
    r = client.post(f"/api/ai/series/{SERIES}/cleanup/apply", headers=headers,
                    json={"status": "finished"})
    assert r.status_code == 403
    r = client.post(f"/api/ai/series/{SERIES}/cleanup/apply", headers=headers,
                    json={"books": [{"book_id": series_books[2].id, "series_index": 3}]})
    assert r.status_code == 403
    r = client.post(f"/api/ai/series/{SERIES}/cleanup/apply", headers=headers,
                    json={"series_name": "The Harbour Tales"})
    assert r.status_code == 403
    assert _audits(db) == []


def test_member_cannot_set_arcs(client: TestClient, db: Session, series_books):
    db.add(Arc(series_name=SERIES, name="Fog Season", start_index=1, end_index=2))
    db.flush()
    before = [(a.name, a.start_index, a.end_index)
              for a in db.query(Arc).filter_by(series_name=SERIES).order_by(Arc.id)]

    _, headers = _make_user(db, "seriesmember3", "member")
    r = client.post(f"/api/ai/series/{SERIES}/cleanup/apply", headers=headers, json={
        "arcs": [{"name": "A", "start_index": 1, "end_index": 2}],
    })
    assert r.status_code == 403

    db.expire_all()
    after = [(a.name, a.start_index, a.end_index)
             for a in db.query(Arc).filter_by(series_name=SERIES).order_by(Arc.id)]
    assert after == before
    assert _audits(db) == []


def test_member_mixed_ownership_series(client: TestClient, db: Session, series_books):
    """A member who uploaded one book of a series that is otherwise someone
    else's may fix that book, but may not rename the series."""
    member, headers = _make_user(db, "seriesmember4", "member")
    own = Book(title="harbour tales 9", series=SERIES, series_index=9.0,
               status="active", added_by=member.id)
    db.add(own)
    db.flush()

    r = client.post(f"/api/ai/series/{SERIES}/cleanup/apply", headers=headers,
                    json={"books": [{"book_id": own.id, "title": "New Title"}]})
    assert r.status_code == 200, r.text

    r = client.post(f"/api/ai/series/{SERIES}/cleanup/apply", headers=headers,
                    json={"series_name": "Renamed"})
    assert r.status_code == 403

    db.expire_all()
    assert db.get(Book, own.id).title == "New Title"
    assert {db.get(Book, b.id).series for b in series_books} == {SERIES}
    assert db.get(Book, own.id).series == SERIES


def test_member_can_clean_up_own_series(client: TestClient, db: Session):
    member, headers = _make_user(db, "seriesowner", "member")
    books = []
    for i in (1, 2):
        b = Book(title=f"Lantern Road {i}", series="Lantern Road", series_index=float(i),
                 status="active", added_by=member.id)
        db.add(b)
        books.append(b)
    db.flush()
    r = client.post("/api/ai/series/Lantern Road/cleanup/apply", headers=headers, json={
        "series_name": "The Lantern Road",
        "books": [{"book_id": books[1].id, "title": "Lantern Road 2: Dusk"}],
    })
    assert r.status_code == 200, r.text
    db.expire_all()
    assert {db.get(Book, b.id).series for b in books} == {"The Lantern Road"}
    assert db.get(Book, books[1].id).title == "Lantern Road 2: Dusk"


def test_manual_editors_still_work(client: TestClient, db: Session, series_books):
    """The shared code paths keep the existing endpoints' behaviour."""
    r = client.put(f"/api/books/{series_books[0].id}", json={"title": "Harbour Tales 1"})
    assert r.status_code == 200 and r.json()["title"] == "Harbour Tales 1"
    r = client.put("/api/books/bulk-metadata",
                   json={"book_ids": [b.id for b in series_books], "series": "HT"})
    assert r.status_code == 200 and r.json() == {"updated": 3}
    r = client.put("/api/series/HT/meta", json={"status": "hiatus"})
    assert r.status_code == 200 and r.json()["status"] == "hiatus"
    r = client.post("/api/series/HT/arcs/bulk", json=[
        {"series_name": "HT", "name": "One", "start_index": 1, "end_index": 2}])
    assert r.status_code == 200 and [a["name"] for a in r.json()] == ["One"]
    r = client.post("/api/series/HT/arcs/bulk", json=[
        {"series_name": "HT", "name": "Bad", "start_index": 3, "end_index": 1}])
    assert r.status_code == 400
