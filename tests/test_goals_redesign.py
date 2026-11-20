"""Reading goals redesign (2026-11): calendar periods on Stats' counting, pace and
projection, week start, the 04:00 reading day, IANA zones, target history,
history states, new kinds, suggestions, undo, notifications, the old response
fields and the upgrade of an old-shape table.

"Now" is pinned through ``backend.services.goals.utcnow``; every timestamp in
these tests is built from a local wall time in the zone under test.
"""
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from backend.models.ko_stats import PageStat
from backend.models.library import BookType
from backend.models.notification import Notification
from backend.models.reading_goal import ReadingGoal, ReadingGoalTarget
from backend.models.tome_sync import ReadingSession
from backend.models.user_book_status import UserBookStatus
from backend.services import goals as gs

VIENNA = "Europe/Vienna"
KOLKATA = "Asia/Kolkata"  # +05:30, no DST


# ── helpers ───────────────────────────────────────────────────────────────────

def utc(local: str, tz: str = "UTC") -> datetime:
    """Naive UTC for a local wall time 'YYYY-MM-DD HH:MM' in ``tz``."""
    dt = datetime.strptime(local, "%Y-%m-%d %H:%M").replace(tzinfo=ZoneInfo(tz))
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


@pytest.fixture()
def now(monkeypatch):
    """Set "now" with now.set('2026-11-20 09:00', tz)."""
    class _Now:
        value = utc("2026-11-20 12:00")

        def set(self, local: str, tz: str = "UTC") -> datetime:
            self.value = utc(local, tz)
            return self.value
    n = _Now()
    monkeypatch.setattr(gs, "utcnow", lambda: n.value)
    return n


def session(db, user_id, book_id, started: datetime, seconds=600, pages=0, device="kindle"):
    s = ReadingSession(user_id=user_id, book_id=book_id, started_at=started,
                       ended_at=started + timedelta(seconds=seconds), duration_seconds=seconds,
                       pages_turned=pages, device=device)
    db.add(s)
    db.flush()
    return s


def finish(db, user_id, book_id, at: datetime):
    row = UserBookStatus(user_id=user_id, book_id=book_id, status="read", finished_at=at)
    db.add(row)
    db.flush()
    return row


def create(client, kind, target=None, tz=None, expect=201, **extra):
    body = {"kind": kind, **extra}
    if target is not None:
        body["target"] = target
    r = client.post("/api/goals" + (f"?tz={tz}" if tz else ""), json=body)
    assert r.status_code == expect, r.text
    return r.json()


def goals(client, tz=None):
    r = client.get("/api/goals" + (f"?tz={tz}" if tz else ""))
    assert r.status_code == 200, r.text
    return {g["kind"]: g for g in r.json()["goals"]}


def history(client, gid, tz=None, periods=None):
    q = []
    if tz:
        q.append(f"tz={tz}")
    if periods:
        q.append(f"periods={periods}")
    r = client.get(f"/api/goals/{gid}/history" + ("?" + "&".join(q) if q else ""))
    assert r.status_code == 200, r.text
    return r.json()


# ── periods ───────────────────────────────────────────────────────────────────

def test_period_bounds_monday_and_sunday_weeks():
    fri = date(2026, 11, 20)
    assert gs.period_bounds("week", fri, "monday") == (date(2026, 11, 16), date(2026, 11, 22))
    assert gs.period_bounds("week", fri, "sunday") == (date(2026, 11, 15), date(2026, 11, 21))
    sun = date(2026, 11, 22)
    assert gs.period_bounds("week", sun, "monday") == (date(2026, 11, 16), date(2026, 11, 22))
    assert gs.period_bounds("week", sun, "sunday") == (date(2026, 11, 22), date(2026, 11, 28))
    assert gs.period_bounds("month", date(2026, 2, 14)) == (date(2026, 2, 1), date(2026, 2, 28))
    assert gs.period_bounds("month", date(2026, 12, 31)) == (date(2026, 12, 1), date(2026, 12, 31))
    assert gs.period_bounds("year", fri) == (date(2026, 1, 1), date(2026, 12, 31))


# ── every kind: the current period ────────────────────────────────────────────

def test_books_year_and_month_pace_projection_days_left(client, make_book, admin_user, db, now):
    user, _ = admin_user
    now.set("2026-11-20 09:00", VIENNA)
    days = ["2026-01-10", "2026-05-23", "2026-11-03", "2026-11-14"]
    for i, d in enumerate(days):
        b = make_book(title=f"F{i}")
        finish(db, user.id, b.id, utc(f"{d} 20:00", VIENNA))
    b = make_book(title="last year")
    finish(db, user.id, b.id, utc("2025-12-31 22:00", VIENNA))
    y = create(client, "books_per_year", 60, tz=VIENNA)
    assert y["current"] == 4 and y["year"] == 2026 and y["name"] == "Books in 2026"
    assert y["days_passed"] == 323 and y["days_total"] == 365 and y["days_left"] == 42
    assert y["expected"] == round(60 * 323 / 365, 1) == 53.1
    assert y["diff"] == -49 and y["status"] == "behind"
    assert y["projection"] == 4 * 365 // 323
    m = create(client, "books_per_month", 6, tz=VIENNA)
    assert m["current"] == 2 and m["name"] == "Books in November"
    assert m["expected"] == 3.8 and m["diff"] == -2 and m["to_go"] == 4 and m["days_left"] == 11
    assert m["projection"] == 3  # floor(2 * 30 / 19)


