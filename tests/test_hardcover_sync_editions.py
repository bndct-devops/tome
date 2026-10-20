"""The Hardcover sync's editions check: the reconcile cycle repairs a stored
ISBN that is an audiobook edition's (or fills a missing one) from the matched
Hardcover book's editions, keeps the match it is syncing, and checks each
book once. Hardcover is mocked with respx; titles are invented."""
import json
from datetime import datetime, timedelta

import httpx
import pytest
import respx

from backend.models.audit_log import AuditLog
from backend.models.user_book_status import UserBookStatus
from backend.services import hardcover_sync as hs
from backend.services.hardcover_sync import HARDCOVER_URL

AUDIO_ISBN = "9781039400001"
EBOOK_ISBN = "9781039400002"
PHYS_ISBN = "9781039400003"
HC_BOOK = 77


@pytest.fixture(autouse=True)
def _no_throttle(monkeypatch):
    monkeypatch.setattr(hs, "MIN_REQUEST_SPACING", 0.0)


@pytest.fixture(autouse=True)
def _run_cache():
    """What run_sync_cycle does: one edition cache per cycle."""
    token = hs._run_editions.set({})
    yield
    hs._run_editions.reset(token)


def _ed(id_, fmt, isbn, *, users=0, pages=None, publisher=None):
    return {"id": id_, "reading_format_id": fmt, "isbn_13": isbn, "isbn_10": None,
            "language": {"code2": "en"}, "users_count": users, "pages": pages,
            "publisher": {"name": publisher} if publisher else None}


EDITIONS = [
    _ed(1, 2, AUDIO_ISBN, users=900),                                # the popular audiobook
    _ed(2, 4, EBOOK_ISBN, users=300, pages=310, publisher="Lantern Press"),
    _ed(3, 1, PHYS_ISBN, users=500, pages=320),
]


def _link(user):
    from backend.core.crypto import encrypt_secret
    user.hardcover_token = encrypt_secret("Bearer t")
    user.hardcover_token_status = "ok"
    user.hardcover_user_id = 42


def _hardcover(seen: list[str]):
    """EditionByIsbn: AUDIO_ISBN is the audiobook edition of book 77.
    BookEdition: book 77's editions. Pushes succeed."""
    def respond(request):
        body = request.read().decode()
        seen.append(body)
        if "EditionByIsbn" in body:
            isbn = json.loads(body)["variables"]["isbn"]
            match = [e for e in EDITIONS if e["isbn_13"] == isbn]
            return httpx.Response(200, json={"data": {"editions": [
                {**e, "title": "Glass Harbor 2", "book_id": HC_BOOK,
                 "book": {"id": HC_BOOK, "title": "Glass Harbor 2", "pages": 310, "slug": "gh-2"}}
                for e in match]}})
        if "BookEdition" in body:
            return httpx.Response(200, json={"data": {"books": [
                {"id": HC_BOOK, "pages": 310, "slug": "gh-2", "editions": EDITIONS}]}})
        if "query UserBook" in body:
            return httpx.Response(200, json={"data": {"user_books": []}})
        if "mutation InsertUserBook" in body:
            return httpx.Response(200, json={"data": {"insert_user_book": {"id": 1001, "error": None}}})
        if "mutation InsertRead" in body:
            return httpx.Response(200, json={"data": {"insert_user_book_read": {"id": 2002, "error": None}}})
        if "mutation UpdateUserBook" in body:
            return httpx.Response(200, json={"data": {"update_user_book": {"id": 1001, "error": None}}})
        return httpx.Response(200, json={"data": {}})
    return respx.post(HARDCOVER_URL).mock(side_effect=respond)


def _count(seen, op):
    return sum(1 for b in seen if op in b)


def _matched(make_book, *, isbn, method="isbn13", title="Glass Harbor 2"):
    book = make_book(title=title, author="Ada Quill", isbn=isbn)
    book.hardcover_book_id = HC_BOOK
    book.hardcover_edition_id = 3
    book.hardcover_pages = 320
    book.hardcover_match_method = method
    book.hardcover_matched_at = datetime.utcnow() - timedelta(days=3)
    return book


def _row(db, user, book, **kw):
    row = UserBookStatus(user_id=user.id, book_id=book.id, status=kw.pop("status", "read"), **kw)
    db.add(row)
    db.flush()
    return row


def _corrections(db):
    return [json.loads(r.details) | {"book_id": r.resource_id}
            for r in db.query(AuditLog).filter(AuditLog.action == "books.isbn_corrected")]


@respx.mock
async def test_matching_corrects_an_audiobook_isbn_and_keeps_the_match(db, admin_user, make_book):
    """A book matched in this cycle by its audiobook ISBN: the edition list
    the matching fetched supplies the new ISBN (no further request), and the
    book stays matched through the ISBN change."""
    user, _ = admin_user
    _link(user)
    book = make_book(title="Glass Harbor 2", author="Ada Quill", isbn=AUDIO_ISBN)
    _row(db, user, book, status="reading", progress_pct=0.4)
    seen: list[str] = []
    _hardcover(seen)

    async with httpx.AsyncClient() as client:
        stats = await hs.sync_user(db, client, user, hs._Budget(300))

    assert stats["pushed"] == 1 and stats["isbns_corrected"] == 1
    db.refresh(book)
    assert book.isbn == EBOOK_ISBN
    assert book.publisher == "Lantern Press"
    assert book.hardcover_book_id == HC_BOOK
    assert book.hardcover_match_method == "isbn13"
    assert book.hardcover_edition_id == 2
    assert book.hardcover_editions_checked_at is not None
    assert _count(seen, "BookEdition") == 1           # the matching's own fetch
    assert _corrections(db) == [{"book_id": book.id, "from": AUDIO_ISBN, "to": EBOOK_ISBN,
                                 "reason": "audiobook", "via": "hardcover_sync"}]


