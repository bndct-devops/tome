"""The Companion overview (``GET /api/ai/companion/overview``,
``backend/services/ai/overview.py``). Invented series only."""
from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from backend.models.ai_chapter_digest import AIChapterDigest
from backend.models.ai_entity import AIEntity
from backend.models.ai_job import AIJob
from backend.models.ai_recap import AIRecap
from backend.models.ai_usage import AIUsage
from backend.models.audit_log import AuditLog
from backend.models.book import Book, BookChapter
from backend.models.tome_sync import TomeSyncPosition
from backend.services.ai import digest, digest_job, overview, volume_recap
from tests.test_ai_digest import _epub_book, _headers, _make_user, _read, _store_digest


@pytest.fixture(autouse=True)
def _jobs_inline(db: Session, monkeypatch):
    @contextmanager
    def same_session():
        s = Session(bind=db.get_bind(), join_transaction_mode="create_savepoint", autoflush=False)
        try:
            yield s
        finally:
            s.close()

    digest_job.reset()
    monkeypatch.setattr(digest_job, "_spawn_worker", lambda: None)
    monkeypatch.setattr(digest_job, "_session_factory", same_session)
    yield
    digest_job.reset()


def _series(db, make_book, tmp_path, name: str, n: int = 3) -> list[Book]:
    return [_epub_book(db, make_book, tmp_path, f"{name} {i}", series=name, index=i)
            for i in range(1, n + 1)]


def _recap(db, vol: Book, digested: int, needed: int) -> None:
    db.add(AIRecap(book_id=vol.id, chapter_idx=needed, scope_hash=volume_recap.own_scope(vol).scope_hash,
                   position_fraction=1.0, text="x", model="m", digested=digested, needed=needed))
    db.flush()


def _entity(db, key: str, kind: str, name: str, merged_into: int | None = None) -> None:
    db.add(AIEntity(series_key=key, kind=kind, name=name, merged_into=merged_into))
    db.flush()


def _job(db, user, book, *, followups=(), upto=5, total=5, status="done",
         created=None, cost=0.0) -> AIJob:
    created = created or datetime.utcnow()
    row = AIJob(id=f"j{abs(hash((book.id, created, upto))) % 10**10:010d}"[:12], user_id=user.id,
                book_id=book.id, kind="digest", scope="series", status=status, upto_chapter=upto,
                total=total, done=total, cost_usd=cost, followups_json=json.dumps(list(followups)),
                created_at=created, finished_at=created + timedelta(seconds=30))
    db.add(row)
    db.flush()
    return row


def _row(payload: dict, key: str) -> dict:
    return next(r for r in payload["series"] if r["key"] == key)


def test_member_sees_only_their_series_with_counts(db, make_book, tmp_path):
    member = _make_user(db, "ovm", "member")
    other = _make_user(db, "ovo", "member")
    mine = _series(db, make_book, tmp_path, "Lantern Coast")
    theirs = _series(db, make_book, tmp_path, "Quiet Orchard", 2)
    _read(db, member, mine[0])
    db.add(TomeSyncPosition(user_id=member.id, book_id=mine[1].id, percentage=0.4))
    _read(db, other, theirs[0])
    for idx in range(4):
        _store_digest(db, mine[0], idx, [])
    db.add(AIChapterDigest(book_id=mine[0].id, chapter_idx=9, model=digest.FAILED_MODEL))
    _store_digest(db, mine[1], 0, [])
    _store_digest(db, theirs[0], 0, [])
    _recap(db, mine[0], 4, 4)
    _recap(db, mine[1], 1, 4)
    key = digest.series_key(mine[0])
    _entity(db, key, "person", "Ada")
    _entity(db, key, "person", "Bren")
    _entity(db, key, "person", "Ada Twin", merged_into=1)
    _entity(db, key, "place", "Pier")
    _entity(db, key, "term", "Tidecall")

    out = overview.overview(db, member)
    assert [r["key"] for r in out["series"]] == [key]
    row = out["series"][0]
    assert row["name"] == "Lantern Coast"
    assert row["author"] == "Test Author"
    assert row["readers"] == []
    assert (row["people"], row["places"], row["terms"]) == (2, 1, 1)
    vols = row["volumes"]
    assert [v["book_id"] for v in vols] == [b.id for b in mine]
    assert vols[0]["chapters"] == 4
    assert (vols[0]["digested"], vols[0]["failed"]) == (4, 1)
    assert vols[0]["last_digest_at"] is not None
    assert (vols[1]["digested"], vols[2]["digested"]) == (1, 0)
    assert [v["recap"] for v in vols] == ["ready", "partial", "missing"]
    assert row["anchor_book_id"] == mine[1].id     # the highest volume with a position
    assert row["last_job"] is None and row["last_merge"] is None and row["active_job"] is None