def test_finish_after_midnight_counts_on_the_reading_day(client, make_book, admin_user, db, now):
    """A book finished at 02:00 on Dec 1 belongs to November's last reading day."""
    user, _ = admin_user
    now.set("2026-12-01 12:00", VIENNA)
    b = make_book(title="late")
    finish(db, user.id, b.id, utc("2026-12-01 02:00", VIENNA))
    m = create(client, "books_per_month", 3, tz=VIENNA)
    assert m["current"] == 0
    h = history(client, m["id"], tz=VIENNA)
    assert h["periods"][-1]["start"] == "2026-11-01" and h["periods"][-1]["value"] == 1


def test_minutes_and_pages_week_follow_stats_counting(client, make_book, admin_user, db, now):
    """Page statistics win over the live session they describe (Stats' rule):
    the session (900 s, 30 pages) is superseded by 3 page rows (2 × 300 s + 600 s)."""
    user, _ = admin_user
    book = make_book(title="KO")
    now.set("2026-11-20 12:00")
    start = utc("2026-11-17 18:00")
    session(db, user.id, book.id, start, seconds=900, pages=30)
    t0 = int(start.replace(tzinfo=timezone.utc).timestamp())
    for i, dur in enumerate((300, 300, 600)):
        db.add(PageStat(user_id=user.id, book_id=book.id, page=i + 1, total_pages=100,
                        start_time=t0 + 10 + i * 300, duration_seconds=dur, device="kindle"))
    db.flush()
    w = create(client, "minutes_per_week", 60)
    assert w["current"] == 20.0 and w["period_start"] == "2026-11-16"
    p = create(client, "pages_per_week", 10)
    assert p["current"] == 3


def test_day_goal_week_strip_and_days_hit(client, make_book, admin_user, db, now):
    user, _ = admin_user
    book = make_book(title="D")
    now.set("2026-11-20 09:00", VIENNA)
    for d, secs in (("2026-11-16", 4121), ("2026-11-17", 5080), ("2026-11-18", 1799),
                    ("2026-11-19", 18), ("2026-11-20", 600), ("2026-11-15", 4000)):
        session(db, user.id, book.id, utc(f"{d} 07:00", VIENNA), seconds=secs)
    g = create(client, "minutes_per_day", 30, tz=VIENNA)
    assert g["current"] == 10.0 and g["status"] == "open" and g["expected"] is None
    states = [(m["date"], m["state"]) for m in g["week"]]
    assert states == [("2026-11-16", "reached"), ("2026-11-17", "reached"), ("2026-11-18", "missed"),
                      ("2026-11-19", "missed"), ("2026-11-20", "today"), ("2026-11-21", "future"),
                      ("2026-11-22", "future")]
    assert g["week"][2]["value"] == 30.0 and g["week"][2]["reached"] is False  # 1799 s < 30 min
    assert g["week"][4]["value"] == 10.0
    assert g["days_hit_this_week"] == 2


def test_reading_days_follow_the_streak_rule(client, make_book, admin_user, db, now):
    """A day counts as the streak counts it: a few seconds in the web reader do
    not, any device session does."""
    user, _ = admin_user
    book = make_book(title="R")
    now.set("2026-11-20 12:00")
    session(db, user.id, book.id, utc("2026-11-16 10:00"), seconds=1200)
    session(db, user.id, book.id, utc("2026-11-17 10:00"), seconds=18, device="web")
    session(db, user.id, book.id, utc("2026-11-18 10:00"), seconds=40, device="kindle")
    session(db, user.id, book.id, utc("2026-11-19 10:00"), seconds=120, device="web")
    g = create(client, "days_per_week", 5)
    assert g["current"] == 3 and g["metric"] == "days" and g["period"] == "week"
    assert [m["state"] for m in g["week"]] == ["reached", "missed", "reached", "reached", "today", "future", "future"]
    assert g["reachable"] is True  # 2 to go, Fri..Sun left
    g2 = client.put(f"/api/goals/{g['id']}", json={"target": 7}).json()
    assert g2["reachable"] is False  # 4 to go, 3 days left


def test_days_per_week_validation(client):
    create(client, "days_per_week", 8, expect=422)
    create(client, "days_per_week", 0, expect=422)
    bt_resp = create(client, "days_per_week", 3)
    assert bt_resp["target"] == 3
    create(client, "days_per_week", 4, expect=409)


def test_book_type_not_allowed_for_new_kinds(client, db):
    bt = BookType(slug="manga-x", label="Manga")
    db.add(bt)
    db.flush()
    create(client, "days_per_week", 3, book_type_id=bt.id, expect=422)


# ── pace and projection thresholds ────────────────────────────────────────────

@pytest.mark.parametrize("local_day, passed, shown", [
    ("2026-11-08", 7, False),   # 7 of 30 days < a quarter
    ("2026-11-09", 8, True),    # 8 of 30 ≥ 7.5
])
def test_month_projection_after_a_quarter(client, make_book, admin_user, db, now, local_day, passed, shown):
    user, _ = admin_user
    now.set(f"{local_day} 12:00")
    b = make_book(title="p")
    finish(db, user.id, b.id, utc("2026-11-02 12:00"))
    m = create(client, "books_per_month", 6)
    assert m["days_passed"] == passed
    assert (m["projection"] is not None) is shown
    if shown:
        assert m["projection"] == 30 // passed


