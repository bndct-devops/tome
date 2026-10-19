"""Edition choice in the metadata fetch and the audiobook-ISBN write guard.

The most-used Hardcover edition of a popular light novel is often the
audiobook; its ISBN used to become the candidate's ISBN and from there the
book's. Hardcover is mocked with respx; titles are invented.
"""
import json
from types import SimpleNamespace

import httpx
import pytest
import respx
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from backend.core.config import settings
from backend.models.audit_log import AuditLog
from backend.services import hardcover_editions as hce
from backend.services import metadata_fetch as mf
from backend.services.ai import fix_book
from backend.services.book_updates import isbn_allowed, write_isbn
from backend.services.metadata_fetch import MetadataCandidate

HC = mf.HARDCOVER_URL
GB = "https://www.googleapis.com/books/v1/volumes"
OL = "https://openlibrary.org/search.json"

AUDIO_ISBN = "9781039400001"
EBOOK_ISBN = "9781039400002"
PHYS_ISBN = "9781039400003"
DE_ISBN = "9783000000004"


def _ed(id_, fmt, isbn, *, lang="en", users=0, pages=None, publisher=None):
    return {"id": id_, "reading_format_id": fmt, "isbn_13": isbn, "isbn_10": None,
            "language": {"code2": lang} if lang else None, "users_count": users,
            "pages": pages, "publisher": {"name": publisher} if publisher else None}


AUDIO = _ed(1, 2, AUDIO_ISBN, users=900, publisher="Podcast House")
EBOOK = _ed(2, 4, EBOOK_ISBN, users=10, pages=610, publisher="Lantern Press")
PHYS = _ed(3, 1, PHYS_ISBN, users=50, pages=640, publisher="Lantern Print")
DE_EBOOK = _ed(4, 4, DE_ISBN, lang="de", users=100, pages=700, publisher="Laterne Verlag")


def _responder(editions, doc_isbns=(AUDIO_ISBN, EBOOK_ISBN)):
    def respond(request):
        body = request.read().decode()
        if "SearchBook" in body:
            doc = {"id": 42, "title": "Glass Harbor 8", "author_names": ["Ada Quill"],
                   "contribution_types": ["Author"], "release_year": 2023,
                   "isbns": list(doc_isbns), "genres": ["Fantasy"]}
            return httpx.Response(200, json={"data": {"search": {
                "ids": [42], "results": {"hits": [{"document": doc}]}}}})
        assert "limit: 30" in body          # the book's editions, not just one
        return httpx.Response(200, json={"data": {"books": [
            {"id": 42, "editions": editions, "book_series": []}]}})
    return respond


@pytest.fixture(autouse=True)
def _hc(monkeypatch):
    monkeypatch.setattr(settings, "hardcover_token", "hc_test")
    monkeypatch.setattr(settings, "google_books_key", None)
    mf._source_cache.clear()
    yield
    mf._source_cache.clear()


def _mock(editions, gb=None, **kw):
    respx.post(HC).mock(side_effect=_responder(editions, **kw))
    respx.get(GB).mock(return_value=httpx.Response(200, json=gb or {"items": []}))
    respx.get(OL).mock(return_value=httpx.Response(200, json={"docs": []}))


async def _fetch(language=None):
    result = await mf.fetch_candidates("Glass Harbor 8", author="Ada Quill", language=language)
    return next(c for c in result.candidates if c.source == "hardcover")


# ── pick_edition ─────────────────────────────────────────────────────────────

def test_pick_edition_rules():
    assert hce.pick_edition([AUDIO, EBOOK, PHYS])["id"] == 2              # ebook over physical over audio
    assert hce.pick_edition([AUDIO, PHYS])["id"] == 3                     # physical when no ebook
    assert hce.pick_edition([AUDIO]) is None                              # never audio
    assert hce.pick_edition([DE_EBOOK, PHYS], language="en")["id"] == 3   # language first
    assert hce.pick_edition([DE_EBOOK, PHYS], language="de")["id"] == 4
    assert hce.pick_edition([DE_EBOOK, PHYS])["id"] == 3                  # unknown language = English
    no_isbn = _ed(5, 4, None, users=999)
    assert hce.pick_edition([no_isbn, PHYS])["id"] == 5
    assert hce.pick_edition([no_isbn, PHYS], require_isbn=True)["id"] == 3


# ── the fetch ────────────────────────────────────────────────────────────────

@pytest.mark.anyio
@respx.mock
async def test_fetch_takes_the_ebook_edition_not_the_popular_audiobook():
    _mock([AUDIO, PHYS, EBOOK])
    c = await _fetch()
    assert c.isbn == EBOOK_ISBN
    assert c.edition_format == "ebook"
    assert c.publisher == "Lantern Press"
    assert c.page_count == 610


@pytest.mark.anyio
@respx.mock
async def test_fetch_falls_back_to_physical():
    _mock([AUDIO, PHYS])
    c = await _fetch()
    assert c.isbn == PHYS_ISBN
    assert c.edition_format == "physical"


@pytest.mark.anyio
@respx.mock
async def test_fetch_prefers_the_books_language():
    _mock([DE_EBOOK, PHYS, AUDIO])
    assert (await _fetch(language="en")).isbn == PHYS_ISBN
    mf._source_cache.clear()
    assert (await _fetch(language="de")).isbn == DE_ISBN


