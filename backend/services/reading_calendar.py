"""Reading Calendar: per-day reading for a month grid, and one day in detail.

Reconciled exactly like the stats page (``reconciled_reading``): imported
KOReader page-stats win per sitting, every other session stays additive. Days
are reading days (local day with the 4h rollover, DST-correct when the client
sends its IANA zone), so the grid, the streaks and the stats heatmap agree.

Rows are pulled for a UTC window padded around the requested days and bucketed
per row in Python via ``DayCtx.py_day`` — a single SQL offset can't be right on
both sides of a DST switch, and a month of rows is small.
"""
from __future__ import annotations

import calendar
from bisect import bisect_left
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from backend.models.book import Book, BookChapter
from backend.models.ko_stats import PageStat
from backend.models.tome_sync import ReadingSession
from backend.models.user_book_status import UserBookStatus
from backend.services import reconciled_reading as rr
from backend.services.reading_day import DayCtx, effective_hours
from backend.services.kosync_stats import reader_days
from backend.services.streaks import _runs, streak_session_clause, streak_summary_from_dates

# Same gap rule as the stats session log: a new sitting after 30 idle minutes.
SITTING_GAP_SECONDS = rr.SESSION_GAP_SECONDS
# Books opened for less than this on a day (an accidental open, a peek) still
# count in the day's totals but aren't listed as something you read.
MIN_LISTED_SECONDS = 60
_EPOCH_ORDINAL = date(1970, 1, 1).toordinal()


def _epoch(dt: datetime) -> int:
    return int(dt.replace(tzinfo=timezone.utc).timestamp())


@dataclass
class _BookDay:
    secs: int = 0
    pages: int = 0
    # Positions read, as fractions of the book (page-stats) ...
    fractions: list[float] = field(default_factory=list)
    # ... and (start, end) progress spans from live sessions.
    spans: list[tuple[float, float]] = field(default_factory=list)
    # (start_epoch, end_epoch) of each raw read: page rows and sessions.
    reads: list[tuple[int, int]] = field(default_factory=list)
    # Some of ``pages`` were attributed from a reader's page counter.
    pages_attributed: bool = False


