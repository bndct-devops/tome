"""The editions pass the AI series cleanup runs after its apply: the same rule
as the Hardcover sync (hardcover_editions.decide_isbn) over each volume's
Hardcover editions. Hardcover is mocked with respx, the metadata fetch with
a stub; titles are invented."""
import json

import httpx
import pytest
import respx
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from backend.core.config import settings
from backend.models.audit_log import AuditLog
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


def _ed(id_, fmt, isbn, users=0, publisher=None):
    return {"id": id_, "reading_format_id": fmt, "isbn_13": isbn, "isbn_10": None,
            "language": {"code2": "en"}, "users_count": users, "pages": 300,
            "publisher": {"name": publisher} if publisher else None}


def _editions(idx: int) -> list[dict]:
    """Hardcover book 900+idx: a popular audiobook and an ebook."""
    audio = AUDIO_ISBN if idx == 2 else f"97810394009{idx:02d}"
    return [_ed(idx * 10 + 1, 2, audio, users=900),
            _ed(idx * 10 + 2, 4, f"97810394001{idx:02d}", users=100, publisher="Lantern Press")]


@pytest.fixture()
def fetches(monkeypatch):
    """Stub fetch: an unrelated Hardcover hit, then the volume's own."""
    calls: list[dict] = []

    async def fake_fetch(**kw):
        calls.append(kw)
        idx = int(kw["series_index"])
        return FetchResult(candidates=[
            MetadataCandidate(source="hardcover", source_id="1", title="Unrelated Cookbook",
                              author="Chef", isbn="9780000000017", edition_format="ebook"),
            MetadataCandidate(source="hardcover", source_id=str(900 + idx), title=kw["title"],
                              author=kw["author"], isbn=f"97810394001{idx:02d}",
                              edition_format="ebook"),
        ], query_used="q")

    monkeypatch.setattr(series_editions, "fetch_candidates", fake_fetch)
    return calls


def _hardcover_mock():
    def respond(request):
        body = json.loads(request.read().decode())
        if "EditionByIsbn" in body["query"]:
            isbn = body["variables"]["isbn"]
            if isbn == AUDIO_ISBN:
                ed = _editions(2)[0]
                return httpx.Response(200, json={"data": {"editions": [
                    {**ed, "title": "Glass Harbor, Vol. 2", "book_id": 902,
                     "book": {"id": 902, "title": "Glass Harbor, Vol. 2", "pages": 300, "slug": "x"}}]}})
            if isbn == GOOD_ISBN:
                return httpx.Response(200, json={"data": {"editions": [
                    {**_ed(5, 1, GOOD_ISBN), "title": "Glass Harbor 3", "book_id": 903,
                     "book": {"id": 903, "title": "Glass Harbor 3", "pages": 300, "slug": "y"}}]}})
            return httpx.Response(200, json={"data": {"editions": []}})
        if "BookEdition" in body["query"]:
            hc_id = body["variables"]["id"]
            return httpx.Response(200, json={"data": {"books": [
                {"id": hc_id, "pages": 300, "slug": "z", "editions": _editions(hc_id - 900)}]}})
        return httpx.Response(200, json={"data": {}})
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
def test_series_cleanup_apply_corrects_editions(client: TestClient, db: Session,
                                                admin_user, volumes, fetches):
    _hardcover_mock()
    user, _ = admin_user
    v1, v2, v3 = volumes
    v2.hardcover_book_id, v2.hardcover_edition_id, v2.hardcover_match_method = 902, 21, "isbn13"
    ai_settings.set_user_key(db, user, "sk-ant-test-editions-000000000000Ef56")
    db.flush()

    r = client.post(f"/api/ai/series/{SERIES}/cleanup/apply", json={"status": "finished"})
    assert r.status_code == 200, r.text
    fixed = {f["book_id"]: f for f in r.json()["isbns_fixed"]}
    assert set(fixed) == {v1.id, v2.id}
    assert fixed[v1.id]["from"] is None and fixed[v1.id]["to"] == "9781039400101"
    assert fixed[v1.id]["reason"] == "missing"
    assert fixed[v2.id]["from"] == AUDIO_ISBN and fixed[v2.id]["to"] == "9781039400102"
    assert fixed[v2.id]["reason"] == "audiobook"

    db.refresh(v1), db.refresh(v2), db.refresh(v3)
    assert v1.isbn == "9781039400101" and v1.publisher == "Lantern Press"
    assert v2.isbn == "9781039400102" and v2.publisher == "Podcast House"  # only filled when empty
    assert v3.isbn == GOOD_ISBN
    assert v2.hardcover_book_id is None                  # automatic match: the sync re-pins
    # only the unmatched volume without an ISBN needed a fetch
    assert [c["series_index"] for c in fetches] == [1.0]

    rows = db.query(AuditLog).filter(AuditLog.action == "books.isbn_corrected").all()
    assert {r.resource_id for r in rows} == {v1.id, v2.id}
    assert all(json.loads(r.details)["via"] == "series_cleanup" for r in rows)


@respx.mock
def test_audio_isbn_without_another_edition_is_removed(db: Session, admin_user, make_book,
                                                       monkeypatch):
    _hardcover_mock()
    user, _ = admin_user
    book = make_book(title="Glass Harbor 4", author="Ada Quill", series=SERIES,
                     series_index=4.0, isbn=AUDIO_ISBN)

    async def no_match(**kw):
        return FetchResult(candidates=[], query_used="q")

    monkeypatch.setattr(series_editions, "fetch_candidates", no_match)
    # The ISBN hit fails the volume check (it names volume 2), so there is no
    # Hardcover book to take an edition from.
    result = series_editions.fix_editions(db, user, SERIES)
    assert result["isbns_fixed"] == [{"book_id": book.id, "title": "Glass Harbor 4",
                                      "from": AUDIO_ISBN, "to": None, "reason": "audiobook"}]
    assert book.isbn is None


@respx.mock
def test_non_hardcover_candidates_are_not_used(db: Session, admin_user, make_book, monkeypatch):
    _hardcover_mock()
    user, _ = admin_user
    book = make_book(title="Glass Harbor 5", author="Ada Quill", series=SERIES,
                     series_index=5.0, isbn=None)

    async def only_google(**kw):
        return FetchResult(candidates=[MetadataCandidate(
            source="google_books", source_id="g", title="Glass Harbor 5", author="Ada Quill",
            isbn="9781039400555")], query_used="q")

    monkeypatch.setattr(series_editions, "fetch_candidates", only_google)
    assert series_editions.fix_editions(db, user, SERIES)["isbns_fixed"] == []
    assert book.isbn is None
