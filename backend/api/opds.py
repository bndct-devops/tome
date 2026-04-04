"""OPDS 1.2 catalog endpoints. Mounted at /opds (not under /api)."""
import logging
from pathlib import Path
from urllib.parse import quote, unquote

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload

from backend.core.database import get_db
from backend.core.security import get_current_user_basic
from backend.core.config import settings
from backend.models.book import Book, BookFile
from backend.models.library import Library
from backend.models.user import User
from backend.models.user_book_status import UserBookStatus
from backend.services.opds import (
    ACQUISITION_TYPE, FORMAT_MIME,
    add_book_entry, add_navigation_entry, add_pagination,
    feed_response, make_feed,
)

router = APIRouter(prefix="/opds", tags=["opds"])
logger = logging.getLogger(__name__)

PER_PAGE = 50


def _base_url(request: Request) -> str:
    return str(request.base_url).rstrip("/")


def _apply_visibility(query, user: User):
    """Replicate the same visibility rules as the main books API."""
    if user.is_admin:
        return query
    return query.join(Book.libraries).filter(
        (Library.is_public == True) |  # noqa: E712
        Library.assigned_users.any(User.id == user.id)
    )


def _book_query(db: Session, user: User):
    return (
        _apply_visibility(
            db.query(Book).options(joinedload(Book.files), joinedload(Book.tags)),
            user,
        )
        .filter(Book.status == "active")
    )


# ── Root catalog ──────────────────────────────────────────────────────────────

@router.get("")
@router.get("/")
def opds_root(request: Request, user: User = Depends(get_current_user_basic)):
    base = _base_url(request)
    feed = make_feed("urn:tome:root", "Tome", f"{base}/opds", kind="navigation")
    add_navigation_entry(feed, "urn:tome:all", "All Books", f"{base}/opds/all",
                         content="Browse the full library")
    add_navigation_entry(feed, "urn:tome:reading", "Currently Reading", f"{base}/opds/reading",
                         content="Books you are currently reading")
    add_navigation_entry(feed, "urn:tome:recent", "Recently Added", f"{base}/opds/recent",
                         content="Last 50 books added")
    add_navigation_entry(feed, "urn:tome:series", "Series", f"{base}/opds/series",
                         content="Browse by series")
    add_navigation_entry(feed, "urn:tome:authors", "Authors", f"{base}/opds/authors",
                         content="Browse by author")
    add_navigation_entry(feed, "urn:tome:libraries", "Libraries", f"{base}/opds/libraries",
                         content="Browse by library")
    return feed_response(feed)


# ── All books (paginated) ─────────────────────────────────────────────────────

@router.get("/all")
def opds_all(
    request: Request,
    page: int = Query(1, ge=1),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_basic),
):
    base = _base_url(request)
    q = _book_query(db, user)
    total = q.distinct().count()
    books = q.distinct().order_by(Book.title.asc()).offset((page - 1) * PER_PAGE).limit(PER_PAGE).all()

    feed = make_feed("urn:tome:all", "All Books", f"{base}/opds/all",
                     kind="acquisition", up_url=f"{base}/opds")
    add_pagination(feed, f"{base}/opds/all", page, PER_PAGE, total)
    for book in books:
        add_book_entry(feed, book, base)
    return feed_response(feed)


# ── Currently reading ─────────────────────────────────────────────────────────

@router.get("/reading")
def opds_reading(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_basic),
):
    base = _base_url(request)
    books = (
        db.query(Book)
        .options(joinedload(Book.files), joinedload(Book.tags))
        .join(UserBookStatus, (UserBookStatus.book_id == Book.id) & (UserBookStatus.user_id == user.id))
        .filter(UserBookStatus.status == "reading", Book.status == "active")
        .order_by(UserBookStatus.updated_at.desc())
        .all()
    )
    feed = make_feed("urn:tome:reading", "Currently Reading", f"{base}/opds/reading",
                     kind="acquisition", up_url=f"{base}/opds")
    for book in books:
        add_book_entry(feed, book, base)
    return feed_response(feed)


# ── Recently added ────────────────────────────────────────────────────────────

@router.get("/recent")
def opds_recent(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_basic),
):
    base = _base_url(request)
    books = (
        _book_query(db, user)
        .distinct()
        .order_by(Book.added_at.desc())
        .limit(50)
        .all()
    )
    feed = make_feed("urn:tome:recent", "Recently Added", f"{base}/opds/recent",
                     kind="acquisition", up_url=f"{base}/opds")
    for book in books:
        add_book_entry(feed, book, base)
    return feed_response(feed)