def _collect(db: Session, user_id: int, day: DayCtx, first: date, last: date,
             covered: list[int], detail: bool = True) -> dict[str, dict[int, _BookDay]]:
    """day_iso -> book_id -> _BookDay for reading days first..last inclusive.
    ``detail=False`` fills only seconds and pages from page-stats (the month grid)."""
    lo = datetime.combine(first - timedelta(days=2), datetime.min.time())
    hi = datetime.combine(last + timedelta(days=3), datetime.min.time())
    out: dict[str, dict[int, _BookDay]] = defaultdict(lambda: defaultdict(_BookDay))
    lo_iso, hi_iso = first.isoformat(), last.isoformat()

    if covered:
        # The reading day comes from SQL (the epoch_day offsets) so it is not
        # worked out per row in Python; rows outside first..last are dropped there.
        # "+user_id" steers SQLite to the start_time index: the window, not every row of the user.
        lo_ord, hi_ord = first.toordinal() - _EPOCH_ORDINAL, last.toordinal() - _EPOCH_ORDINAL
        params = {"uid": user_id, "lo": _epoch(lo), "hi": _epoch(hi), "dlo": lo_ord, "dhi": hi_ord}
        shifted = (f"(start_time + {_epoch_shift_sql(day)}) / 86400 AS d FROM ko_page_stats "
                   "WHERE start_time >= :lo AND start_time < :hi AND +user_id = :uid")
        names: dict[int, str] = {}

        def name(d_ord: int) -> str:
            got = names.get(d_ord)
            if got is None:
                got = names[d_ord] = date.fromordinal(_EPOCH_ORDINAL + d_ord).isoformat()
            return got

        # Ordered by id: the order the old per-row read saw, which breaks ties in the book lists.
        if not detail:
            for bid, d_ord, secs, n in db.execute(text(
                f"SELECT book_id, d, SUM(duration_seconds), COUNT(*) FROM (SELECT id, book_id, duration_seconds, {shifted}) "
                "WHERE d BETWEEN :dlo AND :dhi GROUP BY book_id, d ORDER BY MIN(id)"
            ), params).all():
                e = out[name(d_ord)][bid]
                e.secs += int(secs or 0)
                e.pages += int(n)
        else:
            for bid, page, total, start, dur, d_ord in db.execute(text(
                "SELECT book_id, page, total_pages, start_time, duration_seconds, d FROM (SELECT id, book_id, page, "
                f"total_pages, start_time, duration_seconds, {shifted}) WHERE d BETWEEN :dlo AND :dhi ORDER BY id"
            ), params).all():
                e = out[name(d_ord)][bid]
                e.secs += int(dur or 0)
                e.pages += 1  # one row per page dwell, like the stats page counts them
                if total:
                    e.fractions.append(min(max(page / total, 0.0), 1.0))
                e.reads.append((int(start), int(start) + int(dur or 0)))

    sessions = (
        rr._rs_filtered(db, user_id, covered, lo, hi)
        .filter(ReadingSession.book_id.isnot(None))
        .with_entities(ReadingSession.book_id, ReadingSession.started_at, ReadingSession.ended_at,
                       ReadingSession.duration_seconds, ReadingSession.pages_turned,
                       ReadingSession.progress_start, ReadingSession.progress_end,
                       ReadingSession.pages_attributed)
        .all()
    )
    for bid, started, ended, dur, pages, p0, p1, attributed in sessions:
        start = _epoch(started)
        d = day.py_day(start).isoformat()
        if not (lo_iso <= d <= hi_iso):
            continue
        e = out[d][bid]
        e.secs += int(dur or 0)
        e.pages += int(pages or 0)
        if attributed and pages:
            e.pages_attributed = True
        end = _epoch(ended) if ended else start + int(dur or 0)
        e.reads.append((start, end))
        if p1 is not None:
            # Legacy rows stored percent; everything since is a 0..1 fraction.
            norm = lambda v: min(max((v / 100 if v > 1 else v), 0.0), 1.0)  # noqa: E731
            e.spans.append((norm(p0 if p0 is not None else p1), norm(p1)))
    return out


def _finished_days(db: Session, user_id: int, day: DayCtx, first: date, last: date) -> dict[str, list[int]]:
    """day_iso -> book ids marked read that reading day (by finished_at)."""
    lo = datetime.combine(first - timedelta(days=2), datetime.min.time())
    hi = datetime.combine(last + timedelta(days=3), datetime.min.time())
    rows = (
        db.query(UserBookStatus.book_id, UserBookStatus.finished_at)
        .filter(UserBookStatus.user_id == user_id, UserBookStatus.status == "read",
                UserBookStatus.finished_at >= lo, UserBookStatus.finished_at < hi)
        .all()
    )
    out: dict[str, list[int]] = defaultdict(list)
    for bid, fin in rows:
        d = day.py_day(_epoch(fin))
        if first <= d <= last:
            out[d.isoformat()].append(bid)
    return out


def _book_meta(db: Session, ids) -> dict[int, dict]:
    if not ids:
        return {}
    rows = db.query(Book.id, Book.title, Book.author, Book.series, Book.series_index, Book.cover_path) \
        .filter(Book.id.in_(list(ids))).all()
    return {
        r.id: {"book_id": r.id, "title": r.title, "author": r.author, "series": r.series,
               "series_index": r.series_index, "has_cover": bool(r.cover_path)}
        for r in rows
    }


# Reading to here counts as finishing the book that day, even if it was never
# marked read (KOReader's own "finished" threshold is similar).
END_FRACTION = 0.99


