"""Fix this book (POST /api/ai/books/{id}/fix) and the ai_assisted flag on
POST /api/books/{id}/apply-metadata. The provider is always the FakeProvider
and the metadata sources are mocked; titles are neutral and made up."""
import json
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from backend.core.security import create_access_token, hash_password
from backend.models.ai_usage import AIUsage
from backend.models.audit_log import AuditLog
from backend.models.book import Book
from backend.models.library import Library
from backend.models.user import User
from backend.services import ai
from backend.services.ai import fix_book, settings as ai_settings
from backend.services.metadata_fetch import FetchResult, MetadataCandidate
from tests.ai_fake import FakeProvider

KEY = "sk-ant-test-fixbook-00000000000Cd34"

CANDIDATE = MetadataCandidate(
    source="open_library", source_id="OL123W", title="The Quiet Harbour",
    author="M. Vale", description="A town, a fog, a missing ferry.",
    cover_url="https://covers.example.org/1.jpg", publisher="Lantern Press",
    year=2019, isbn="978-0-00-000000-2", language="en",
    tags=["Mystery", "Coastal Fiction"], series="Harbour Tales", series_index=2.0,
)
OTHER = MetadataCandidate(
    source="google_books", source_id="gb-9", title="The Quiet Harbour Cookbook",
    author="A. Nobody", description="Recipes.", cover_url=None, publisher=None,
    year=2001, isbn="9781111111111", language="en", tags=["Cooking"],
    series=None, series_index=None,
)


def _make_user(db: Session, username: str, role: str) -> tuple[User, dict]:
    user = User(username=username, email=f"{username}@example.com",
                hashed_password=hash_password("pw123456"), is_active=True,
                is_admin=(role == "admin"), role=role, must_change_password=False)
    db.add(user)
    db.flush()
    return user, {"Authorization": f"Bearer {create_access_token(subject=user.id)}"}


@pytest.fixture()
def sources(monkeypatch) -> list[dict]:
    """Mock the metadata sources; records each fetch's kwargs."""
    seen: list[dict] = []

    async def fake_fetch(**kwargs):
        seen.append(kwargs)
        return FetchResult(candidates=[CANDIDATE, OTHER], query_used=kwargs.get("title", ""))

    monkeypatch.setattr("backend.services.ai.fix_book.fetch_candidates", fake_fetch)
    return seen


@pytest.fixture()
def admin_with_key(db: Session, admin_user) -> User:
    user, _ = admin_user
    ai_settings.set_user_key(db, user, KEY)
    db.flush()
    return user


@pytest.fixture()
def messy_book(make_book) -> Book:
    return make_book(title="quiet harbour v02", author="Vale, M.", year=None,
                     isbn=None, publisher=None, description=None, language=None,
                     tags=["Shelved"])


def _answer(**overrides) -> dict:
    base = {
        "candidate_id": None, "title": None, "author": None, "publisher": None,
        "year": None, "language": None, "isbn": None, "series": None,
        "series_index": None, "tags": None, "use_candidate_description": False,
        "use_candidate_cover": False, "confidence": 0.5, "evidence": "Nothing to change.",
    }
    base.update(overrides)
    return base


# ── proposal ─────────────────────────────────────────────────────────────────

