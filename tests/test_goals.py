"""Tests for the reading goals API — GET/POST/PUT/DELETE /api/goals."""
from datetime import datetime, timedelta

import pytest
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from backend.core.security import create_access_token, hash_password
from backend.models.library import BookType
from backend.models.notification import Notification
from backend.models.reading_goal import ReadingGoal
from backend.models.tome_sync import ReadingSession
from backend.models.user import User, UserPermission
from backend.models.user_book_status import UserBookStatus


# ── helpers ───────────────────────────────────────────────────────────────────

def _create_goal(
    client: TestClient,
    kind: str,
    target: int,
    book_type_id: int | None = None,
    expected_status: int = 201,
) -> dict:
    body: dict = {"kind": kind, "target": target}
    if book_type_id is not None:
        body["book_type_id"] = book_type_id
    resp = client.post("/api/goals", json=body)
    assert resp.status_code == expected_status, resp.text
    return resp.json() if resp.status_code == 201 else {}


def _list_goals(client: TestClient) -> list[dict]:
    resp = client.get("/api/goals")
    assert resp.status_code == 200, resp.text
    return resp.json()["goals"]


def _update_goal(client: TestClient, goal_id: int, target: int, expected_status: int = 200) -> dict:
    resp = client.put(f"/api/goals/{goal_id}", json={"target": target})
    assert resp.status_code == expected_status, resp.text
    return resp.json() if resp.status_code == 200 else {}


def _delete_goal(client: TestClient, goal_id: int, expected_status: int = 204) -> None:
    resp = client.delete(f"/api/goals/{goal_id}")
    assert resp.status_code == expected_status, resp.text


def _make_type(db: Session, slug: str = "test-manga", label: str = "Manga") -> BookType:
    bt = BookType(slug=slug, label=label)
    db.add(bt)
    db.flush()
    return bt


def _finish_book(db: Session, user_id: int, book_id: int, updated_at: datetime | None = None) -> UserBookStatus:
    ubs = UserBookStatus(
        user_id=user_id,
        book_id=book_id,
        status="read",
    )
    db.add(ubs)
    db.flush()
    if updated_at is not None:
        ubs.updated_at = updated_at
        db.flush()
    return ubs


def _add_session(
    db: Session,
    user_id: int,
    book_id: int,
    started_at: datetime,
    duration_seconds: int = 600,
    pages_turned: int = 0,
) -> ReadingSession:
    s = ReadingSession(
        user_id=user_id,
        book_id=book_id,
        started_at=started_at,
        ended_at=started_at + timedelta(seconds=duration_seconds),
        duration_seconds=duration_seconds,
        pages_turned=pages_turned,
    )
    db.add(s)
    db.flush()
    return s


# ── tests — CRUD ──────────────────────────────────────────────────────────────

def test_create_and_list_goal(client: TestClient):
    """POST creates the goal; GET /goals returns it."""
    result = _create_goal(client, "books_per_year", 20)
    assert result["kind"] == "books_per_year"
    assert result["target"] == 20
    assert result["book_type_id"] is None
    assert "id" in result
    assert "year" in result
    assert "current" in result
    assert "pct" in result

    goals = _list_goals(client)
    assert len(goals) == 1
    assert goals[0]["kind"] == "books_per_year"
    assert goals[0]["target"] == 20


def test_duplicate_goal_rejected(client: TestClient):
    """A second goal with the same (kind, book type) is a 409."""
    _create_goal(client, "books_per_year", 10)
    _create_goal(client, "books_per_year", 25, expected_status=409)

    goals = _list_goals(client)
    assert len(goals) == 1
    assert goals[0]["target"] == 10


def test_same_kind_different_type_coexist(client: TestClient, db: Session):
    """'20 books this year' and '20 manga this year' are separate goals."""
    manga = _make_type(db)
    _create_goal(client, "books_per_year", 20)
    typed = _create_goal(client, "books_per_year", 20, book_type_id=manga.id)
    assert typed["book_type_id"] == manga.id
    assert typed["book_type_label"] == "Manga"
    # but a second manga goal of the same kind is a duplicate
    _create_goal(client, "books_per_year", 30, book_type_id=manga.id, expected_status=409)

    goals = _list_goals(client)
    assert len(goals) == 2


