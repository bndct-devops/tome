"""The stats request memo sums page-stats in Python from rows read once
(reconciled_reading._page_rows). Every helper must answer exactly what its SQL
path answers: same windows, same DST-correct days and hours, same clusters."""
from datetime import datetime, timedelta, timezone

import pytest

from backend.models.ko_stats import PageStat
from backend.models.tome_sync import ReadingSession
from backend.services import reconciled_reading as rr
from backend.services.reading_day import DayCtx


def _ts(y, mo, d, h, mi=0):
    return int(datetime(y, mo, d, h, mi, tzinfo=timezone.utc).timestamp())


@pytest.fixture
def rows(db, admin_user, make_book):
    user, _ = admin_user
    a, b = make_book(title="Alpha"), make_book(title="Beta")
    # Sittings around both 2026 Vienna DST changes, across local midnight and the
    # 4 h rollover, two books interleaved, a re-read page, a sub-10 s flip and a
    # gap just over and just under 30 minutes.
    starts = [
        (a, _ts(2026, 3, 29, 0, 55)), (a, _ts(2026, 3, 29, 1, 5)), (a, _ts(2026, 3, 29, 1, 35)),
        (b, _ts(2026, 3, 29, 1, 40)), (a, _ts(2026, 3, 29, 2, 6)), (a, _ts(2026, 3, 29, 2, 36)),
        (a, _ts(2026, 10, 25, 0, 30)), (a, _ts(2026, 10, 25, 1, 30)), (b, _ts(2026, 10, 25, 2, 10)),
        (b, _ts(2026, 10, 25, 22, 59)), (b, _ts(2026, 10, 25, 23, 29)), (a, _ts(2026, 11, 2, 3, 0)),
        (a, _ts(2026, 11, 3, 3, 0)), (b, _ts(2026, 11, 3, 3, 0)),
    ]
    for i, (book, st) in enumerate(starts):
        db.add(PageStat(user_id=user.id, book_id=book.id, page=1 + i % 4, total_pages=100,
                        start_time=st, duration_seconds=5 if i == 3 else 60 + i,
                        device="Kindle" if i % 2 else "Kobo"))
    db.add(ReadingSession(user_id=user.id, book_id=a.id, started_at=datetime(2026, 11, 4, 20),
                          duration_seconds=300, pages_turned=3, device="web"))
    db.flush()
    return user


WINDOWS = [
    (None, None),
    (datetime(2026, 3, 29, 1, 20), None),
    (datetime(2026, 3, 29, 1, 20), datetime(2026, 10, 25, 1, 30)),
    (datetime(2026, 10, 25, 1, 0), datetime(2026, 11, 3, 3, 0)),
]
CTXS = [DayCtx(-60, "Europe/Vienna"), DayCtx(-60, None), DayCtx(300, "America/New_York"), DayCtx(0, "UTC")]


def _both(db, fn):
    db.info.pop("rr_clusters", None)
    sql = fn()
    db.info["rr_clusters"] = {}
    try:
        py = fn()
    finally:
        db.info.pop("rr_clusters", None)
    return sql, py


@pytest.mark.parametrize("ctx", CTXS)
@pytest.mark.parametrize("cutoff,end", WINDOWS)
def test_python_path_matches_sql(db, rows, ctx, cutoff, end):
    uid = rows.id
    cov = rr.covered_book_ids(db, uid)
    checks = {
        "totals": lambda: rr.totals(db, uid, ctx, cov, cutoff, end),
        "daily": lambda: rr.daily_map(db, uid, ctx, cov, cutoff, end),
        "books": lambda: rr.book_seconds(db, uid, ctx, cov, cutoff, end),
        "hour_dow": lambda: rr.hour_dow(db, uid, ctx, cov, cutoff, end),
        "months": lambda: rr.monthly_map(db, uid, ctx, cov, cutoff),
        "book_months": lambda: rr.book_month_seconds(db, uid, ctx, cov, cutoff),
        "book_days": lambda: rr.book_day_seconds(db, uid, ctx, cov),
        "active": lambda: rr.active_days(db, uid, ctx, cov),
        "clusters": lambda: [vars(c) for c in rr._cluster_rows(db, uid, ctx, cutoff, end)],
        "clusters_all": lambda: [vars(c) for c in rr._cluster_rows(db, uid, ctx, cutoff, end, min_secs=0)],
    }
    for name, fn in checks.items():
        sql, py = _both(db, fn)
        assert sql == py, name