@respx.mock
async def test_check_corrects_matched_books_and_leaves_the_rest(db, admin_user, make_book):
    user, _ = admin_user
    _link(user)
    audio = _matched(make_book, isbn=AUDIO_ISBN, title="Glass Harbor 2")
    missing = _matched(make_book, isbn=None, method="search", title="Glass Harbor 3")
    good = _matched(make_book, isbn=PHYS_ISBN, title="Glass Harbor 4")
    excluded = make_book(title="Glass Harbor 5", author="Ada Quill", isbn=AUDIO_ISBN)
    excluded.hardcover_match_method = "excluded"
    for b in (audio, missing, good, excluded):
        _row(db, user, b)
    db.commit()
    good_updated = good.updated_at
    seen: list[str] = []
    _hardcover(seen)

    async with httpx.AsyncClient() as client:
        fixed = await hs.check_user_editions(db, client, user, hs._Budget(300), 100, set())

    assert fixed == 2
    for b in (audio, missing, good, excluded):
        db.refresh(b)
    assert audio.isbn == EBOOK_ISBN and audio.hardcover_book_id == HC_BOOK
    assert audio.hardcover_match_method == "isbn13"
    assert missing.isbn == EBOOK_ISBN and missing.hardcover_book_id == HC_BOOK
    assert missing.hardcover_match_method == "search"
    assert good.isbn == PHYS_ISBN and good.hardcover_edition_id == 3
    assert good.hardcover_editions_checked_at is not None
    assert good.updated_at == good_updated              # a check is not an edit
    assert excluded.isbn == AUDIO_ISBN and excluded.hardcover_editions_checked_at is None
    # one edition list for the Hardcover book, cached for the run
    assert _count(seen, "BookEdition") == 1
    assert {c["book_id"]: c["reason"] for c in _corrections(db)} == {
        audio.id: "audiobook", missing.id: "missing"}

    # checked once: the next cycle asks Hardcover nothing
    seen.clear()
    hs._run_editions.set({})
    async with httpx.AsyncClient() as client:
        assert await hs.check_user_editions(db, client, user, hs._Budget(300), 100, set()) == 0
    assert seen == []


@respx.mock
async def test_manual_match_keeps_its_pin_but_loses_the_audiobook_isbn(db, admin_user, make_book):
    user, _ = admin_user
    _link(user)
    manual = _matched(make_book, isbn=AUDIO_ISBN, method="manual", title="Glass Harbor 6")
    manual_blank = _matched(make_book, isbn=None, method="manual", title="Glass Harbor 7")
    for b in (manual, manual_blank):
        _row(db, user, b)
    db.commit()
    _hardcover([])

    async with httpx.AsyncClient() as client:
        assert await hs.check_user_editions(db, client, user, hs._Budget(300), 100, set()) == 1

    db.refresh(manual), db.refresh(manual_blank)
    assert manual.isbn == EBOOK_ISBN
    assert manual.hardcover_match_method == "manual"
    assert manual.hardcover_book_id == HC_BOOK
    assert manual.hardcover_edition_id == 3              # the user's pin stays
    assert manual_blank.isbn is None                     # a manual match is not filled


@respx.mock
async def test_check_spends_only_spare_budget(db, admin_user, make_book):
    user, _ = admin_user
    _link(user)
    book = _matched(make_book, isbn=AUDIO_ISBN)
    _row(db, user, book)
    db.commit()
    seen: list[str] = []
    _hardcover(seen)
    budget = hs._Budget(1)
    budget.used = 1
    async with httpx.AsyncClient() as client:
        assert await hs.check_user_editions(db, client, user, budget, 100, set()) == 0
    assert seen == [] and not budget.exhausted
    db.refresh(book)
    assert book.isbn == AUDIO_ISBN and book.hardcover_editions_checked_at is None


def test_isbn_edit_by_a_person_still_clears_the_automatic_match(make_book):
    book = _matched(make_book, isbn=PHYS_ISBN)
    book.hardcover_editions_checked_at = datetime.utcnow()
    book.isbn = EBOOK_ISBN
    assert book.hardcover_book_id is None and book.hardcover_match_method is None
    assert book.hardcover_editions_checked_at is None


def test_fix_editions_endpoint_is_gone(client):
    """No manual button any more: no POST route on the series path at all
    (405 because GET /series/{name:path} matches the URL)."""
    assert client.post("/api/series/Glass Harbor/fix-editions").status_code == 405
    assert not [r for r in client.app.routes
                if getattr(r, "path", "").endswith("/fix-editions")]
