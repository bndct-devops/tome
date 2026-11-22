"""Books for files a reader has and Tome does not.

A reader that sends document metadata with its progress push (CrossInk's
"Send Document Metadata", KOReader's optional ``metadata`` object) tells Tome
which book a file hash is. An unlinked document is then matched exactly to a
library book, or gets a book record without a file so its reading shows up.

The rules these tests hold on to:

- nothing is guessed: exact ISBN, or exact title and author, and only when
  one book fits;
- a file-less book never appears where files are listed or served;
- a reader's odd metadata never fails its progress sync;
- when the real file arrives and the document is linked to it, the file-less
  record hands over and goes.
"""
import time

import pytest

from backend.core.security import create_access_token, hash_password
from backend.models.book import BOOK_NO_FILE, Book
from backend.models.kosync import (
    KOSyncDocumentMap,
    KOSyncDocumentMeta,
    KOSyncUser,
    OPDSPendingLink,
    ReadingHistory,
)
from backend.models.library import Library
from backend.models.tome_sync import ReadingSession
from backend.models.user import User
from backend.models.user_book_status import UserBookStatus

STATS = "/api/v1/api/v1"
DOC = "0123456789abcdef0123456789abcdef"
DOC2 = "fedcba9876543210fedcba9876543210"
META = {"filename": "Il nome della rosa.epub", "title": "Il nome della rosa", "authors": "Umberto Eco"}


# ── helpers ───────────────────────────────────────────────────────────────────

@pytest.fixture()
def acc(db, admin_user):
    """The admin's sync account, under the Tome username (the manual link
    endpoint finds it by that name)."""
    user, _ = admin_user
    row = KOSyncUser(username=user.username, userkey="k" * 32, user_id=user.id)
    db.add(row)
    db.flush()
    return row


def _headers(acc):
    return {"x-auth-user": acc.username, "x-auth-key": acc.userkey}


def _push(client, acc, document=DOC, percentage=0.25, metadata=META, **extra):
    body = {"document": document, "percentage": percentage, "progress": "/body/x[1]",
            "device": "CrossInk", "device_id": "A1B2C3D4E5F6", **extra}
    if metadata is not None:
        body["metadata"] = metadata
    return client.put("/api/v1/syncs/progress", headers=_headers(acc), json=body)


def _session(client, acc, document=DOC, session_id="a" * 32, seconds=600):
    start = int(time.time()) - 7200
    item = {"session_id": session_id, "document": document, "started_at": start,
            "ended_at": start + seconds, "seconds": seconds, "utc_offset_minutes": 60}
    r = client.put(f"{STATS}/stats/sessions", headers=_headers(acc),
                   json={"device_id": "A1B2C3D4E5F6", "items": [item]})
    assert r.status_code == 200, r.text


def _no_file_books(db):
    return db.query(Book).filter(Book.status == BOOK_NO_FILE).all()


def _linked_book_id(db, user, document=DOC):
    row = db.query(KOSyncDocumentMap).filter_by(tome_user_id=user.id, document=document).first()
    return row.book_id if row else None


def _member(db, name="member1"):
    user = User(username=name, email=f"{name}@example.com", hashed_password=hash_password("pass"),
                is_active=True, role="member", must_change_password=False)
    db.add(user)
    db.flush()
    account = KOSyncUser(username=name, userkey="m" * 32, user_id=user.id)
    db.add(account)
    db.flush()
    return user, account, {"Authorization": f"Bearer {create_access_token(user.id)}"}


# ── what the reader sent ──────────────────────────────────────────────────────

def test_the_readers_metadata_is_kept_per_document(db, client, admin_user, acc):
    user, _ = admin_user
    assert _push(client, acc, metadata={**META, "isbn": "978-88-452-9283-3", "series": "S", "series_index": 2}).status_code == 200
    meta = db.query(KOSyncDocumentMeta).one()
    assert (meta.user_id, meta.document, meta.title, meta.authors) == (user.id, DOC, "Il nome della rosa", "Umberto Eco")
    assert (meta.filename, meta.isbn, meta.series, meta.series_index) == ("Il nome della rosa.epub", "978-88-452-9283-3", "S", 2.0)

    _push(client, acc, metadata={**META, "title": "Il nome della rosa (ed. 2020)"})
    db.expire_all()
    assert db.query(KOSyncDocumentMeta).one().title == "Il nome della rosa (ed. 2020)"