@pytest.mark.parametrize("local_day, shown", [("2026-11-17", False), ("2026-11-18", True)])
def test_week_projection_after_two_days(client, make_book, admin_user, db, now, local_day, shown):
    user, _ = admin_user
    book = make_book(title="w")
    now.set(f"{local_day} 12:00")
    session(db, user.id, book.id, utc("2026-11-16 12:00"), pages=100)
    w = create(client, "pages_per_week", 700)
    assert (w["projection"] is not None) is shown
    if shown:
        assert w["projection"] == 100 * 7 // 2


def test_year_projection_threshold():
    """92 of 365 full days is the first day "on course for" shows for a year."""
    assert 91 < 365 * gs.PROJECTION_MIN_FRACTION <= 92


def test_status_reached_ahead_on_pace(client, make_book, admin_user, db, now):
    user, _ = admin_user
    book = make_book(title="s")
    now.set("2026-11-19 12:00")  # Thursday: 3 full days passed
    session(db, user.id, book.id, utc("2026-11-16 12:00"), pages=300)
    w = create(client, "pages_per_week", 700)
    assert w["expected"] == 300.0 and w["status"] == "on_pace" and w["diff"] == 0
    session(db, user.id, book.id, utc("2026-11-17 12:00"), pages=400)
    w = goals(client)["pages_per_week"]
    assert w["status"] == "reached" and w["reached"] is True and w["to_go"] == 0


# ── week start ────────────────────────────────────────────────────────────────

def test_week_start_sunday_setting(client, make_book, admin_user, db, now):
    user, _ = admin_user
    book = make_book(title="ws")
    now.set("2026-11-20 12:00")  # Friday
    session(db, user.id, book.id, utc("2026-11-15 12:00"), pages=50)  # Sunday
    session(db, user.id, book.id, utc("2026-11-16 12:00"), pages=20)  # Monday
    w = create(client, "pages_per_week", 700)
    assert w["period_start"] == "2026-11-16" and w["current"] == 20
    me = client.get("/api/auth/me").json()
    assert me["week_start"] == "monday"
    assert client.put("/api/auth/me", json={"week_start": "friday"}).status_code == 422
    r = client.put("/api/auth/me", json={"week_start": "sunday"})
    assert r.status_code == 200 and r.json()["week_start"] == "sunday"
    body = client.get("/api/goals").json()
    assert body["week_start"] == "sunday"
    w = body["goals"][0]
    assert w["period_start"] == "2026-11-15" and w["period_end"] == "2026-11-21"
    assert w["current"] == 70 and w["days_left"] == 2 and w["days_passed"] == 5
    d = create(client, "minutes_per_day", 10)
    assert d["week"][0]["date"] == "2026-11-15" and d["week"][-1]["date"] == "2026-11-21"


# ── reading day, zones, DST ───────────────────────────────────────────────────

def test_four_am_day_boundary(client, make_book, admin_user, db, now):
    user, _ = admin_user
    book = make_book(title="night")
    now.set("2026-11-20 03:30", VIENNA)  # still Thursday's reading day
    session(db, user.id, book.id, utc("2026-11-20 01:00", VIENNA), seconds=1800)
    session(db, user.id, book.id, utc("2026-11-19 03:59", VIENNA), seconds=600)  # Wednesday's
    g = create(client, "minutes_per_day", 30, tz=VIENNA)
    assert g["period_start"] == "2026-11-19"
    assert g["current"] == 30.0 and g["reached"] is True
    now.set("2026-11-20 04:01", VIENNA)
    g = goals(client, tz=VIENNA)["minutes_per_day"]
    assert g["period_start"] == "2026-11-20" and g["current"] == 0.0
    assert g["week"][3]["value"] == 30.0 and g["week"][2]["value"] == 10.0


def test_half_hour_zone(client, make_book, admin_user, db, now):
    """+05:30: 03:50 local is the previous reading day, 04:10 the new one. The
    first goals build used whole hours (+6), which moved both."""
    user, _ = admin_user
    book = make_book(title="kol")
    now.set("2026-11-20 20:00", KOLKATA)
    session(db, user.id, book.id, utc("2026-11-20 03:50", KOLKATA), seconds=600)
    session(db, user.id, book.id, utc("2026-11-20 04:10", KOLKATA), seconds=1200)
    g = create(client, "minutes_per_day", 15, tz=KOLKATA)
    assert g["current"] == 20.0
    assert g["week"][3]["value"] == 10.0  # Thursday
    # the old client parameter: tz_offset alone (-330 for +05:30) gives the same
    r = client.get("/api/goals?tz_offset=-330").json()["goals"][0]
    assert r["current"] == 20.0