@pytest.mark.anyio
@respx.mock
async def test_fetch_only_audio_gives_no_isbn_anywhere():
    """No text edition: no ISBN, and a Google record of the same book that
    carries the audiobook ISBN loses it too."""
    gb = {"items": [{"id": "g1", "volumeInfo": {
        "title": "Glass Harbor 8 (Audiobook)", "authors": ["Someone Else"], "language": "en",
        "industryIdentifiers": [{"type": "ISBN_13", "identifier": AUDIO_ISBN}]}}]}
    _mock([AUDIO], gb=gb, doc_isbns=(AUDIO_ISBN,))
    result = await mf.fetch_candidates("Glass Harbor 8", author="Ada Quill")
    assert result.candidates
    assert all(c.isbn != AUDIO_ISBN for c in result.candidates)
    hc = next(c for c in result.candidates if c.source == "hardcover")
    assert hc.isbn is None
    assert hc.publisher is None          # the audiobook's publisher is not the book's
    assert hce.is_known_audio(AUDIO_ISBN)


@pytest.mark.anyio
@respx.mock
async def test_fetch_without_edition_data_keeps_the_document_isbn_as_unknown():
    _mock([], doc_isbns=(EBOOK_ISBN,))
    c = await _fetch()
    assert c.isbn == EBOOK_ISBN
    assert c.edition_format == "unknown"


# ── the write guard ──────────────────────────────────────────────────────────

def test_isbn_allowed_and_write_isbn():
    assert not isbn_allowed(AUDIO_ISBN, "audiobook")
    assert isbn_allowed(EBOOK_ISBN, "ebook")
    assert isbn_allowed(AUDIO_ISBN, None)
    hce.note_audio_isbn(AUDIO_ISBN)
    assert not isbn_allowed(AUDIO_ISBN, None)          # known from an earlier fetch
    assert not isbn_allowed("978-1-0394-0000-1", "unknown")
    book = SimpleNamespace(isbn=None)
    assert not write_isbn(book, AUDIO_ISBN, "unknown")
    assert book.isbn is None
    assert write_isbn(book, EBOOK_ISBN, "ebook")
    assert book.isbn == EBOOK_ISBN


def test_apply_metadata_refuses_an_audiobook_isbn(client: TestClient, db: Session, make_book):
    book = make_book(title="Glass Harbor 8", isbn=None)
    r = client.post(f"/api/books/{book.id}/apply-metadata", json={
        "isbn": AUDIO_ISBN, "isbn_edition_format": "audiobook", "publisher": "Lantern Press"})
    assert r.status_code == 200, r.text
    db.refresh(book)
    assert book.isbn is None
    assert book.publisher == "Lantern Press"            # the rest still applies
    row = db.query(AuditLog).filter(AuditLog.action == "books.metadata_applied").one()
    details = json.loads(row.details)
    assert "isbn" not in details["fields"]
    assert details["isbn_refused"] == "audiobook"

    r = client.post(f"/api/books/{book.id}/apply-metadata", json={
        "isbn": EBOOK_ISBN, "isbn_edition_format": "ebook"})
    assert r.status_code == 200
    db.refresh(book)
    assert book.isbn == EBOOK_ISBN


def test_isbn_change_clears_an_automatic_hardcover_pin(make_book):
    book = make_book(title="Glass Harbor 8", isbn=AUDIO_ISBN)
    book.hardcover_book_id, book.hardcover_edition_id = 42, 1
    book.hardcover_match_method, book.hardcover_pages = "isbn13", None
    book.isbn = EBOOK_ISBN
    assert book.hardcover_book_id is None and book.hardcover_edition_id is None
    assert book.hardcover_match_method is None          # the next sync re-matches

    manual = make_book(title="Glass Harbor 9", isbn=AUDIO_ISBN)
    manual.hardcover_book_id, manual.hardcover_edition_id = 43, 7
    manual.hardcover_match_method = "manual"
    manual.isbn = EBOOK_ISBN
    assert manual.hardcover_book_id == 43 and manual.hardcover_edition_id == 7

    same = make_book(title="Glass Harbor 10", isbn=EBOOK_ISBN)
    same.hardcover_book_id, same.hardcover_match_method = 44, "isbn13"
    same.isbn = "978-1-0394-0000-2"                    # same ISBN, other spelling
    assert same.hardcover_book_id == 44


# ── AI fix ───────────────────────────────────────────────────────────────────

def _answer(**kw):
    base = {"candidate_id": "c1", "title": None, "author": None, "publisher": None, "year": None,
            "language": None, "isbn": None, "series": None, "series_index": None, "tags": None,
            "use_candidate_description": False, "use_candidate_cover": False,
            "confidence": 0.9, "evidence": "x"}
    base.update(kw)
    return base


def test_ai_fix_sanitiser_drops_an_audiobook_isbn():
    book = SimpleNamespace(title="Glass Harbor 8", author="Ada Quill", description=None,
                           publisher=None, year=None, language="en", isbn=None, series=None,
                           series_index=None, tags=[], cover_path=None)
    audio = MetadataCandidate(source="hardcover", source_id="42", title="Glass Harbor 8",
                              isbn=AUDIO_ISBN, edition_format="audiobook")
    assert fix_book.sanitize(_answer(isbn=AUDIO_ISBN), book, [audio], audio)["isbn"] is None
    ebook = MetadataCandidate(source="hardcover", source_id="42", title="Glass Harbor 8",
                              isbn=EBOOK_ISBN, edition_format="ebook")
    assert fix_book.sanitize(_answer(isbn=EBOOK_ISBN), book, [ebook], ebook)["isbn"] == EBOOK_ISBN
    # the re-fetch fill takes the same rail
    proposal = {k: None for k in fix_book.FIELDS}
    assert fix_book.fill_from_candidate(proposal, book, audio)["isbn"] is None
    assert "audiobook" in fix_book.SYSTEM_PROMPT