def test_a_push_without_metadata_creates_nothing(db, client, acc):
    """Stock KOSync clients: an unknown file stays unlinked, as before."""
    assert _push(client, acc, metadata=None).status_code == 200
    assert db.query(KOSyncDocumentMeta).count() == 0
    assert db.query(KOSyncDocumentMap).count() == 0
    assert _no_file_books(db) == []


@pytest.mark.parametrize("metadata", [
    "a string", 42, [], {}, {"title": 7, "authors": ["x"]}, {"title": "   ", "filename": ""},
    {"title": None, "filename": None, "series_index": "three"},
    {"title": "x" * 5000, "authors": "y" * 5000, "isbn": "9" * 500, "series_index": 1e308},
    {"title": "Ok", "series_index": True},
])
def test_odd_metadata_never_fails_the_progress_sync(db, client, acc, metadata):
    """The position is what the reader came for."""
    r = _push(client, acc, metadata=metadata)
    assert r.status_code == 200, r.text
    assert r.json()["document"] == DOC
    for book in _no_file_books(db):
        assert 0 < len(book.title) <= 500


def test_an_account_without_a_tome_user_gets_no_book(db, client, admin_user):
    unlinked = KOSyncUser(username="nobody", userkey="k" * 32, user_id=None)
    db.add(unlinked)
    db.flush()
    assert _push(client, unlinked).status_code == 200
    assert db.query(KOSyncDocumentMeta).count() == 0
    assert _no_file_books(db) == []


# ── no match: a book without a file ───────────────────────────────────────────

def test_an_unknown_file_gets_a_book_without_a_file(db, client, admin_user, acc):
    user, _ = admin_user
    _session(client, acc)                                   # arrived before the book was known
    assert db.query(ReadingSession).one().book_id is None

    assert _push(client, acc, percentage=0.4, metadata={
        **META, "isbn": "9788845292833", "series": "Narratori", "series_index": 3}).status_code == 200
    db.expire_all()
    book = db.query(Book).filter(Book.status == BOOK_NO_FILE).one()
    assert (book.title, book.author, book.isbn) == ("Il nome della rosa", "Umberto Eco", "9788845292833")
    assert (book.series, book.series_index, book.added_by, book.files) == ("Narratori", 3.0, user.id, [])
    assert _linked_book_id(db, user) == book.id
    # progress, read status and the waiting session all land on it
    status = db.query(UserBookStatus).filter_by(user_id=user.id, book_id=book.id).one()
    assert (status.status, status.progress_pct) == ("reading", 0.4)
    assert db.query(ReadingSession).one().book_id == book.id


def test_later_pushes_reuse_the_same_book(db, client, admin_user, acc):
    user, _ = admin_user
    _push(client, acc, percentage=0.1)
    _push(client, acc, percentage=0.5)
    _push(client, acc, percentage=1.0)
    db.expire_all()
    book = db.query(Book).filter(Book.status == BOOK_NO_FILE).one()
    status = db.query(UserBookStatus).filter_by(user_id=user.id, book_id=book.id).one()
    assert status.status == "read"


def test_a_file_without_a_title_is_named_after_the_file(db, client, acc):
    _push(client, acc, metadata={"filename": "eco-pendolo.epub", "title": "", "authors": ""})
    book = db.query(Book).filter(Book.status == BOOK_NO_FILE).one()
    assert (book.title, book.author) == ("eco-pendolo", None)


def test_a_renamed_file_keeps_its_book(db, client, admin_user, acc):
    """The hash is of the file name, so a rename is a new document. Same
    title and author: same record, not a twin."""
    user, _ = admin_user
    _push(client, acc)
    _push(client, acc, document=DOC2, metadata={**META, "filename": "rosa (1).epub"})
    assert len(_no_file_books(db)) == 1
    assert _linked_book_id(db, user, DOC) == _linked_book_id(db, user, DOC2)