def test_fix_returns_sanitised_proposal(client: TestClient, db: Session, admin_with_key,
                                        messy_book: Book, sources):
    fake = FakeProvider(responses=[_answer(
        candidate_id="c1", title="The Quiet Harbour", author="M. Vale",
        publisher="Lantern Press", year=2019, language="EN", isbn="9780000000002",
        series="Harbour Tales", series_index=2, tags=["mystery", "Invented Tag"],
        use_candidate_description=True, use_candidate_cover=True,
        confidence=1.4, evidence="Title page and the Open Library ISBN agree.",
    )])
    ai.set_provider_override(fake)

    r = client.post(f"/api/ai/books/{messy_book.id}/fix")
    assert r.status_code == 200, r.text
    data = r.json()

    p = data["proposal"]
    assert set(p) == set(fix_book.FIELDS)
    assert p["title"] == "The Quiet Harbour"
    assert p["author"] == "M. Vale"
    assert p["publisher"] == "Lantern Press"
    assert p["year"] == 2019
    assert p["language"] == "en"
    assert p["isbn"] == "978-0-00-000000-2"          # the candidate's own form
    assert p["series"] == "Harbour Tales"
    assert p["series_index"] == 2.0
    assert p["tags"] == ["Mystery"]                  # invented tag dropped, canonical case
    assert p["description"] == CANDIDATE.description  # copied, never written by the model
    assert p["cover_url"] == CANDIDATE.cover_url

    assert data["confidence"] == 1.0                 # clamped
    assert data["evidence"].startswith("Title page")
    assert data["candidate_source"] == "open_library"
    assert data["candidate"]["source_id"] == "OL123W"
    assert data["current"]["title"] == "quiet harbour v02"
    assert data["current"]["tags"] == ["Shelved"]
    assert data["current"]["has_cover"] is False
    assert "title" in data["changed_fields"] and "cover_url" in data["changed_fields"]
    assert 0 < data["threshold"] <= 1

    # One model call, with the schema and the candidates as evidence.
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["feature"] == "fix_book"
    assert call["schema"] == fix_book.build_schema()
    evidence = json.loads(call["user_content"][0]["text"].split("\n\n", 1)[1])
    assert evidence["book"]["title"] == "quiet harbour v02"
    assert [c["candidate_id"] for c in evidence["candidates"]] == ["c1", "c2"]
    assert evidence["files"] == ["quiet_harbour_v02.epub"]

    # Metadata fetch used the book's own fields; usage recorded.
    assert sources[0]["title"] == "quiet harbour v02"
    assert sources[0]["query_override"] is None
    assert db.query(AIUsage).filter(AIUsage.feature == "fix_book").count() == 1

    # Nothing was written.
    db.refresh(messy_book)
    assert messy_book.title == "quiet harbour v02"


def test_null_means_no_change(client: TestClient, admin_with_key, messy_book: Book, sources):
    ai.set_provider_override(FakeProvider(responses=[_answer()]))
    r = client.post(f"/api/ai/books/{messy_book.id}/fix")
    assert r.status_code == 200, r.text
    data = r.json()
    assert all(v is None for v in data["proposal"].values())
    assert data["changed_fields"] == []
    assert data["candidate"] is None and data["candidate_source"] is None


def test_restated_and_unsafe_values_become_no_change(client: TestClient, admin_with_key,
                                                     make_book, sources):
    book = make_book(title="The Quiet Harbour", author="M. Vale", year=2019,
                     language="en", isbn="9780000000002", tags=["Mystery"])
    ai.set_provider_override(FakeProvider(responses=[_answer(
        candidate_id="c9",                      # unknown candidate
        title="The Quiet Harbour",              # same as current
        author="  ",                            # blank
        year=2019,                              # same
        language="EN",                          # same, different case
        isbn="9799999999999",                   # no candidate carries it
        tags=["mystery"],                       # same set
        use_candidate_description=True,         # no matched candidate
        use_candidate_cover=True,
    )]))
    r = client.post(f"/api/ai/books/{book.id}/fix")
    assert r.status_code == 200, r.text
    data = r.json()
    assert all(v is None for v in data["proposal"].values()), data["proposal"]
    assert data["candidate"] is None


@pytest.mark.parametrize("candidate_id", [None, "c1"])
def test_isbn_only_from_the_matched_candidate(client: TestClient, admin_with_key,
                                              messy_book: Book, sources, candidate_id):
    """c2's ISBN is another book's: it never lands on this one, whether the
    model matched nothing or matched c1."""
    ai.set_provider_override(FakeProvider(responses=[_answer(
        candidate_id=candidate_id, isbn=OTHER.isbn)]))
    r = client.post(f"/api/ai/books/{messy_book.id}/fix")
    assert r.status_code == 200, r.text
    assert r.json()["proposal"]["isbn"] is None


