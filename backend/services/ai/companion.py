"""Reading companion: the reader's position, the text window, the glossary
payload and the gates shared by the web (``/api/ai``) and plugin
(``/api/tome-sync/ai``) endpoints.

The window. A synced position is a page fraction (KOReader) or a section
fraction (web reader), not the word fraction the chapter map uses, and it
runs ahead of it on books with images. ``book_text.reading_fraction`` turns it
into a word fraction ``w`` that is never past the reader, from the stored
xpointer or CFI when there is one. The reader is then in chapter
``k = book_text.chapter_for_fraction(w)`` (rounded DOWN; 0 when ``w`` is
before chapter 0). Everything the companion shows for that position is built
from:

- the digests of the chapters before ``k`` in the reader's scope (their read
  earlier volumes in full, then chapters ``0..k-1`` of this book), and
- for recap and live Recall, the text of chapter ``k`` cut at ``w``.

Nothing after ``w`` is ever read. A ``chapter`` asked for explicitly (web)
may not be past the reader's own position chapter.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from backend.core.permissions import is_member_or_above
from backend.models.ai_chapter_digest import AIChapterDigest
from backend.models.book import Book, BookChapter
from backend.models.tome_sync import TomeSyncPosition
from backend.models.user import User
from backend.models.user_book_status import UserBookStatus
from backend.services import ai, book_text
from backend.services.ai import digest, digest_job, settings as ai_settings
from backend.services.ai.provider import AIBudgetExceeded, AINotConfigured

RECAP = "recap"
RECALL = "recall"
DIGEST = digest.FEATURE

NO_CHAPTERS = book_text.NO_CHAPTERS
NOT_EPUB = book_text.NOT_EPUB
NO_POSITION = "no_position"
FEATURE_OFF = "feature_off"
NO_KEY = "no_key"

MAX_WINDOW_WORDS = 6000   # current-chapter text sent to recap and live Recall


class WindowError(Exception):
    """The requested window cannot be served. ``status_code`` + a plain
    English ``message`` for the API layer; ``reason`` is machine-readable."""

    def __init__(self, status_code: int, reason: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.reason = reason
        self.message = message


# ── position ─────────────────────────────────────────────────────────────────

@dataclass
class Position:
    pct: float                  # as synced (page or section fraction): for display
    source: str                 # "device" | "web"
    updated_at: datetime | None
    word_pct: float = 0.0       # the same position as a word fraction, never past it


def _position(book: Book, db: Session, pct: float, locator: str | None, source: str,
              updated_at: datetime | None) -> Position:
    pct = max(0.0, min(1.0, float(pct)))
    word_pct = book_text.reading_fraction(book, pct, locator, db)
    return Position(pct=pct, source=source, updated_at=updated_at,
                    word_pct=pct if word_pct is None else word_pct)


def reader_position(db: Session, user: User, book: Book) -> Position | None:
    """The user's latest position in ``book``: the synced position row (device
    or web reader), else the status row's progress (a book marked read counts
    as 1.0). None when there is neither."""
    pos = (
        db.query(TomeSyncPosition)
        .filter(TomeSyncPosition.user_id == user.id, TomeSyncPosition.book_id == book.id)
        .first()
    )
    if pos is not None and pos.percentage is not None:
        return _position(book, db, pos.percentage, pos.progress,
                         "web" if (pos.device or "") == "web" else "device", pos.updated_at)
    st = (
        db.query(UserBookStatus)
        .filter(UserBookStatus.user_id == user.id, UserBookStatus.book_id == book.id)
        .first()
    )
    if st is None:
        return None
    if st.status == "read":
        return Position(pct=1.0, source="web", updated_at=st.finished_at or st.updated_at,
                        word_pct=1.0)
    if st.progress_pct is not None and st.progress_pct > 0:
        return _position(book, db, st.progress_pct, st.cfi, "web", st.updated_at)
    return None


def position_chapter(book: Book, word_pct: float, db: Session) -> int:
    """Chapter a WORD fraction falls in, rounded down; 0 before chapter 0."""
    k = book_text.chapter_for_fraction(book, word_pct, db)
    return k if k is not None else 0


@dataclass
class Window:
    chapter_idx: int            # the chapter the reader is in (fold covers < this)
    until: float | None         # book fraction the chapter text is cut at; None = whole chapter
    chapters: list[BookChapter]
    position: Position | None

    @property
    def chapter(self) -> BookChapter | None:
        return next((c for c in self.chapters if c.idx == self.chapter_idx), None)

    @property
    def chapter_title(self) -> str | None:
        ch = self.chapter
        return ch.title if ch else None

    @property
    def end_fraction(self) -> float:
        """Where the text this window may see ends (cache validity)."""
        ch = self.chapter
        if ch is None:
            return 1.0
        if self.until is not None:
            return min(self.until, ch.end_fraction)
        return ch.end_fraction


def web_window(db: Session, user: User, book: Book, chapter: int | None) -> Window:
    """The window for a web request: the stored position, or an explicit
    ``chapter`` at or before the position chapter. Raises WindowError."""
    _require_chapters(book, db)
    chapters = book_text.book_chapters(book, db)
    pos = reader_position(db, user, book)
    if pos is None:
        raise WindowError(409, NO_POSITION, "There is no reading position for this book yet.")
    pos_k = position_chapter(book, pos.word_pct, db)
    if chapter is None or chapter == pos_k:
        return Window(chapter_idx=pos_k, until=pos.word_pct, chapters=chapters, position=pos)
    if chapter < 0 or chapter >= len(chapters):
        raise WindowError(422, "bad_chapter", "This book has no such chapter.")
    if chapter > pos_k:
        raise WindowError(422, "past_position", "That chapter is past your reading position.")
    return Window(chapter_idx=chapter, until=None, chapters=chapters, position=pos)


def pct_window(db: Session, book: Book, pct: float, locator: str | None = None) -> Window:
    """The window for a fraction sent by a device, with its xpointer when it
    sends one (exact; without it the fraction is backed off by a margin).
    Raises WindowError."""
    _require_chapters(book, db)
    chapters = book_text.book_chapters(book, db)
    pos = _position(book, db, pct, locator, "device", None)
    return Window(chapter_idx=position_chapter(book, pos.word_pct, db), until=pos.word_pct,
                  chapters=chapters, position=pos)


def _require_chapters(book: Book, db: Session) -> None:
    reason = book_text.unavailable_reason(book, db)
    if reason == NOT_EPUB:
        raise WindowError(422, NOT_EPUB, "The reading companion works with EPUB books only.")
    if reason == NO_CHAPTERS:
        raise WindowError(422, NO_CHAPTERS,
                          "This book has no chapter map yet. The word-count job in Admin builds it.")


def window_text(book: Book, window: Window, db: Session) -> str:
    """Chapter ``k``'s text up to the window's end, at most the last 6000 words
    (the part closest to the reader)."""
    if window.chapter is None:
        return ""
    text = book_text.chapter_text(book, window.chapter_idx, db, until=window.until) or ""
    return book_text.truncate_words(text, MAX_WINDOW_WORDS, keep="tail")


# ── gates ────────────────────────────────────────────────────────────────────

def features_for(db: Session, user: User) -> dict[str, bool]:
    """What this user can use right now: ``recap`` and ``recall`` need AI on,
    the feature on and a key that resolves (recap also needs digests on);
    ``glossary`` is served from storage and needs AI and digests on."""
    if not ai_settings.ai_enabled(db) or not is_member_or_above(user):
        return {"recap": False, "recall": False, "glossary": False}
    has_key = ai_settings.resolve_key(db, user) is not None
    digests_on = ai_settings.feature_enabled(db, DIGEST)
    return {
        "recap": has_key and digests_on and ai_settings.feature_enabled(db, RECAP),
        "recall": has_key and ai_settings.feature_enabled(db, RECALL),
        "glossary": digests_on,
    }


# ── glossary ─────────────────────────────────────────────────────────────────

def digest_rev(digests: list[AIChapterDigest]) -> str:
    """Hash of the digest ids a fold ran over: changes when a digest is added."""
    ids = ",".join(str(i) for i in sorted(d.id for d in digests))
    return hashlib.sha256(ids.encode()).hexdigest()[:16]


def _chapter_titles(db: Session, book_ids: list[int]) -> dict[tuple[int, int], str]:
    if not book_ids:
        return {}
    rows = db.query(BookChapter.book_id, BookChapter.idx, BookChapter.title).filter(
        BookChapter.book_id.in_(book_ids))
    return {(b, i): t for b, i, t in rows}


def entries_to_dicts(db: Session, scope: digest.Scope, entries: list[digest.Entry]) -> list[dict[str, Any]]:
    """``Entry.to_dict()`` with ``first_seen``/``last_seen`` as ``{book_id,
    book_title, chapter_idx, chapter_title}``."""
    vols = scope.volumes()
    titles = _chapter_titles(db, [v.id for v in vols])
    book_titles = {v.id: v.title for v in vols}

    def where(loc: tuple[int, int]) -> dict[str, Any]:
        return {"book_id": loc[0], "book_title": book_titles.get(loc[0]),
                "chapter_idx": loc[1], "chapter_title": titles.get(loc)}

    out = []
    for e in entries:
        d = e.to_dict()
        d["first_seen"] = where(e.first_seen)
        d["last_seen"] = where(e.last_seen)
        out.append(d)
    return out


def glossary_payload(db: Session, user: User, book: Book, window: Window) -> dict[str, Any]:
    """The fold for ``window`` with scope, coverage, the active job (if any)
    and ``rev``. Read-only: never queues work (``start_digest`` does).

    ``entries`` are in order of first appearance; each carries ``first_seen``
    and ``last_seen`` as ``{book_id, book_title, chapter_idx, chapter_title}``."""
    scope = digest.user_scope(db, user, book)
    upto = window.chapter_idx
    digests = digest.scoped_digests(db, scope, upto)
    entries = digest.fold_entries(digests)
    cov = digest.coverage(db, scope, upto)
    out = entries_to_dicts(db, scope, entries)
    return {
        "book_id": book.id,
        "chapter_idx": upto,
        "chapter_title": window.chapter_title,
        "entries": out,
        "count": len(out),
        "scope": scope.to_dict(),
        "coverage": cov,
        "job": digest_job.active_for(user.id, book.id),
        "rev": digest_rev(digests),
    }


# ── starting a digest job (the explicit "read these chapters first") ─────────

DIGEST_SCOPES = ("book", "series")


def _live_jobs_pending(user: User, book: Book) -> float:
    """USD the user's other live jobs are still expected to spend (estimate
    minus spent so far, floored at 0). A live job on this book is left out:
    a new start coalesces into it."""
    total = 0.0
    for snap in digest_job.running(user.id):
        if snap.get("book_id") == book.id:
            continue
        total += max(0.0, (snap.get("est_cost_usd") or 0.0) - (snap.get("cost_usd") or 0.0))
    return total


def _less_pending(est: dict[str, Any] | None, pending: float) -> dict[str, Any] | None:
    """``est`` with ``remaining_budget_usd``/``over_budget`` net of what
    the user's other live jobs will still spend."""
    if est is None or pending <= 0 or est.get("remaining_budget_usd") is None:
        return est
    left = max(0.0, est["remaining_budget_usd"] - pending)
    return {**est, "remaining_budget_usd": round(left, 4),
            "over_budget": est["est_cost_usd"] > left + 1e-9}