def test_each_user_gets_their_own_file_less_book(db, client, admin_user, acc):
    admin, _ = admin_user
    member, member_acc, _ = _member(db)
    _push(client, acc)
    _push(client, member_acc)
    owners = sorted(b.added_by for b in _no_file_books(db))
    assert owners == sorted([admin.id, member.id])


# ── match: a library book ─────────────────────────────────────────────────────

def test_the_same_title_and_author_links_to_the_library_book(db, client, admin_user, acc, make_book):
    user, _ = admin_user
    book = make_book(title="Il Nome della Rosa", author="umberto  ECO")
    _session(client, acc)
    assert _push(client, acc).status_code == 200
    db.expire_all()
    assert _linked_book_id(db, user) == book.id
    assert _no_file_books(db) == []
    assert db.query(ReadingSession).one().book_id == book.id


def test_the_same_isbn_links_even_when_the_title_differs(db, client, admin_user, acc, make_book):
    user, _ = admin_user
    book = make_book(title="The Name of the Rose", author="Umberto Eco", isbn="978-88-452-9283-3")
    _push(client, acc, metadata={**META, "isbn": "9788845292833"})
    assert _linked_book_id(db, user) == book.id
    assert _no_file_books(db) == []


@pytest.mark.parametrize("title, author", [
    ("Il nome della rosa", "Someone Else"),          # other author
    ("Il nome della rosa", None),                    # the library copy names no author
    ("Il nome della rosa: romanzo", "Umberto Eco"),  # near miss on the title
    ("Il nome della ros", "Umberto Eco"),
])
def test_a_near_miss_is_not_a_match(db, client, admin_user, acc, make_book, title, author):
    """Similar is not the same: the reading goes to its own record instead of
    onto a book that might be another one."""
    user, _ = admin_user
    library_book = make_book(title=title, author=author)
    _push(client, acc)
    linked = _linked_book_id(db, user)
    assert linked != library_book.id
    assert db.get(Book, linked).status == BOOK_NO_FILE


def test_several_matching_books_are_left_for_the_user(db, client, admin_user, acc, make_book):
    user, _ = admin_user
    make_book(title="Il nome della rosa", author="Umberto Eco", file_path="/books/rosa-1.epub")
    make_book(title="Il nome della rosa", author="Umberto Eco", file_path="/books/rosa-2.epub")
    _session(client, acc)
    assert _push(client, acc).status_code == 200
    assert _linked_book_id(db, user) is None
    assert _no_file_books(db) == []
    # ... but the session list can say which book is waiting
    row = client.get("/api/stats/sessions").json()["sessions"][0]
    assert row["book_title"] == "Il nome della rosa (not linked yet)"
    assert row["book_id"] is None


def test_the_last_opds_download_breaks_a_tie(db, client, admin_user, acc, make_book):
    user, _ = admin_user
    make_book(title="Il nome della rosa", author="Umberto Eco", file_path="/books/rosa-1.epub")
    second = make_book(title="Il nome della rosa", author="Umberto Eco", file_path="/books/rosa-2.epub")
    db.add(OPDSPendingLink(user_id=user.id, book_id=second.id))
    db.flush()
    _push(client, acc)
    assert _linked_book_id(db, user) == second.id
    assert db.query(OPDSPendingLink).count() == 0


def test_a_named_book_never_takes_an_unrelated_pending_download(db, client, admin_user, acc, make_book):
    """Without metadata the next unknown push claims the oldest OPDS download
    (a guess). A reader that names its book is not guessed at."""
    user, _ = admin_user
    other = make_book(title="Baudolino", author="Umberto Eco")
    db.add(OPDSPendingLink(user_id=user.id, book_id=other.id))
    db.flush()
    _push(client, acc)
    linked = _linked_book_id(db, user)
    assert linked != other.id and db.get(Book, linked).status == BOOK_NO_FILE
    assert db.query(OPDSPendingLink).count() == 1