def test_dst_change_week(client, make_book, admin_user, db, now):
    """Vienna leaves summer time on Sun 2026-10-25. A session at 03:30 CET on
    Monday Oct 26 belongs to Sunday's reading day (last week). A fixed offset
    taken from summer time would have put it on Monday."""
    user, _ = admin_user
    book = make_book(title="dst")
    now.set("2026-10-28 12:00", VIENNA)
    session(db, user.id, book.id, utc("2026-10-26 03:30", VIENNA), seconds=3600)
    session(db, user.id, book.id, utc("2026-10-25 02:30", VIENNA), seconds=600)  # 02:30 CET: Saturday's
    session(db, user.id, book.id, utc("2026-10-26 12:00", VIENNA), seconds=1200)
    w = create(client, "minutes_per_week", 100, tz=VIENNA)
    assert w["period_start"] == "2026-10-26" and w["current"] == 20.0
    h = history(client, w["id"], tz=VIENNA, periods=1)
    assert h["periods"][-1]["start"] == "2026-10-19" and h["periods"][-1]["value"] == 70.0
    d = create(client, "minutes_per_day", 5, tz=VIENNA)
    dh = history(client, d["id"], tz=VIENNA, periods=2)
    prev = {m["date"]: m["value"] for m in dh["weeks"][0]["days"]}
    assert prev["2026-10-25"] == 60.0 and prev["2026-10-24"] == 10.0


# ── per-day series equals Stats ───────────────────────────────────────────────

def test_day_series_equals_stats_daily(client, make_book, admin_user, db, now):
    """Goal minutes and pages per day are Stats' numbers: same zone, same
    reconciliation (live sessions, page statistics, superseded sessions)."""
    user, _ = admin_user
    a, b = make_book(title="A"), make_book(title="B")
    real_now = datetime.utcnow()
    now.value = real_now
    base = real_now - timedelta(days=12)
    for i in range(10):
        st = base + timedelta(days=i, hours=i % 5)
        session(db, user.id, a.id, st, seconds=300 + 97 * i, pages=5 + i, device="web" if i % 3 else "kindle")
        if i % 2:
            t0 = int((st + timedelta(hours=7)).replace(tzinfo=timezone.utc).timestamp())
            for p in range(4):
                db.add(PageStat(user_id=user.id, book_id=b.id, page=p + 1, total_pages=50,
                                start_time=t0 + 60 * p, duration_seconds=55, device="kindle"))
    # a live session superseded by page-stats of the same sitting
    st = base + timedelta(days=3, hours=7)
    session(db, user.id, b.id, st, seconds=500, pages=9)
    db.flush()
    stats = client.get(f"/api/stats?days=30&tz={VIENNA}").json()["daily"]
    sd = {d["date"]: d for d in stats[1:]}  # the first day of a range is partial
    m = create(client, "minutes_per_day", 5, tz=VIENNA)
    p = create(client, "pages_per_day", 5, tz=VIENNA)
    mh = history(client, m["id"], tz=VIENNA, periods=5)
    ph = history(client, p["id"], tz=VIENNA, periods=5)
    seen = 0
    for wm, wp in zip(mh["weeks"], ph["weeks"]):
        for dm, dp in zip(wm["days"], wp["days"]):
            if dm["date"] in sd:
                seen += 1
                assert dm["seconds"] == sd[dm["date"]]["seconds"], dm["date"]
                assert dp["value"] == sd[dm["date"]]["pages"], dm["date"]
    assert seen >= 25
    assert sum(d["seconds"] for d in sd.values()) > 0


# ── target history, start date, history states ────────────────────────────────

def test_target_changed_mid_period_keeps_past_targets(client, make_book, admin_user, db, now):
    user, _ = admin_user
    book = make_book(title="t")
    for d, pages in (("2026-11-03", 400), ("2026-11-10", 250), ("2026-11-17", 350)):
        session(db, user.id, book.id, utc(f"{d} 12:00"), pages=pages)
    now.set("2026-11-04 12:00")
    g = create(client, "pages_per_week", 300)
    now.set("2026-11-18 12:00")
    upd = client.put(f"/api/goals/{g['id']}", json={"target": 200})
    assert upd.status_code == 200 and upd.json()["target"] == 200 and upd.json()["reached"] is True
    h = history(client, g["id"], periods=3)
    by = {p["start"]: p for p in h["periods"]}
    assert by["2026-11-02"]["target"] == 300 and by["2026-11-02"]["state"] == "met"
    assert by["2026-11-09"]["target"] == 300 and by["2026-11-09"]["state"] == "missed"
    assert by["2026-10-26"]["state"] == "no_data"
    assert h["current"]["target"] == 200 and h["current"]["state"] == "current"
    rows = db.query(ReadingGoalTarget).filter_by(goal_id=g["id"]).order_by(ReadingGoalTarget.id).all()
    assert [(r.target, r.changed_on) for r in rows] == [(300, date(2026, 11, 4)), (200, date(2026, 11, 18))]
    assert h["summary"]["met"] == 1 and h["summary"]["missed"] == 1 and h["summary"]["reached"] == 1


