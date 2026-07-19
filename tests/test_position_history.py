"""Position history + restore — the safety net under position sync."""
from datetime import datetime

from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from backend.models.tome_sync import PositionHistory
from backend.models.user_book_status import UserBookStatus
from backend.services.book_progress import HISTORY_KEEP, upsert_position


def _entries(db: Session, user_id: int, book_id: int) -> list[PositionHistory]:
    return (
        db.query(PositionHistory)
        .filter(PositionHistory.user_id == user_id, PositionHistory.book_id == book_id)
        .order_by(PositionHistory.id)
        .all()
    )


def test_heartbeat_noise_not_recorded(db: Session, admin_user, make_book):
    user, _ = admin_user
    book = make_book(title="Heartbeat Book")
    upsert_position(db, user_id=user.id, book_id=book.id,
                    percentage=0.42, progress="cfi(a)", device="Kindle")
    # Same position re-PUT (idle heartbeat) — no new entry.
    upsert_position(db, user_id=user.id, book_id=book.id,
                    percentage=0.42, progress="cfi(a)", device="Kindle")
    upsert_position(db, user_id=user.id, book_id=book.id,
                    percentage=0.4205, progress="cfi(a)", device="Kindle")  # sub-threshold
    db.flush()
    assert len(_entries(db, user.id, book.id)) == 1


def test_movement_recorded_and_pruned(db: Session, admin_user, make_book):
    user, _ = admin_user
    book = make_book(title="Mover Book")
    for i in range(HISTORY_KEEP + 15):
        upsert_position(db, user_id=user.id, book_id=book.id,
                        percentage=i / 100.0, progress=f"cfi({i})", device="Kindle")
    db.flush()
    rows = _entries(db, user.id, book.id)
    assert len(rows) == HISTORY_KEEP
    # Newest survive the prune.
    assert rows[-1].percentage == (HISTORY_KEEP + 14) / 100.0


def test_history_endpoint_lists_newest_first(client: TestClient, db: Session, admin_user, make_book):
    user, _ = admin_user
    book = make_book(title="Listed Book")
    for pct in (0.1, 0.5, 0.9):
        upsert_position(db, user_id=user.id, book_id=book.id,
                        percentage=pct, progress=f"cfi({pct})", device="Kindle")
    db.flush()
    r = client.get(f"/api/books/{book.id}/position-history")
    assert r.status_code == 200
    data = r.json()
    assert data["current"]["percentage"] == 0.9
    assert [h["percentage"] for h in data["history"]] == [0.9, 0.5, 0.1]


def test_restore_reverts_false_completion(client: TestClient, db: Session, admin_user, make_book):
    user, _ = admin_user
    book = make_book(title="Jumped Book")
    upsert_position(db, user_id=user.id, book_id=book.id,
                    percentage=0.45, progress="cfi(mid)", device="Kindle")
    # The bad sync: device reports 100%, book gets finished.
    upsert_position(db, user_id=user.id, book_id=book.id,
                    percentage=1.0, progress="cfi(end)", device="Kindle")
    db.add(UserBookStatus(user_id=user.id, book_id=book.id, status="read",
                          progress_pct=1.0, finished_at=datetime(2026, 7, 1)))
    db.flush()

    hid = next(h["id"] for h in client.get(f"/api/books/{book.id}/position-history").json()["history"]
               if h["percentage"] == 0.45)
    r = client.post(f"/api/books/{book.id}/position-history/{hid}/restore")
    assert r.status_code == 200
    assert r.json()["status"] == "reading"

    data = client.get(f"/api/books/{book.id}/position-history").json()
    assert data["current"]["percentage"] == 0.45
    assert data["current"]["device"] == "restore"
    status = db.query(UserBookStatus).filter_by(user_id=user.id, book_id=book.id).one()
    db.refresh(status)
    assert status.status == "reading"
    assert status.finished_at is None
    assert status.progress_pct == 0.45


def test_restore_rejects_foreign_entry(client: TestClient, db: Session, admin_user, make_book):
    from backend.core.security import hash_password
    from backend.models.user import User, UserPermission

    other = User(username="ph_other", email="ph_other@example.com",
                 hashed_password=hash_password("pass1234"), is_active=True,
                 is_admin=False, role="member", must_change_password=False)
    db.add(other)
    db.flush()
    db.add(UserPermission(user_id=other.id))
    book = make_book(title="Foreign Book")
    upsert_position(db, user_id=other.id, book_id=book.id,
                    percentage=0.3, progress="cfi(x)", device="Kindle")
    db.flush()
    foreign_id = _entries(db, other.id, book.id)[0].id
    # Admin (the client's default auth) must not restore another user's entry.
    r = client.post(f"/api/books/{book.id}/position-history/{foreign_id}/restore")
    assert r.status_code == 404
