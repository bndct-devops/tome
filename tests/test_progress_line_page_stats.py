"""The book page's progress line on a book with KOReader page-stats.

A Kindle sitting reaches Tome twice: as the plugin's live session (which
carries ``progress_end``) and as imported page-stats (pages and time, no
position). Where both cover the same minutes the page-stats supersede the
session's time. The line used to drop the superseded sessions too, so a
page-stats book got a line from the few sittings the page-stats missed, or
none. The position of a superseded sitting is still where the reader was.

TomeSync's ``position_history`` (the plugin's and the web reader's position
writes) feeds the line the same way the KOSync push log does.
"""
from datetime import datetime, timedelta, timezone

from backend.models.ko_stats import PageStat
from backend.models.tome_sync import PositionHistory, ReadingSession
from backend.services.book_progress import upsert_position

# Noon UTC, ten days back: far enough from "now" that no test day is today.
D0 = (datetime.utcnow() - timedelta(days=10)).replace(hour=12, minute=0, second=0, microsecond=0)


def _day(n: int) -> str:
    return (D0 + timedelta(days=n)).date().isoformat()


def _line(client, book):
    r = client.get(f"/api/books/{book.id}/reading-stats")
    assert r.status_code == 200, r.text
    return {row["date"]: row["progress_pct"] for row in r.json()["own"]["session_timeline"]}


def _sitting(db, user, book, at: datetime, pct, *, page_stats: bool, with_position: bool = True):
    """A 30-minute Kindle sitting: the plugin's session, and the page-stats
    KOReader synced for the same minutes when ``page_stats`` is set."""
    db.add(ReadingSession(
        user_id=user.id, book_id=book.id, started_at=at, ended_at=at + timedelta(minutes=30),
        duration_seconds=1800, pages_turned=20, device="kindle",
        progress_start=(pct - 0.05) if with_position else None,
        progress_end=pct if with_position else None,
    ))
    if page_stats:
        epoch = int(at.replace(tzinfo=timezone.utc).timestamp())
        for i in range(20):
            db.add(PageStat(user_id=user.id, book_id=book.id, page=100 + i, total_pages=400,
                            start_time=epoch + i * 90, duration_seconds=90, device="kindle"))
    db.flush()


def _position(db, user, book, pct, at: datetime, device="kindle"):
    """A TomeSync position write, logged as if made at ``at`` (UTC)."""
    upsert_position(db, user_id=user.id, book_id=book.id, percentage=pct,
                    progress=f"/body/DocFragment[{int(pct * 100)}]", device=device)
    row = db.query(PositionHistory).order_by(PositionHistory.id.desc()).first()
    row.created_at = at
    db.flush()


def test_plugin_sittings_alone_draw_the_line(db, client, admin_user, make_book):
    user = admin_user[0]
    book = make_book(title="Plain Lantern")
    _sitting(db, user, book, D0, 0.30, page_stats=False)
    _sitting(db, user, book, D0 + timedelta(days=1), 0.60, page_stats=False)
    db.commit()
    assert _line(client, book) == {_day(0): 30.0, _day(1): 60.0}


def test_the_same_sittings_with_page_stats_draw_the_same_line(db, client, admin_user, make_book):
    """The page-stats supersede the sessions' time, not their positions."""
    user = admin_user[0]
    book = make_book(title="Kindle Lantern")
    _sitting(db, user, book, D0, 0.30, page_stats=True)
    _sitting(db, user, book, D0 + timedelta(days=1), 0.60, page_stats=True)
    db.commit()
    assert _line(client, book) == {_day(0): 30.0, _day(1): 60.0}


def test_a_mixed_book_does_not_stall_at_the_last_uncovered_sitting(db, client, admin_user, make_book):
    """Three sittings, page-stats missed the first: the line used to show
    30% on all three days, and 30% on a finished book."""
    user = admin_user[0]
    book = make_book(title="Half Covered")
    _sitting(db, user, book, D0, 0.30, page_stats=False)
    _sitting(db, user, book, D0 + timedelta(days=1), 0.65, page_stats=True)
    _sitting(db, user, book, D0 + timedelta(days=2), 1.0, page_stats=True)
    db.commit()
    assert _line(client, book) == {_day(0): 30.0, _day(1): 65.0, _day(2): 100.0}


def test_the_time_totals_still_come_from_the_page_stats(db, client, admin_user, make_book):
    """Only the line changed: a superseded sitting's minutes are not counted twice."""
    user = admin_user[0]
    book = make_book(title="Counted Once")
    _sitting(db, user, book, D0, 0.30, page_stats=True)
    db.commit()
    r = client.get(f"/api/books/{book.id}/reading-stats")
    assert r.status_code == 200, r.text
    assert r.json()["own"]["total_seconds"] == 1800


# ── position history ──────────────────────────────────────────────────────────