# ── Series index ──────────────────────────────────────────────────────────────

@router.get("/series")
def opds_series(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_basic),
):
    base = _base_url(request)
    rows = (
        _apply_visibility(db.query(Book.series, func.count(Book.id.distinct())), user)
        .filter(Book.series.isnot(None), Book.status == "active")
        .group_by(Book.series)
        .order_by(Book.series.asc())
        .all()
    )
    feed = make_feed("urn:tome:series", "Series", f"{base}/opds/series",
                     kind="navigation", up_url=f"{base}/opds")
    for series_name, count in rows:
        add_navigation_entry(
            feed, f"urn:tome:series:{series_name}", series_name,
            f"{base}/opds/series/{quote(series_name, safe='')}",
            content=f"{count} book{'s' if count != 1 else ''}",
        )
    return feed_response(feed)


@router.get("/series/{series_name}")
def opds_series_books(
    series_name: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_basic),
):
    base = _base_url(request)
    name = unquote(series_name)
    books = (
        _book_query(db, user)
        .filter(Book.series == name)
        .distinct()
        .order_by(Book.series_index.asc().nullslast(), Book.title.asc())
        .all()
    )
    feed = make_feed(f"urn:tome:series:{name}", name, f"{base}/opds/series/{series_name}",
                     kind="acquisition", up_url=f"{base}/opds/series")
    for book in books:
        if book.series_index is not None:
            vol = int(book.series_index) if book.series_index == int(book.series_index) else book.series_index
            display_title = f"Vol. {vol} — {book.title}"
        else:
            display_title = book.title
        add_book_entry(feed, book, base, display_title=display_title)
    return feed_response(feed)


# ── Authors index ─────────────────────────────────────────────────────────────

@router.get("/authors")
def opds_authors(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_basic),
):
    base = _base_url(request)
    rows = (
        _apply_visibility(db.query(Book.author, func.count(Book.id.distinct())), user)
        .filter(Book.author.isnot(None), Book.status == "active")
        .group_by(Book.author)
        .order_by(Book.author.asc())
        .all()
    )
    feed = make_feed("urn:tome:authors", "Authors", f"{base}/opds/authors",
                     kind="navigation", up_url=f"{base}/opds")
    for author_name, count in rows:
        add_navigation_entry(
            feed, f"urn:tome:author:{author_name}", author_name,
            f"{base}/opds/authors/{quote(author_name, safe='')}",
            content=f"{count} book{'s' if count != 1 else ''}",
        )
    return feed_response(feed)


@router.get("/authors/{author_name}")
def opds_author_books(
    author_name: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_basic),
):
    base = _base_url(request)
    name = unquote(author_name)
    books = (
        _book_query(db, user)
        .filter(Book.author == name)
        .distinct()
        .order_by(Book.title.asc())
        .all()
    )
    feed = make_feed(f"urn:tome:author:{name}", name, f"{base}/opds/authors/{author_name}",
                     kind="acquisition", up_url=f"{base}/opds/authors")
    for book in books:
        add_book_entry(feed, book, base)
    return feed_response(feed)


# ── Libraries ─────────────────────────────────────────────────────────────────

@router.get("/libraries")
def opds_libraries(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_basic),
):
    base = _base_url(request)
    if user.is_admin:
        libs = db.query(Library).order_by(Library.sort_order.asc(), Library.name.asc()).all()
    else:
        libs = (
            db.query(Library)
            .filter(
                (Library.is_public == True) |  # noqa: E712
                Library.assigned_users.any(User.id == user.id)
            )
            .order_by(Library.sort_order.asc(), Library.name.asc())
            .all()
        )
    feed = make_feed("urn:tome:libraries", "Libraries", f"{base}/opds/libraries",
                     kind="navigation", up_url=f"{base}/opds")
    for lib in libs:
        count = len(lib.books)
        add_navigation_entry(
            feed, f"urn:tome:library:{lib.id}", lib.name,
            f"{base}/opds/libraries/{lib.id}",
            content=f"{count} book{'s' if count != 1 else ''}",
        )
    return feed_response(feed)