def _end_days(db: Session, user_id: int, day: DayCtx, book_ids) -> dict[int, date]:
    """book_id -> the reading day its position FIRST reached the end. Later
    visits to the last pages (a re-read, a peek at the back matter) don't
    count again."""
    if not book_ids:
        return {}
    ids = list(book_ids)
    out: dict[int, date] = {}
    ps = (
        db.query(PageStat.book_id, func.min(PageStat.start_time))
        .filter(PageStat.user_id == user_id, PageStat.book_id.in_(ids), PageStat.total_pages > 0,
                PageStat.page * 1.0 / PageStat.total_pages >= END_FRACTION)
        .group_by(PageStat.book_id).all()
    )
    for bid, start in ps:
        out[bid] = day.py_day(int(start))
    rs = (
        db.query(ReadingSession.book_id, func.min(ReadingSession.started_at))
        .filter(ReadingSession.user_id == user_id, ReadingSession.book_id.in_(ids),
                ReadingSession.progress_end >= END_FRACTION,
                # legacy percent rows: 99..100; fractions never exceed 1
                ~((ReadingSession.progress_end > 1) & (ReadingSession.progress_end < END_FRACTION * 100)))
        .group_by(ReadingSession.book_id).all()
    )
    for bid, started in rs:
        d = day.py_day(_epoch(started))
        out[bid] = min(out.get(bid, d), d)
    return out


def _unique_best(day_secs: dict[str, int]) -> Optional[str]:
    """The month's biggest day, or None when the top is a tie: five equal
    25-minute days don't have a "biggest" one."""
    top = sorted(day_secs.items(), key=lambda kv: -kv[1])
    if not top or top[0][1] <= 0 or (len(top) > 1 and top[1][1] == top[0][1]):
        return None
    return top[0][0]


def _epoch_shift_sql(day: DayCtx) -> str:
    """Integer SQL seconds that ``DayCtx.epoch_day`` adds to ``start_time``: the
    same fixed offset, or the same per-row CASE over the zone's transitions."""
    if day.tz_name is None:
        return str(effective_hours(day.tz_offset_minutes, day.rollover_hours) * 3600)
    trans = day._trans()
    shift = day.rollover_hours * 60
    whens = " ".join(f"WHEN start_time >= {int(i)} THEN {(int(o) - shift) * 60}" for i, o in reversed(trans[1:]))
    first = (int(trans[0][1]) - shift) * 60
    return f"CASE {whens} ELSE {first} END" if whens else str(first)


@dataclass
class _PageDays:
    """All-time page-stat seconds and rows per reading day, and the first row."""
    by_day: dict[date, tuple[int, int]]
    first_start: Optional[int]


def _page_days(db: Session, user_id: int, day: DayCtx) -> _PageDays:
    """One grouped pass over the user's page-stats. Day ``n`` since the epoch is
    the date ``epoch_day`` would give (start_time is positive, so SQLite's
    integer division floors)."""
    sql = text(
        f"SELECT (start_time + {_epoch_shift_sql(day)}) / 86400 AS d, SUM(duration_seconds), COUNT(*), "
        "MIN(start_time) FROM ko_page_stats WHERE user_id = :uid GROUP BY d"
    )
    by_day: dict[date, tuple[int, int]] = {}
    first: Optional[int] = None
    for d, secs, n, mn in db.execute(sql, {"uid": user_id}).all():
        by_day[date.fromordinal(_EPOCH_ORDINAL + int(d))] = (int(secs or 0), int(n))
        first = int(mn) if first is None else min(first, int(mn))
    return _PageDays(by_day, first)



def _shade_scale(db: Session, user_id: int, day: DayCtx, covered: list[int], pages: _PageDays) -> int:
    """Seconds that count as a full-colour day: your 90th-percentile reading
    day, all time (at least 30 min). A fixed personal scale keeps a month of
    short days light instead of stretching its longest one to full colour.
    Per-day seconds as ``rr.daily_map`` over all time counts them."""
    rows = (
        rr._rs_filtered(db, user_id, covered, None, None)
        .with_entities(day.dt_day(ReadingSession.started_at).label("day"),
                       func.coalesce(func.sum(ReadingSession.duration_seconds), 0))
        .group_by("day").all()
    )
    per_day: dict[str, int] = {d: int(s or 0) for d, s in rows}
    if covered:
        for d, (s, _n) in pages.by_day.items():
            k = d.isoformat()
            per_day[k] = per_day.get(k, 0) + s
    secs = sorted(v for v in per_day.values() if v > 0)
    if not secs:
        return 1800
    return max(1800, secs[min(len(secs) - 1, int(len(secs) * 0.9))])