def test_volume_without_chapters_has_recap_none(db, make_book, tmp_path):
    member = _make_user(db, "ovn", "member")
    first = _epub_book(db, make_book, tmp_path, "Plain Lantern 1", series="Lantern Plain", index=1)
    vol = make_book(title="Plain Lantern 2", series="Lantern Plain", series_index=2)
    _read(db, member, first)
    row = overview.overview(db, member)["series"][0]
    assert row["volumes"][1]["book_id"] == vol.id
    assert row["volumes"][1]["chapters"] == 0
    assert row["volumes"][1]["recap"] == "none"


def test_series_without_chapter_text_has_no_row(db, make_book, admin_user):
    member = _make_user(db, "ovt", "member")
    vol = make_book(title="Scanned Lantern", series="Lantern Scans", series_index=1)
    _read(db, member, vol)
    _store_digest(db, vol, 0, [])       # a stray digest does not make it readable
    assert overview.overview(db, member)["series"] == []
    assert overview.overview(db, admin_user[0])["series"] == []


def test_admin_sees_every_digested_series_with_readers(client, db, admin_user, make_book, tmp_path):
    a = _make_user(db, "ova", "member")
    b = _make_user(db, "ovb", "member")
    s1 = _series(db, make_book, tmp_path, "Lantern Coast")
    s2 = _series(db, make_book, tmp_path, "Quiet Orchard", 2)
    _series(db, make_book, tmp_path, "Unread Hollow", 1)    # no digest: no row
    _read(db, a, s1[0])
    db.add(TomeSyncPosition(user_id=b.id, book_id=s1[1].id, percentage=0.2))
    _store_digest(db, s1[0], 0, [])
    _store_digest(db, s1[1], 0, [])
    _store_digest(db, s2[0], 0, [])
    db.commit()

    res = client.get("/api/ai/companion/overview")
    assert res.status_code == 200
    keys = {r["key"] for r in res.json()["series"]}
    assert keys == {digest.series_key(s1[0]), digest.series_key(s2[0])}
    row = _row(res.json(), digest.series_key(s1[0]))
    assert [r["username"] for r in row["readers"]] == ["ova", "ovb"]
    assert row["anchor_book_id"] == s1[1].id      # highest volume with a digest
    assert row["in_scope"] is False               # the admin reads none of it
    assert _row(res.json(), digest.series_key(s2[0]))["readers"] == []

    # a member only gets their own
    res = client.get("/api/ai/companion/overview", headers=_headers(a))
    assert [r["key"] for r in res.json()["series"]] == [digest.series_key(s1[0])]
    assert res.json()["series"][0]["readers"] == []
    assert res.json()["series"][0]["in_scope"] is True
    # guests are refused
    guest = _make_user(db, "ovg", "guest", key=False)
    db.commit()
    assert client.get("/api/ai/companion/overview", headers=_headers(guest)).status_code == 403


def test_admin_anchor_is_their_own_volume_first(db, admin_user, make_book, tmp_path):
    admin = admin_user[0]
    reader = _make_user(db, "ovr", "member")
    vols = _series(db, make_book, tmp_path, "Lantern Coast")
    _store_digest(db, vols[2], 0, [])     # someone read volume 3
    _read(db, reader, vols[2])
    _read(db, admin, vols[0])             # the admin is on volume 1
    row = _row(overview.overview(db, admin), digest.series_key(vols[0]))
    assert row["anchor_book_id"] == vols[0].id
    assert row["in_scope"] is True


def test_standalone_book_is_its_own_row(db, make_book, tmp_path):
    member = _make_user(db, "ovs", "member")
    book = _epub_book(db, make_book, tmp_path, "The Salt Ledger")
    _read(db, member, book)
    _store_digest(db, book, 0, [])
    row = overview.overview(db, member)["series"][0]
    assert row["key"] == f"book:{book.id}"
    assert row["name"] == "The Salt Ledger"
    assert [v["book_id"] for v in row["volumes"]] == [book.id]
    assert row["anchor_book_id"] == book.id


