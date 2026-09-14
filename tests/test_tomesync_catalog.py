"""Plugin catalog batch (build 33) — server endpoints.

Author browse axis, device search, read-status write-back, and the series-list
N+1 rewrite (single query, response shape unchanged).
"""
from backend.models.tome_sync import ApiKey
from backend.models.user import User
from backend.models.user_book_status import UserBookStatus
from backend.core.security import hash_password


def _api_key_for(db, user_id: int) -> str:
    plaintext = ApiKey.generate()
    db.add(ApiKey(user_id=user_id, key_hash=ApiKey.hash_key(plaintext),
                  key_prefix=plaintext[:11], label="test"))
    db.flush()
    return plaintext


def _hdr(db, user):
    return {"Authorization": f"Bearer {_api_key_for(db, user.id)}"}


# ── series list (N+1 rewrite parity) ─────────────────────────────────────────

def test_series_list_shape_and_first_book(db, client, admin_user, make_book):
    user, _ = admin_user
    make_book(title="B two", series="Alpha", series_index=2, author="A. Author")
    make_book(title="A one", series="Alpha", series_index=1, author="A. Author")
    make_book(title="Standalone", author="S. Alone")
    r = client.get("/api/tome-sync/series", headers=_hdr(db, user))
    assert r.status_code == 200
    data = r.json()
    alpha = [s for s in data if s["name"] == "Alpha"][0]
    assert alpha["book_count"] == 2
    assert alpha["author"] == "A. Author"
    # first_book_id must be the lowest series_index, not insertion order
    books = client.get(f"/api/tome-sync/series/{alpha['first_book_id']}",
                       headers=_hdr(db, user)).json()["books"]
    assert books[0]["title"] == "A one"
    # unserialized bucket appended last
    assert data[-1]["name"] == "__unserialized__"
    assert data[-1]["book_count"] == 1


def test_series_index_null_sorts_last_for_first_book(db, client, admin_user, make_book):
    user, _ = admin_user
    noidx = make_book(title="No index", series="Beta", author="X")
    make_book(title="Indexed", series="Beta", series_index=1, author="X")
    r = client.get("/api/tome-sync/series", headers=_hdr(db, user)).json()
    beta = [s for s in r if s["name"] == "Beta"][0]
    assert beta["first_book_id"] != noidx.id


# ── authors axis ─────────────────────────────────────────────────────────────

def test_authors_list_counts_and_unknown_bucket(db, client, admin_user, make_book):
    user, _ = admin_user
    make_book(title="One", author="Zed Writer")
    make_book(title="Two", author="Zed Writer", series="S", series_index=1)
    make_book(title="Anon", author=None)
    r = client.get("/api/tome-sync/authors", headers=_hdr(db, user))
    assert r.status_code == 200
    data = r.json()
    zed = [a for a in data if a["name"] == "Zed Writer"][0]
    assert zed["book_count"] == 2
    assert data[-1] == {"name": "__unknown__", "book_count": 1}


def test_author_books_shape_and_unknown(db, client, admin_user, make_book):
    user, _ = admin_user
    make_book(title="Vol 2", author="Zed Writer", series="S", series_index=2)
    make_book(title="Vol 1", author="Zed Writer", series="S", series_index=1)
    make_book(title="Alone", author="Zed Writer")
    anon = make_book(title="Anon", author=None)
    r = client.get("/api/tome-sync/author-books", params={"author": "Zed Writer"},
                   headers=_hdr(db, user))
    assert r.status_code == 200
    data = r.json()
    assert [b["title"] for b in data["books"]] == ["Vol 1", "Vol 2", "Alone"]
    assert data["books"][0]["series"] == "S"
    assert "series" not in data["books"][2]          # no JSON null for the plugin
    assert data["books"][0]["status"] == "unread"    # default
    assert isinstance(data["books"][0]["files"], list)

    r2 = client.get("/api/tome-sync/author-books", params={"author": "__unknown__"},
                    headers=_hdr(db, user)).json()
    assert [b["id"] for b in r2["books"]] == [anon.id]


# ── search ───────────────────────────────────────────────────────────────────

def test_search_matches_title_author_series_all_terms(db, client, admin_user, make_book):
    user, _ = admin_user
    make_book(title="The Iron Duke", author="Meljean Brook", series="Iron Seas", series_index=1)
    make_book(title="Something Else", author="Other Person")
    hdr = _hdr(db, user)

    r = client.get("/api/tome-sync/search", params={"q": "iron"}, headers=hdr).json()
    assert r["total"] == 1 and r["books"][0]["title"] == "The Iron Duke"
    # multi-term: all must match (title+author across fields)
    r = client.get("/api/tome-sync/search", params={"q": "iron brook"}, headers=hdr).json()
    assert r["total"] == 1
    r = client.get("/api/tome-sync/search", params={"q": "iron nobody"}, headers=hdr).json()
    assert r["total"] == 0
    # blank query is a no-op, not a full dump
    r = client.get("/api/tome-sync/search", params={"q": "   "}, headers=hdr).json()
    assert r["total"] == 0 and r["books"] == []