def test_sanitize_edge_rails():
    """Each of these model values comes out as "no change"."""
    book = SimpleNamespace(title="T", author="A", description="Same desc", publisher=None,
                           year=2000, language="en", isbn="978-0-00-000000-2", series="S",
                           series_index=1.0, tags=[])
    cand = MetadataCandidate(source="open_library", source_id="X1", title="T",
                             description="  Same desc ", cover_url=None, isbn="9780000000002")
    raw = _answer(series_index=-1, year=0, language="abcdefghi", isbn="9780000000002",
                  use_candidate_cover=True, use_candidate_description=True)
    out = fix_book.sanitize(raw, book, [cand], cand)
    for key in ("series_index", "year", "language", "isbn", "cover_url", "description"):
        assert out[key] is None, key
    assert fix_book.sanitize(_answer(year=2101), book, [cand], cand)["year"] is None


def test_series_siblings_are_evidence(client: TestClient, db: Session, admin_with_key,
                                      make_book, sources):
    """The model sees the series' title pattern, so it can follow it instead of
    stripping a titled volume back to a bare title."""
    for i in (1, 2, 3):
        make_book(title=f"Lantern Road, Vol. {i}: Part {i}", series="Lantern Road",
                  series_index=float(i))
    target = make_book(title="lantern road 4", series="Lantern Road", series_index=4.0)
    make_book(title="Unrelated", series="Other Road", series_index=1.0)
    fake = FakeProvider(responses=[_answer()])
    ai.set_provider_override(fake)

    r = client.post(f"/api/ai/books/{target.id}/fix")
    assert r.status_code == 200, r.text
    evidence = json.loads(fake.calls[0]["user_content"][0]["text"].split("\n\n", 1)[1])
    assert evidence["series_siblings"] == [
        {"title": f"Lantern Road, Vol. {i}: Part {i}", "series_index": float(i)} for i in (1, 2, 3)
    ]
    assert "series_siblings" in fake.calls[0]["system"]


def test_series_siblings_skip_books_the_member_cannot_see(client: TestClient, db: Session,
                                                          admin_user, make_book, sources):
    admin, _ = admin_user
    hidden = make_book(title="Lantern Road, Vol. 1: Secret", series="Lantern Road",
                       series_index=1.0)
    make_book(title="Lantern Road, Vol. 2: Open", series="Lantern Road", series_index=2.0)
    target = make_book(title="lantern road 3", series="Lantern Road", series_index=3.0)
    private = Library(name="Admin Private", is_public=False, owner_id=admin.id)
    db.add(private)
    db.flush()
    private.books.append(hidden)
    db.flush()

    member, headers = _make_user(db, "fixmember3", "member")
    ai_settings.set_user_key(db, member, KEY)
    db.flush()
    fake = FakeProvider(responses=[_answer()])
    ai.set_provider_override(fake)
    r = client.post(f"/api/ai/books/{target.id}/fix", headers=headers)
    assert r.status_code == 200, r.text
    evidence = json.loads(fake.calls[0]["user_content"][0]["text"].split("\n\n", 1)[1])
    assert [s["title"] for s in evidence["series_siblings"]] == ["Lantern Road, Vol. 2: Open"]


def test_empty_tag_list_never_clears_tags(client: TestClient, admin_with_key, messy_book: Book, sources):
    ai.set_provider_override(FakeProvider(responses=[_answer(candidate_id="c1", tags=["Not A Tag"])]))
    r = client.post(f"/api/ai/books/{messy_book.id}/fix")
    assert r.json()["proposal"]["tags"] is None


def test_query_override_is_passed_to_the_fetch(client: TestClient, admin_with_key,
                                               messy_book: Book, sources):
    ai.set_provider_override(FakeProvider(responses=[_answer()]))
    r = client.post(f"/api/ai/books/{messy_book.id}/fix", json={"query": "harbour vale"})
    assert r.status_code == 200, r.text
    assert sources[0]["query_override"] == "harbour vale"


def test_candidate_fetch_failure_still_proposes(client: TestClient, admin_with_key,
                                                messy_book: Book, monkeypatch):
    async def broken(**kwargs):
        raise RuntimeError("sources down")
    monkeypatch.setattr("backend.services.ai.fix_book.fetch_candidates", broken)
    fake = FakeProvider(responses=[_answer(title="The Quiet Harbour", confidence=0.4)])
    ai.set_provider_override(fake)
    r = client.post(f"/api/ai/books/{messy_book.id}/fix")
    assert r.status_code == 200, r.text
    assert r.json()["proposal"]["title"] == "The Quiet Harbour"
    evidence = json.loads(fake.calls[0]["user_content"][0]["text"].split("\n\n", 1)[1])
    assert evidence["candidates"] == []


