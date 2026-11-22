"""Books for files a reader has and Tome does not.

A KOSync push names a file by a hash. A sideloaded file's hash means nothing
to Tome, so its progress and reading sessions had no book to belong to until
someone linked the document by hand.

Readers that send document metadata with the push (KOReader's optional
``metadata`` object; CrossInk calls the switch "Send Document Metadata") say
which book the hash is. With that, an unlinked document is resolved here:

1. exactly one library book the user can see is the same book  ->  the
   document is linked to it;
2. nothing matches  ->  the document gets a book record without a file
   (``BOOK_NO_FILE``), created from the metadata;
3. several library books match  ->  nothing is done; a guess could put the
   reading on the wrong book, and the manual link on the book page settles it.

"The same book" is exact, never similar: a wrong link moves someone's reading
history. It means the same title and the same author(s) after folding case,
width, whitespace and the order of authors, and no disagreement about the
volume: a reader that says "volume 2" only matches a book Tome has as volume
2, and a volume number in the file name vetoes a book with another one. A
shared ISBN counts only when the title or the author agrees too (libraries
carry wrong-edition ISBNs). A title alone, with no author on the reader's
side, is never matched: "Unknown" and "Volume 1" are not identities.
"""
from __future__ import annotations

import logging
import math
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any, Callable, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.core.permissions import book_visibility_filter
from backend.models.book import BOOK_ACTIVE, BOOK_NO_FILE, Book
from backend.models.kosync import KOSyncDocumentMap, KOSyncDocumentMeta
from backend.models.user import User

logger = logging.getLogger(__name__)

MAX_TEXT = 500
MAX_ISBN = 32
_SPACE = re.compile(r"\s+")
_AUTHOR_SPLIT = re.compile(r"[;,&]|\band\b", re.IGNORECASE)
_FIELDS = ("filename", "title", "authors", "isbn", "series", "series_index")


# ── what the reader sent ─────────────────────────────────────────────────────

def _text(value: Any, limit: int = MAX_TEXT) -> Optional[str]:
    if not isinstance(value, str):
        return None
    value = _SPACE.sub(" ", value.replace("\x00", "")).strip()
    return value[:limit] or None


def clean_metadata(raw: Any) -> Optional[dict[str, Any]]:
    """The fields Tome keeps from a push's ``metadata`` object, or None when
    there is nothing usable in it. Never raises: a reader's odd metadata must
    not fail its progress sync."""
    if not isinstance(raw, dict):
        return None
    index = raw.get("series_index")
    if isinstance(index, bool) or not isinstance(index, (int, float)) or not math.isfinite(index):
        index = None
    authors = raw.get("authors")
    if isinstance(authors, str):
        # KOReader joins several authors with a newline.
        authors = ", ".join(part.strip() for part in authors.splitlines() if part.strip())
    out = {
        "filename": _text(raw.get("filename")),
        "title": _text(raw.get("title")),
        "authors": _text(authors),
        "isbn": _text(raw.get("isbn"), MAX_ISBN),
        "series": _text(raw.get("series")),
        "series_index": float(index) if index is not None else None,
    }
    if not (out["title"] or out["filename"]):
        return None
    return out


def store_metadata(db: Session, user_id: int, document: str, raw: Any) -> Optional[KOSyncDocumentMeta]:
    """Remember what the reader said about ``document``. Writes only when it
    differs from what is stored. Does not commit."""
    fields = clean_metadata(raw)
    if fields is None:
        return None
    row = (
        db.query(KOSyncDocumentMeta)
        .filter(KOSyncDocumentMeta.user_id == user_id, KOSyncDocumentMeta.document == document)
        .first()
    )
    if row is None:
        row = KOSyncDocumentMeta(user_id=user_id, document=document, ignored=False)
        db.add(row)
    elif all(getattr(row, key) == fields[key] for key in _FIELDS):
        return row
    for key in _FIELDS:
        setattr(row, key, fields[key])
    row.updated_at = datetime.utcnow()
    return row