def test_goal_created_mid_year_counts_the_year_and_not_before(client, make_book, admin_user, db, now):
    user, _ = admin_user
    for i, d in enumerate(("2024-03-01", "2025-06-01", "2025-07-01", "2026-02-01", "2026-03-01")):
        b = make_book(title=f"y{i}")
        finish(db, user.id, b.id, utc(f"{d} 12:00"))
    now.set("2026-11-20 12:00")
    g = create(client, "books_per_year", 2)
    assert g["current"] == 2 and g["reached"] is True and g["start_date"] == "2026-11-20"
    h = history(client, g["id"], periods=4)
    st = [(p["start"][:4], p["state"], p["reached"]) for p in h["periods"]]
    assert st == [("2022", "no_data", None), ("2023", "no_data", None),
                  ("2024", "before_goal", False), ("2025", "before_goal", True)]
    s = h["summary"]
    assert s["met"] == 0 and s["missed"] == 0 and s["before_goal"] == 2 and s["reached"] == 1
    assert s["current_run"] == 2 and s["current_run_includes_current"] is True


def test_books_history_bulk_day_and_median(client, make_book, admin_user, db, now):
    user, _ = admin_user
    n = 0
    for d, k in (("2026-05-23", 4), ("2026-06-02", 1), ("2026-06-03", 1), ("2026-07-09", 3), ("2026-07-10", 1)):
        for _ in range(k):
            b = make_book(title=f"b{n}")
            finish(db, user.id, b.id, utc(f"{d} 12:00"))
            n += 1
    now.set("2026-08-15 12:00")
    g = create(client, "books_per_month", 3)
    h = history(client, g["id"], periods=4)
    by = {p["start"]: p for p in h["periods"]}
    assert by["2026-04-01"]["state"] == "no_data"
    assert (by["2026-05-01"]["bulk_day"], by["2026-05-01"]["bulk_count"]) == ("2026-05-23", 4)
    assert by["2026-06-01"]["bulk_day"] is None
    assert (by["2026-07-01"]["bulk_day"], by["2026-07-01"]["bulk_count"]) == ("2026-07-09", 3)
    assert h["summary"]["usual"] == 4  # median of 4, 2, 4
    assert [x["start"] for x in h["summary"]["bulk"]] == ["2026-05-01", "2026-07-01"]


def test_day_goal_history_runs(client, make_book, admin_user, db, now):
    user, _ = admin_user
    book = make_book(title="runs")
    now.set("2026-11-20 12:00")
    day0 = date(2026, 10, 1)
    for i in range(50):
        d = day0 + timedelta(days=i)
        if d in (date(2026, 10, 10), date(2026, 11, 19)):
            continue
        session(db, user.id, book.id, utc(f"{d.isoformat()} 12:00"), seconds=1800)
    g = create(client, "minutes_per_day", 30)
    h = history(client, g["id"], periods=3)
    s = h["summary"]
    assert s["data_from"] == "2026-10-01"
    assert s["best_run"] == 39 and s["best_run_start"] == "2026-10-11" and s["best_run_end"] == "2026-11-18"
    assert s["current_run"] == 0 and s["yesterday_value"] == 0.0
    assert s["with_data"] == 50 and s["reached"] == 48
    assert len(h["weeks"]) == 3 and h["weeks"][-1]["start"] == "2026-11-16"
    marks = {m["date"]: m["state"] for w in h["weeks"] for m in w["days"]}
    assert marks["2026-11-19"] == "before_goal" and marks["2026-11-20"] == "today"
    assert marks["2026-11-22"] == "future"


# ── finish by a date ──────────────────────────────────────────────────────────

def test_finish_by_series_counts_the_started_volume(client, make_book, admin_user, db, now):
    user, _ = admin_user
    now.set("2026-11-20 12:00")
    v = [make_book(title=f"S{i}", series="Saga", series_index=float(i)) for i in (1, 2, 3, 4)]
    for i, b in enumerate(v[:2]):
        finish(db, user.id, b.id, utc(f"2026-10-0{i + 1} 12:00"))
        session(db, user.id, b.id, utc(f"2026-09-1{i} 12:00"), seconds=3000 + 1200 * i)  # 3000, 4200
    db.add(UserBookStatus(user_id=user.id, book_id=v[2].id, status="reading", progress_pct=0.25))
    db.flush()
    pre = client.get(f"/api/goals/finish-preview?series={quote('Saga')}&due=2026-11-29")
    assert pre.status_code == 200, pre.text
    f = pre.json()
    assert f["volumes_total"] == 4 and f["volumes_read"] == 2 and f["volumes_left"] == 2
    assert f["per_volume_seconds"] == 3600 and f["basis"] == "series"
    assert f["remaining_seconds"] == round(0.75 * 3600 + 3600)
    assert f["days_left"] == 10 and f["needed_seconds_per_day"] == round(6300 / 10)
    assert f["in_progress"][0]["book_id"] == v[2].id and f["progress_volumes"] == 2.25
    g = create(client, "finish_by", series="Saga", due_date="2026-11-29")
    assert g["metric"] == "volumes" and g["period"] == "deadline"
    assert g["current"] == 2 and g["target"] == 4 and g["pct"] == 56.2
    assert g["name"] == "Saga by Nov 29" and g["expected"] is None
    assert g["status"] == "behind"  # no recent reading covers 630 s a day
    create(client, "finish_by", series="Saga", due_date="2026-12-31", expect=409)
    create(client, "finish_by", series="Saga", due_date="2026-11-01", expect=422)
    create(client, "finish_by", due_date="2026-12-31", expect=422)
    upd = client.put(f"/api/goals/{g['id']}", json={"due_date": "2026-12-08"}).json()
    assert upd["due_date"] == "2026-12-08" and upd["days_left"] == 19


