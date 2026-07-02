"""Reusable reading-statistics aggregation over ReadingSession records.

Used by:
  - GET /books/{book_id}/reading-stats  (per-book stats, Step 1)
  - GET /series/{name}/reading-stats    (per-series stats, Step 2)
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, Integer
from sqlalchemy.orm import Session

from backend.models.book import Book
from backend.models.tome_sync import ReadingSession
from backend.models.user_book_status import UserBookStatus
from backend.services.reading_day import date_modifier, effective_today, epoch_day, epoch_day_int


def _covered_fraction(page_rows) -> float:
    """Fraction of the book covered by dwelled pages, pagination-robust.

    Each (page, total_pages) row covers the fraction interval
    [(page-1)/total, page/total); the union's length is the coverage. Mixing
    absolute page numbers across paginations either under- or over-counts —
    250 pages of a fully-read 250-page pagination cover the whole book even if
    the latest pagination says 1571 pages.
    """
    ivs = sorted(
        ((p - 1) / tp, p / tp)
        for p, tp in page_rows
        if tp and tp > 0 and p and p > 0
    )
    covered = 0.0
    cur_s = cur_e = None
    for s, e in ivs:
        if cur_e is None or s > cur_e + 1e-9:
            if cur_e is not None:
                covered += cur_e - cur_s
            cur_s, cur_e = s, e
        else:
            cur_e = max(cur_e, e)
    if cur_e is not None:
        covered += cur_e - cur_s
    return min(covered, 1.0)


# ── Per-book, per-user ────────────────────────────────────────────────────────

def compute_book_reading_stats(
    db: Session,
    *,
    user_id: int,
    book_id: int,
    tz_offset: int = 0,
) -> dict:
    """Return reading statistics for one user on one book.

    ``tz_offset`` (JS getTimezoneOffset minutes) buckets the timeline/momentum
    by the user's reading day — local day with 4h rollover, matching streaks.

    Returns a dict with keys:
      total_seconds, sessions, pages_turned, avg_session_seconds,
      pace_pages_per_min, first_read, last_read, progress, status,
      session_timeline, estimated_finish_seconds
    """
    base = (
        db.query(ReadingSession)
        .filter(
            ReadingSession.user_id == user_id,
            ReadingSession.book_id == book_id,
        )
    )

    # ── Aggregate totals ─────────────────────────────────────────────────────
    agg = base.with_entities(
        func.coalesce(func.sum(ReadingSession.duration_seconds), 0).label("total_seconds"),
        func.count(ReadingSession.id).label("sessions"),
        func.coalesce(func.sum(ReadingSession.pages_turned), 0).label("pages_turned"),
        func.min(ReadingSession.started_at).label("first_read"),
        func.max(
            func.coalesce(ReadingSession.ended_at, ReadingSession.started_at)
        ).label("last_read"),
    ).first()

    total_seconds: int = int(agg.total_seconds) if agg and agg.total_seconds else 0
    sessions: int = int(agg.sessions) if agg and agg.sessions else 0
    pages_turned: int = int(agg.pages_turned) if agg and agg.pages_turned else 0

    avg_session_seconds: int = (
        round(total_seconds / sessions) if sessions > 0 else 0
    )

    # Pace: pages per minute
    total_minutes = total_seconds / 60.0
    if total_minutes > 0 and pages_turned > 0:
        pace_pages_per_min: Optional[float] = round(pages_turned / total_minutes, 2)
    else:
        pace_pages_per_min = None

    first_read: Optional[str] = (
        agg.first_read.isoformat() + "Z" if agg and agg.first_read else None
    )
    last_read: Optional[str] = (
        agg.last_read.isoformat() + "Z" if agg and agg.last_read else None
    )

    # ── Reading status + progress ────────────────────────────────────────────
    status_row = (
        db.query(UserBookStatus)
        .filter_by(user_id=user_id, book_id=book_id)
        .first()
    )
    book_status: str = status_row.status if status_row else "unread"
    # progress_pct is stored as 0-1 fraction in UserBookStatus
    progress: Optional[float] = status_row.progress_pct if status_row else None

    # ── Session timeline — reading-day buckets ────────────────────────────────
    day_mod = date_modifier(tz_offset)
    timeline_rows = (
        base.with_entities(
            func.date(ReadingSession.started_at, day_mod).label("date"),
            func.coalesce(func.sum(ReadingSession.duration_seconds), 0).label("seconds"),
            func.coalesce(func.sum(ReadingSession.pages_turned), 0).label("pages"),
        )
        .group_by(func.date(ReadingSession.started_at, day_mod))
        .order_by(func.date(ReadingSession.started_at, day_mod))
        .all()
    )
    session_timeline = [
        {"date": row.date, "seconds": int(row.seconds), "pages": int(row.pages)}
        for row in timeline_rows
    ]

    # ── Reconcile with imported KOReader page-stats ──────────────────────────
    # Page-stats win per book for *device* reading (same rule as the dashboard's
    # reconciled_reading), so a book read only on the device isn't shown empty.
    # Web-reader and manual-log sessions are invisible to KOReader's history and
    # stay ADDITIVE on top — replacing them made a hand-logged paper session on
    # a Kindle-synced book vanish without a trace. Untouched when the book has
    # no page-stats — ps_seconds is 0 and this whole block is skipped.
    from backend.models.ko_stats import PageStat
    from backend.services.reconciled_reading import NON_DEVICE_SOURCES

    ps = (
        db.query(
            func.coalesce(func.sum(PageStat.duration_seconds), 0),
            func.min(PageStat.start_time),
            func.max(PageStat.start_time),
            # Furthest *position* as a per-row fraction: a page number only means
            # anything against its own row's pagination (font changes re-paginate),
            # so max(page)/max(total_pages) across paginations understates badly.
            func.max(PageStat.page * 1.0 / PageStat.total_pages),
        )
        .filter(PageStat.user_id == user_id, PageStat.book_id == book_id)
        .one()
    )
    ps_seconds = int(ps[0] or 0)
    ps_total_pages = 0
    ps_position = 0.0
    if ps_seconds > 0:
        ps_position = min(float(ps[3] or 0.0), 1.0)   # furthest fraction reached
        # Latest pagination — total_pages from the max(start_time) row (SQLite's
        # bare-column rule applies: exactly one aggregate). max(total_pages)
        # would let a long-gone pagination inflate the denominator forever.
        latest = (
            db.query(PageStat.total_pages, func.max(PageStat.start_time))
            .filter(PageStat.user_id == user_id, PageStat.book_id == book_id,
                    PageStat.total_pages > 0)
            .one()
        )
        ps_total_pages = int(latest[0] or 0)
        # Pages read = covered fraction × latest pagination, so coverage from
        # different paginations doesn't mix absolute page numbers ("250 of 1571").
        page_rows = (
            db.query(PageStat.page, PageStat.total_pages)
            .filter(PageStat.user_id == user_id, PageStat.book_id == book_id,
                    PageStat.total_pages > 0)
            .distinct()
            .all()
        )
        if ps_total_pages > 0:
            pages_turned = round(_covered_fraction(page_rows) * ps_total_pages)
        else:
            pages_turned = len({p for p, _tp in page_rows})
        # Daily buckets from page-stats, by reading day (local + 4h rollover).
        day_rows = (
            db.query(
                epoch_day(PageStat.start_time, tz_offset).label("day"),
                func.coalesce(func.sum(PageStat.duration_seconds), 0).label("seconds"),
                func.count(func.distinct(PageStat.page)).label("pages"),
            )
            .filter(PageStat.user_id == user_id, PageStat.book_id == book_id)
            .group_by("day")
            .order_by("day")
            .all()
        )

        # Additive sessions: web-reader + manual logs merge on top of page-stats.
        additive = base.filter(ReadingSession.device.in_(NON_DEVICE_SOURCES))
        add_agg = additive.with_entities(
            func.coalesce(func.sum(ReadingSession.duration_seconds), 0),
            func.count(ReadingSession.id),
            func.coalesce(func.sum(ReadingSession.pages_turned), 0),
            func.min(ReadingSession.started_at),
            func.max(func.coalesce(ReadingSession.ended_at, ReadingSession.started_at)),
        ).one()
        add_secs, add_count, add_pages = int(add_agg[0] or 0), int(add_agg[1] or 0), int(add_agg[2] or 0)

        total_seconds = ps_seconds + add_secs
        pages_turned += add_pages

        day_map: dict[str, list[int]] = {r.day: [int(r.seconds), int(r.pages)] for r in day_rows}
        ps_day_count = len(day_map)
        if add_count:
            add_rows = (
                additive.with_entities(
                    func.date(ReadingSession.started_at, day_mod).label("date"),
                    func.coalesce(func.sum(ReadingSession.duration_seconds), 0).label("seconds"),
                    func.coalesce(func.sum(ReadingSession.pages_turned), 0).label("pages"),
                )
                .group_by("date")
                .all()
            )
            for r in add_rows:
                e = day_map.setdefault(r.date, [0, 0])
                e[0] += int(r.seconds or 0)
                e[1] += int(r.pages or 0)
        session_timeline = [
            {"date": d, "seconds": v[0], "pages": v[1]}
            for d, v in sorted(day_map.items())
        ]
        # Device reading counts one "session" per reading day; additive sessions
        # keep their real counts.
        sessions = ps_day_count + add_count
        avg_session_seconds = round(total_seconds / sessions) if sessions else 0

        first_dt = (
            datetime.fromtimestamp(int(ps[1]), timezone.utc).replace(tzinfo=None)
            if ps[1] else None
        )
        last_dt = (
            datetime.fromtimestamp(int(ps[2]), timezone.utc).replace(tzinfo=None)
            if ps[2] else None
        )
        if add_agg[3] is not None:
            first_dt = min(first_dt, add_agg[3]) if first_dt else add_agg[3]
        if add_agg[4] is not None:
            last_dt = max(last_dt, add_agg[4]) if last_dt else add_agg[4]
        if first_dt:
            first_read = first_dt.isoformat() + "Z"
        if last_dt:
            last_read = last_dt.isoformat() + "Z"

        total_minutes = total_seconds / 60.0
        if total_minutes > 0 and pages_turned > 0:
            pace_pages_per_min = round(pages_turned / total_minutes, 2)

    # ── Journey: cumulative progress per reading day (the progress line) ──────
    # Augments each session_timeline day with the furthest-progress reached, so
    # the frontend can plot a progress arc over the minutes-per-day bars.
    # Page-stat days carry no position (dwell is *coverage* — you can be 35% in
    # having dwelled on 11% of pages), but web/manual sessions record
    # progress_end, so their days contribute points even on a device-synced book.
    day_progress: dict[str, float] = {}
    prog_base = (
        base.filter(ReadingSession.device.in_(NON_DEVICE_SOURCES))
        if ps_seconds > 0 else base
    )
    prog_rows = (
        prog_base.with_entities(
            func.date(ReadingSession.started_at, day_mod).label("date"),
            func.max(ReadingSession.progress_end).label("p"),
        )
        .group_by(func.date(ReadingSession.started_at, day_mod))
        .all()
    )
    for r in prog_rows:
        if r.p is not None:
            day_progress[r.date] = min(float(r.p), 1.0)

    # Forward-fill so the arc never dips on a day with reading-time-but-no-progress.
    running = 0.0
    for row in session_timeline:
        if row["date"] in day_progress:
            running = max(running, day_progress[row["date"]])
        row["progress_pct"] = round(running * 100, 1) if running > 0 else None

    # ── Where the reading time came from (web reader vs device) ───────────────
    if ps_seconds > 0:
        # Page-stat devices + additive web/manual sessions, so a mixed reader
        # actually sees the split (device-only rows used to hide the section).
        src_rows = list(
            db.query(
                func.coalesce(PageStat.device, "koreader").label("device"),
                func.coalesce(func.sum(PageStat.duration_seconds), 0).label("seconds"),
                func.count(func.distinct(epoch_day(PageStat.start_time, tz_offset))).label("units"),
            )
            .filter(PageStat.user_id == user_id, PageStat.book_id == book_id)
            .group_by("device")
            .all()
        ) + list(
            base.filter(ReadingSession.device.in_(NON_DEVICE_SOURCES))
            .with_entities(
                ReadingSession.device.label("device"),
                func.coalesce(func.sum(ReadingSession.duration_seconds), 0).label("seconds"),
                func.count(ReadingSession.id).label("units"),
            )
            .group_by("device")
            .all()
        )
    else:
        src_rows = (
            base.with_entities(
                func.coalesce(ReadingSession.device, "web-reader").label("device"),
                func.coalesce(func.sum(ReadingSession.duration_seconds), 0).label("seconds"),
                func.count(ReadingSession.id).label("units"),
            )
            .group_by("device")
            .all()
        )
    by_source = [
        {"device": r.device, "seconds": int(r.seconds), "sessions": int(r.units)}
        for r in sorted(src_rows, key=lambda r: -int(r.seconds))
        if int(r.seconds) > 0
    ]

    # ── Reading momentum: last 7 days vs the 7 before ─────────────────────────
    # "Today" is the user's current reading day, matching the timeline buckets.
    today = effective_today(tz_offset)
    recent_seconds = prior_seconds = 0
    for row in session_timeline:
        try:
            d = datetime.strptime(row["date"], "%Y-%m-%d").date()
        except (ValueError, TypeError):
            continue
        delta_days = (today - d).days
        if 0 <= delta_days < 7:
            recent_seconds += row["seconds"]
        elif 7 <= delta_days < 14:
            prior_seconds += row["seconds"]
    momentum: Optional[dict] = None
    if recent_seconds or prior_seconds:
        if recent_seconds > prior_seconds:
            direction = "up"
        elif recent_seconds < prior_seconds:
            direction = "down"
        else:
            direction = "flat"
        momentum = {
            "recent_seconds": recent_seconds,
            "prior_seconds": prior_seconds,
            # None = no prior-week baseline to compare against.
            "delta_pct": (
                round((recent_seconds - prior_seconds) / prior_seconds * 100)
                if prior_seconds > 0
                else None
            ),
            "direction": direction,
        }

    # ── Finished date (when the book was marked read) ─────────────────────────
    # Prefer the explicit finished_at; updated_at also moves on rating/review/
    # CFI writes, so it only serves as a legacy fallback for pre-column rows.
    finished_at: Optional[str] = None
    if status_row and status_row.status == "read":
        ts = status_row.finished_at or status_row.updated_at
        if ts:
            finished_at = ts.isoformat() + "Z"

    # ── Progress = how far through the book ──────────────────────────────────
    # Best available evidence of position: the synced reading position OR the
    # furthest position reached (per-row page/total_pages fraction — robust to
    # pagination changes). Page-stats are *coverage*, so use the furthest
    # position, never the distinct-page count (which trails position when pages
    # are skimmed). max() handles both directions: a synced position ahead of
    # the dwell history (you skimmed/jumped), and a stale-low position behind
    # pages you've since read. A finished book is always 100%.
    page_progress = ps_position
    if book_status == "read":
        progress = 1.0
    else:
        candidates = [p for p in (progress, page_progress) if p and p > 0]
        if candidates:
            progress = min(max(candidates), 1.0)

    # ── Estimated time to finish ─────────────────────────────────────────────
    estimated_finish_seconds: Optional[int] = None
    if (
        progress is not None
        and 0 < progress < 1
        and total_seconds > 0
    ):
        # T/p*(1-p): at current pace, how many more seconds remain?
        estimated_finish_seconds = round(total_seconds / progress * (1 - progress))

    return {
        "total_seconds": total_seconds,
        "sessions": sessions,
        "pages_turned": pages_turned,
        "avg_session_seconds": avg_session_seconds,
        "pace_pages_per_min": pace_pages_per_min,
        "first_read": first_read,
        "last_read": last_read,
        "finished_at": finished_at,
        "progress": progress,
        "status": book_status,
        "session_timeline": session_timeline,
        "by_source": by_source,
        "momentum": momentum,
        "estimated_finish_seconds": estimated_finish_seconds,
    }


# ── Per-book reading intensity (imported KOReader page-stats) ─────────────────

def compute_book_page_intensity(
    db: Session,
    *,
    user_id: int,
    book_id: int,
    bins: int = 50,
    tz_offset: int = 0,
) -> Optional[dict]:
    """Per-page reading intensity for one book, from imported KOReader page-stats.

    Returns None when the user has no page-stats for this book (e.g. it was only
    ever read in the web reader) — the caller hides the section in that case.

    KOReader re-paginates whenever font/margins change, so a row's absolute page
    number is only meaningful against its own ``total_pages``. We map each dwell
    row to a fraction-of-book (``page / total_pages``) and bucket into ``bins``
    slots — pagination-robust, and exactly the "where did the time go across the
    book" curve we want. Distinct pages read (against the latest pagination) gives
    an honest "X of Y pages" denominator without needing an intrinsic page count.
    """
    from backend.models.ko_stats import PageStat

    rows = (
        db.query(
            PageStat.page,
            PageStat.total_pages,
            PageStat.duration_seconds,
            PageStat.start_time,
        )
        .filter(
            PageStat.user_id == user_id,
            PageStat.book_id == book_id,
            PageStat.total_pages > 0,
        )
        .all()
    )
    if not rows:
        return None

    curve = [0] * bins
    bin_days: dict[int, set] = defaultdict(set)
    total_seconds = 0
    # "Latest" pagination = the most recent row's total_pages. NOT max():
    # reopening a finished 250-page book once at a 1571-page pagination must
    # not turn it into "250 of 1571 pages".
    latest_total_pages = 0
    latest_start = -1
    page_pairs: set[tuple[int, int]] = set()

    for page, total_pages, dur, start_time in rows:
        if not total_pages or total_pages <= 0:
            continue
        frac = (page - 1) / total_pages          # page is 1-based
        frac = min(max(frac, 0.0), 0.99999)
        b = min(int(frac * bins), bins - 1)
        dur = int(dur or 0)
        curve[b] += dur
        total_seconds += dur
        page_pairs.add((int(page), int(total_pages)))
        if int(start_time or 0) >= latest_start:
            latest_start = int(start_time or 0)
            latest_total_pages = int(total_pages)
        # reading-day bucket (page revisited on a different day ⇒ a re-read)
        bin_days[b].add(epoch_day_int(int(start_time), tz_offset) if start_time else 0)

    # Coverage as a fraction-space interval union — pagination-robust — then
    # expressed against the latest pagination for the "X of Y pages" line.
    covered = _covered_fraction(page_pairs)
    pages_read = round(covered * latest_total_pages) if latest_total_pages else 0
    pct_read = min(round(covered * 100, 1), 100.0) if latest_total_pages else 0.0
    reread_bins = sum(1 for days in bin_days.values() if len(days) > 1)

    return {
        "bins": bins,
        "curve": curve,                 # seconds of dwell per fraction-bin (0..bins-1)
        "total_seconds": total_seconds,
        "total_pages": latest_total_pages,
        "pages_read": pages_read,
        "pct_read": pct_read,           # distinct pages read ÷ total (capped 100)
        "reread_bins": reread_bins,     # bins revisited on a later day
    }


# ── Admin aggregate — all users, one book ────────────────────────────────────

def compute_book_aggregate_stats(
    db: Session,
    *,
    book_id: int,
) -> dict:
    """Return library-wide reading statistics for one book (all users combined).

    Reconciled per user the same way the per-book/per-user stats are: imported
    KOReader page-stats win over live sessions for a given reader, so a book read
    only on the device still counts here (was previously shown as 0).

    ``total_sessions`` counts *reading days* for both sources — page-stats have
    no natural session, and mixing raw session counts with day counts made the
    labelled sum lie for mixed-reader books.

    Returns a dict with keys:
      total_seconds, total_sessions, distinct_readers
    """
    from backend.models.ko_stats import PageStat

    # Per-user live-session totals (seconds, distinct reading days).
    sess_by_user = {
        uid: (int(secs or 0), int(cnt or 0))
        for uid, secs, cnt in (
            db.query(
                ReadingSession.user_id,
                func.coalesce(func.sum(ReadingSession.duration_seconds), 0),
                func.count(func.distinct(func.date(ReadingSession.started_at))),
            )
            .filter(ReadingSession.book_id == book_id)
            .group_by(ReadingSession.user_id)
            .all()
        )
    }
    # Per-user page-stat totals (seconds, distinct reading days = "sessions").
    # UTC days on purpose: this aggregates across ALL readers, so there is no
    # single viewer timezone to bucket by — and only the day *count* is used.
    ps_by_user = {
        uid: (int(secs or 0), int(days or 0))
        for uid, secs, days in (
            db.query(
                PageStat.user_id,
                func.coalesce(func.sum(PageStat.duration_seconds), 0),
                func.count(func.distinct(func.cast(PageStat.start_time / 86400, Integer))),
            )
            .filter(PageStat.book_id == book_id)
            .group_by(PageStat.user_id)
            .all()
        )
    }

    total_seconds = 0
    total_sessions = 0
    readers = 0
    for uid in set(sess_by_user) | set(ps_by_user):
        # Page-stats win for this reader when they have any.
        if uid in ps_by_user and ps_by_user[uid][0] > 0:
            secs, units = ps_by_user[uid]
        else:
            secs, units = sess_by_user.get(uid, (0, 0))
        if secs > 0 or units > 0:
            total_seconds += secs
            total_sessions += units
            readers += 1

    return {
        "total_seconds": total_seconds,
        "total_sessions": total_sessions,
        "distinct_readers": readers,
    }


# ── Per-series, per-user ──────────────────────────────────────────────────────

def compute_series_reading_stats(
    db: Session,
    *,
    user: "User",  # type: ignore[name-defined]
    series_name: str,
) -> dict:
    """Return the current user's reading statistics across all visible books in a series.

    Returns a dict with keys:
      total_seconds, sessions, pages_turned,
      books_total, books_finished, books_in_progress, books_with_sessions,
      completion_pct, avg_volume_seconds, estimated_remaining_seconds,
      longest_volume, first_read, last_read, per_volume
    """
    from backend.core.permissions import book_visibility_filter

    # ── Visible books in this series ────────────────────────────────────────
    visibility = book_visibility_filter(db, user)
    books_q = (
        db.query(Book)
        .filter(
            Book.series == series_name,
            Book.status == "active",
        )
    )
    if visibility is not True:
        books_q = books_q.filter(visibility)

    visible_books = books_q.order_by(Book.series_index).all()
    book_ids = [b.id for b in visible_books]
    books_total = len(book_ids)

    if books_total == 0:
        return _empty_series_stats()

    # ── UserBookStatus for visible books ────────────────────────────────────
    status_rows = (
        db.query(UserBookStatus)
        .filter(
            UserBookStatus.user_id == user.id,
            UserBookStatus.book_id.in_(book_ids),
        )
        .all()
    )
    status_by_book: dict[int, str] = {r.book_id: r.status for r in status_rows}
    books_finished = sum(1 for bid in book_ids if status_by_book.get(bid) == "read")
    books_in_progress = sum(1 for bid in book_ids if status_by_book.get(bid) == "reading")

    # ── Aggregate reading sessions ───────────────────────────────────────────
    agg = (
        db.query(ReadingSession)
        .filter(
            ReadingSession.user_id == user.id,
            ReadingSession.book_id.in_(book_ids),
        )
        .with_entities(
            func.coalesce(func.sum(ReadingSession.duration_seconds), 0).label("total_seconds"),
            func.count(ReadingSession.id).label("sessions"),
            func.coalesce(func.sum(ReadingSession.pages_turned), 0).label("pages_turned"),
            func.min(ReadingSession.started_at).label("first_read"),
            func.max(
                func.coalesce(ReadingSession.ended_at, ReadingSession.started_at)
            ).label("last_read"),
        )
        .first()
    )

    total_seconds: int = int(agg.total_seconds) if agg and agg.total_seconds else 0
    sessions: int = int(agg.sessions) if agg and agg.sessions else 0
    pages_turned: int = int(agg.pages_turned) if agg and agg.pages_turned else 0

    first_read: Optional[str] = (
        agg.first_read.isoformat() + "Z" if agg and agg.first_read else None
    )
    last_read: Optional[str] = (
        agg.last_read.isoformat() + "Z" if agg and agg.last_read else None
    )

    # ── Per-volume seconds ───────────────────────────────────────────────────
    vol_agg_rows = (
        db.query(ReadingSession)
        .filter(
            ReadingSession.user_id == user.id,
            ReadingSession.book_id.in_(book_ids),
        )
        .with_entities(
            ReadingSession.book_id.label("book_id"),
            func.coalesce(func.sum(ReadingSession.duration_seconds), 0).label("seconds"),
        )
        .group_by(ReadingSession.book_id)
        .all()
    )
    seconds_by_book: dict[int, int] = {r.book_id: int(r.seconds) for r in vol_agg_rows}
    books_with_sessions = len(seconds_by_book)

    # ── avg_volume_seconds: mean time per FINISHED volume ───────────────────
    finished_seconds = [
        seconds_by_book.get(bid, 0)
        for bid in book_ids
        if status_by_book.get(bid) == "read"
    ]
    if finished_seconds:
        avg_volume_seconds: int = sum(finished_seconds) // len(finished_seconds)
    elif books_with_sessions > 0:
        avg_volume_seconds = total_seconds // books_with_sessions
    else:
        avg_volume_seconds = 0

    # ── estimated_remaining_seconds ─────────────────────────────────────────
    unfinished_count = books_total - books_finished
    if books_finished > 0 and avg_volume_seconds > 0:
        # Use avg of finished volumes as estimate per remaining volume
        finished_avg = sum(finished_seconds) // len(finished_seconds)
        estimated_remaining_seconds: Optional[int] = finished_avg * unfinished_count
    else:
        estimated_remaining_seconds = None

    # ── longest_volume ───────────────────────────────────────────────────────
    longest_volume: Optional[dict] = None
    if seconds_by_book:
        best_bid = max(seconds_by_book, key=seconds_by_book.__getitem__)
        best_book = next((b for b in visible_books if b.id == best_bid), None)
        if best_book:
            longest_volume = {
                "book_id": best_book.id,
                "title": best_book.title,
                "series_index": best_book.series_index,
                "seconds": seconds_by_book[best_bid],
            }

    # ── completion_pct ───────────────────────────────────────────────────────
    completion_pct = round(books_finished / books_total * 100, 1) if books_total > 0 else 0.0

    # ── per_volume list ──────────────────────────────────────────────────────
    per_volume = [
        {
            "book_id": b.id,
            "series_index": b.series_index,
            "title": b.title,
            "seconds": seconds_by_book.get(b.id, 0),
            "status": status_by_book.get(b.id, "unread"),
        }
        for b in visible_books
    ]

    return {
        "total_seconds": total_seconds,
        "sessions": sessions,
        "pages_turned": pages_turned,
        "books_total": books_total,
        "books_finished": books_finished,
        "books_in_progress": books_in_progress,
        "books_with_sessions": books_with_sessions,
        "completion_pct": completion_pct,
        "avg_volume_seconds": avg_volume_seconds,
        "estimated_remaining_seconds": estimated_remaining_seconds,
        "longest_volume": longest_volume,
        "first_read": first_read,
        "last_read": last_read,
        "per_volume": per_volume,
    }


def _empty_series_stats() -> dict:
    return {
        "total_seconds": 0,
        "sessions": 0,
        "pages_turned": 0,
        "books_total": 0,
        "books_finished": 0,
        "books_in_progress": 0,
        "books_with_sessions": 0,
        "completion_pct": 0.0,
        "avg_volume_seconds": 0,
        "estimated_remaining_seconds": None,
        "longest_volume": None,
        "first_read": None,
        "last_read": None,
        "per_volume": [],
    }


# ── Admin aggregate — all users, one series ───────────────────────────────────

def compute_series_aggregate_stats(
    db: Session,
    *,
    series_name: str,
) -> dict:
    """Return library-wide reading statistics for a series (all users combined).

    Returns a dict with keys:
      total_seconds, total_sessions, distinct_readers
    """
    # Collect all book ids in this series (any status — admin sees everything)
    book_ids = [
        row[0]
        for row in db.query(Book.id).filter(
            Book.series == series_name,
            Book.status == "active",
        ).all()
    ]

    if not book_ids:
        return {"total_seconds": 0, "total_sessions": 0, "distinct_readers": 0}

    agg = (
        db.query(ReadingSession)
        .filter(ReadingSession.book_id.in_(book_ids))
        .with_entities(
            func.coalesce(func.sum(ReadingSession.duration_seconds), 0).label("total_seconds"),
            func.count(ReadingSession.id).label("total_sessions"),
            func.count(func.distinct(ReadingSession.user_id)).label("distinct_readers"),
        )
        .first()
    )

    return {
        "total_seconds": int(agg.total_seconds) if agg and agg.total_seconds else 0,
        "total_sessions": int(agg.total_sessions) if agg and agg.total_sessions else 0,
        "distinct_readers": int(agg.distinct_readers) if agg and agg.distinct_readers else 0,
    }