def test_a_book_the_user_cannot_see_is_not_matched(db, client, admin_user, make_book):
    admin, _ = admin_user
    member, member_acc, _ = _member(db)
    hidden = make_book(title="Il nome della rosa", author="Umberto Eco")
    library = Library(name="Private", is_public=False, owner_id=admin.id)
    library.books.append(hidden)
    db.add(library)
    db.flush()
    _push(client, member_acc)
    linked = _linked_book_id(db, member)
    assert linked != hidden.id
    book = db.get(Book, linked)
    assert (book.status, book.added_by) == (BOOK_NO_FILE, member.id)


def test_an_existing_link_is_never_moved_by_metadata(db, client, admin_user, acc, make_book):
    user, _ = admin_user
    linked = make_book(title="Linked By Hand", author="A")
    make_book(title="Il nome della rosa", author="Umberto Eco")
    db.add(KOSyncDocumentMap(tome_user_id=user.id, document=DOC, book_id=linked.id))
    db.flush()
    _push(client, acc)
    assert _linked_book_id(db, user) == linked.id


# ── where a file-less book shows, and where it does not ───────────────────────

def test_it_shows_where_reading_shows(db, client, admin_user, acc):
    _session(client, acc, seconds=900)
    _push(client, acc, percentage=0.3)
    book = db.query(Book).filter(Book.status == BOOK_NO_FILE).one()

    focus = client.get("/api/home/focus").json()
    assert focus["ready"] is True
    assert (focus["book"]["book_id"], focus["book"]["title"], focus["book"]["no_file"]) == (book.id, book.title, True)
    assert focus["reading"][0]["no_file"] is True

    session = client.get("/api/stats/sessions").json()["sessions"][0]
    assert (session["book_id"], session["book_title"]) == (book.id, "Il nome della rosa")

    months = {time.strftime("%Y-%m", time.gmtime(time.time() - d * 86400)) for d in (0, 1)}
    days = [d for m in months for d in client.get(f"/api/stats/calendar?month={m}").json()["days"]]
    assert [b["title"] for d in days for b in d["books"]] == ["Il nome della rosa"]

    timeline = client.get("/api/stats/timeline").json()["books"]
    assert [b["title"] for b in timeline] == ["Il nome della rosa"]

    detail = client.get(f"/api/books/{book.id}")
    assert detail.status_code == 200
    assert (detail.json()["status"], detail.json()["files"]) == (BOOK_NO_FILE, [])
    # everything its own page loads
    stats = client.get(f"/api/books/{book.id}/reading-stats")
    assert stats.status_code == 200 and stats.json()["own"]["total_seconds"] == 900, stats.text
    assert client.get(f"/api/books/{book.id}/estimate").status_code == 200
    assert client.get(f"/api/books/{book.id}/annotations").status_code == 200
    assert client.get(f"/api/books/{book.id}/kosync-progress").json()["linked"] is True


def test_it_stays_out_of_everything_that_lists_or_serves_files(db, client, admin_user, acc, make_book):
    real = make_book(title="A Real Book", series="Narratori", series_index=1)
    _push(client, acc, metadata={**META, "series": "Narratori", "series_index": 2})
    ghost = db.query(Book).filter(Book.status == BOOK_NO_FILE).one()

    listed = client.get("/api/books").json()
    assert [b["id"] for b in listed] == [real.id]
    assert ghost.title not in client.get("/api/books/series").text
    assert ghost.title not in client.get("/api/books?q=rosa").text
    assert client.get(f"/api/books/{ghost.id}").json()["files"] == []      # nothing to download
    # library totals count books Tome has
    assert client.get("/api/stats?days=30").json()["tbr"]["owned"] == 1
    # Home does not offer it as the next volume of a series
    upcoming = client.get(f"/api/home/focus?book_id={ghost.id}").json()["upcoming"]
    assert ghost.id not in [u["book_id"] for u in upcoming]


def test_opds_does_not_offer_it(db, client, admin_user, acc, make_book):
    user, _ = admin_user
    real = make_book(title="A Real Book", author="Umberto Eco")
    db.add(UserBookStatus(user_id=user.id, book_id=real.id, status="reading", progress_pct=0.1))
    db.flush()
    _push(client, acc)                                   # file-less, also "reading"
    for path in ("/opds/all", "/opds/reading", "/opds/recent", "/opds/authors/Umberto%20Eco",
                 "/opds/search/results?q=rosa"):
        feed = client.get(path, auth=(user.username, "adminpass123"))
        assert feed.status_code == 200, path
        assert "Il nome della rosa" not in feed.text, path
    assert "A Real Book" in client.get("/opds/reading", auth=(user.username, "adminpass123")).text