@router.get("/libraries/{library_id}")
def opds_library_books(
    library_id: int,
    request: Request,
    page: int = Query(1, ge=1),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_basic),
):
    base = _base_url(request)
    lib = db.get(Library, library_id)
    if not lib:
        raise HTTPException(status_code=404, detail="Library not found")
    if not user.is_admin and not lib.is_public:
        if not any(u.id == user.id for u in lib.assigned_users):
            raise HTTPException(status_code=403)

    q = (
        db.query(Book)
        .options(joinedload(Book.files), joinedload(Book.tags))
        .join(Book.libraries)
        .filter(Library.id == library_id, Book.status == "active")
    )
    total = q.distinct().count()
    books = q.distinct().order_by(Book.title.asc()).offset((page - 1) * PER_PAGE).limit(PER_PAGE).all()

    feed = make_feed(f"urn:tome:library:{library_id}", lib.name,
                     f"{base}/opds/libraries/{library_id}",
                     kind="acquisition", up_url=f"{base}/opds/libraries")
    add_pagination(feed, f"{base}/opds/libraries/{library_id}", page, PER_PAGE, total)
    for book in books:
        add_book_entry(feed, book, base)
    return feed_response(feed)


# ── Search ────────────────────────────────────────────────────────────────────

@router.get("/search")
def opds_search_descriptor(request: Request, user: User = Depends(get_current_user_basic)):
    base = _base_url(request)
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<OpenSearchDescription xmlns="http://a9.com/-/spec/opensearch/1.1/">'
        f'<ShortName>Tome</ShortName>'
        f'<Description>Search your Tome library</Description>'
        f'<InputEncoding>UTF-8</InputEncoding>'
        f'<OutputEncoding>UTF-8</OutputEncoding>'
        f'<Url type="{ACQUISITION_TYPE}"'
        f'     template="{base}/opds/search?q={{searchTerms}}"/>'
        '</OpenSearchDescription>'
    )
    return Response(content=xml, media_type="application/opensearchdescription+xml")


@router.get("/search/results")
def opds_search_results(
    request: Request,
    q: str = Query(...),
    page: int = Query(1, ge=1),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_basic),
):
    base = _base_url(request)
    like = f"%{q}%"
    base_q = (
        _book_query(db, user)
        .filter(
            or_(Book.title.ilike(like), Book.author.ilike(like), Book.series.ilike(like))
        )
    )
    total = base_q.distinct().count()
    books = base_q.distinct().order_by(Book.title.asc()).offset((page - 1) * PER_PAGE).limit(PER_PAGE).all()

    feed = make_feed(f"urn:tome:search:{q}", f"Search: {q}",
                     f"{base}/opds/search/results?q={quote(q)}",
                     kind="acquisition", up_url=f"{base}/opds")
    add_pagination(feed, f"{base}/opds/search/results?q={quote(q)}", page, PER_PAGE, total)
    for book in books:
        add_book_entry(feed, book, base)
    return feed_response(feed)


# ── Download ──────────────────────────────────────────────────────────────────

@router.get("/download/{book_id}/{file_id}")
def opds_download(
    book_id: int,
    file_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_basic),
):
    if not user.is_admin and not (user.permissions and user.permissions.can_download):
        raise HTTPException(status_code=403, detail="Download permission required")

    book = db.get(Book, book_id)
    if not book or book.status != "active":
        raise HTTPException(status_code=404)

    book_file = db.query(BookFile).filter(
        BookFile.id == file_id, BookFile.book_id == book_id
    ).first()
    if not book_file:
        raise HTTPException(status_code=404)

    file_path = Path(book_file.file_path)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found on disk")

    from backend.services.audit import audit
    audit(db, "books.downloaded",
          user_id=user.id, username=user.username,
          resource_type="book", resource_id=book.id, resource_title=book.title)

    # Queue this download so the next unknown KOSync progress push auto-links to this book
    try:
        from backend.models.kosync import OPDSPendingLink, KOSyncDocumentMap
        already_mapped = db.query(KOSyncDocumentMap).filter(
            KOSyncDocumentMap.tome_user_id == user.id,
            KOSyncDocumentMap.book_id == book.id,
        ).first()
        if not already_mapped:
            db.add(OPDSPendingLink(user_id=user.id, book_id=book.id))
            db.commit()
    except Exception:
        pass

    media_type = FORMAT_MIME.get(book_file.format, "application/octet-stream")
    filename = f"{book.title}.{book_file.format}"
    return FileResponse(str(file_path), media_type=media_type, filename=filename)