def test_positions_draw_the_line_where_sessions_carry_none(db, client, admin_user, make_book):
    """Sessions without a position (older plugin data, page-stats sittings)
    get the line from the positions the device synced those days."""
    user = admin_user[0]
    book = make_book(title="Logged Positions")
    _sitting(db, user, book, D0, 0.0, page_stats=True, with_position=False)
    _sitting(db, user, book, D0 + timedelta(days=1), 0.0, page_stats=True, with_position=False)
    _position(db, user, book, 0.22, D0 + timedelta(minutes=31))
    _position(db, user, book, 0.47, D0 + timedelta(days=1, minutes=31))
    db.commit()
    assert _line(client, book) == {_day(0): 22.0, _day(1): 47.0}


def test_the_further_of_session_and_position_wins_per_day(db, client, admin_user, make_book):
    user = admin_user[0]
    book = make_book(title="Two Sources")
    _sitting(db, user, book, D0, 0.30, page_stats=False)
    _position(db, user, book, 0.34, D0 + timedelta(minutes=31))        # synced a little later
    _sitting(db, user, book, D0 + timedelta(days=1), 0.60, page_stats=False)
    _position(db, user, book, 0.55, D0 + timedelta(days=1, minutes=5))  # the session went further
    db.commit()
    assert _line(client, book) == {_day(0): 34.0, _day(1): 60.0}


def test_a_position_on_a_day_without_reading_lifts_the_days_after(db, client, admin_user, make_book):
    """Read in the evening, the device syncs its position the next morning."""
    user = admin_user[0]
    book = make_book(title="Morning Sync")
    _sitting(db, user, book, D0, 0.0, page_stats=False, with_position=False)
    _position(db, user, book, 0.40, D0 + timedelta(days=1, hours=-3))   # next morning, no reading
    _sitting(db, user, book, D0 + timedelta(days=2), 0.0, page_stats=False, with_position=False)
    db.commit()
    assert _line(client, book) == {_day(0): None, _day(2): 40.0}


def test_a_position_moved_back_does_not_pull_the_line_down(db, client, admin_user, make_book):
    """The device sync tracks the position last-write-wins, downward included;
    the line keeps the furthest point."""
    user = admin_user[0]
    book = make_book(title="Flipped Back")
    _sitting(db, user, book, D0, 0.0, page_stats=False, with_position=False)
    _position(db, user, book, 0.50, D0 + timedelta(minutes=31))
    _sitting(db, user, book, D0 + timedelta(days=1), 0.0, page_stats=False, with_position=False)
    _position(db, user, book, 0.12, D0 + timedelta(days=1, minutes=31))   # went back to check something
    db.commit()
    assert _line(client, book) == {_day(0): 50.0, _day(1): 50.0}


def test_a_restored_position_cancels_the_bad_jump_before_it(db, client, admin_user, make_book):
    """A device falsely jumped the book to 100%; the user restored an earlier
    position. The jump stays in the log (Position history shows it) but the
    line must not sit at 100% from that day on."""
    user = admin_user[0]
    book = make_book(title="False Finish")
    _sitting(db, user, book, D0, 0.0, page_stats=False, with_position=False)
    _position(db, user, book, 0.40, D0 + timedelta(minutes=31))
    _position(db, user, book, 1.0, D0 + timedelta(minutes=50))               # the bad write
    _position(db, user, book, 0.40, D0 + timedelta(hours=2), device="restore")
    _sitting(db, user, book, D0 + timedelta(days=1), 0.0, page_stats=False, with_position=False)
    _position(db, user, book, 0.52, D0 + timedelta(days=1, minutes=31))
    db.commit()
    assert _line(client, book) == {_day(0): 40.0, _day(1): 52.0}


def test_a_restore_keeps_positions_below_it(db, client, admin_user, make_book):
    """Restoring to 60% after a jump to 100% keeps the earlier 30% and 60%."""
    user = admin_user[0]
    book = make_book(title="Partial Undo")
    _sitting(db, user, book, D0, 0.0, page_stats=False, with_position=False)
    _position(db, user, book, 0.30, D0 + timedelta(minutes=31))
    _sitting(db, user, book, D0 + timedelta(days=1), 0.0, page_stats=False, with_position=False)
    _position(db, user, book, 0.60, D0 + timedelta(days=1, minutes=31))
    _position(db, user, book, 1.0, D0 + timedelta(days=1, minutes=40))
    _position(db, user, book, 0.60, D0 + timedelta(days=1, hours=3), device="restore")
    db.commit()
    assert _line(client, book) == {_day(0): 30.0, _day(1): 60.0}


def test_another_users_positions_do_not_leak_into_the_line(db, client, admin_user, make_book):
    from backend.models.user import User
    user = admin_user[0]
    other = User(username="neighbour", email="n@example.com", hashed_password="x", role="member")
    db.add(other)
    db.flush()
    book = make_book(title="Shared Copy")
    _sitting(db, user, book, D0, 0.0, page_stats=False, with_position=False)
    _sitting(db, user, book, D0 + timedelta(days=1), 0.0, page_stats=False, with_position=False)
    _position(db, other, book, 0.90, D0 + timedelta(minutes=31))
    db.commit()
    assert _line(client, book) == {_day(0): None, _day(1): None}