def test_it_is_private_to_the_person_whose_reader_reported_it(db, client, admin_user, acc):
    """An unfiled book uploaded by an admin is shared with everyone. This one
    is the admin's reading, not an upload."""
    _member_user, _member_acc, as_member = _member(db)
    _push(client, acc)
    book = db.query(Book).filter(Book.status == BOOK_NO_FILE).one()
    assert client.get(f"/api/books/{book.id}").status_code == 200            # the admin, its owner
    for path in ("", "/reading-stats", "/estimate"):
        assert client.get(f"/api/books/{book.id}{path}", headers=as_member).status_code == 404, path
    r = client.post(f"/api/books/{book.id}/link-kosync", json={"document": DOC}, headers=as_member)
    assert r.status_code == 404


def test_the_owner_can_delete_it(db, client, admin_user, acc):
    _push(client, acc)
    book = db.query(Book).filter(Book.status == BOOK_NO_FILE).one()
    assert client.delete(f"/api/books/{book.id}").status_code == 204
    db.expire_all()
    assert _no_file_books(db) == []


# ── the real file arrives ─────────────────────────────────────────────────────

def test_the_link_picker_offers_a_document_that_only_has_a_file_less_book(db, client, admin_user, acc, make_book):
    _push(client, acc, percentage=0.3)
    ghost = db.query(Book).filter(Book.status == BOOK_NO_FILE).one()
    real = make_book(title="The Name of the Rose", author="Umberto Eco")

    offered = client.get(f"/api/books/{real.id}/kosync-progress").json()
    assert offered["linked"] is False
    assert [(d["document"], d["title"]) for d in offered["unlinked_documents"]] == [(DOC, "Il nome della rosa")]
    # its own page shows it as linked, not as an open document
    own = client.get(f"/api/books/{ghost.id}/kosync-progress").json()
    assert own["linked"] is True and own["percentage"] == 0.3


def test_linking_the_real_book_takes_the_file_less_one_over(db, client, admin_user, acc, make_book):
    user, _ = admin_user
    _session(client, acc, seconds=900)
    _push(client, acc, percentage=0.6)
    ghost = db.query(Book).filter(Book.status == BOOK_NO_FILE).one()
    ghost_id = ghost.id
    status = db.query(UserBookStatus).filter_by(user_id=user.id, book_id=ghost_id).one()
    status.rating = 4.5
    db.flush()

    real = make_book(title="The Name of the Rose", author="Umberto Eco")
    r = client.post(f"/api/books/{real.id}/link-kosync", json={"document": DOC})
    assert r.status_code == 200, r.text
    db.expire_all()

    assert _linked_book_id(db, user) == real.id
    assert db.query(ReadingSession).one().book_id == real.id
    moved = db.query(UserBookStatus).filter_by(user_id=user.id).one()      # one row, carried over
    assert (moved.book_id, moved.rating, moved.status, moved.progress_pct) == (real.id, 4.5, "reading", 0.6)
    assert db.get(Book, ghost_id) is None
    assert client.get("/api/home/focus").json()["book"]["book_id"] == real.id


def test_the_real_books_own_status_wins_over_the_file_less_one(db, client, admin_user, acc, make_book):
    user, _ = admin_user
    _push(client, acc, percentage=0.2)
    real = make_book(title="The Name of the Rose", author="Umberto Eco")
    db.add(UserBookStatus(user_id=user.id, book_id=real.id, status="reading", progress_pct=0.1, rating=3.0))
    db.flush()
    assert client.post(f"/api/books/{real.id}/link-kosync", json={"document": DOC}).status_code == 200
    db.expire_all()
    row = db.query(UserBookStatus).filter_by(user_id=user.id).one()
    assert (row.book_id, row.rating, row.progress_pct) == (real.id, 3.0, 0.2)
    assert _no_file_books(db) == []