def test_update_goal_target(client: TestClient):
    """PUT updates the target."""
    goal = _create_goal(client, "books_per_year", 10)
    updated = _update_goal(client, goal["id"], 25)
    assert updated["target"] == 25

    goals = _list_goals(client)
    assert len(goals) == 1
    assert goals[0]["target"] == 25


def test_delete_goal(client: TestClient):
    """DELETE removes the goal; subsequent GET returns empty list."""
    goal = _create_goal(client, "books_per_year", 12)
    _delete_goal(client, goal["id"])
    goals = _list_goals(client)
    assert goals == []


def test_delete_nonexistent_returns_404(client: TestClient):
    """DELETE on a goal that doesn't exist returns 404."""
    _delete_goal(client, 9999, expected_status=404)


def test_update_nonexistent_returns_404(client: TestClient):
    _update_goal(client, 9999, 10, expected_status=404)


def test_invalid_kind_rejected(client: TestClient):
    """POST with an unknown kind returns 422."""
    resp = client.post("/api/goals", json={"kind": "daily_pages", "target": 50})
    assert resp.status_code == 422


def test_unknown_book_type_rejected(client: TestClient):
    resp = client.post("/api/goals", json={"kind": "books_per_year", "target": 10, "book_type_id": 4242})
    assert resp.status_code == 422


def test_non_positive_target_rejected(client: TestClient):
    """POST with target <= 0 returns 422."""
    resp = client.post("/api/goals", json={"kind": "books_per_year", "target": 0})
    assert resp.status_code == 422
    resp = client.post("/api/goals", json={"kind": "books_per_year", "target": -5})
    assert resp.status_code == 422


def test_allowed_kinds_all_accepted(client: TestClient):
    """All 6 allowed kinds are accepted by POST."""
    allowed = [
        "books_per_year", "books_per_month",
        "minutes_per_day", "minutes_per_week",
        "pages_per_day", "pages_per_week",
    ]
    for kind in allowed:
        result = _create_goal(client, kind, 10)
        assert result["kind"] == kind
        assert result["metric"] in ("books", "minutes", "pages")
        assert result["period"] in ("day", "week", "month", "year")
    goals = _list_goals(client)
    assert len(goals) == 6


def test_out_of_set_kind_rejected(client: TestClient):
    """A kind that looks valid but isn't in ALLOWED_KINDS is rejected."""
    resp = client.post("/api/goals", json={"kind": "books_per_week", "target": 3})
    assert resp.status_code == 422
    resp = client.post("/api/goals", json={"kind": "pages_per_year", "target": 1000})
    assert resp.status_code == 422


# ── tests — books_per_year progress ──────────────────────────────────────────

def test_books_per_year_counts_this_year_only(
    client: TestClient, make_book, admin_user, db: Session
):
    """books_per_year.current only counts reads updated this calendar year."""
    user, _ = admin_user
    book1 = make_book(title="Book A")
    book2 = make_book(title="Book B")
    book3 = make_book(title="Book C — Last Year")

    now = datetime.utcnow()
    year_start = datetime(now.year, 1, 1)
    last_year = datetime(now.year - 1, 6, 1)

    _finish_book(db, user.id, book1.id, updated_at=now - timedelta(days=10))
    _finish_book(db, user.id, book2.id, updated_at=year_start + timedelta(days=1))
    _finish_book(db, user.id, book3.id, updated_at=last_year)

    _create_goal(client, "books_per_year", 20)
    goals = _list_goals(client)
    by_year = next(g for g in goals if g["kind"] == "books_per_year")

    # Only book1 and book2 are this-year reads
    assert by_year["current"] == 2
    assert by_year["year"] == now.year