def _active_days(db: Session, user_id: int, day: DayCtx, covered: list[int], pages: _PageDays) -> set[date]:
    """The streak's day set, as ``rr.active_days`` builds it."""
    rs_days = {
        r[0] for r in
        rr._rs_filtered(db, user_id, [], None, None)
        .filter(streak_session_clause())
        .with_entities(day.dt_day(ReadingSession.started_at)).distinct().all()
        if r[0]
    }
    out = {date.fromisoformat(d) for d in rs_days}
    if covered:
        out |= set(pages.by_day)
    return out


def _first_reading_day(db: Session, user_id: int, day: DayCtx, pages: _PageDays) -> Optional[date]:
    ps = pages.first_start
    rs = db.query(func.min(ReadingSession.started_at)).filter(ReadingSession.user_id == user_id).scalar()
    cands = [day.py_day(int(ps))] if ps else []
    if rs:
        cands.append(day.py_day(_epoch(rs)))
    return min(cands) if cands else None


def _week_start_of(db: Session, user_id: int) -> str:
    """The reader's week start (Settings › Week starts on) for the weekly streak."""
    from backend.models.user import User
    ws = db.query(User.week_start).filter(User.id == user_id).scalar()
    return ws if ws in ("monday", "sunday") else "monday"


def month_view(db: Session, user_id: int, day: DayCtx, year: int, month: int) -> dict:
    first = date(year, month, 1)
    last = date(year, month, calendar.monthrange(year, month)[1])
    covered = rr.covered_book_ids(db, user_id)
    pages = _page_days(db, user_id, day)
    # One day either side so streak bands can run across the month edge.
    data = _collect(db, user_id, day, first - timedelta(days=1), last + timedelta(days=1), covered, detail=False)
    finished = _finished_days(db, user_id, day, first, last)
    meta = _book_meta(db, {b for books in data.values() for b in books} | {b for ids in finished.values() for b in ids})
    ends = _end_days(db, user_id, day, {b for books in data.values() for b in books})

    days = []
    for d_iso in sorted(data):
        books = data[d_iso]
        secs = sum(b.secs for b in books.values())
        if secs <= 0:
            continue
        ordered = sorted(books.items(), key=lambda kv: -kv[1].secs)
        done = list(dict.fromkeys(finished.get(d_iso, []) + [bid for bid, _b in ordered if ends.get(bid) == date.fromisoformat(d_iso)]))
        days.append({
            "date": d_iso,
            "seconds": secs,
            "pages": sum(b.pages for b in books.values()),
            "books": [{**meta[bid], "seconds": b.secs}
                      for bid, b in ordered if bid in meta and b.secs >= MIN_LISTED_SECONDS],
            "finished": [meta[b] for b in done if b in meta],
        })
    in_month = [d for d in days if first.isoformat() <= d["date"] <= last.isoformat()]
    first_day = _first_reading_day(db, user_id, day, pages)
    # Days a KOSync reader was read on before it synced to Tome: reading days
    # with no time, pages or books (kosync_stats.reader_days).
    all_reader = reader_days(db, user_id)
    have = {d["date"] for d in days}
    shown_reader = sorted(
        d.isoformat() for d in all_reader
        if first - timedelta(days=1) <= d <= last + timedelta(days=1) and d.isoformat() not in have)
    reader_in_month = [d for d in shown_reader if first.isoformat() <= d <= last.isoformat()]
    if all_reader and (first_day is None or min(all_reader) < first_day):
        first_day = min(all_reader)
    return {
        "month": f"{year:04d}-{month:02d}",
        "today": day.today().isoformat(),
        "first_day": first_day.isoformat() if first_day else None,
        "days": days,
        "reader_days": shown_reader,
        "totals": {
            "seconds": sum(d["seconds"] for d in in_month),
            "pages": sum(d["pages"] for d in in_month),
            "reading_days": len(in_month) + len(reader_in_month),
            "books_finished": sum(len(d["finished"]) for d in in_month),
            "best_day": _unique_best({d["date"]: d["seconds"] for d in in_month}),
        },
        "scale_seconds": _shade_scale(db, user_id, day, covered, pages),
        # Same as streaks.reconciled_streak_summary, from the page-day pass above.
        "streaks": streak_summary_from_dates(_active_days(db, user_id, day, covered, pages) | all_reader,
                                             day.today(), _week_start_of(db, user_id)).as_dict(),
    }