def test_a_file_less_book_another_document_still_uses_is_kept(db, client, admin_user, acc, make_book):
    user, _ = admin_user
    _push(client, acc)
    _push(client, acc, document=DOC2, metadata={**META, "filename": "rosa (1).epub"})
    ghost = db.query(Book).filter(Book.status == BOOK_NO_FILE).one()
    real = make_book(title="The Name of the Rose", author="Umberto Eco")
    assert client.post(f"/api/books/{real.id}/link-kosync", json={"document": DOC}).status_code == 200
    db.expire_all()
    assert db.get(Book, ghost.id) is not None
    assert _linked_book_id(db, user, DOC2) == ghost.id


def test_relinking_between_two_real_books_deletes_nothing(db, client, admin_user, acc, make_book):
    user, _ = admin_user
    first = make_book(title="Il nome della rosa", author="Umberto Eco")
    second = make_book(title="Another", author="B")
    _push(client, acc)
    assert _linked_book_id(db, user) == first.id
    assert client.post(f"/api/books/{second.id}/link-kosync", json={"document": DOC}).status_code == 200
    db.expire_all()
    assert db.get(Book, first.id) is not None and _linked_book_id(db, user) == second.id


# ── volumes, authors, ISBNs: what "the same book" means ───────────────────────

VOLUME = {"filename": "Overlord.epub", "title": "Overlord", "authors": "Kugane Maruyama"}


def test_another_volume_under_the_same_title_is_not_the_same_book(db, client, admin_user, acc, make_book):
    """Light novels and manga often carry the bare series name as their title."""
    user, _ = admin_user
    first = make_book(title="Overlord", author="Kugane Maruyama", series="Overlord", series_index=1)
    _push(client, acc, metadata={**VOLUME, "series": "Overlord", "series_index": 2})
    linked = _linked_book_id(db, user)
    assert linked != first.id
    ghost = db.get(Book, linked)
    assert (ghost.status, ghost.series_index) == (BOOK_NO_FILE, 2.0)
    # the right volume still links
    _push(client, acc, document=DOC2, metadata={**VOLUME, "series": "Overlord", "series_index": 1})
    assert _linked_book_id(db, user, DOC2) == first.id


def test_a_volume_number_in_the_file_name_vetoes_another_volume(db, client, admin_user, acc, make_book):
    user, _ = admin_user
    first = make_book(title="Overlord", author="Kugane Maruyama", series="Overlord", series_index=1)
    _push(client, acc, metadata={**VOLUME, "filename": "Overlord v02.epub"})
    assert _linked_book_id(db, user) != first.id
    _push(client, acc, document=DOC2, metadata={**VOLUME, "filename": "Overlord v01.epub"})
    assert _linked_book_id(db, user, DOC2) == first.id


def test_a_stated_volume_does_not_match_a_book_without_one(db, client, admin_user, acc, make_book):
    user, _ = admin_user
    unnumbered = make_book(title="Overlord", author="Kugane Maruyama")
    _push(client, acc, metadata={**VOLUME, "series": "Overlord", "series_index": 2})
    assert _linked_book_id(db, user) != unnumbered.id


def test_two_volumes_do_not_share_one_file_less_book(db, client, admin_user, acc):
    user, _ = admin_user
    _push(client, acc, metadata={**VOLUME, "series": "Overlord", "series_index": 1})
    _push(client, acc, document=DOC2, metadata={**VOLUME, "series": "Overlord", "series_index": 2})
    assert _linked_book_id(db, user, DOC) != _linked_book_id(db, user, DOC2)
    assert sorted(b.series_index for b in _no_file_books(db)) == [1.0, 2.0]


def test_two_volumes_told_apart_by_file_name_only(db, client, admin_user, acc):
    user, _ = admin_user
    _push(client, acc, metadata={**VOLUME, "filename": "Overlord v01.epub"})
    _push(client, acc, document=DOC2, metadata={**VOLUME, "filename": "Overlord v02.epub"})
    assert _linked_book_id(db, user, DOC) != _linked_book_id(db, user, DOC2)
    assert len(_no_file_books(db)) == 2