def test_books_per_year_pct_calculation(
    client: TestClient, make_book, admin_user, db: Session
):
    """pct = current / target * 100, rounded to 1 decimal."""
    user, _ = admin_user
    now = datetime.utcnow()
    for i in range(3):
        book = make_book(title=f"Pct Book {i}")
        _finish_book(db, user.id, book.id, updated_at=now - timedelta(days=i))

    result = _create_goal(client, "books_per_year", 12)
    assert result["current"] == 3
    assert result["pct"] == pytest.approx(25.0, abs=0.1)


def test_book_type_filter_counts_only_that_type(
    client: TestClient, make_book, admin_user, db: Session
):
    """A manga-scoped goal ignores finished books of other types."""
    user, _ = admin_user
    manga = _make_type(db)
    novel = make_book(title="A Novel")
    manga_book = make_book(title="A Manga Volume")
    manga_book.book_type_id = manga.id
    db.flush()

    now = datetime.utcnow()
    _finish_book(db, user.id, novel.id, updated_at=now - timedelta(days=1))
    _finish_book(db, user.id, manga_book.id, updated_at=now - timedelta(days=2))

    all_goal = _create_goal(client, "books_per_year", 20)
    manga_goal = _create_goal(client, "books_per_year", 20, book_type_id=manga.id)

    assert all_goal["current"] == 2
    assert manga_goal["current"] == 1


def test_book_type_filter_on_session_metrics(
    client: TestClient, make_book, admin_user, db: Session
):
    """Typed minutes/pages goals only count sessions on books of that type."""
    user, _ = admin_user
    manga = _make_type(db)
    novel = make_book(title="Session Novel")
    manga_book = make_book(title="Session Manga")
    manga_book.book_type_id = manga.id
    db.flush()

    now = datetime.utcnow()
    _add_session(db, user.id, novel.id, now - timedelta(minutes=30), duration_seconds=1200, pages_turned=40)
    _add_session(db, user.id, manga_book.id, now - timedelta(minutes=10), duration_seconds=600, pages_turned=25)

    minutes_goal = _create_goal(client, "minutes_per_day", 60, book_type_id=manga.id)
    assert minutes_goal["current"] == pytest.approx(10.0, abs=0.5)

    pages_goal = _create_goal(client, "pages_per_day", 100, book_type_id=manga.id)
    assert pages_goal["current"] == 25


# ── tests — books_per_month progress ─────────────────────────────────────────

def test_books_per_month_counts_this_month_only(
    client: TestClient, make_book, admin_user, db: Session
):
    """books_per_month.current only counts reads in the current calendar month."""
    user, _ = admin_user
    book1 = make_book(title="This Month Book")
    book2 = make_book(title="Last Month Book")

    now = datetime.utcnow()
    month_start = datetime(now.year, now.month, 1)

    _finish_book(db, user.id, book1.id, updated_at=month_start + timedelta(days=1))
    # Put last month's book clearly before this month started
    last_month = month_start - timedelta(days=5)
    _finish_book(db, user.id, book2.id, updated_at=last_month)

    result = _create_goal(client, "books_per_month", 4)
    assert result["kind"] == "books_per_month"
    assert result["metric"] == "books"
    assert result["period"] == "month"
    assert result["current"] == 1
    assert result["pct"] == pytest.approx(25.0, abs=0.1)


def test_books_per_month_response_fields(client: TestClient):
    """books_per_month response has the generic fields (no year field)."""
    result = _create_goal(client, "books_per_month", 4)
    assert "kind" in result
    assert "metric" in result
    assert "period" in result
    assert "current" in result
    assert "target" in result
    assert "pct" in result
    # days_hit_this_week is for daily goals only
    assert "days_hit_this_week" not in result


# ── tests — pace (expected) ───────────────────────────────────────────────────

