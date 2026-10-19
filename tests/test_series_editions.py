"""Fix editions on a series: POST /api/series/{name}/fix-editions and the
pass the AI series cleanup runs after its apply. Hardcover's ISBN lookup is
mocked with respx, the metadata fetch with a stub; titles are invented."""
import json

import httpx
import pytest
import respx
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from backend.core.config import settings
from backend.core.security import create_access_token, hash_password
from backend.models.audit_log import AuditLog
from backend.models.user import User
from backend.services import series_editions
from backend.services.ai import settings as ai_settings
from backend.services.metadata_fetch import HARDCOVER_URL, FetchResult, MetadataCandidate

SERIES = "Glass Harbor"
AUDIO_ISBN = "9781039400001"
GOOD_ISBN = "9781039400009"
REAL_RUN = series_editions._run


@pytest.fixture(autouse=True)
def _real_pass(monkeypatch):
    monkeypatch.setattr(series_editions, "_run", REAL_RUN)
    monkeypatch.setattr(settings, "hardcover_token", "hc_test")


@pytest.fixture()
def fetches(monkeypatch):
    """Stub fetch: one ebook candidate per volume, ISBN 97810394001NN."""
    calls: list[dict] = []

    async def fake_fetch(**kw):
        calls.append(kw)
        idx = int(kw["series_index"])
        return FetchResult(candidates=[
            MetadataCandidate(source="hardcover", source_id="x", title="Unrelated Cookbook",
                              author="Chef", isbn="9780000000017", edition_format="ebook"),
            MetadataCandidate(source="hardcover", source_id=str(idx), title=kw["title"],
                              author=kw["author"], isbn=f"97810394001{idx:02d}",
                              edition_format="ebook", publisher="Lantern Press", year=2020 + idx),
        ], query_used="q")

    monkeypatch.setattr(series_editions, "fetch_candidates", fake_fetch)
    return calls


def _lookup_mock():
    """EditionByIsbn: the audiobook ISBN is audio, everything else is a book."""
    def respond(request):
        body = json.loads(request.read().decode())
        isbn = body["variables"]["isbn"]
        fmt = 2 if isbn == AUDIO_ISBN else 1
        return httpx.Response(200, json={"data": {"editions": [
            {"id": 1, "title": "x", "pages": None, "book_id": 9, "reading_format_id": fmt,
             "language": {"code2": "en"}, "book": {"id": 9, "title": "x", "pages": 1, "slug": "x"}}]}})
    return respx.post(HARDCOVER_URL).mock(side_effect=respond)


@pytest.fixture()
def volumes(make_book):
    return [
        make_book(title="Glass Harbor 1", author="Ada Quill", series=SERIES, series_index=1.0,
                  isbn=None),
        make_book(title="Glass Harbor 2", author="Ada Quill", series=SERIES, series_index=2.0,
                  isbn=AUDIO_ISBN, publisher="Podcast House"),
        make_book(title="Glass Harbor 3", author="Ada Quill", series=SERIES, series_index=3.0,
                  isbn=GOOD_ISBN),
    ]


@respx.mock
def test_fix_editions_fills_null_replaces_audio_keeps_good(client: TestClient, db: Session,
                                                           volumes, fetches):
    lookups = _lookup_mock()
    v1, v2, v3 = volumes
    v2.hardcover_book_id, v2.hardcover_edition_id, v2.hardcover_match_method = 9, 1, "isbn13"
    db.flush()

    r = client.post(f"/api/series/{SERIES}/fix-editions")
    assert r.status_code == 200, r.text
    fixed = {f["book_id"]: f for f in r.json()["isbns_fixed"]}
    assert set(fixed) == {v1.id, v2.id}
    assert fixed[v1.id]["from"] is None and fixed[v1.id]["to"] == "9781039400101"
    assert fixed[v2.id]["from"] == AUDIO_ISBN and fixed[v2.id]["to"] == "9781039400102"
    assert fixed[v2.id]["reason"] == "audiobook"

    db.refresh(v1), db.refresh(v2), db.refresh(v3)
    assert v1.isbn == "9781039400101" and v1.publisher == "Lantern Press" and v1.year == 2021
    assert v2.isbn == "9781039400102" and v2.publisher == "Podcast House"   # only filled when empty
    assert v3.isbn == GOOD_ISBN
    assert v2.hardcover_book_id is None and v2.hardcover_edition_id is None  # sync re-pins
    # only volumes that needed it were fetched; each stored ISBN looked up once
    assert sorted(c["series_index"] for c in fetches) == [1.0, 2.0]
    assert all(c["isbn"] is None for c in fetches)
    looked_up = [json.loads(c.request.read().decode())["variables"]["isbn"] for c in lookups.calls]
    assert sorted(looked_up) == sorted([AUDIO_ISBN, GOOD_ISBN])

    row = db.query(AuditLog).filter(AuditLog.action == "series.editions_fixed").one()
    details = json.loads(row.details)
    assert details["checked"] == 3
    assert {f["book_id"] for f in details["isbns_fixed"]} == {v1.id, v2.id}