def titles_for(db: Session, user_id: int, documents: list[str]) -> dict[str, str]:
    """Display title per document, for documents the reader described. Keys
    are the documents as given; the lookup ignores the case of the hash."""
    if not documents:
        return {}
    wanted = {d.lower(): d for d in documents}
    rows = (
        db.query(KOSyncDocumentMeta)
        .filter(KOSyncDocumentMeta.user_id == user_id, func.lower(KOSyncDocumentMeta.document).in_(list(wanted)))
        .all()
    )
    return {wanted[r.document.lower()]: t for r in rows if (t := display_title(r))}


def display_title(meta: KOSyncDocumentMeta) -> Optional[str]:
    """The book's title, or the file name without its extension."""
    if meta.title:
        return meta.title
    if meta.filename:
        return PurePosixPath(meta.filename).stem or meta.filename
    return None


# ── matching ─────────────────────────────────────────────────────────────────

def _fold(text: Optional[str]) -> str:
    if not text:
        return ""
    return _SPACE.sub(" ", unicodedata.normalize("NFKC", text)).strip().casefold()


def _author_key(text: Optional[str]) -> tuple[str, ...]:
    """The authors as a sorted tuple of folded names: "A & B" is "B, A"."""
    if not text:
        return ()
    return tuple(sorted({name for part in _AUTHOR_SPLIT.split(text) if (name := _fold(part))}))


def _isbn(text: Optional[str]) -> str:
    if not text:
        return ""
    digits = re.sub(r"[^0-9Xx]", "", text).upper()
    return digits if len(digits) in (10, 13) else ""


def volume_hint(meta: KOSyncDocumentMeta) -> Optional[float]:
    """Which volume the reader's file is: what it says, else a volume number
    in the file name ("Series v02.epub")."""
    if meta.series_index is not None:
        return float(meta.series_index)
    if not meta.filename:
        return None
    try:
        from backend.services.filename_parser import parse_filename
        index = parse_filename(meta.filename).series_index
        return float(index) if index is not None else None
    except Exception:  # noqa: BLE001  a file name must never fail a sync
        return None


def _volume_conflict(meta: KOSyncDocumentMeta, hint: Optional[float], book_index: Optional[float]) -> bool:
    """Both sides name a volume and it is not the same one."""
    return hint is not None and book_index is not None and float(book_index) != hint


def _volume_agrees(meta: KOSyncDocumentMeta, hint: Optional[float], book_index: Optional[float]) -> bool:
    """For a match by name. A reader that states the volume only matches a
    book Tome has as that volume; otherwise it is enough not to disagree."""
    if meta.series_index is not None:
        return book_index is not None and float(book_index) == float(meta.series_index)
    return not _volume_conflict(meta, hint, book_index)


@dataclass(frozen=True)
class Candidate:
    """A library book with the keys ``find_match`` compares, folded once."""
    id: int
    series_index: Optional[float]
    title_key: str
    author_key: tuple[str, ...]
    isbn_key: str


def library_candidates(db: Session, user: User) -> list[Candidate]:
    """The library books ``find_match`` compares against (active books the
    user can see), folded once. For a caller resolving many documents in one
    go; the live sync resolves one document and folds as it compares."""
    rows = (
        db.query(Book.id, Book.title, Book.author, Book.isbn, Book.series_index)
        .filter(Book.status == BOOK_ACTIVE, book_visibility_filter(db, user))
        .all()
    )
    return [Candidate(r.id, r.series_index, _fold(r.title), _author_key(r.author), _isbn(r.isbn)) for r in rows]