@pytest.mark.parametrize("library_author, reader_authors", [
    ("Terry Pratchett, Neil Gaiman", "Neil Gaiman & Terry Pratchett"),
    ("Terry Pratchett, Neil Gaiman", "Terry Pratchett\nNeil Gaiman"),      # KOReader's separator
    ("Terry Pratchett & Neil Gaiman", "neil gaiman; terry pratchett"),
    ("Terry Pratchett and Neil Gaiman", "Terry Pratchett, Neil Gaiman"),
])
def test_the_same_authors_in_another_order_or_spelling_of_and(db, client, admin_user, acc, make_book,
                                                             library_author, reader_authors):
    user, _ = admin_user
    book = make_book(title="Good Omens", author=library_author)
    _push(client, acc, metadata={"filename": "go.epub", "title": "Good Omens", "authors": reader_authors})
    assert _linked_book_id(db, user) == book.id
    assert _no_file_books(db) == []


def test_a_title_without_an_author_is_never_matched(db, client, admin_user, acc, make_book):
    """"Unknown" and "Volume 1" are not identities."""
    user, _ = admin_user
    library_book = make_book(title="Unknown", author=None)
    _push(client, acc, metadata={"filename": "a.epub", "title": "Unknown", "authors": ""})
    _push(client, acc, document=DOC2, metadata={"filename": "b.epub", "title": "Unknown", "authors": ""})
    first, second = _linked_book_id(db, user, DOC), _linked_book_id(db, user, DOC2)
    assert library_book.id not in (first, second)
    assert first != second and len(_no_file_books(db)) == 2


def test_a_shared_isbn_needs_the_title_or_the_author_to_agree(db, client, admin_user, acc, make_book):
    """Libraries carry wrong-edition ISBNs (the manga's on the light novel)."""
    user, _ = admin_user
    manga = make_book(title="Overlord (Manga)", author="Satoshi Oshio", isbn="9780316272278")
    _push(client, acc, metadata={**VOLUME, "isbn": "978-0-316-27227-8"})
    assert _linked_book_id(db, user) != manga.id


def test_the_book_just_downloaded_over_opds_links_by_title_alone(db, client, admin_user, acc, make_book):
    """The download is the evidence a differently spelled author lacks."""
    user, _ = admin_user
    book = make_book(title="Il nome della rosa", author="Eco, Umberto")
    db.add(OPDSPendingLink(user_id=user.id, book_id=book.id))
    db.flush()
    _push(client, acc)
    assert _linked_book_id(db, user) == book.id
    assert _no_file_books(db) == [] and db.query(OPDSPendingLink).count() == 0


def test_unchanged_metadata_is_not_written_again(db, client, acc):
    _push(client, acc)
    stamp = db.query(KOSyncDocumentMeta).one().updated_at
    _push(client, acc, percentage=0.5)
    db.expire_all()
    assert db.query(KOSyncDocumentMeta).one().updated_at == stamp


# ── deleting, merging, handing over ───────────────────────────────────────────

def test_a_deleted_file_less_book_stays_deleted(db, client, admin_user, acc, make_book):
    """Deleting it means: do not track this file."""
    user, _ = admin_user
    _session(client, acc)
    _push(client, acc)
    book = db.query(Book).filter(Book.status == BOOK_NO_FILE).one()
    assert client.delete(f"/api/books/{book.id}").status_code == 204

    assert _push(client, acc, percentage=0.6).status_code == 200      # the reader keeps syncing
    db.expire_all()
    assert _no_file_books(db) == [] and _linked_book_id(db, user) is None
    row = client.get("/api/stats/sessions").json()["sessions"][0]
    assert (row["book_id"], row["book_title"]) == (None, "Il nome della rosa (not linked yet)")

    # ... and a link by hand still works and brings the reading back
    real = make_book(title="The Name of the Rose", author="Umberto Eco")
    assert client.post(f"/api/books/{real.id}/link-kosync", json={"document": DOC}).status_code == 200
    db.expire_all()
    assert db.query(ReadingSession).one().book_id == real.id
    assert db.query(KOSyncDocumentMeta).one().ignored is False


