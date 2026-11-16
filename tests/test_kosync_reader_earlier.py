"""Reading a CrossInk reader counted before it first synced to Tome.

The reader's lifetime snapshot (``PUT .../stats/global``) holds lifetime
totals and a calendar of read days, one bit per day. Before the device's
first session in Tome that is all there is: Tome counts those days as reading
days without detail (streaks, calendar) and adds the totals beyond what it
holds from the device to the all-time figures. From the first session on,
Tome's own sessions are the record.
"""
import base64
from datetime import date, datetime, timedelta, timezone

import pytest

from backend.services import kosync_stats
from backend.services.reading_day import DayCtx
from tests.test_kosync_book_pages import BASE, DEVICE, DOC, _account, _headers, _item, _link, _put_books
from tests.test_kosync_stats import _global

DAY_ZERO = date(2000, 1, 1)


def _history(anchor: date, days) -> dict:
    bits = bytearray(92)
    for d in days:
        i = (anchor - d).days
        bits[i // 8] |= 1 << (i % 8)
    return {"anchor_day": (anchor - DAY_ZERO).days, "history_b64": base64.b64encode(bytes(bits)).decode()}


def _noon(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, 12, tzinfo=timezone.utc).timestamp())


def _sess(client, acc, n, d: date, seconds=600, document=DOC):
    start = _noon(d)
    sid = f"{n:032x}"
    r = client.put(f"{BASE}/stats/sessions", headers=_headers(acc), json={
        "device_id": DEVICE, "items": [{
            "session_id": sid, "document": document, "started_at": start, "ended_at": start + seconds,
            "seconds": seconds, "utc_offset_minutes": 0,
        }],
    })
    assert r.status_code == 200 and r.json()["accepted_session_ids"] == [sid], r.text


def _put_global(client, acc, **fields):
    r = client.put(f"{BASE}/stats/global", headers=_headers(acc), json=_global(**fields))
    assert r.status_code == 200, r.text


def _lifetime(client):
    return client.get("/api/stats?days=0&tz_offset=0").json()["lifetime"]


@pytest.fixture()
def user(admin_user):
    return admin_user[0]


@pytest.fixture()
def acc(db, user):
    return _account(db, user)


@pytest.fixture()
def today():
    return DayCtx(0).today()


def test_the_calendar_bits_count_back_from_the_anchor_day():
    anchor = date(2026, 11, 9)
    marked = {anchor, anchor - timedelta(days=1), anchor - timedelta(days=8), anchor - timedelta(days=91),
              anchor - timedelta(days=729)}
    assert kosync_stats.history_days(_history(anchor, marked)) == marked
    assert kosync_stats.history_days({"anchor_day": 9809, "history_b64": "not base64 !"}) == set()
    assert kosync_stats.history_days({"anchor_day": 0, "history_b64": "AAAA"}) == set()
    assert kosync_stats.history_days({}) == set()


def test_days_and_totals_from_before_the_first_sync_reach_the_all_time_figures(db, client, acc, user, today, make_book):
    book = make_book(title="Started Before Tome")
    _link(db, user, book)
    first = today - timedelta(days=1)                       # the reader's first session in Tome
    before = [first - timedelta(days=n) for n in range(1, 6)]
    _sess(client, acc, 1, first, seconds=900)
    _put_books(client, acc, _item(pages=40, seconds=900))
    _put_global(client, acc, sessions=110, seconds=65820, pages=2793, **_history(today, before + [first]))

    life = _lifetime(client)
    assert life["reader_earlier"] == {"seconds": 65820 - 900, "pages": 2793 - 40, "sessions": 109, "days": 5}
    # Tome's part plus the earlier part is what the reader counts.
    assert (life["seconds"], life["sessions"], life["pages"], life["active_days"]) == (65820, 110, 2793, 6)

    stats = client.get("/api/stats?days=0&tz_offset=0").json()["headline"]
    assert stats["current_streak_days"] == 6 and stats["longest_streak_days"] == 6
    # The "All" range shows the all-time figures; a dated range has none of it.
    assert (stats["total_reading_seconds"], stats["total_sessions"], stats["pages_turned"]) == (65820, 110, 2793)
    assert stats["reader_earlier"] == life["reader_earlier"]
    month = client.get("/api/stats?days=30&tz_offset=0").json()["headline"]
    assert (month["total_reading_seconds"], month["total_sessions"], month["reader_earlier"]) == (900, 1, None)
    assert client.get("/api/home/stats?tz_offset=0").json()["current_streak_days"] == 6

    yesterday = before[0]
    month = client.get(f"/api/stats/calendar?month={yesterday.isoformat()[:7]}&tz_offset=0").json()
    assert yesterday.isoformat() in month["reader_days"]
    assert first.isoformat() not in month["reader_days"]          # Tome has that day itself
    assert month["first_day"] == min(before).isoformat()
    assert month["streaks"]["current_days"] == 6
    in_month = [d for d in before if d.isoformat()[:7] == month["month"]]
    own = [d for d in month["days"] if d["date"][:7] == month["month"]]
    assert month["totals"]["reading_days"] == len(in_month) + len(own)

    day = client.get(f"/api/stats/calendar/day?day={yesterday.isoformat()}&tz_offset=0").json()
    assert day["reader_day"] is True and day["books"] == [] and day["seconds"] == 0
    assert day["streak"]["length"] == 6
    assert client.get(f"/api/stats/calendar/day?day={first.isoformat()}&tz_offset=0").json()["reader_day"] is False


def test_from_the_first_session_on_only_tomes_sessions_count(db, client, acc, user, today, make_book):
    """A day the reader marks after its first session in Tome, with no session
    for it in Tome (deleted there, or cut at another hour), is not a reading day."""
    _link(db, user, make_book(title="Synced From The Start"))
    first = today - timedelta(days=4)
    _sess(client, acc, 1, first, seconds=600)
    _put_global(client, acc, sessions=1, seconds=600, pages=0,
                **_history(today, [first, today - timedelta(days=2)]))
    assert kosync_stats.reader_days(db, user.id) == set()
    life = _lifetime(client)
    assert life["reader_earlier"] is None and life["active_days"] == 1


def test_a_session_deleted_in_tome_does_not_come_back_as_earlier_reading(db, client, acc, user, today, make_book):
    _link(db, user, make_book(title="Trimmed In Tome"))
    _sess(client, acc, 1, today - timedelta(days=1), seconds=1200)
    _put_global(client, acc, sessions=1, seconds=1200, pages=0, **_history(today, []))
    session_id = client.get("/api/stats/sessions").json()["sessions"][0]["id"]
    assert client.delete(f"/api/stats/sessions/{session_id}").status_code in (200, 204)
    life = _lifetime(client)
    assert life["reader_earlier"] is None and life["seconds"] == 0


def test_no_reader_snapshot_changes_nothing(client, user):
    life = _lifetime(client)
    assert life["reader_earlier"] is None
    assert (life["seconds"], life["sessions"], life["pages"], life["active_days"]) == (0, 0, 0, 0)