def _usual(db: Session, user_id: int, covered: list[int], book_ids) -> dict[int, tuple[int, int]]:
    """book_id -> all-time (seconds, pages), as ``rr.book_seconds`` counts them,
    for just these books."""
    if not book_ids:
        return {}
    ids = list(book_ids)
    out: dict[int, list[int]] = {
        bid: [int(s or 0), int(p or 0)] for bid, s, p in
        rr._rs_filtered(db, user_id, covered, None, None)
        .filter(ReadingSession.book_id.in_(ids))
        .with_entities(ReadingSession.book_id,
                       func.coalesce(func.sum(ReadingSession.duration_seconds), 0),
                       func.coalesce(func.sum(ReadingSession.pages_turned), 0))
        .group_by(ReadingSession.book_id).all()
    }
    if covered:
        for bid, s, n in (
            db.query(PageStat.book_id, func.sum(PageStat.duration_seconds), func.count(PageStat.id))
            .filter(PageStat.user_id == user_id, PageStat.book_id.in_(ids))
            .group_by(PageStat.book_id).all()
        ):
            e = out.setdefault(bid, [0, 0])
            e[0] += int(s or 0); e[1] += int(n or 0)
    return {b: (v[0], v[1]) for b, v in out.items()}


def _month_seconds(db: Session, user_id: int, day: DayCtx, covered: list[int], pages: _PageDays,
                   first: date, last: date) -> dict[str, int]:
    """day_iso -> seconds for first..last, the per-day sums of ``_collect``."""
    out: dict[str, int] = defaultdict(int)
    if covered:
        for d, (s, _n) in pages.by_day.items():
            if first <= d <= last:
                out[d.isoformat()] += s
    lo = datetime.combine(first - timedelta(days=2), datetime.min.time())
    hi = datetime.combine(last + timedelta(days=3), datetime.min.time())
    for started, dur in (
        rr._rs_filtered(db, user_id, covered, lo, hi)
        .filter(ReadingSession.book_id.isnot(None))
        .with_entities(ReadingSession.started_at, ReadingSession.duration_seconds).all()
    ):
        d = day.py_day(_epoch(started))
        if first <= d <= last:
            out[d.isoformat()] += int(dur or 0)
    return out