def test_spend_sums_this_month_for_the_viewer(db, make_book, tmp_path, admin_user):
    member = _make_user(db, "ovp", "member")
    other = _make_user(db, "ovq", "member")
    vols = _series(db, make_book, tmp_path, "Lantern Coast", 2)
    _read(db, member, vols[0])
    job = _job(db, member, vols[1], cost=0.5)
    now = datetime.utcnow()

    def use(user, cost, *, book=None, job_id=None, when=now):
        db.add(AIUsage(user_id=user.id, feature="reading_digest", model="m", key_source="user",
                       cost_usd=cost, created_at=when, book_id=book.id if book else None, job_id=job_id))

    use(member, 0.25, book=vols[0])
    use(member, 0.5, job_id=job.id)                              # job row without a book
    use(member, 0.1, book=vols[1], when=now - timedelta(days=40))  # last month
    use(other, 2.0, book=vols[0])
    db.flush()

    row = overview.overview(db, member)["series"][0]
    assert row["spend_month_usd"] == pytest.approx(0.75)
    admin = admin_user[0]
    _store_digest(db, vols[0], 0, [])     # the admin sees series with a digest only
    arow = _row(overview.overview(db, admin), digest.series_key(vols[0]))
    assert arow["spend_month_usd"] == pytest.approx(2.75)


def test_last_job_kinds_and_last_merge(db, make_book, tmp_path):
    member = _make_user(db, "ovk", "member")
    vols = _series(db, make_book, tmp_path, "Lantern Coast", 2)
    _read(db, member, vols[0])
    t0 = datetime.utcnow() - timedelta(hours=3)
    merge = _job(db, member, vols[1], upto=0, total=0, created=t0, followups=[
        {"name": "reconcile", "status": "done", "candidates": 7, "merged": 3}])
    out = overview.overview(db, member)["series"][0]
    assert out["last_job"]["kind"] == "merge"
    assert out["last_merge"] == {"finished_at": merge.finished_at.isoformat() + "Z",
                                 "candidates": 7, "merged": 3}

    _job(db, member, vols[1], created=t0 + timedelta(hours=1), cost=0.41, followups=[
        {"name": "reconcile", "status": "done", "candidates": 0, "merged": 0},
        {"name": "volume_recaps", "status": "done"}])
    out = overview.overview(db, member)["series"][0]
    assert out["last_job"]["kind"] == "write_all"
    assert out["last_job"]["cost_usd"] == pytest.approx(0.41)
    assert out["last_merge"]["candidates"] == 0     # the newest finished merge step

    t2 = t0 + timedelta(hours=2)
    db.add(AuditLog(user_id=member.id, action="ai.reread", resource_type="book",
                    resource_id=vols[1].id, created_at=t2))
    _job(db, member, vols[1], created=t2 + timedelta(seconds=1), followups=[
        {"name": "reconcile", "status": "pending"}], status="cancelled")
    out = overview.overview(db, member)["series"][0]
    assert out["last_job"]["kind"] == "reread"
    assert out["last_job"]["status"] == "cancelled"

    # Rows from Read again carry the kind themselves; no audit row needed.
    tagged = _job(db, member, vols[1], created=t2 + timedelta(minutes=2))
    tagged.kind = "reread"
    db.flush()
    assert overview.overview(db, member)["series"][0]["last_job"]["kind"] == "reread"

    _job(db, member, vols[1], created=t2 + timedelta(minutes=5))
    assert overview.overview(db, member)["series"][0]["last_job"]["kind"] == "read"


def test_active_job_for_the_viewer(db, make_book, tmp_path):
    member = _make_user(db, "ovj", "member")
    other = _make_user(db, "ovx", "member")
    vols = _series(db, make_book, tmp_path, "Lantern Coast", 2)
    _read(db, member, vols[0])
    _read(db, other, vols[0])
    snap = digest_job.enqueue(user_id=member.id, book_id=vols[1].id, upto=2)
    row = overview.overview(db, member)["series"][0]
    assert row["active_job"]["id"] == snap["id"]
    assert row["last_job"] is None                  # live jobs are not "last"
    assert overview.overview(db, other)["series"][0]["active_job"] is None


def test_cap_at_max_series(db, admin_user, monkeypatch):
    monkeypatch.setattr(overview, "MAX_SERIES", 5)
    for i in range(7):
        b = Book(title=f"Loose Page {i}", status="active", added_by=admin_user[0].id)
        db.add(b)
        db.flush()
        db.add(BookChapter(book_id=b.id, idx=0, title="One", start_fraction=0.0, end_fraction=1.0))
        _store_digest(db, b, 0, [])
    assert len(overview.overview(db, admin_user[0])["series"]) == 5
    monkeypatch.setattr(overview, "MAX_SERIES", 200)
    assert len(overview.overview(db, admin_user[0])["series"]) == 7