def test_search_caps_at_50_but_reports_total(db, client, admin_user, make_book):
    user, _ = admin_user
    for i in range(55):
        make_book(title=f"Common Word {i}", author="Bulk")
    r = client.get("/api/tome-sync/search", params={"q": "common word"},
                   headers=_hdr(db, user)).json()
    assert r["total"] == 55
    assert len(r["books"]) == 50


# ── read-status write-back ───────────────────────────────────────────────────

def test_status_writeback_upserts_and_reads_back(db, client, admin_user, make_book):
    user, _ = admin_user
    book = make_book(title="Statusable", author="X", series="St", series_index=1)
    hdr = _hdr(db, user)

    r = client.put(f"/api/tome-sync/status/{book.id}", json={"status": "reading"}, headers=hdr)
    assert r.status_code == 200 and r.json()["status"] == "reading"
    row = db.query(UserBookStatus).filter_by(user_id=user.id, book_id=book.id).first()
    assert row is not None and row.status == "reading"

    # update, not duplicate
    client.put(f"/api/tome-sync/status/{book.id}", json={"status": "read"}, headers=hdr)
    assert db.query(UserBookStatus).filter_by(user_id=user.id, book_id=book.id).count() == 1

    # and the volume list reflects it
    books = client.get(f"/api/tome-sync/series/{book.id}", headers=hdr).json()["books"]
    assert books[0]["status"] == "read"


def test_status_writeback_stamps_finish_date_and_restarts_rereads(db, client, admin_user, make_book):
    """The device's "mark read" must record a finish date like the web endpoint
    does (stats and the Hardcover mirror read it - without it Hardcover shows
    the sync day, #217), and "reading" on a finished book starts over (#219)."""
    from backend.models.tome_sync import TomeSyncPosition
    from backend.services.book_progress import upsert_position
    user, _ = admin_user
    book = make_book(title="Device Finished", author="X", series="Df", series_index=1)
    hdr = _hdr(db, user)
    upsert_position(db, user_id=user.id, book_id=book.id, percentage=0.9, progress="p", device="Kindle")
    db.commit()

    assert client.put(f"/api/tome-sync/status/{book.id}", json={"status": "read"}, headers=hdr).status_code == 200
    row = db.query(UserBookStatus).filter_by(user_id=user.id, book_id=book.id).one()
    db.refresh(row)
    assert row.status == "read" and row.finished_at is not None
    first_finish = row.finished_at
    row.hardcover_synced_pct = 1.0
    row.hardcover_read_id = 3003
    row.progress_pct = 1.0
    row.cfi = "end"
    db.commit()

    # idempotent: marking read again keeps the original finish date
    client.put(f"/api/tome-sync/status/{book.id}", json={"status": "read"}, headers=hdr)
    db.refresh(row)
    assert row.finished_at == first_finish

    assert client.put(f"/api/tome-sync/status/{book.id}", json={"status": "reading"}, headers=hdr).status_code == 200
    db.refresh(row)
    assert row.status == "reading" and row.finished_at is None
    assert row.progress_pct is None and row.cfi is None
    assert row.hardcover_synced_pct is None and row.hardcover_read_id is None
    assert db.query(TomeSyncPosition).filter_by(user_id=user.id, book_id=book.id).count() == 0


def test_status_writeback_validates_and_404s(db, client, admin_user, make_book):
    user, _ = admin_user
    book = make_book(title="V", author="X")
    hdr = _hdr(db, user)
    assert client.put(f"/api/tome-sync/status/{book.id}",
                      json={"status": "devoured"}, headers=hdr).status_code == 422
    assert client.put("/api/tome-sync/status/999999",
                      json={"status": "read"}, headers=hdr).status_code == 404


# ── visibility ───────────────────────────────────────────────────────────────

def test_catalog_endpoints_respect_visibility(db, client, admin_user, make_book):
    """A member must not see (or mutate status of) another member's private
    unfiled upload via authors/search/status."""
    admin, _ = admin_user
    owner = User(username="owner", email="o@example.com",
                 hashed_password=hash_password("pw"), is_active=True, role="member")
    peeker = User(username="peeker", email="p@example.com",
                  hashed_password=hash_password("pw"), is_active=True, role="member")
    db.add_all([owner, peeker])
    db.flush()
    private = make_book(title="Private Thing", author="Hidden Person")
    private.added_by = owner.id
    db.flush()
    peek_hdr = _hdr(db, peeker)

    authors = client.get("/api/tome-sync/authors", headers=peek_hdr).json()
    assert not any(a["name"] == "Hidden Person" for a in authors)
    search = client.get("/api/tome-sync/search", params={"q": "private thing"},
                        headers=peek_hdr).json()
    assert search["total"] == 0
    assert client.put(f"/api/tome-sync/status/{private.id}",
                      json={"status": "read"}, headers=peek_hdr).status_code == 404