def find_match(
    db: Session, user: User, meta: KOSyncDocumentMeta, prefer_book_id: Optional[int] = None,
    candidates: Optional[list[Candidate]] = None,
) -> tuple[Optional[Book], bool]:
    """``(book, ambiguous)`` for the reader's description of a file.

    Library books the user can see come first; one of the user's own
    file-less books for the same title, author and volume is reused, so a
    renamed file does not get a second record. ``ambiguous`` is True when more
    than one library book fits and Tome will not choose, unless
    ``prefer_book_id`` (the book the user last downloaded over OPDS) is one
    of them.
    """
    title = _fold(display_title(meta))
    authors = _author_key(meta.authors)
    isbn = _isbn(meta.isbn)
    hint = volume_hint(meta)

    rows: list[Any]
    if candidates is None:
        rows = (
            db.query(Book.id, Book.title, Book.author, Book.isbn, Book.series_index)
            .filter(Book.status == BOOK_ACTIVE, book_visibility_filter(db, user))
            .all()
        )
        title_of: Callable[[Any], str] = lambda r: _fold(r.title)  # noqa: E731
        authors_of: Callable[[Any], tuple[str, ...]] = lambda r: _author_key(r.author)  # noqa: E731
        isbn_of: Callable[[Any], str] = lambda r: _isbn(r.isbn)  # noqa: E731
    else:
        rows = candidates
        title_of, authors_of, isbn_of = (lambda r: r.title_key), (lambda r: r.author_key), (lambda r: r.isbn_key)

    def pick(ids: list[int]) -> tuple[Optional[Book], bool]:
        if len(ids) == 1:
            return db.get(Book, ids[0]), False
        if prefer_book_id in ids:
            return db.get(Book, prefer_book_id), False
        return None, True

    if isbn:
        by_isbn = [
            r.id for r in rows
            if isbn_of(r) == isbn
            and (title_of(r) == title or (authors and authors_of(r) == authors))
            and not _volume_conflict(meta, hint, r.series_index)
        ]
        if by_isbn:
            return pick(by_isbn)
    if not title:
        return None, False
    if authors:
        by_name = [
            r.id for r in rows
            if title_of(r) == title and authors_of(r) == authors
            and _volume_agrees(meta, hint, r.series_index)
        ]
        if by_name:
            return pick(by_name)
    # The file the user just downloaded from Tome over OPDS, under the same
    # title: the download is the evidence the author spelling lacks.
    if prefer_book_id is not None:
        for r in rows:
            if r.id == prefer_book_id and title_of(r) == title and not _volume_conflict(meta, hint, r.series_index):
                return db.get(Book, r.id), False
    if not authors:
        return None, False

    own = (
        db.query(Book)
        .filter(Book.status == BOOK_NO_FILE, Book.added_by == user.id)
        .order_by(Book.id.asc())
        .all()
    )
    for book in own:
        if _fold(book.title) != title or _author_key(book.author) != authors:
            continue
        if (book.series_index is None) != (meta.series_index is None):
            continue
        if meta.series_index is not None and float(book.series_index) != float(meta.series_index):
            continue
        if meta.series_index is None and _document_hints(db, user.id, book.id) not in (set(), {hint}):
            continue
        return book, False
    return None, False


def _document_hints(db: Session, user_id: int, book_id: int) -> set[Optional[float]]:
    """The volume hints of the documents already linked to a book."""
    rows = (
        db.query(KOSyncDocumentMeta)
        .join(KOSyncDocumentMap, (KOSyncDocumentMap.document == KOSyncDocumentMeta.document)
              & (KOSyncDocumentMap.tome_user_id == KOSyncDocumentMeta.user_id))
        .filter(KOSyncDocumentMap.book_id == book_id, KOSyncDocumentMeta.user_id == user_id)
        .all()
    )
    return {volume_hint(r) for r in rows}


def resolve(
    db: Session, user: User, document: str, meta: KOSyncDocumentMeta,
    prefer_book_id: Optional[int] = None, commit: bool = True,
    candidates: Optional[list[Candidate]] = None,
) -> Optional[KOSyncDocumentMap]:
    """Give an unlinked ``document`` its book: a matching library book, or a
    new record without a file. Returns None when several books match, the
    metadata names nothing, or the user deleted this file's record before.
    Commits, unless ``commit`` is False: then it only flushes, and the audit
    entry joins the caller's transaction (the CrossPoint Sync import).
    ``candidates``: pre-loaded ``library_candidates`` (see there)."""
    title = display_title(meta)
    if not title or meta.ignored:
        return None
    book, ambiguous = find_match(db, user, meta, prefer_book_id, candidates=candidates)
    if ambiguous:
        logger.info("kosync: document %s matches several books for user %s, left unlinked", document, user.id)
        return None

    created = book is None
    if book is None:
        book = Book(
            title=title,
            author=meta.authors,
            series=meta.series,
            series_index=meta.series_index,
            isbn=meta.isbn,
            status=BOOK_NO_FILE,
            is_reviewed=True,
            added_by=user.id,
        )
        db.add(book)
        db.flush()

    doc_map = KOSyncDocumentMap(tome_user_id=user.id, document=document, book_id=book.id)
    db.add(doc_map)
    if commit:
        db.commit()
    else:
        db.flush()

    if created:
        from backend.services.audit import audit
        audit(db, "books.created_from_reader", user_id=user.id, username=user.username,
              resource_type="book", resource_id=book.id, resource_title=book.title,
              details={"document": document, "filename": meta.filename}, commit=commit)
        logger.info("kosync: created file-less book %s for document %s", book.id, document)
    else:
        logger.info("kosync: linked document %s to book %s by its metadata", document, book.id)
    return doc_map