def test_duplicate_detection_leaves_file_less_books_alone(db, client, admin_user, acc, make_book):
    """It is one user's reading record, not a second copy in the library.
    Merging would hide the real file inside it, or delete the record."""
    # Two real copies, so the duplicates page has something to show ...
    real = make_book(title="Il nome della rosa", author="Umberto Eco", isbn="9788845292833", file_path="/books/rosa-1.epub")
    twin = make_book(title="Il nome della rosa", author="Umberto Eco", isbn="9788845292833", file_path="/books/rosa-2.epub")
    # ... and a file-less book with the same ISBN, title and author next to them.
    ghost = Book(title="Il nome della rosa", author="Umberto Eco", isbn="9788845292833",
                 status=BOOK_NO_FILE, is_reviewed=True, added_by=admin_user[0].id)
    db.add(ghost)
    db.flush()

    groups = client.get("/api/admin/duplicates").json()["groups"]
    listed = {b["id"] for g in groups for b in g["books"]}
    assert listed == {real.id, twin.id}

    for keep, remove in ((ghost.id, real.id), (real.id, ghost.id)):
        r = client.post("/api/admin/duplicates/merge", json={"keep_id": keep, "remove_ids": [remove]})
        assert r.status_code == 400, r.text
    db.expire_all()
    assert db.get(Book, real.id).files and db.get(Book, ghost.id) is not None
    assert sorted(b["id"] for b in client.get("/api/books").json()) == sorted([real.id, twin.id])


def test_handing_over_keeps_what_was_recorded_on_the_file_less_book(db, client, admin_user, acc, make_book):
    """The real book was only on the wish list; the reading happened on the reader."""
    from datetime import datetime
    user, _ = admin_user
    _push(client, acc, percentage=0.95)
    ghost = db.query(Book).filter(Book.status == BOOK_NO_FILE).one()
    ghost_id = ghost.id
    row = db.query(UserBookStatus).filter_by(user_id=user.id, book_id=ghost_id).one()
    row.status, row.rating, row.review, row.finished_at = "read", 5.0, "Loved it", datetime(2026, 11, 1)
    db.flush()
    assert db.query(ReadingHistory).filter_by(user_id=user.id, book_id=ghost_id).count() == 1

    real = make_book(title="The Name of the Rose", author="Umberto Eco")
    db.add(UserBookStatus(user_id=user.id, book_id=real.id, status="want_to_read"))
    db.flush()
    assert client.post(f"/api/books/{real.id}/link-kosync", json={"document": DOC}).status_code == 200
    db.expire_all()

    kept = db.query(UserBookStatus).filter_by(user_id=user.id).one()
    assert (kept.book_id, kept.status, kept.rating, kept.review) == (real.id, "read", 5.0, "Loved it")
    assert kept.finished_at == datetime(2026, 11, 1)
    assert db.get(Book, ghost_id) is None
    assert db.query(ReadingHistory).filter_by(user_id=user.id, book_id=real.id).count() == 1


def test_editing_the_book_in_tome_keeps_the_link_to_the_readers_file(db, client, admin_user, acc):
    """The link is the file's hash to the book's id. Fetching metadata or
    editing the title in Tome changes neither."""
    user, _ = admin_user
    _push(client, acc, percentage=0.2)
    book_id = _linked_book_id(db, user)

    r = client.post(f"/api/books/{book_id}/apply-metadata", json={
        "title": "The Name of the Rose", "author": "Eco, Umberto", "isbn": "9780156001311",
        "series": "Vintage Classics", "series_index": 7, "description": "A murder in an abbey.", "year": 1980})
    assert r.status_code == 200, r.text
    assert (r.json()["title"], r.json()["status"]) == ("The Name of the Rose", BOOK_NO_FILE)

    _session(client, acc)
    assert _push(client, acc, percentage=0.5).status_code == 200      # same file, same reader-side metadata
    db.expire_all()
    assert _linked_book_id(db, user) == book_id
    assert [b.id for b in _no_file_books(db)] == [book_id]
    assert db.query(ReadingSession).one().book_id == book_id
    status = db.query(UserBookStatus).filter_by(user_id=user.id, book_id=book_id).one()
    assert status.progress_pct == 0.5
    assert db.get(Book, book_id).title == "The Name of the Rose"       # the reader does not overwrite the edit