def test_rows_reread_for_an_earlier_window(db, rows):
    db.info["rr_clusters"] = {}
    try:
        late = rr._page_rows(db, rows.id, datetime(2026, 10, 1))
        assert late.since is not None and len(late.start) == 8
        assert rr._page_rows(db, rows.id, datetime(2026, 11, 1)) is late
        assert len(rr._page_rows(db, rows.id, None).start) == 14
    finally:
        db.info.pop("rr_clusters", None)


def test_stats_answer_same_with_and_without_rows(client, db, rows, monkeypatch):
    q = "/api/stats?days=0&tz_offset=-60&tz=Europe/Vienna"
    fast = client.get(q).json()
    monkeypatch.setattr(rr, "_page_rows", lambda *a, **k: None)
    assert client.get(q).json() == fast


# ── Seconds without session counts ─────────────────────────────────────────────
# A reading pace, the goals and the finish-by estimate need seconds and pages
# only; ``sessions=False`` skips the gap-clustering that counts page-stat
# sessions. Seconds and pages must be exactly the counted path's.

def _minute_ctx():
    from backend.services.goals import _MinuteDayCtx
    return _MinuteDayCtx(-330, None)


@pytest.mark.parametrize("memo", [False, True])
@pytest.mark.parametrize("ctx", CTXS + [_minute_ctx()])
@pytest.mark.parametrize("cutoff,end", WINDOWS)
def test_seconds_only_matches_counted(db, rows, ctx, cutoff, end, memo):
    uid = rows.id
    cov = rr.covered_book_ids(db, uid)
    a_id = min(rr.book_seconds(db, uid, ctx, cov, None, None))
    if memo:
        db.info["rr_clusters"] = {}
    try:
        full, fast = rr.totals(db, uid, ctx, cov, cutoff, end), rr.totals(db, uid, ctx, cov, cutoff, end, sessions=False)
        assert (fast[0], fast[2]) == (full[0], full[2]) and fast[1] is None

        full, fast = rr.book_seconds(db, uid, ctx, cov, cutoff, end), rr.book_seconds(db, uid, ctx, cov, cutoff, end, sessions=False)
        assert {b: (v[0], v[2]) for b, v in fast.items()} == {b: (v[0], v[2]) for b, v in full.items()}
        assert all(v[1] is None for v in fast.values())

        for ids in (None, [a_id], []):
            full = rr.daily_map(db, uid, ctx, cov, cutoff, end, book_ids=ids)
            fast = rr.daily_map(db, uid, ctx, cov, cutoff, end, book_ids=ids, sessions=False)
            assert {d: (v[0], v[2]) for d, v in fast.items()} == \
                {d: (v[0], v[2]) for d, v in full.items() if d in fast}
            # A day only a session count put in the map has nothing else.
            assert all((v[0], v[2]) == (0, 0) for d, v in full.items() if d not in fast)
            assert all(v[1] is None for v in fast.values())
    finally:
        db.info.pop("rr_clusters", None)


def test_seconds_only_never_clusters(db, rows, monkeypatch):
    uid = rows.id
    cov = rr.covered_book_ids(db, uid)
    assert cov

    def boom(*a, **k):
        raise AssertionError("clustered")
    monkeypatch.setattr(rr, "_cluster_rows", boom)
    ctx = DayCtx(-60, "Europe/Vienna")
    rr.totals(db, uid, ctx, cov, None, None, sessions=False)
    rr.book_seconds(db, uid, ctx, cov, None, None, sessions=False)
    rr.daily_map(db, uid, ctx, cov, None, None, sessions=False)