def start_digest(db: Session, user: User, book: Book, window: Window,
                 scope: str = "book") -> tuple[int, dict[str, Any]]:
    """(HTTP status, body) for an explicit start: digest the missing chapters
    before the window's chapter, of this book only (``scope="book"``) or of
    the read earlier volumes too (``"series"``).

    202 ``{job, estimate, coverage, scope, chapter_idx, chapter_title}``
    with the queued (or coalesced) job; 200 with ``job`` = the active job or
    None when nothing in the window is missing. Raises ``AIDisabled``
    (digests off), ``AINotConfigured`` (no key) and ``AIBudgetExceeded``
    (409 ``reason: "budget"``: the estimate is over what is left of a
    monthly limit) before anything is queued. A user-started job becomes
    the latest for the book, which lets the automatic triggers act again
    after a cancel."""
    from backend.services.ai import budget, estimate

    ai.check_feature_available(db, DIGEST)
    if ai_settings.resolve_key(db, user) is None:
        raise AINotConfigured("No AI key is configured. Add an Anthropic key in Settings.")
    if scope not in DIGEST_SCOPES:
        scope = "book"
    sc = digest.Scope(book=book) if scope == "book" else digest.user_scope(db, user, book)
    upto = window.chapter_idx
    body: dict[str, Any] = {
        "coverage": digest.coverage(db, sc, upto), "scope": scope,
        "chapter_idx": upto, "chapter_title": window.chapter_title,
    }
    if not digest.missing_chapters(db, sc, upto):
        return 200, {"job": digest_job.active_for(user.id, book.id), "estimate": None, **body}
    pending = _live_jobs_pending(user, book)
    est = _less_pending(estimate.estimate(db, user, book, scope, upto), pending)
    if est["over_budget"]:
        b = budget.status(db, user)
        err = b.exceeded_by(est["est_cost_usd"] + pending)
        if err is not None:
            raise AIBudgetExceeded(
                f"Reading these chapters costs about {est['est_cost_usd']:.2f} USD, more than the "
                f"{est['remaining_budget_usd']:.2f} USD left of the monthly AI budget "
                f"({err.limit:.2f} USD). Raise it in Settings > AI.",
                which=err.which, spent=err.spent, limit=err.limit)
    job = digest_job.enqueue(user_id=user.id, book_id=book.id, upto=upto,
                             current_book_only=(scope == "book"))
    if job.get("expanding") or not job.get("total"):
        # The worker has not planned the job yet: show the estimate's figures
        # rather than "0 of 0" until the first poll.
        job = {**job, "total": est["chapters"], "est_chapters": est["chapters"],
               "est_cost_usd": est["est_cost_usd"], "est_seconds": est["est_seconds"]}
    return 202, {"job": job, "estimate": est, **body}