def test_expected_present_for_calendar_windows(client: TestClient):
    """expected is the prorated target for month/year, None for day/week."""
    year_goal = _create_goal(client, "books_per_year", 365)
    assert year_goal["expected"] is not None
    # prorated target can never exceed the target itself
    assert 0 <= year_goal["expected"] <= 365

    day_goal = _create_goal(client, "minutes_per_day", 30)
    assert day_goal["expected"] is None

    week_goal = _create_goal(client, "pages_per_week", 350)
    assert week_goal["expected"] is None


# ── tests — minutes_per_day progress ─────────────────────────────────────────

def test_minutes_per_day_today_calculation(
    client: TestClient, make_book, admin_user, db: Session
):
    """minutes_per_day.current counts only sessions whose local date == today."""
    user, _ = admin_user
    book = make_book(title="Timed Book")
    now = datetime.utcnow()

    # 10 min today
    _add_session(db, user.id, book.id, now - timedelta(minutes=5), duration_seconds=600)
    # 5 min yesterday — should NOT count toward today
    _add_session(db, user.id, book.id, now - timedelta(days=1), duration_seconds=300)

    result = _create_goal(client, "minutes_per_day", 30)
    assert result["kind"] == "minutes_per_day"
    # current should be ~10 (from the 600s session today)
    assert result["current"] == pytest.approx(10.0, abs=0.5)
    assert "days_hit_this_week" in result


def test_minutes_per_day_days_hit_this_week(
    client: TestClient, make_book, admin_user, db: Session
):
    """days_hit_this_week counts days in the last 7 where goal was met."""
    user, _ = admin_user
    book = make_book(title="Week Book")
    now = datetime.utcnow()
    target_minutes = 20
    target_seconds = target_minutes * 60

    # 3 days ago — hit (1200s = 20min)
    _add_session(db, user.id, book.id, now - timedelta(days=3), duration_seconds=target_seconds)
    # 5 days ago — hit
    _add_session(db, user.id, book.id, now - timedelta(days=5), duration_seconds=target_seconds + 60)
    # 1 day ago — miss (only 600s = 10min)
    _add_session(db, user.id, book.id, now - timedelta(days=1), duration_seconds=600)

    result = _create_goal(client, "minutes_per_day", target_minutes)
    assert result["days_hit_this_week"] == 2


# ── tests — minutes_per_week progress ────────────────────────────────────────

def test_minutes_per_week_sums_last_7_days(
    client: TestClient, make_book, admin_user, db: Session
):
    """minutes_per_week sums sessions from the last 7 local days."""
    user, _ = admin_user
    book = make_book(title="Week Minutes Book")
    now = datetime.utcnow()

    # 1 day ago: 30 min
    _add_session(db, user.id, book.id, now - timedelta(days=1), duration_seconds=1800)
    # 4 days ago: 60 min
    _add_session(db, user.id, book.id, now - timedelta(days=4), duration_seconds=3600)
    # 8 days ago: 45 min — outside 7-day window
    _add_session(db, user.id, book.id, now - timedelta(days=8), duration_seconds=2700)

    result = _create_goal(client, "minutes_per_week", 120)
    assert result["kind"] == "minutes_per_week"
    assert result["metric"] == "minutes"
    assert result["period"] == "week"
    # Should sum only the in-window sessions: 30 + 60 = 90 min
    assert result["current"] == pytest.approx(90.0, abs=1.0)
    # No days_hit_this_week for weekly goals
    assert "days_hit_this_week" not in result


def test_minutes_per_week_pct(
    client: TestClient, make_book, admin_user, db: Session
):
    """minutes_per_week pct is computed correctly."""
    user, _ = admin_user
    book = make_book(title="Pct Week Book")
    now = datetime.utcnow()

    # 60 min in window
    _add_session(db, user.id, book.id, now - timedelta(days=2), duration_seconds=3600)

    result = _create_goal(client, "minutes_per_week", 120)
    assert result["pct"] == pytest.approx(50.0, abs=1.0)


# ── tests — pages_per_day progress ───────────────────────────────────────────