@respx.mock
def test_audio_isbn_without_an_agreeing_candidate_is_removed(client: TestClient, db: Session,
                                                             make_book, monkeypatch):
    _lookup_mock()
    book = make_book(title="Glass Harbor 4", author="Ada Quill", series=SERIES,
                     series_index=4.0, isbn=AUDIO_ISBN)

    async def no_match(**kw):
        return FetchResult(candidates=[MetadataCandidate(
            source="hardcover", source_id="x", title="Unrelated Cookbook", author="Chef",
            isbn="9780000000017", edition_format="ebook")], query_used="q")

    monkeypatch.setattr(series_editions, "fetch_candidates", no_match)
    r = client.post(f"/api/series/{SERIES}/fix-editions")
    assert r.status_code == 200
    assert r.json()["isbns_fixed"] == [{"book_id": book.id, "title": "Glass Harbor 4",
                                        "from": AUDIO_ISBN, "to": None, "reason": "audiobook"}]
    db.refresh(book)
    assert book.isbn is None


@respx.mock
def test_candidate_with_an_audio_isbn_is_skipped(client: TestClient, db: Session, make_book,
                                                 monkeypatch):
    _lookup_mock()
    book = make_book(title="Glass Harbor 5", author="Ada Quill", series=SERIES,
                     series_index=5.0, isbn=None)

    async def only_audio(**kw):
        return FetchResult(candidates=[MetadataCandidate(
            source="google_books", source_id="g", title="Glass Harbor 5", author="Ada Quill",
            isbn=AUDIO_ISBN)], query_used="q")

    monkeypatch.setattr(series_editions, "fetch_candidates", only_audio)
    r = client.post(f"/api/series/{SERIES}/fix-editions")
    assert r.status_code == 200
    assert r.json()["isbns_fixed"] == []
    db.refresh(book)
    assert book.isbn is None


def _member(db: Session, name: str, role: str) -> dict:
    user = User(username=name, email=f"{name}@example.com", hashed_password=hash_password("pw123456"),
                is_active=True, is_admin=False, role=role, must_change_password=False)
    db.add(user)
    db.flush()
    return {"Authorization": f"Bearer {create_access_token(subject=user.id)}"}


@respx.mock
def test_fix_editions_auth(client: TestClient, db: Session, volumes, fetches):
    _lookup_mock()
    guest = _member(db, "guestie", "guest")
    assert client.post(f"/api/series/{SERIES}/fix-editions", headers=guest).status_code == 403
    # a member only touches books they uploaded: none here
    member = _member(db, "membie", "member")
    r = client.post(f"/api/series/{SERIES}/fix-editions", headers=member)
    assert r.status_code == 200
    assert r.json() == {"checked": 0, "isbns_fixed": []}
    assert fetches == []
    assert client.post("/api/series/__unserialized__/fix-editions").status_code == 404
    client.headers.pop("Authorization", None)
    assert client.post(f"/api/series/{SERIES}/fix-editions").status_code == 401


@respx.mock
def test_series_cleanup_apply_runs_the_editions_pass(client: TestClient, db: Session,
                                                     admin_user, volumes, fetches):
    _lookup_mock()
    user, _ = admin_user
    ai_settings.set_user_key(db, user, "sk-ant-test-editions-000000000000Ef56")
    db.flush()
    r = client.post(f"/api/ai/series/{SERIES}/cleanup/apply", json={"status": "finished"})
    assert r.status_code == 200, r.text
    fixed = r.json()["isbns_fixed"]
    assert {f["book_id"] for f in fixed} == {volumes[0].id, volumes[1].id}
    row = db.query(AuditLog).filter(AuditLog.action == "series.editions_fixed").one()
    assert json.loads(row.details)["via"] == "series_cleanup"