def _sittings(reads: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[list[int]] = []
    for s, e in sorted(reads):
        if out and s - out[-1][1] <= SITTING_GAP_SECONDS:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return [(s, e) for s, e in out]


def _chapters(chapters: list[BookChapter], fractions: list[float], spans: list[tuple[float, float]]) -> list[str]:
    hit = []
    fs = sorted(fractions)
    at_end = bool(fs) and fs[-1] >= 1.0
    for c in chapters:
        i = bisect_left(fs, c.start_fraction)
        if (i < len(fs) and fs[i] < c.end_fraction) or (at_end and c.end_fraction >= 1.0) \
                or any(a < c.end_fraction and b > c.start_fraction for a, b in spans):
            hit.append(c.title)
    return hit


def day_view(db: Session, user_id: int, day: DayCtx, d: date) -> dict:
    covered = rr.covered_book_ids(db, user_id)
    books = _collect(db, user_id, day, d, d, covered).get(d.isoformat(), {})
    total_secs = sum(b.secs for b in books.values())
    total_pages = sum(b.pages for b in books.values())
    books = {bid: b for bid, b in books.items() if b.secs >= MIN_LISTED_SECONDS}
    finished_ids = set(_finished_days(db, user_id, day, d, d).get(d.isoformat(), []))
    meta = _book_meta(db, set(books) | finished_ids)

    usual = _usual(db, user_id, covered, books)  # all-time (secs, pages) of the listed books

    chapter_map: dict[int, list[BookChapter]] = defaultdict(list)
    if books:
        for c in db.query(BookChapter).filter(BookChapter.book_id.in_(list(books))).order_by(BookChapter.book_id, BookChapter.idx):
            chapter_map[c.book_id].append(c)

    # A book "started" today when today is its first reading day ever.
    ends = _end_days(db, user_id, day, books)
    started = set()
    if books:
        ids = list(books)
        ps_first = dict(db.query(PageStat.book_id, func.min(PageStat.start_time))
                        .filter(PageStat.user_id == user_id, PageStat.book_id.in_(ids))
                        .group_by(PageStat.book_id).all())
        rs_first = dict(db.query(ReadingSession.book_id, func.min(ReadingSession.started_at))
                        .filter(ReadingSession.user_id == user_id, ReadingSession.book_id.in_(ids))
                        .group_by(ReadingSession.book_id).all())
        for bid in books:
            ps, rs = ps_first.get(bid), rs_first.get(bid)
            firsts = ([day.py_day(int(ps))] if ps else []) + ([day.py_day(_epoch(rs))] if rs else [])
            if firsts and min(firsts) == d:
                started.add(bid)

    out_books, all_sittings = [], []
    for bid, b in sorted(books.items(), key=lambda kv: -kv[1].secs):
        if bid not in meta:
            continue
        positions = b.fractions + [x for span in b.spans for x in span]
        u_secs, u_pages = usual.get(bid, (0, 0))
        sittings = _sittings(b.reads)
        all_sittings += [{"book_id": bid, "start": s, "end": e} for s, e in sittings]
        out_books.append({
            **meta[bid],
            "seconds": b.secs,
            "pages": b.pages,
            "pages_attributed": b.pages_attributed,
            "progress_from": round(min(positions), 4) if positions else None,
            "progress_to": round(max(positions), 4) if positions else None,
            "chapters": _chapters(chapter_map.get(bid, []), b.fractions, b.spans),
            "pages_per_hour": round(b.pages / (b.secs / 3600), 1) if b.secs and b.pages else None,
            "usual_pages_per_hour": round(u_pages / (u_secs / 3600), 1) if u_secs and u_pages else None,
            "finished": bid in finished_ids or ends.get(bid) == d,
            "started": bid in started,
            "sittings": len(sittings),
        })

    # Streak position: which run of consecutive reading days this day sits in.
    pages = _page_days(db, user_id, day)
    from_reader = reader_days(db, user_id)
    active = _active_days(db, user_id, day, covered, pages) | from_reader
    streak = None
    if d in active:
        runs = _runs(sorted(active), 1)
        run = next(r for r in runs if r[0] <= d <= r[1])
        longest = max(((r[1] - r[0]).days + 1 for r in runs), default=0)
        length = (run[1] - run[0]).days + 1
        streak = {"day": (d - run[0]).days + 1, "length": length, "start": run[0].isoformat(),
                  "end": run[1].isoformat(), "is_best": length == longest}

    secs = total_secs
    m_first = d.replace(day=1)
    m_last = d.replace(day=calendar.monthrange(d.year, d.month)[1])
    month_secs = _month_seconds(db, user_id, day, covered, pages, m_first, m_last)
    return {
        "date": d.isoformat(),
        "seconds": secs,
        "pages": total_pages,
        # Read on a reader before it synced to Tome: a reading day without detail.
        "reader_day": total_secs <= 0 and d in from_reader,
        "books": out_books,
        "finished_elsewhere": [meta[b] for b in finished_ids if b not in books and b in meta],
        "sittings": sorted(all_sittings, key=lambda s: s["start"]),
        "streak": streak,
        "is_month_best": _unique_best(month_secs) == d.isoformat(),
    }