def test_pages_per_day_today_calculation(
    client: TestClient, make_book, admin_user, db: Session
):
    """pages_per_day.current sums pages_turned from today's sessions only."""
    user, _ = admin_user
    book = make_book(title="Pages Book")
    now = datetime.utcnow()

    # 50 pages today
    _add_session(db, user.id, book.id, now - timedelta(minutes=10), duration_seconds=600, pages_turned=50)
    # 30 pages yesterday — should NOT count
    _add_session(db, user.id, book.id, now - timedelta(days=1), duration_seconds=600, pages_turned=30)

    result = _create_goal(client, "pages_per_day", 100)
    assert result["kind"] == "pages_per_day"
    assert result["metric"] == "pages"
    assert result["period"] == "day"
    assert result["current"] == 50
    assert result["pct"] == pytest.approx(50.0, abs=0.1)
    assert "days_hit_this_week" in result


def test_pages_per_day_days_hit_this_week(
    client: TestClient, make_book, admin_user, db: Session
):
    """days_hit_this_week counts days in the last 7 where pages goal was met."""
    user, _ = admin_user
    book = make_book(title="Pages Week Book")
    now = datetime.utcnow()
    target_pages = 50

    # 2 days ago — hit (60 pages)
    _add_session(db, user.id, book.id, now - timedelta(days=2), pages_turned=60)
    # 4 days ago — hit exactly (50 pages)
    _add_session(db, user.id, book.id, now - timedelta(days=4), pages_turned=target_pages)
    # 1 day ago — miss (20 pages)
    _add_session(db, user.id, book.id, now - timedelta(days=1), pages_turned=20)

    result = _create_goal(client, "pages_per_day", target_pages)
    assert result["days_hit_this_week"] == 2


# ── tests — pages_per_week progress ──────────────────────────────────────────

def test_pages_per_week_sums_last_7_days(
    client: TestClient, make_book, admin_user, db: Session
):
    """pages_per_week sums pages_turned from the last 7 local days."""
    user, _ = admin_user
    book = make_book(title="Pages Per Week Book")
    now = datetime.utcnow()

    # 80 pages 2 days ago
    _add_session(db, user.id, book.id, now - timedelta(days=2), pages_turned=80)
    # 100 pages 5 days ago
    _add_session(db, user.id, book.id, now - timedelta(days=5), pages_turned=100)
    # 200 pages 9 days ago — outside window
    _add_session(db, user.id, book.id, now - timedelta(days=9), pages_turned=200)

    result = _create_goal(client, "pages_per_week", 350)
    assert result["kind"] == "pages_per_week"
    assert result["metric"] == "pages"
    assert result["period"] == "week"
    # In-window: 80 + 100 = 180
    assert result["current"] == 180
    assert "days_hit_this_week" not in result


# ── tests — goal_reached notifications ────────────────────────────────────────

def test_year_goal_reached_creates_notification_once(
    client: TestClient, make_book, admin_user, db: Session
):
    """Reaching a year goal notifies exactly once per window."""
    user, _ = admin_user
    now = datetime.utcnow()
    for i in range(2):
        book = make_book(title=f"Reached Book {i}")
        _finish_book(db, user.id, book.id, updated_at=now - timedelta(days=i + 1))

    goal = _create_goal(client, "books_per_year", 2)
    assert goal["current"] == 2

    notes = db.query(Notification).filter(Notification.kind == "goal_reached").all()
    assert len(notes) == 1
    assert notes[0].user_id == user.id
    assert "2 books" in (notes[0].body or "")
    assert notes[0].link == "/stats"

    # Re-reading the list must not create a second notification
    _list_goals(client)
    notes = db.query(Notification).filter(Notification.kind == "goal_reached").all()
    assert len(notes) == 1


def test_unreached_goal_does_not_notify(client: TestClient, db: Session):
    _create_goal(client, "books_per_year", 50)
    _list_goals(client)
    notes = db.query(Notification).filter(Notification.kind == "goal_reached").all()
    assert notes == []