def test_finish_by_completed_notifies_once_and_expires(client, make_book, admin_user, db, now):
    user, _ = admin_user
    now.set("2026-11-20 12:00")
    b = make_book(title="Solo")
    g = create(client, "finish_by", book_id=b.id, due_date="2026-11-25")
    assert g["target"] == 1 and g["current"] == 0 and g["status"] in ("open", "behind", "on_pace")
    now.set("2026-11-26 12:00")
    assert goals(client)["finish_by"]["status"] == "expired"
    finish(db, user.id, b.id, utc("2026-11-26 10:00"))
    g = goals(client)["finish_by"]
    assert g["status"] == "completed" and g["completed_at"] is not None
    goals(client)
    notes = db.query(Notification).filter_by(kind="goal_reached").all()
    assert len(notes) == 1 and "Solo" in notes[0].body


# ── notifications ─────────────────────────────────────────────────────────────

def test_weekly_goal_notifies_once_per_week(client, make_book, admin_user, db, now):
    user, _ = admin_user
    book = make_book(title="n")
    now.set("2026-11-18 12:00")
    session(db, user.id, book.id, utc("2026-11-17 12:00"), pages=500)
    create(client, "pages_per_week", 400)
    goals(client)
    assert db.query(Notification).filter_by(kind="goal_reached").count() == 1
    assert "400 pages this week" in db.query(Notification).first().body
    now.set("2026-11-24 12:00")
    session(db, user.id, book.id, utc("2026-11-23 12:00"), pages=450)
    goals(client)
    goals(client)
    assert db.query(Notification).filter_by(kind="goal_reached").count() == 2


def test_reading_days_goal_notifies(client, make_book, admin_user, db, now):
    user, _ = admin_user
    book = make_book(title="rd")
    now.set("2026-11-18 12:00")
    for d in ("2026-11-16", "2026-11-17"):
        session(db, user.id, book.id, utc(f"{d} 12:00"))
    create(client, "days_per_week", 2)
    notes = db.query(Notification).filter_by(kind="goal_reached").all()
    assert len(notes) == 1 and "reading on 2 days this week" in notes[0].body


def test_upgrade_does_not_renotify_a_reached_month(client, make_book, admin_user, db, now):
    """The notified key is the one the first goals build stored (UTC instant of
    local midnight on the period's first day), so a month already notified
    before the upgrade stays notified."""
    user, _ = admin_user
    now.set("2026-11-20 12:00", VIENNA)
    b = make_book(title="m")
    finish(db, user.id, b.id, utc("2026-11-03 12:00", VIENNA))
    goal = ReadingGoal(user_id=user.id, kind="books_per_month", target=1,
                       notified_window_start=datetime(2026, 10, 31, 23, 0))  # old: local - offset
    db.add(goal)
    db.flush()
    goals(client, tz=VIENNA)
    assert db.query(Notification).filter_by(kind="goal_reached").count() == 0


# ── undo ──────────────────────────────────────────────────────────────────────

def test_delete_is_undoable_then_purged(client, now, db):
    now.set("2026-11-20 12:00")
    g = create(client, "minutes_per_day", 30)
    assert client.delete(f"/api/goals/{g['id']}").status_code == 204
    assert goals(client) == {}
    r = client.post(f"/api/goals/{g['id']}/restore")
    assert r.status_code == 200 and r.json()["id"] == g["id"]
    assert list(goals(client)) == ["minutes_per_day"]
    client.delete(f"/api/goals/{g['id']}")
    now.value = now.value + gs.RESTORE_WINDOW + timedelta(minutes=1)
    assert client.post(f"/api/goals/{g['id']}/restore").status_code == 404
    assert db.query(ReadingGoal).count() == 0


def test_new_goal_replaces_a_deleted_twin(client, now):
    g = create(client, "minutes_per_day", 30)
    client.delete(f"/api/goals/{g['id']}")
    g2 = create(client, "minutes_per_day", 45)
    assert client.post(f"/api/goals/{g['id']}/restore").status_code == 404
    assert goals(client)["minutes_per_day"]["id"] == g2["id"]


# ── kinds and suggestions ─────────────────────────────────────────────────────

def test_kinds_lists_the_readers_goals(client, now):
    g = create(client, "books_per_year", 12)
    r = client.get("/api/goals/kinds").json()
    by = {k["kind"]: k for k in r["kinds"]}
    assert set(by) == set(gs.KINDS)
    assert by["books_per_year"]["existing"][0]["goal_id"] == g["id"]
    assert by["days_per_week"]["new"] is True and by["days_per_week"]["max"] == 7
    assert by["finish_by"]["book_type_scoped"] is False and by["minutes_per_day"]["presets"] == [15, 30, 60]


def test_suggestions_without_history_give_presets(client, now):
    s = client.get("/api/goals/suggestions?kind=minutes_per_day").json()
    k = s["kinds"]["minutes_per_day"]
    assert k["suggested"] is None
    assert [p["value"] for p in k["presets"]] == [15, 30, 60]
    assert client.get("/api/goals/suggestions?kind=finish_by").status_code == 422


