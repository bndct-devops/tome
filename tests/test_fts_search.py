"""Korean/CJK full-text search: trigram migration + short-term fallback.

books_fts uses SQLite's trigram tokenizer so a 3+ character term can match
anywhere inside a word (not just its start) — but the trigram tokenizer
tokenizes nothing for a term under 3 characters, so a short term needs its
own fallback. See services.fts.search_book_ids docstring and init_fts's
docstring (backend/core/database.py) for the full rationale.
"""
from sqlalchemy import create_engine, text

from backend.core.database import init_fts
from backend.services.fts import search_book_ids


def _insert(db, book_id, title="", description=""):
    db.execute(text(
        "INSERT INTO books_fts(rowid, title, author, series, description, tags) "
        "VALUES (:id, :title, '', '', :description, '')"
    ), {"id": book_id, "title": title, "description": description})


def test_init_fts_migrates_pre_trigram_table_to_trigram():
    """An existing installation's non-trigram books_fts must be upgraded."""
    engine = create_engine("sqlite:///:memory:")
    with engine.connect() as conn:
        conn.execute(text(
            "CREATE VIRTUAL TABLE books_fts USING fts5(title, author, series, description, tags)"
        ))
        conn.commit()

    init_fts(engine)

    with engine.connect() as conn:
        sql = conn.execute(text(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='books_fts'"
        )).scalar()
    assert "trigram" in sql

    # Idempotent: calling it again on an already-migrated table is a no-op,
    # not a second drop-and-recreate.
    with engine.connect() as conn:
        conn.execute(text(
            "INSERT INTO books_fts(rowid, title, author, series, description, tags) "
            "VALUES (1, 'x', '', '', '', '')"
        ))
        conn.commit()
    init_fts(engine)
    with engine.connect() as conn:
        n = conn.execute(text("SELECT count(*) FROM books_fts")).scalar()
    assert n == 1


def test_search_three_plus_char_term_matches_mid_word(client, make_book, db):
    """Trigram indexing: a 3+ char term matches inside a longer word, not just
    at its start — the original ask this tokenizer choice exists for."""
    target = make_book(title="동아시아 개관", description="")
    other = make_book(title="Cooking Basics", description="")
    _insert(db, target.id, title="동아시아 개관")
    _insert(db, other.id, title="Cooking Basics")
    db.flush()

    resp = client.get("/api/books", params={"q": "아시아"})
    assert resp.status_code == 200
    ids = {b["id"] for b in resp.json()}
    assert target.id in ids
    assert other.id not in ids


def test_search_short_term_falls_back_to_substring_match(client, make_book, db):
    """A term under 3 characters can't be tokenized by trigram at all (SQLite
    matches nothing for it), so it must fall back to a LIKE scan — this is a
    *whole word* length in Korean/Chinese/Japanese, not a rare edge case."""
    daehan = make_book(title="대한민국 개요", description="대한민국은 동아시아의 국가이다")
    gongheo = make_book(title="공허한가", description="우리는 왜 공허한가에 대한 성찰")
    unrelated = make_book(title="Cooking Basics", description="A book about pasta")
    for b, title, desc in (
        (daehan, "대한민국 개요", "대한민국은 동아시아의 국가이다"),
        (gongheo, "공허한가", "우리는 왜 공허한가에 대한 성찰"),
        (unrelated, "Cooking Basics", "A book about pasta"),
    ):
        _insert(db, b.id, title=title, description=desc)
    db.flush()

    # Exact 2-char word
    ids = {b["id"] for b in client.get("/api/books", params={"q": "국가"}).json()}
    assert daehan.id in ids
    assert unrelated.id not in ids

    # 2-char term in the *middle* of a word — was the original bug report
    ids = {b["id"] for b in client.get("/api/books", params={"q": "허한"}).json()}
    assert gongheo.id in ids
    assert daehan.id not in ids

    # 2-char English term
    another = make_book(title="10-Year IT Planner's Notes", description="")
    _insert(db, another.id, title="10-Year IT Planner's Notes")
    db.flush()
    ids = {b["id"] for b in client.get("/api/books", params={"q": "IT"}).json()}
    assert another.id in ids


def test_search_mixed_length_terms_use_and_semantics(client, make_book, db):
    """A query combining a 3+ char term (FTS) and a <3 char term (LIKE) must
    still AND them together, matching FTS5's default multi-term semantics."""
    daehan = make_book(title="대한민국 개요", description="대한민국은 동아시아의 국가이다")
    _insert(db, daehan.id, title="대한민국 개요", description="대한민국은 동아시아의 국가이다")
    db.flush()

    # "역사" doesn't appear anywhere in this book — must exclude it
    ids = {b["id"] for b in client.get("/api/books", params={"q": "대한민국 역사"}).json()}
    assert daehan.id not in ids

    # "국가" does appear — must include it
    ids = {b["id"] for b in client.get("/api/books", params={"q": "대한민국 국가"}).json()}
    assert daehan.id in ids


def test_search_book_ids_empty_query_returns_empty_set(db):
    assert search_book_ids(db, "   ") == set()