def test_daily_goal_does_not_notify(
    client: TestClient, make_book, admin_user, db: Session
):
    """Day/week goals never create notifications (rolling windows would flood)."""
    user, _ = admin_user
    book = make_book(title="Daily Notify Book")
    now = datetime.utcnow()
    _add_session(db, user.id, book.id, now - timedelta(minutes=5), duration_seconds=3600)

    goal = _create_goal(client, "minutes_per_day", 10)
    assert goal["current"] >= 10

    notes = db.query(Notification).filter(Notification.kind == "goal_reached").all()
    assert notes == []


def test_raised_target_can_renotify(
    client: TestClient, make_book, admin_user, db: Session
):
    """Editing the target clears the notified marker; reaching the new target notifies again."""
    user, _ = admin_user
    now = datetime.utcnow()
    books = [make_book(title=f"Renotify Book {i}") for i in range(3)]
    for i, b in enumerate(books[:2]):
        _finish_book(db, user.id, b.id, updated_at=now - timedelta(days=i + 1))

    goal = _create_goal(client, "books_per_year", 2)  # reached → notification 1
    _update_goal(client, goal["id"], 3)  # raised above current → marker cleared
    _finish_book(db, user.id, books[2].id, updated_at=now)
    _list_goals(client)  # reached again → notification 2

    notes = db.query(Notification).filter(Notification.kind == "goal_reached").all()
    assert len(notes) == 2


# ── tests — per-user isolation ────────────────────────────────────────────────

def test_goals_are_per_user_isolated(
    client: TestClient, make_book, admin_user, db: Session
):
    """One user's goal is invisible to another user."""
    user, _ = admin_user

    # Create a second user
    other = User(
        username="other_user",
        email="other@example.com",
        hashed_password=hash_password("pass"),
        is_active=True,
        is_admin=False,
        role="member",
        must_change_password=False,
    )
    db.add(other)
    db.flush()
    db.add(UserPermission(user_id=other.id))
    db.flush()
    other_token = create_access_token(subject=other.id)

    # Admin sets a goal
    _create_goal(client, "books_per_year", 15)

    # Other user sees no goals
    from backend.main import create_app
    from backend.core.database import get_db

    app = create_app()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db

    from starlette.testclient import TestClient as TC
    with TC(app, raise_server_exceptions=True) as other_client:
        other_client.headers["Authorization"] = f"Bearer {other_token}"
        resp = other_client.get("/api/goals")
        assert resp.status_code == 200
        assert resp.json()["goals"] == []

    app.dependency_overrides.clear()


def test_progress_is_per_user(
    client: TestClient, make_book, admin_user, db: Session
):
    """Books finished by another user don't count toward the current user's goal."""
    user, _ = admin_user
    other = User(
        username="other2",
        email="other2@example.com",
        hashed_password=hash_password("pass"),
        is_active=True,
        is_admin=False,
        role="member",
        must_change_password=False,
    )
    db.add(other)
    db.flush()
    db.add(UserPermission(user_id=other.id))
    db.flush()

    book = make_book(title="Shared Book")
    now = datetime.utcnow()
    # Other user finished the book — admin user did not
    _finish_book(db, other.id, book.id, updated_at=now - timedelta(days=1))

    result = _create_goal(client, "books_per_year", 10)
    assert result["current"] == 0


def test_delete_other_users_goal_404(
    client: TestClient, admin_user, db: Session
):
    """Deleting a goal owned by another user is a 404, not a 204."""
    other = User(
        username="other3",
        email="other3@example.com",
        hashed_password=hash_password("pass"),
        is_active=True,
        is_admin=False,
        role="member",
        must_change_password=False,
    )
    db.add(other)
    db.flush()
    goal = ReadingGoal(user_id=other.id, kind="books_per_year", target=5)
    db.add(goal)
    db.flush()

    _delete_goal(client, goal.id, expected_status=404)
    assert db.query(ReadingGoal).filter(ReadingGoal.id == goal.id).first() is not None