# ── when a file-less book goes ───────────────────────────────────────────────

def forget_documents(db: Session, book: Book) -> None:
    """The user is deleting a file-less book: do not make it again from the
    same files on their next sync. A manual link still works and lifts this.
    Does not commit."""
    if book.status != BOOK_NO_FILE:
        return
    for doc_map in db.query(KOSyncDocumentMap).filter(KOSyncDocumentMap.book_id == book.id).all():
        db.query(KOSyncDocumentMeta).filter(
            KOSyncDocumentMeta.user_id == doc_map.tome_user_id,
            KOSyncDocumentMeta.document == doc_map.document,
        ).update({KOSyncDocumentMeta.ignored: True}, synchronize_session=False)


def remember_document(db: Session, user_id: int, document: str) -> None:
    """The user linked this document by hand: it is tracked again. Does not commit."""
    db.query(KOSyncDocumentMeta).filter(
        KOSyncDocumentMeta.user_id == user_id, KOSyncDocumentMeta.document == document,
    ).update({KOSyncDocumentMeta.ignored: False}, synchronize_session=False)


_STATUS_RANK = {"read": 3, "reading": 2}


def hand_over(db: Session, user: User, old_book_id: Optional[int], new_book_id: int) -> Optional[Book]:
    """A document moved from ``old_book_id`` to ``new_book_id``. When the old
    one is a file-less book of this user that no document or session points
    at any more, the real book takes over what the user recorded on it (read
    status, finish date, rating, review, the further progress) and the old
    book is returned for the caller to delete after its commit. Otherwise
    None. Does not commit."""
    from backend.models.kosync import ReadingHistory
    from backend.models.tome_sync import ReadingSession
    from backend.models.user_book_status import UserBookStatus

    if not old_book_id or old_book_id == new_book_id:
        return None
    old = db.get(Book, old_book_id)
    if old is None or old.status != BOOK_NO_FILE or old.added_by != user.id:
        return None
    db.flush()
    if db.query(KOSyncDocumentMap.id).filter(KOSyncDocumentMap.book_id == old_book_id).first():
        return None
    if db.query(ReadingSession.id).filter(ReadingSession.book_id == old_book_id).first():
        return None

    db.query(ReadingHistory).filter(
        ReadingHistory.user_id == user.id, ReadingHistory.book_id == old_book_id,
    ).update({ReadingHistory.book_id: new_book_id}, synchronize_session=False)

    row = (
        db.query(UserBookStatus)
        .filter(UserBookStatus.user_id == user.id, UserBookStatus.book_id == old_book_id)
        .first()
    )
    if row is not None:
        kept = (
            db.query(UserBookStatus)
            .filter(UserBookStatus.user_id == user.id, UserBookStatus.book_id == new_book_id)
            .first()
        )
        if kept is None:
            row.book_id = new_book_id
        else:
            if _STATUS_RANK.get(row.status, 1) > _STATUS_RANK.get(kept.status, 1):
                kept.status = row.status
            if kept.finished_at is None:
                kept.finished_at = row.finished_at
            if kept.rating is None and row.rating is not None:
                kept.rating, kept.rated_at = row.rating, row.rated_at
            if not kept.review:
                kept.review = row.review
            if (row.progress_pct or 0) > (kept.progress_pct or 0):
                kept.progress_pct = row.progress_pct
            db.delete(row)
        db.flush()
    return old
