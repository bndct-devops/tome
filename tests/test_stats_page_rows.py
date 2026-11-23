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