# ── access ───────────────────────────────────────────────────────────────────

def test_missing_book_is_404(client: TestClient, admin_with_key, sources):
    ai.set_provider_override(FakeProvider(responses=[_answer()]))
    r = client.post("/api/ai/books/999999/fix")
    assert r.status_code == 404


def test_hidden_book_is_404_for_member(client: TestClient, db: Session, admin_user,
                                       messy_book: Book, sources):
    admin, _ = admin_user
    private = Library(name="Admin Private", is_public=False, owner_id=admin.id)
    db.add(private)
    db.flush()
    private.books.append(messy_book)
    db.flush()

    member, headers = _make_user(db, "fixmember", "member")
    ai_settings.set_user_key(db, member, KEY)
    db.flush()
    fake = FakeProvider(responses=[_answer()])
    ai.set_provider_override(fake)

    r = client.post(f"/api/ai/books/{messy_book.id}/fix", headers=headers)
    assert r.status_code == 404
    assert fake.calls == [] and sources == []


def test_visible_book_works_for_member(client: TestClient, db: Session, messy_book: Book, sources):
    member, headers = _make_user(db, "fixmember2", "member")
    ai_settings.set_user_key(db, member, KEY)
    db.flush()
    ai.set_provider_override(FakeProvider(responses=[_answer(title="The Quiet Harbour")]))
    r = client.post(f"/api/ai/books/{messy_book.id}/fix", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["proposal"]["title"] == "The Quiet Harbour"


def test_guest_forbidden(client: TestClient, db: Session, messy_book: Book, sources):
    _, headers = _make_user(db, "fixguest", "guest")
    fake = FakeProvider(responses=[_answer()])
    ai.set_provider_override(fake)
    r = client.post(f"/api/ai/books/{messy_book.id}/fix", headers=headers)
    assert r.status_code == 403
    assert fake.calls == []


def test_without_key_is_409_and_skips_fetch(client: TestClient, messy_book: Book, sources):
    fake = FakeProvider(responses=[_answer()])
    ai.set_provider_override(fake)
    r = client.post(f"/api/ai/books/{messy_book.id}/fix")
    assert r.status_code == 409
    assert sources == [] and fake.calls == []


def test_feature_switched_off_is_404(client: TestClient, db: Session, admin_with_key,
                                     messy_book: Book, sources):
    ai_settings.set_feature_enabled(db, "fix_book", False)
    db.flush()
    ai.set_provider_override(FakeProvider(responses=[_answer()]))
    r = client.post(f"/api/ai/books/{messy_book.id}/fix")
    assert r.status_code == 404
    assert sources == []


def test_refusal_is_422(client: TestClient, admin_with_key, messy_book: Book, sources):
    ai.set_provider_override(FakeProvider(refuse=True))
    r = client.post(f"/api/ai/books/{messy_book.id}/fix")
    assert r.status_code == 422


# ── apply with ai_assisted ───────────────────────────────────────────────────

def _apply_audits(db: Session) -> list[dict]:
    rows = db.query(AuditLog).filter(AuditLog.action == "books.metadata_applied").all()
    return [json.loads(r.details) for r in rows]


def test_apply_with_ai_assisted_is_audited(client: TestClient, db: Session, messy_book: Book):
    r = client.post(f"/api/books/{messy_book.id}/apply-metadata",
                    json={"title": "The Quiet Harbour", "year": 2019, "ai_assisted": True})
    assert r.status_code == 200, r.text
    assert r.json()["title"] == "The Quiet Harbour"
    details = _apply_audits(db)
    assert details == [{"fields": ["title", "year"], "ai_assisted": True}]


def test_apply_without_flag_has_no_ai_marker(client: TestClient, db: Session, messy_book: Book):
    r = client.post(f"/api/books/{messy_book.id}/apply-metadata",
                    json={"author": "M. Vale", "tags": ["Mystery"]})
    assert r.status_code == 200, r.text
    details = _apply_audits(db)
    assert details == [{"fields": ["author", "tags"]}]