def digest_estimates(db: Session, user: User, book: Book, window: Window) -> dict[str, Any]:
    """``{book, series, chapter_idx, chapter_title}``: what digesting the
    missing chapters before the window's chapter would cost and take, for
    this book only and with the read earlier volumes (``series`` None when
    there are none). See ``estimate.estimates``."""
    from backend.services.ai import estimate

    ai.check_feature_available(db, DIGEST)
    both = estimate.estimates(db, user, book, window.chapter_idx)
    pending = _live_jobs_pending(user, book)
    return {"book": _less_pending(both["book"], pending), "series": _less_pending(both["series"], pending),
            "chapter_idx": window.chapter_idx, "chapter_title": window.chapter_title}


def glossary_rev(db: Session, user: User, book: Book, pct: float,
                 locator: str | None = None) -> str | None:
    """``rev`` of the glossary at ``pct`` (with the device's xpointer) for the
    plugin's position response; None when the glossary is off for this user
    or the book has no chapters."""
    if not features_for(db, user)["glossary"]:
        return None
    if book_text.unavailable_reason(book, db):
        return None
    scope = digest.user_scope(db, user, book)
    word_pct = book_text.reading_fraction(book, pct, locator, db) or 0.0
    return digest_rev(digest.scoped_digests(db, scope, position_chapter(book, word_pct, db)))