@pytest.fixture
def paced(db, rows, make_book):
    """``rows`` plus a three-volume series (two read, word-counted), a live
    device session the page-stats supersede and one they do not, and reading
    inside the last 30 days, so a pace, goals and a finish estimate all have
    something to say."""
    from backend.models.user_book_status import UserBookStatus
    from backend.models.book import Book

    uid = rows.id
    a, b = (db.query(Book).filter(Book.title == t).one() for t in ("Alpha", "Beta"))
    for i, bk in enumerate((a, b), 1):
        bk.series, bk.series_index, bk.word_count = "Saga", float(i), 4000 * i
        db.add(UserBookStatus(user_id=uid, book_id=bk.id, status="read",
                              finished_at=datetime(2026, 11, 3, 12)))
    c = make_book(title="Gamma", series="Saga", series_index=3.0)
    c.word_count = 9000
    make_book(title="Delta", series="Saga", series_index=4.0)  # no word count
    now = datetime.utcnow()
    recent = [now - timedelta(days=d, hours=2) for d in (1, 3, 8, 20, 45, 70)]
    for i, st in enumerate(recent):
        db.add(PageStat(user_id=uid, book_id=a.id if i % 2 else b.id, page=10 + i, total_pages=100,
                        start_time=int(st.replace(tzinfo=timezone.utc).timestamp()),
                        duration_seconds=1500 + 100 * i, device="Kindle"))
    # Superseded: a Kindle session over the page rows of the first recent sitting.
    db.add(ReadingSession(user_id=uid, book_id=b.id, started_at=recent[0] - timedelta(seconds=30),
                          ended_at=recent[0] + timedelta(minutes=20), duration_seconds=1200,
                          pages_turned=9, device="Kindle"))
    # Counted: a phone session on its own, and one on the unread volume.
    db.add(ReadingSession(user_id=uid, book_id=a.id, started_at=now - timedelta(days=5),
                          ended_at=now - timedelta(days=5) + timedelta(minutes=40), duration_seconds=2400,
                          pages_turned=20, device="Phone"))
    db.add(ReadingSession(user_id=uid, book_id=c.id, started_at=now - timedelta(days=2),
                          duration_seconds=900, pages_turned=6, device="web"))
    db.flush()
    return rows


def _counted_everywhere(monkeypatch):
    """Make every rr helper count sessions again, whatever its caller asks:
    the computation as it was before ``sessions=False``."""
    for name in ("totals", "book_seconds", "daily_map"):
        orig = getattr(rr, name)

        def counted(*a, _orig=orig, **k):
            k.pop("sessions", None)
            return _orig(*a, **k)
        monkeypatch.setattr(rr, name, counted)


def test_book_page_answers_same_as_counted(client, db, paced, monkeypatch):
    from backend.models.book import Book

    for body in ({"kind": "minutes_per_week", "target": 60}, {"kind": "pages_per_day", "target": 5},
                 {"kind": "days_per_week", "target": 3}, {"kind": "minutes_per_day", "target": 10},
                 {"kind": "finish_by", "series": "Saga", "due_date": "2027-01-31"}):
        assert client.post("/api/goals?tz_offset=-60&tz=Europe/Vienna", json=body).status_code in (200, 201)
    ids = [b.id for b in db.query(Book).order_by(Book.id)]
    goal_ids = [g["id"] for g in client.get("/api/goals").json()["goals"]]
    urls = ["/api/books/series-detail?name=Saga"]
    for tz in ("tz_offset=-60&tz=Europe/Vienna", "tz_offset=0", "tz_offset=-330", "tz_offset=600&tz=Pacific/Honolulu"):
        urls += [f"/api/goals?{tz}", f"/api/goals/suggestions?{tz}",
                 f"/api/goals/finish-preview?due=2027-03-01&series=Saga&{tz}"]
        urls += [f"/api/goals/{g}/history?{tz}" for g in goal_ids]
        urls += [f"/api/books/{i}/estimate?{tz}" for i in ids]
    # The first read of a reached goal marks it notified (and bumps its
    # updated_at); compare the answers after that has settled.
    [client.get(u) for u in urls]
    fast = [client.get(u).json() for u in urls]
    assert fast[0]["backlog"]["pace"]["wpm"] and fast[0]["backlog"]["pace"]["minutes_per_day"]
    assert any(g["finish"]["basis"] for g in fast[1]["goals"] if g["kind"] == "finish_by")
    _counted_everywhere(monkeypatch)
    assert [client.get(u).json() for u in urls] == fast