def test_suggestions_from_ninety_days(client, make_book, admin_user, db, now):
    user, _ = admin_user
    now.set("2026-11-20 12:00")
    book = make_book(title="sg")
    today = date(2026, 11, 20)
    for i in range(1, 91):  # minutes 1..90 on the 90 days before today
        d = today - timedelta(days=i)
        session(db, user.id, book.id, utc(f"{d.isoformat()} 12:00"), seconds=60 * i)
    for i in range(3):
        b = make_book(title=f"fin{i}")
        finish(db, user.id, b.id, utc(f"2026-10-1{i} 12:00"))
    s = client.get("/api/goals/suggestions").json()
    k = s["kinds"]["minutes_per_day"]
    assert k["average"] == 45.5 and k["count"] == 90
    assert [c["value"] for c in k["suggested"]] == [20, 45, 65]  # 23, 46, 68 rounded down to 5
    assert [c["reached"] for c in k["suggested"]] == [71, 46, 26]
    per_book = sum(range(1, 91)) / 3
    assert s["per_book"]["minutes"] == round(per_book, 1)
    assert k["suggested"][1]["books_per_month"] == round(45 * gs.DAYS_PER_MONTH / per_book, 2)
    assert s["kinds"]["minutes_per_week"]["suggested"] is not None
    assert s["kinds"]["books_per_month"]["suggested"] is None  # one month with finishes


# ── backward compatibility ────────────────────────────────────────────────────

OLD_FIELDS = {"id", "kind", "metric", "period", "target", "book_type_id", "book_type_label",
              "current", "pct", "expected"}


@pytest.mark.parametrize("kind", gs.LEGACY_KINDS)
def test_old_fields_kept_for_the_six_old_kinds(client, kind, now):
    g = create(client, kind, 10)
    assert OLD_FIELDS <= set(g)
    metric, period = kind.split("_per_")
    assert (g["metric"], g["period"]) == (metric, period)
    assert isinstance(g["target"], int) and isinstance(g["pct"], float)
    assert ("days_hit_this_week" in g) is (period == "day")
    assert ("year" in g) is (period == "year")
    assert (g["expected"] is None) is (period == "day")
    if metric == "minutes":
        assert isinstance(g["current"], float)
    else:
        assert isinstance(g["current"], int)
    listed = client.get("/api/goals").json()["goals"][0]
    assert OLD_FIELDS <= set(listed)


def test_new_kinds_send_metric_and_period_strings(client, make_book, now):
    """iOS decodes metric and period as non-optional strings and target as a number."""
    b = make_book(title="x")
    for g in (create(client, "days_per_week", 4),
              create(client, "finish_by", book_id=b.id, due_date="2026-12-31")):
        assert isinstance(g["metric"], str) and isinstance(g["period"], str)
        assert isinstance(g["target"], int)


# ── upgrade of an old-shape table ─────────────────────────────────────────────

def test_upgrade_old_shape_table_in_place(tmp_path):
    from backend.main import upgrade_reading_goals

    eng = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with eng.begin() as c:
        c.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY)"))
        c.execute(text("CREATE TABLE books (id INTEGER PRIMARY KEY)"))
        c.execute(text("CREATE TABLE book_types (id INTEGER PRIMARY KEY)"))
        c.execute(text(
            "CREATE TABLE reading_goals (id INTEGER NOT NULL, user_id INTEGER NOT NULL, kind VARCHAR(32) NOT NULL,"
            " target INTEGER NOT NULL, book_type_id INTEGER, notified_window_start DATETIME,"
            " created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL, PRIMARY KEY (id))"))
        c.execute(text("INSERT INTO users (id) VALUES (1)"))
        c.execute(text(
            "INSERT INTO reading_goals VALUES (7, 1, 'books_per_year', 60, NULL, '2025-12-31 23:00:00',"
            " '2026-03-04 10:00:00', '2026-05-01 10:00:00')"))
    upgrade_reading_goals(eng)
    upgrade_reading_goals(eng)  # idempotent
    with eng.connect() as c:
        cols = {r[1] for r in c.execute(text("PRAGMA table_info(reading_goals)"))}
        assert {"start_date", "target_series", "target_book_id", "due_date", "completed_at", "deleted_at"} <= cols
        row = c.execute(text("SELECT id, kind, target, notified_window_start, start_date FROM reading_goals")).one()
        assert row == (7, "books_per_year", 60, "2025-12-31 23:00:00", "2026-03-04")
        targets = c.execute(text("SELECT goal_id, target, changed_on FROM reading_goal_targets")).all()
        assert targets == [(7, 60, "2026-03-04")]


# ── follow-ups: books suggestions from reading time, reading-days card history ──

def _reader_with_minutes(db, make_book, user, today, minutes_for_day):
    book = make_book(title="pace")
    for i in range(1, 91):
        d = today - timedelta(days=i)
        m = minutes_for_day(i)
        if m:
            session(db, user.id, book.id, utc(f"{d.isoformat()} 12:00"), seconds=60 * m)


