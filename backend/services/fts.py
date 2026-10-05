"""Incremental full-text search index maintenance for the books_fts table.

`books_fts` is a standard (self-contained) FTS5 table — see
`backend/core/database.py`. Standard FTS5 supports DELETE/INSERT by rowid, so we
keep the index in sync one book at a time instead of rebuilding the whole thing.

History: the index used to be contentless (`content=''`), which cannot be
mutated by rowid — so the only way to maintain it was a full rebuild on every
startup. That left every freshly-scanned or uploaded book unsearchable until the
next restart. Indexing inline fixes that, and matches how other library servers
maintain their search index during ingest.

Call `index_book(db, book)` after a book AND its tags are in place, inside the
same transaction the caller commits. The book must already be flushed (have an
id). For paths that add tags via `db.add(BookTag(...))` rather than the
relationship, flush before calling so `book.tags` reflects them.
"""
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.models.book import Book


def _tags_str(book: Book) -> str:
    return " ".join(t.tag for t in book.tags if t.tag)


def index_book(db: Session, book: Book, tags: list[str] | None = None) -> None:
    """Upsert a single book's FTS row (delete-then-insert by rowid).

    Pass ``tags`` (a list of tag strings) to skip a lazy-load of ``book.tags`` —
    significant in a bulk scan, where it's one wasted SELECT per book. When
    omitted, falls back to reading the relationship.
    """
    tag_str = " ".join(t for t in tags if t) if tags is not None else _tags_str(book)
    db.execute(text("DELETE FROM books_fts WHERE rowid = :id"), {"id": book.id})
    db.execute(
        text(
            "INSERT INTO books_fts(rowid, title, author, series, description, tags) "
            "VALUES (:id, :title, :author, :series, :description, :tags)"
        ),
        {
            "id": book.id,
            "title": book.title or "",
            "author": book.author or "",
            "series": book.series or "",
            "description": book.description or "",
            "tags": tag_str,
        },
    )


def unindex_book(db: Session, book_id: int) -> None:
    """Remove a book's FTS row (call before deleting the book)."""
    db.execute(text("DELETE FROM books_fts WHERE rowid = :id"), {"id": book_id})


def search_book_ids(db: Session, q: str) -> set[int]:
    """Resolve a `q=` search string to the set of matching book ids.

    Splits `q` into whitespace-separated terms (AND semantics, matching FTS5's
    default) and looks each up against `books_fts`. Terms of 3+ characters use
    FTS5 prefix matching as before. `books_fts` uses the trigram tokenizer,
    which only tokenizes (and so can only match) substrings of 3 or more
    characters — https://www.sqlite.org/fts5.html: "Substrings consisting of
    fewer than 3 unicode characters do not match any rows". That silently
    drops every 1-2 character term, which is far more common than it sounds:
    it's a normal *whole word* length in Korean, Chinese and Japanese (e.g.
    Korean "역사" = "history", "조선" = "Joseon), not just a rare substring.
    Those short terms fall back to a plain substring LIKE scan over the same
    FTS columns. Per-term result sets (whichever path found them) are
    intersected so mixed-length queries still get AND semantics.
    """
    terms = [t for t in q.split() if t]
    long_terms = [t for t in terms if len(t) >= 3]
    short_terms = [t for t in terms if len(t) < 3]

    id_sets: list[set[int]] = []
    if long_terms:
        fts_term = " ".join(f'"{t.replace(chr(34), "")}"*' for t in long_terms)
        fts_rows = db.execute(
            text("SELECT rowid FROM books_fts WHERE books_fts MATCH :q"),
            {"q": fts_term},
        ).fetchall()
        id_sets.append({row[0] for row in fts_rows})
    for t in short_terms:
        like_rows = db.execute(
            text(
                "SELECT rowid FROM books_fts WHERE "
                "title LIKE :p OR author LIKE :p OR series LIKE :p "
                "OR description LIKE :p OR tags LIKE :p"
            ),
            {"p": f"%{t}%"},
        ).fetchall()
        id_sets.append({row[0] for row in like_rows})

    return set.intersection(*id_sets) if id_sets else set()