def test_books_suggestions_come_from_reading_time_not_bulk_finish_dates(client, make_book, admin_user, db, now):
    user, _ = admin_user
    now.set("2026-11-20 12:00")
    today = date(2026, 11, 20)
    _reader_with_minutes(db, make_book, user, today, lambda i: i)  # 1..90 min a day
    # Three timed books (300, 600, 900 min: median 600) read long ago...
    for k, mins in enumerate((300, 600, 900)):
        b = make_book(title=f"timed{k}")
        session(db, user.id, b.id, utc("2025-01-0%d 12:00" % (k + 1)), seconds=60 * mins)
        finish(db, user.id, b.id, utc("2026-06-28 12:00"))
    # ...and forty books marked read on one day without any reading time.
    for k in range(40):
        b = make_book(title=f"bulk{k}")
        finish(db, user.id, b.id, utc("2026-06-28 12:00"))
    s = client.get("/api/goals/suggestions").json()
    assert s["per_book"]["median_minutes"] == 600.0
    month = s["kinds"]["books_per_month"]
    # Minutes-a-day percentiles 23 / 46 / 68 (index q·90 of 1..90) × 30.44 ÷ 600.
    assert [c["value"] for c in month["suggested"]] == [1, 2, 3]
    assert month["over"] == "days" and month["suggested"][1]["minutes_per_day"] == round(2 * 600 / gs.DAYS_PER_MONTH, 1)
    assert month["suggested"][1]["reached"] == sum(1 for m in range(1, 91) if m >= round(2 * 600 / gs.DAYS_PER_MONTH, 1))
    year = s["kinds"]["books_per_year"]
    # 23 / 46 / 68 × 365 ÷ 600 = 13.99 / 27.98 / 41.4: below 40 at the median, so whole books.
    assert [c["value"] for c in year["suggested"]] == [13, 27, 41]
    assert all(p["of"] == 90 for p in year["presets"])


def test_books_suggestions_fall_back_to_presets_without_timed_books(client, make_book, admin_user, db, now):
    user, _ = admin_user
    now.set("2026-11-20 12:00")
    today = date(2026, 11, 20)
    _reader_with_minutes(db, make_book, user, today, lambda i: 40)
    for k in range(10):  # finishes, but none with recorded reading
        b = make_book(title=f"f{k}")
        finish(db, user.id, b.id, utc(f"2026-0{1 + k % 9}-15 12:00"))
    s = client.get("/api/goals/suggestions").json()
    for kind in ("books_per_month", "books_per_year"):
        assert s["kinds"][kind]["suggested"] is None
        assert [p["value"] for p in s["kinds"][kind]["presets"]] == gs.PRESETS[kind]


def test_reading_days_card_counts_the_last_twelve_weeks(client, make_book, admin_user, db, now):
    user, _ = admin_user
    now.set("2026-11-20 09:00", VIENNA)  # a Friday; this week starts Mon Nov 16
    book = make_book(title="days")
    week0 = date(2026, 11, 16)
    # Five complete weeks of data: the last three with 5 reading days, two before with 2.
    for w in range(1, 6):
        start = week0 - timedelta(days=7 * w)
        n = 5 if w <= 3 else 2
        for i in range(n):
            session(db, user.id, book.id, utc(f"{(start + timedelta(days=i)).isoformat()} 12:00", VIENNA), seconds=600)
    g = create(client, "days_per_week", 5, tz=VIENNA)
    assert g["recent_weeks"] == {"reached": 3, "of": 5}
    assert goals(client, tz=VIENNA)["days_per_week"]["recent_weeks"] == {"reached": 3, "of": 5}


def test_reading_days_history_has_the_weeks_day_by_day(client, make_book, admin_user, db, now):
    user, _ = admin_user
    now.set("2026-11-20 09:00", VIENNA)
    book = make_book(title="grid")
    session(db, user.id, book.id, utc("2026-11-17 12:00", VIENNA), seconds=600)
    g = create(client, "days_per_week", 3, tz=VIENNA)
    h = client.get(f"/api/goals/{g['id']}/history?tz={VIENNA}&periods=4").json()
    assert len(h["weeks"]) == 4 and h["weeks"][-1]["start"] == "2026-11-16"
    days = {d["date"]: d for d in h["weeks"][-1]["days"]}
    assert days["2026-11-17"]["value"] == 1 and days["2026-11-17"]["reached"] is True
    assert days["2026-11-18"]["state"] == "before_goal" and days["2026-11-18"]["reached"] is False
    assert days["2026-11-20"]["state"] == "today" and days["2026-11-22"]["state"] == "future"
    assert h["weeks"][0]["days"][0]["state"] == "no_data"


def test_weekly_streak_follows_the_week_start():
    from backend.services.streaks import streak_summary_from_dates
    # Read on Sun Nov 8 and Sun Nov 15, 2026 only; today Mon Nov 16.
    days = {date(2026, 11, 8), date(2026, 11, 15)}
    mon = streak_summary_from_dates(days, date(2026, 11, 16))
    sun = streak_summary_from_dates(days, date(2026, 11, 16), "sunday")
    # Monday weeks: Nov 2-8 and Nov 9-15, two weeks in a row. Sunday weeks: Nov 8-14 and Nov 15-21.
    assert mon.current_weeks == 2 and mon.current_weeks_start == date(2026, 11, 2)
    assert sun.current_weeks == 2 and sun.current_weeks_start == date(2026, 11, 8)
    assert sun.longest_weeks_end == date(2026, 11, 21)
