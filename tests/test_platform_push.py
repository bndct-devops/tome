"""Nightlamp link + push (backend/api/platform.py, backend/services/platform_push.py).

Nightlamp is mocked with respx; the real service is never called.
"""
import base64
import io
import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx
from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.core.crypto import decrypt_secret, encrypt_secret
from backend.core.security import create_access_token, hash_password
from backend.models.audit_log import AuditLog
from backend.models.library import BookType
from backend.models.platform import PlatformCoverSent, PlatformLink
from backend.models.tome_sync import ReadingSession, TomeSyncPosition
from backend.models.user import User, UserPermission
from backend.models.user_book_status import UserBookStatus
from backend.services import platform_push as pp

NL = "http://nightlamp.test"
UTC = ZoneInfo("UTC")
VIENNA = ZoneInfo("Europe/Vienna")


@pytest.fixture(autouse=True)
def _platform_on(monkeypatch, db: Session):
    monkeypatch.setattr(settings, "platform_url", NL)
    monkeypatch.setattr(settings, "platform_enabled", True)
    # Pushes run inline on the test session instead of a thread.
    monkeypatch.setattr(pp, "spawn_push", lambda user_id, full=False: pp.push_user(db, user_id, full=full))
    pp.clear_dirty()
    from backend.api import platform as api
    api._pending.clear()
    yield
    pp.clear_dirty()


def _utc(local: datetime, tz: ZoneInfo) -> datetime:
    """A naive UTC datetime for a local wall time (how sessions are stored)."""
    return local.replace(tzinfo=tz).astimezone(ZoneInfo("UTC")).replace(tzinfo=None)


def _session(db, user_id, start: datetime, minutes: float, book_id=None, ended=True):
    s = ReadingSession(user_id=user_id, book_id=book_id, started_at=start,
                       ended_at=start + timedelta(minutes=minutes) if ended else None,
                       duration_seconds=int(minutes * 60))
    db.add(s)
    db.flush()
    return s


def _member(db: Session, name="member1", role="member") -> tuple[User, str]:
    u = User(username=name, email=f"{name}@example.com", hashed_password=hash_password("pass"),
             is_active=True, is_admin=False, role=role, must_change_password=False)
    db.add(u)
    db.flush()
    db.add(UserPermission(user_id=u.id))
    db.flush()
    return u, create_access_token(subject=u.id)


def _link(db, user, tz="Europe/Vienna", share=False, token="nl_abcdefgh_" + "x" * 43, **kw) -> PlatformLink:
    link = PlatformLink(user_id=user.id, handle="reader-owl", push_token_enc=encrypt_secret(token),
                        timezone=tz, share_titles=share, linked_at=datetime.utcnow(), **kw)
    db.add(link)
    db.flush()
    return link


def _days(body) -> dict[str, dict]:
    return {d["date"]: d for d in body["days"]}


# ── midnight split ───────────────────────────────────────────────────────────

def test_session_across_midnight_vienna_splits_by_time():
    start = datetime(2026, 3, 10, 22, 0, tzinfo=VIENNA).astimezone(ZoneInfo("UTC"))
    end = start + timedelta(hours=3)
    segs = pp.split_by_midnight(start, end, 3 * 3600, VIENNA)
    assert [(s.day, round(s.seconds / 60)) for s in segs] == [(date(2026, 3, 10), 120), (date(2026, 3, 11), 60)]


def test_dst_night_counts_real_minutes():
    # 2026-10-25: Vienna falls back 03:00 CEST -> 02:00 CET. 00:30 CEST to
    # 03:30 CET is four real hours, all on the 25th.
    start = datetime(2026, 10, 24, 22, 30, tzinfo=ZoneInfo("UTC"))
    end = datetime(2026, 10, 25, 2, 30, tzinfo=ZoneInfo("UTC"))
    segs = pp.split_by_midnight(start, end, 4 * 3600, VIENNA)
    assert [(s.day, round(s.seconds / 60)) for s in segs] == [(date(2026, 10, 25), 240)]
    # 23:00 CEST on the 24th for two hours: 60 + 60 across midnight.
    s2 = datetime(2026, 10, 24, 21, 0, tzinfo=ZoneInfo("UTC"))
    segs2 = pp.split_by_midnight(s2, s2 + timedelta(hours=2), 7200, VIENNA)
    assert [(s.day, round(s.seconds / 60)) for s in segs2] == [(date(2026, 10, 24), 60), (date(2026, 10, 25), 60)]


def test_auckland_near_midnight_utc():
    # 11:30-12:30 UTC on Jan 10 = 00:30-01:30 NZDT on Jan 11 (UTC+13).
    start = datetime(2026, 1, 10, 11, 30, tzinfo=ZoneInfo("UTC"))
    segs = pp.split_by_midnight(start, start + timedelta(hours=1), 3600, ZoneInfo("Pacific/Auckland"))
    assert [(s.day, round(s.seconds / 60)) for s in segs] == [(date(2026, 1, 11), 60)]
    # 10:30-11:30 UTC = 23:30-00:30 NZDT: 30 + 30.
    s2 = datetime(2026, 1, 10, 10, 30, tzinfo=ZoneInfo("UTC"))
    segs2 = pp.split_by_midnight(s2, s2 + timedelta(hours=1), 3600, ZoneInfo("Pacific/Auckland"))
    assert [(s.day, round(s.seconds / 60)) for s in segs2] == [(date(2026, 1, 10), 30), (date(2026, 1, 11), 30)]


def test_aggregate_session_without_end_uses_duration_and_skips_empty(db, admin_user):
    user, _ = admin_user
    _session(db, user.id, _utc(datetime(2026, 3, 10, 22, 0), VIENNA), 180, ended=False)
    db.add(ReadingSession(user_id=user.id, started_at=datetime(2026, 3, 1, 10), ended_at=None, duration_seconds=None))
    db.flush()
    agg = pp.aggregate(db, user, VIENNA)
    assert agg.days[date(2026, 3, 10)]["minutes_read"] == 120
    assert agg.days[date(2026, 3, 11)]["minutes_read"] == 60
    assert date(2026, 3, 1) not in agg.days


# ── sittings, types, finished ────────────────────────────────────────────────

def test_sittings_merge_under_15_minutes_and_types(db, admin_user, make_book):
    user, _ = admin_user
    manga = BookType(slug="manga", label="Manga")
    db.add(manga)
    db.flush()
    novel = make_book(title="A Novel")
    comic = make_book(title="A Manga")
    comic.book_type_id = manga.id
    db.flush()
    day = datetime(2026, 5, 2, 9, 0)
    _session(db, user.id, _utc(day, VIENNA), 30, novel.id)                                # 09:00-09:30
    _session(db, user.id, _utc(day + timedelta(minutes=40), VIENNA), 20, comic.id)       # 09:40-10:00 (gap 10)
    _session(db, user.id, _utc(day + timedelta(hours=3), VIENNA), 45, novel.id)          # 12:00-12:45 (gap 2h)
    agg = pp.aggregate(db, user, VIENNA)
    d = agg.days[date(2026, 5, 2)]
    assert d["minutes_read"] == 95
    assert d["sittings"] == 2
    assert d["longest_sitting_minutes"] == 50
    assert d["minutes_by_type"] == {"manga": 20, "other": 75}


def test_finished_book_lands_on_local_day_and_respects_visibility(db, admin_user, make_book):
    user, _ = admin_user
    member, _tok = _member(db)
    book = make_book(title="Done")
    # 23:30 UTC on May 3 = 01:30 Vienna on May 4.
    db.add(UserBookStatus(user_id=member.id, book_id=book.id, status="read",
                          finished_at=datetime(2026, 5, 3, 23, 30)))
    _session(db, member.id, datetime(2026, 5, 3, 20), 30, book.id)
    db.flush()
    agg = pp.aggregate(db, member, VIENNA)
    assert agg.finished_by_day == {date(2026, 5, 4): [book.id]}
    # Put the book in a private library of the admin: the member no longer sees it.
    from backend.models.library import Library
    lib = Library(name="Private", is_public=False, owner_id=user.id)
    db.add(lib)
    db.flush()
    book.libraries.append(lib)
    db.flush()
    assert pp.aggregate(db, member, VIENNA).finished_by_day == {}


def test_current_prefers_newest_activity_and_reports_fields(db, admin_user, make_book):
    user, _ = admin_user
    a = make_book(title="Older status")
    b = make_book(title="Newer position", series="Saga", series_index=2)
    now = datetime.utcnow()
    db.add(UserBookStatus(user_id=user.id, book_id=a.id, status="reading", progress_pct=0.3,
                          updated_at=now - timedelta(hours=1)))
    db.add(UserBookStatus(user_id=user.id, book_id=b.id, status="reading", progress_pct=0.62,
                          updated_at=now - timedelta(days=2)))
    db.add(TomeSyncPosition(user_id=user.id, book_id=b.id, percentage=0.62, updated_at=now))
    _session(db, user.id, datetime(2026, 4, 1, 8), 30, b.id)
    _session(db, user.id, datetime(2026, 4, 1, 8, 40), 30, b.id)
    _session(db, user.id, datetime(2026, 4, 3, 8), 10, b.id)
    db.flush()
    link = _link(db, user, tz="UTC", share=True)
    env = pp.build_envelope(db, user, link, full=True, today=date(2026, 4, 3))
    cur = env.body["current"]
    _iid, secret = pp.instance_identity(db)
    assert cur["key"] == pp.book_key(secret, b.id)
    assert cur["title"] == "Newer position" and cur["series"] == "Saga" and cur["series_index"] == 2
    assert cur["progress_pct"] == 62
    assert cur["minutes_in"] == 70
    assert cur["started"] == "2026-04-01"
    assert cur["sittings"] == 2


def test_current_is_null_without_reading_book_and_absent_without_titles(db, admin_user):
    user, _ = admin_user
    link = _link(db, user, tz="UTC", share=True)
    assert pp.build_envelope(db, user, link, full=True).body["current"] is None
    link.share_titles = False
    assert "current" not in pp.build_envelope(db, user, link, full=True).body


# ── keys, isbn and covers ────────────────────────────────────────────────────

def _cover(tmp_path, monkeypatch, name="c.jpg", size=(600, 900), noise=False) -> str:
    from PIL import Image
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    (tmp_path / "covers").mkdir(exist_ok=True)
    if noise:
        import os
        img = Image.frombytes("RGB", size, os.urandom(size[0] * size[1] * 3))
    else:
        img = Image.new("RGB", size, (120, 40, 40))
    img.save(tmp_path / "covers" / name, "JPEG", quality=95)
    return name


def _jpeg(b64: str):
    from PIL import Image
    raw = base64.b64decode(b64)
    assert raw[:3] == b"\xff\xd8\xff"
    return raw, Image.open(io.BytesIO(raw))


def test_keys_are_hashes_isbn_and_cover_go_once_and_again_on_resync(db, admin_user, make_book, tmp_path,
                                                                     monkeypatch):
    user, _ = admin_user
    book = make_book(title="Hail", isbn="978-0-593-13520-4", cover_path=_cover(tmp_path, monkeypatch))
    db.add(UserBookStatus(user_id=user.id, book_id=book.id, status="read", finished_at=datetime(2026, 4, 2, 12)))
    _session(db, user.id, datetime(2026, 4, 2, 10), 60, book.id)
    _link(db, user, tz="UTC", share=True)
    bodies = []
    with respx.mock(base_url=NL) as mock:
        mock.post("/api/push").mock(side_effect=lambda req: (bodies.append(json.loads(req.content)),
                                                             httpx.Response(200, json={"accepted": 1, "rejected": [], "flagged": 0}))[1])
        assert pp.push_user(db, user.id, full=True) == "ok"
        assert pp.push_user(db, user.id, full=False, since=date(2026, 4, 2)) == "ok"
        _iid, secret = pp.instance_identity(db)
        key = pp.book_key(secret, book.id)
        assert key != str(book.id) and len(key) == 64
        first = _days(bodies[0])["2026-04-02"]["finished"][0]
        b64 = first.pop("cover_jpeg_b64")
        assert first == {"key": key, "title": "Hail", "author": book.author, "series": book.series,
                         "series_index": book.series_index, "isbn": "9780593135204", "minutes_total": 60}
        raw, img = _jpeg(b64)
        assert img.width == 160 and len(raw) <= pp.MAX_COVER
        second = _days(bodies[1])["2026-04-02"]["finished"][0]
        assert second["isbn"] == "9780593135204" and "cover_jpeg_b64" not in second
        assert db.query(PlatformCoverSent).filter_by(user_id=user.id, book_key=key).count() == 1
        # "Resync everything" sends it again.
        assert pp.push_user(db, user.id, full=True) == "ok"
        assert "cover_jpeg_b64" in _days(bodies[2])["2026-04-02"]["finished"][0]
        assert db.query(PlatformCoverSent).filter_by(user_id=user.id).count() == 1
        sent = bodies[0]
        assert sent["schema_version"] == 1 and sent["timezone"] == "UTC" and sent["share_titles"] is True
        assert mock.calls[0].request.headers["authorization"].startswith("Bearer nl_")


def test_current_carries_isbn_and_cover_once(db, admin_user, make_book, tmp_path, monkeypatch):
    user, _ = admin_user
    book = make_book(title="Reading", isbn="0-306-40615-2", cover_path=_cover(tmp_path, monkeypatch))
    db.add(UserBookStatus(user_id=user.id, book_id=book.id, status="reading", progress_pct=0.3))
    _session(db, user.id, datetime(2026, 4, 2, 10), 30, book.id)
    _link(db, user, tz="UTC", share=True)
    db.commit()
    with respx.mock(base_url=NL) as mock:
        route = mock.post("/api/push").mock(return_value=httpx.Response(200, json={"accepted": 1}))
        assert pp.push_user(db, user.id, full=True) == "ok"
        assert pp.push_user(db, user.id) == "ok"
        cur1 = json.loads(route.calls[0].request.content)["current"]
        cur2 = json.loads(route.calls[1].request.content)["current"]
    assert cur1["isbn"] == "0306406152" and _jpeg(cur1["cover_jpeg_b64"])[1].width == 160
    assert cur2["isbn"] == "0306406152" and "cover_jpeg_b64" not in cur2


def test_no_cover_when_the_book_has_none_or_the_file_is_gone(db, admin_user, make_book, tmp_path, monkeypatch):
    user, _ = admin_user
    _cover(tmp_path, monkeypatch)
    bare = make_book(title="Bare")
    gone = make_book(title="Gone", cover_path="missing.jpg")
    for b in (bare, gone):
        db.add(UserBookStatus(user_id=user.id, book_id=b.id, status="read", finished_at=datetime(2026, 4, 2, 12)))
        _session(db, user.id, datetime(2026, 4, 2, 10), 30, b.id)
    db.commit()
    link = _link(db, user, tz="UTC", share=True)
    env = pp.build_envelope(db, user, link, full=True, today=date(2026, 4, 2))
    items = _days(env.body)["2026-04-02"]["finished"]
    assert len(items) == 2 and all("cover_jpeg_b64" not in i for i in items)
    assert env.cover_keys == set()


def test_cover_stays_under_the_size_limit(make_book, tmp_path, monkeypatch):
    # Random noise compresses badly: quality steps down until it fits.
    name = _cover(tmp_path, monkeypatch, name="noise.jpg", size=(400, 2000), noise=True)
    book = make_book(title="Noise", cover_path=name)
    monkeypatch.setattr(pp, "MAX_COVER", 40 * 1024)
    b64 = pp.cover_jpeg_b64(book)
    assert b64 is not None
    raw, img = _jpeg(b64)
    assert len(raw) <= 40 * 1024 and img.width == 160
    monkeypatch.setattr(pp, "MAX_COVER", 100)  # impossible: no cover rather than an oversized one
    assert pp.cover_jpeg_b64(book) is None


def test_no_covers_without_share_titles(db, admin_user, make_book, tmp_path, monkeypatch):
    user, _ = admin_user
    book = make_book(title="Hidden", cover_path=_cover(tmp_path, monkeypatch))
    db.add(UserBookStatus(user_id=user.id, book_id=book.id, status="read", finished_at=datetime(2026, 4, 2, 12)))
    _session(db, user.id, datetime(2026, 4, 2, 10), 30, book.id)
    db.commit()
    link = _link(db, user, tz="UTC", share=False)
    env = pp.build_envelope(db, user, link, full=True, today=date(2026, 4, 2))
    assert "cover" not in json.dumps(env.body) and env.cover_keys == set()


def test_rejected_cover_is_not_marked_sent(db, admin_user, make_book, tmp_path, monkeypatch):
    user, _ = admin_user
    book = make_book(title="Bad", cover_path=_cover(tmp_path, monkeypatch))
    db.add(UserBookStatus(user_id=user.id, book_id=book.id, status="read", finished_at=datetime(2026, 4, 2, 12)))
    _session(db, user.id, datetime(2026, 4, 2, 10), 30, book.id)
    _link(db, user, tz="UTC", share=True)
    db.commit()
    _iid, secret = pp.instance_identity(db)
    key = pp.book_key(secret, book.id)
    with respx.mock(base_url=NL) as mock:
        mock.post("/api/push").mock(return_value=httpx.Response(200, json={
            "accepted": 1, "rejected": [{"key": key, "reason": "cover_invalid"}]}))
        assert pp.push_user(db, user.id, full=True) == "ok"
    assert db.query(PlatformCoverSent).filter_by(user_id=user.id).count() == 0


# ── ranges ───────────────────────────────────────────────────────────────────

def test_incremental_range_vs_resync_range(db, admin_user):
    user, _ = admin_user
    _session(db, user.id, datetime(2026, 4, 1, 10), 30)
    _session(db, user.id, datetime(2026, 4, 9, 10), 30)
    link = _link(db, user, tz="UTC")
    full = pp.build_envelope(db, user, link, full=True, today=date(2026, 4, 10))
    assert [d["date"] for d in full.body["days"]][0] == "2026-04-01"
    assert len(full.body["days"]) == 10
    link.pushed_through = date(2026, 4, 9)
    inc = pp.build_envelope(db, user, link, full=False, today=date(2026, 4, 10))
    assert [d["date"] for d in inc.body["days"]] == ["2026-04-08", "2026-04-09", "2026-04-10"]
    assert _days(inc.body)["2026-04-08"]["minutes_read"] == 0  # zeros go as tombstones
    # A session written for an older day widens the range.
    inc2 = pp.build_envelope(db, user, link, full=False, since=date(2026, 4, 1), today=date(2026, 4, 10))
    assert inc2.body["days"][0]["date"] == "2026-04-01"


def test_chunking_splits_large_bodies_and_keeps_current_last(monkeypatch):
    monkeypatch.setattr(pp, "BODY_BUDGET", 5000)
    body = {"schema_version": 1, "timezone": "UTC", "share_titles": True,
            "days": [{"date": f"d{i:03d}"} for i in range(450)], "current": None}
    parts = pp.chunk_bodies(body)
    assert [len(p["days"]) for p in parts] == [200, 200, 50]
    assert "current" not in parts[0] and parts[-1]["current"] is None


def test_chunking_by_size_keeps_cover_heavy_bodies_under_the_limit(monkeypatch):
    monkeypatch.setattr(pp, "BODY_BUDGET", 20_000)
    cover = "A" * 6000
    days = [{"date": f"d{i:03d}", "finished": [{"key": str(i), "cover_jpeg_b64": cover}] if i % 3 == 0 else []}
            for i in range(60)]
    body = {"schema_version": 1, "timezone": "UTC", "share_titles": True, "days": days,
            "current": {"key": "c", "cover_jpeg_b64": cover}}
    parts = pp.chunk_bodies(body)
    assert len(parts) > 1
    assert [d for p in parts for d in p["days"]] == days
    assert all(len(json.dumps(p, separators=(",", ":"))) <= 20_000 for p in parts)
    assert parts[-1]["current"] == body["current"] and all("current" not in p for p in parts[:-1])


# ── push outcomes ────────────────────────────────────────────────────────────

def test_401_marks_revoked_and_stops(db, admin_user):
    user, _ = admin_user
    _session(db, user.id, datetime(2026, 4, 1, 10), 30)
    link = _link(db, user, tz="UTC")
    with respx.mock(base_url=NL) as mock:
        route = mock.post("/api/push").mock(return_value=httpx.Response(401, json={"error": "revoked"}))
        assert pp.push_user(db, user.id, full=True) == "revoked"
        assert link.revoked_at is not None
        assert pp.push_user(db, user.id) == "skipped"
        assert route.call_count == 1


def test_other_errors_are_stored_and_retried(db, admin_user):
    user, _ = admin_user
    link = _link(db, user, tz="UTC")
    with respx.mock(base_url=NL) as mock:
        mock.post("/api/push").mock(side_effect=[httpx.Response(500), httpx.Response(200, json={"accepted": 0})])
        assert pp.push_user(db, user.id, full=True) == "error"
        assert "500" in link.last_push_error and link.revoked_at is None
        assert pp.push_user(db, user.id, full=True) == "ok"
        assert link.last_push_error is None and link.last_push_at is not None
        assert link.pushed_through is not None


# ── triggers ─────────────────────────────────────────────────────────────────

def test_session_write_marks_user_dirty_and_tick_debounces(db, admin_user):
    user, _ = admin_user
    _link(db, user, tz="UTC")
    db.commit()
    _session(db, user.id, datetime(2026, 4, 1, 10), 30)
    db.commit()
    assert user.id in pp._dirty
    with respx.mock(base_url=NL) as mock:
        route = mock.post("/api/push").mock(return_value=httpx.Response(200, json={"accepted": 1}))
        import time
        assert pp.tick(db, now_mono=time.monotonic()) == []        # under 2 minutes: wait
        assert pp.tick(db, now_mono=time.monotonic() + 121) == [user.id]
        assert route.call_count == 1
        # The session was for April 1: the range reaches back to it.
        assert json.loads(route.calls[0].request.content)["days"][0]["date"] == "2026-04-01"


def test_status_change_to_read_marks_dirty(db, admin_user, make_book):
    user, _ = admin_user
    book = make_book()
    row = UserBookStatus(user_id=user.id, book_id=book.id, status="unread")
    db.add(row)
    db.commit()
    pp.clear_dirty()
    row.status = "read"
    db.commit()
    assert user.id in pp._dirty


def test_hourly_sweep_pushes_stale_links(db, admin_user):
    user, _ = admin_user
    _link(db, user, tz="UTC", last_push_at=datetime.utcnow() - timedelta(hours=2))
    with respx.mock(base_url=NL) as mock:
        route = mock.post("/api/push").mock(return_value=httpx.Response(200, json={"accepted": 0}))
        assert pp.tick(db, sweep=True) == [user.id]
        assert pp.tick(db, sweep=True) == []  # just pushed
        assert route.call_count == 1


# ── API ──────────────────────────────────────────────────────────────────────

def test_default_url_is_nightlamp_page_and_active(monkeypatch):
    from backend.core.config import Settings
    monkeypatch.delenv("TOME_PLATFORM_URL", raising=False)
    monkeypatch.delenv("TOME_PLATFORM_ENABLED", raising=False)
    s = Settings(_env_file=None)
    assert s.platform_url == "https://nightlamp.page"
    assert s.platform_enabled is True and s.platform_active is True


def test_platform_enabled_false_env_turns_it_off(monkeypatch):
    from backend.core.config import Settings
    monkeypatch.delenv("TOME_PLATFORM_URL", raising=False)
    monkeypatch.setenv("TOME_PLATFORM_ENABLED", "false")
    s = Settings(_env_file=None)
    assert s.platform_url == "https://nightlamp.page" and s.platform_active is False


def test_disabled_hides_api_and_starts_no_job(db, admin_user, monkeypatch):
    from starlette.testclient import TestClient
    from backend.core.database import get_db
    from backend.main import create_app
    monkeypatch.setattr(settings, "platform_enabled", False)
    started = []

    async def _fake_loop():
        started.append(True)

    monkeypatch.setattr(pp, "platform_loop", _fake_loop)
    app = create_app()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    _user, token = admin_user
    with TestClient(app) as c:
        c.headers["Authorization"] = f"Bearer {token}"
        assert c.get("/api/platform/status").status_code == 404
    assert started == []
    # And the same app with the switch on does start it.
    monkeypatch.setattr(settings, "platform_enabled", True)
    with TestClient(app) as c:
        assert c.get("/api/platform/status", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    assert started == [True]


def test_feature_off_404_and_no_dirty(client, db, admin_user, monkeypatch):
    monkeypatch.setattr(settings, "platform_enabled", False)
    assert client.get("/api/platform/status").status_code == 404
    assert client.post("/api/platform/link", json={"timezone": "UTC"}).status_code == 404
    user, _ = admin_user
    _session(db, user.id, datetime(2026, 4, 1, 10), 30)
    db.commit()
    assert pp._dirty == {}


def test_guest_gets_403(client, db):
    _guest, token = _member(db, "guest1", role="guest")
    client.headers["Authorization"] = f"Bearer {token}"
    assert client.get("/api/platform/status").status_code == 403


def test_link_flow_poll_stores_token_and_pushes(client, db, admin_user):
    user, _ = admin_user
    _session(db, user.id, datetime(2026, 4, 1, 10), 30)
    db.commit()
    with respx.mock(base_url=NL) as mock:
        req = mock.post("/api/link/request").mock(return_value=httpx.Response(200, json={
            "request_id": "r1", "poll_secret": "ps", "url": f"{NL}/link?t=tok", "expires_at": "x"}))
        poll = mock.get("/api/link/request/r1").mock(side_effect=[
            httpx.Response(200, json={"status": "pending"}),
            httpx.Response(200, json={"status": "done", "handle": "testadmin-owl",
                                      "push_token": "nl_abcdefgh_" + "y" * 43}),
        ])
        push = mock.post("/api/push").mock(return_value=httpx.Response(200, json={"accepted": 1}))
        r = client.post("/api/platform/link", json={"timezone": "Europe/Vienna"})
        assert r.status_code == 200 and r.json() == {"url": f"{NL}/link?t=tok", "request_id": "r1"}
        sent = json.loads(req.calls[0].request.content)
        iid, secret = pp.instance_identity(db)
        assert sent["instance"]["id"] == iid and len(iid) == 22 and len(secret) == 43
        assert sent["instance"]["name_hint"] == "testadmin's Tome"
        assert sent["tome_user"] == {"id": pp.tome_user_id(secret, user.id), "display_name": "testadmin",
                                     "role": "admin"}
        assert sent["timezone"] == "Europe/Vienna"
        assert client.get("/api/platform/link/r1").json() == {"status": "pending"}
        assert client.get("/api/platform/link/r1").json() == {"status": "done", "handle": "testadmin-owl"}
        assert poll.calls[0].request.url.params["poll"] == "ps"
        link = db.query(PlatformLink).filter_by(user_id=user.id).one()
        assert link.handle == "testadmin-owl" and link.timezone == "Europe/Vienna"
        assert decrypt_secret(link.push_token_enc) == "nl_abcdefgh_" + "y" * 43
        assert push.call_count == 1  # the full push after linking
        status = client.get("/api/platform/status").json()
        assert status["linked"] and status["handle"] == "testadmin-owl" and status["last_push_at"]
    actions = {a.action for a in db.query(AuditLog).all()}
    assert {"platform.link_requested", "platform.linked"} <= actions
    # The instance secret is stored encrypted.
    from backend.models.instance_setting import InstanceSetting
    assert db.get(InstanceSetting, pp.KEY_INSTANCE_SECRET).value != secret


def test_poll_with_unknown_request_is_expired(client):
    assert client.get("/api/platform/link/nope").json() == {"status": "expired"}


def test_relink_after_revoke_clears_revoked(client, db, admin_user):
    user, _ = admin_user
    link = _link(db, user, revoked_at=datetime.utcnow())
    db.commit()
    assert client.get("/api/platform/status").json()["revoked"] is True
    with respx.mock(base_url=NL) as mock:
        mock.post("/api/link/request").mock(return_value=httpx.Response(200, json={
            "request_id": "r2", "poll_secret": "ps", "url": f"{NL}/link?t=t2"}))
        mock.get("/api/link/request/r2").mock(return_value=httpx.Response(200, json={
            "status": "done", "handle": "reader-owl", "push_token": "nl_zzzzzzzz_" + "z" * 43}))
        mock.post("/api/push").mock(return_value=httpx.Response(200, json={"accepted": 0}))
        client.post("/api/platform/link", json={"timezone": "UTC"})
        client.get("/api/platform/link/r2")
    db.refresh(link)
    assert link.revoked_at is None and decrypt_secret(link.push_token_enc).startswith("nl_zzzzzzzz_")


def test_share_titles_off_pushes_without_titles_and_on_resyncs(client, db, admin_user, make_book):
    user, _ = admin_user
    book = make_book(title="Secret Title")
    db.add(UserBookStatus(user_id=user.id, book_id=book.id, status="read", finished_at=datetime(2026, 4, 2, 12)))
    _session(db, user.id, datetime(2026, 4, 2, 10), 30, book.id)
    _link(db, user, tz="UTC", share=True, pushed_through=date(2026, 4, 3))
    db.commit()
    with respx.mock(base_url=NL) as mock:
        route = mock.post("/api/push").mock(return_value=httpx.Response(200, json={"accepted": 1}))
        r = client.patch("/api/platform/settings", json={"share_titles": False})
        assert r.status_code == 200 and r.json()["share_titles"] is False
        body = json.loads(route.calls[0].request.content)
        assert body["share_titles"] is False and "current" not in body
        assert "Secret Title" not in route.calls[0].request.content.decode()
        client.patch("/api/platform/settings", json={"share_titles": True})
        body2 = json.loads(route.calls[1].request.content)
        assert body2["share_titles"] is True and body2["days"][0]["date"] == "2026-04-02"  # full resync
        assert _days(body2)["2026-04-02"]["finished"][0]["title"] == "Secret Title"
        client.patch("/api/platform/settings", json={"timezone": "Pacific/Auckland"})
        assert json.loads(route.calls[2].request.content)["timezone"] == "Pacific/Auckland"
        assert client.patch("/api/platform/settings", json={"timezone": "Mars/Base"}).status_code == 422
    audits = db.query(AuditLog).filter_by(action="platform.settings_changed").all()
    assert len(audits) == 3
    assert all("Secret" not in (a.details or "") for a in audits)


def test_resync_and_unlink(client, db, admin_user):
    user, _ = admin_user
    _link(db, user, tz="UTC")
    db.commit()
    with respx.mock(base_url=NL) as mock:
        route = mock.post("/api/push").mock(return_value=httpx.Response(200, json={"accepted": 0}))
        assert client.post("/api/platform/resync").json() == {"started": True}
        assert route.call_count == 1
    db.add(PlatformCoverSent(user_id=user.id, book_key="k"))
    db.commit()
    assert client.post("/api/platform/unlink").json() == {"ok": True}
    assert db.query(PlatformLink).filter_by(user_id=user.id).count() == 0
    assert db.query(PlatformCoverSent).filter_by(user_id=user.id).count() == 0
    assert client.get("/api/platform/status").json()["linked"] is False
    actions = {a.action for a in db.query(AuditLog).all()}
    assert {"platform.resync", "platform.unlinked"} <= actions


def test_open_requires_link_and_keeps_timezone(client, db, admin_user):
    user, _ = admin_user
    assert client.post("/api/platform/open").status_code == 409
    _link(db, user, tz="Asia/Tokyo")
    db.commit()
    with respx.mock(base_url=NL) as mock:
        req = mock.post("/api/link/request").mock(return_value=httpx.Response(200, json={
            "request_id": "r3", "poll_secret": "ps", "url": f"{NL}/link?t=t3"}))
        assert client.post("/api/platform/open").status_code == 200
        assert json.loads(req.calls[0].request.content)["timezone"] == "Asia/Tokyo"


@pytest.mark.parametrize("raw,want", [
    ("978-0-593-13520-4", "9780593135204"),
    ("0 306 40615 2", "0306406152"),
    ("080442957x", "080442957X"),
    ("12345", None),
    ("978-0-593-13520", None),
    ("ISBN 9780593135204", None),
    ("", None),
    (None, None),
])
def test_clean_isbn(raw, want):
    assert pp.clean_isbn(raw) == want


def test_isbn_null_when_the_book_has_none_and_on_current(db, admin_user, make_book):
    user, _ = admin_user
    done = make_book(title="No Isbn")
    reading = make_book(title="Reading", isbn="0-306-40615-2")
    db.add(UserBookStatus(user_id=user.id, book_id=done.id, status="read", finished_at=datetime(2026, 4, 2, 12)))
    _session(db, user.id, datetime(2026, 4, 2, 10), 30, done.id)
    db.add(UserBookStatus(user_id=user.id, book_id=reading.id, status="reading", progress_pct=0.3))
    db.commit()
    link = _link(db, user, tz="UTC", share=True)
    body = pp.build_envelope(db, user, link, full=True, today=date(2026, 4, 2)).body
    item = _days(body)["2026-04-02"]["finished"][0]
    assert "isbn" in item and item["isbn"] is None
    assert body["current"]["isbn"] == "0306406152"


# ── minutes by hour ──────────────────────────────────────────────────────────

def test_minutes_by_hour_splits_a_session_proportionally(db, admin_user):
    user, _ = admin_user
    _session(db, user.id, _utc(datetime(2026, 5, 2, 21, 40), VIENNA), 90)  # 21:40-23:10
    d = pp.aggregate(db, user, VIENNA).days[date(2026, 5, 2)]
    hours = d["minutes_by_hour"]
    assert len(hours) == 24 and sum(hours) == d["minutes_read"] == 90
    assert (hours[21], hours[22], hours[23]) == (20, 60, 10)
    assert sum(hours[:21]) == 0


def test_minutes_by_hour_across_midnight(db, admin_user):
    user, _ = admin_user
    _session(db, user.id, _utc(datetime(2026, 5, 2, 23, 30), VIENNA), 75)  # 23:30-00:45
    agg = pp.aggregate(db, user, VIENNA)
    first, second = agg.days[date(2026, 5, 2)], agg.days[date(2026, 5, 3)]
    assert first["minutes_by_hour"][23] == 30 and sum(first["minutes_by_hour"]) == 30
    assert second["minutes_by_hour"][0] == 45 and sum(second["minutes_by_hour"]) == 45


def test_minutes_by_hour_rounding_sums_to_minutes_read(db, admin_user):
    user, _ = admin_user
    # Three 20.5-minute sessions each straddling an hour boundary: per-hour
    # values round unevenly, the sum must still match.
    for h in (8, 10, 12):
        _session(db, user.id, _utc(datetime(2026, 5, 2, h, 49, 40), VIENNA), 20.5)
    d = pp.aggregate(db, user, VIENNA).days[date(2026, 5, 2)]
    assert d["minutes_read"] == round(3 * 20.5)
    assert sum(d["minutes_by_hour"]) == d["minutes_read"]
    assert all(m >= 0 for m in d["minutes_by_hour"])


def test_zero_day_carries_24_zero_hours(db, admin_user):
    user, _ = admin_user
    _session(db, user.id, datetime(2026, 4, 1, 10), 30)
    link = _link(db, user, tz="UTC")
    env = pp.build_envelope(db, user, link, full=True, today=date(2026, 4, 2))
    assert _days(env.body)["2026-04-02"]["minutes_by_hour"] == [0] * 24
    assert _days(env.body)["2026-04-01"]["minutes_by_hour"][10] == 30


# ── goal ─────────────────────────────────────────────────────────────────────

def _goal(db, user, kind, target, book_type_id=None):
    from backend.models.reading_goal import ReadingGoal
    g = ReadingGoal(user_id=user.id, kind=kind, target=target, book_type_id=book_type_id)
    db.add(g)
    db.flush()
    return g


def test_goal_books_yearly(db, admin_user):
    user, _ = admin_user
    _goal(db, user, "books_per_year", 30)
    _goal(db, user, "books_per_month", 3)
    link = _link(db, user, tz="UTC")
    env = pp.build_envelope(db, user, link, full=True, today=date(2026, 4, 2))
    assert env.body["goal"] == {"year": 2026, "kind": "books", "target": 30}


def test_goal_hours_converts_minutes(db, admin_user):
    user, _ = admin_user
    _goal(db, user, "minutes_per_year", 6000)
    link = _link(db, user, tz="UTC")
    env = pp.build_envelope(db, user, link, full=True, today=date(2026, 4, 2))
    assert env.body["goal"] == {"year": 2026, "kind": "hours", "target": 100}


def test_goal_omitted_when_none_or_type_filtered(db, admin_user):
    user, _ = admin_user
    manga = BookType(slug="manga", label="Manga")
    db.add(manga)
    db.flush()
    _goal(db, user, "books_per_year", 20, book_type_id=manga.id)
    _goal(db, user, "minutes_per_day", 30)
    link = _link(db, user, tz="UTC")
    assert "goal" not in pp.build_envelope(db, user, link, full=True, today=date(2026, 4, 2)).body


def test_goal_deleted_sends_null_once(db, admin_user):
    user, _ = admin_user
    g = _goal(db, user, "books_per_year", 30)
    _link(db, user, tz="UTC")
    db.commit()
    bodies = []
    with respx.mock(base_url=NL) as mock:
        mock.post("/api/push").mock(side_effect=lambda req: (bodies.append(json.loads(req.content)),
                                                             httpx.Response(200, json={"accepted": 1}))[1])
        assert pp.push_user(db, user.id, full=True) == "ok"
        assert bodies[0]["goal"]["target"] == 30
        db.delete(g)
        db.commit()
        assert pp.push_user(db, user.id) == "ok"
        assert "goal" in bodies[1] and bodies[1]["goal"] is None
        assert pp.push_user(db, user.id) == "ok"
        assert "goal" not in bodies[2]


def test_goal_change_marks_dirty(db, admin_user):
    user, _ = admin_user
    _link(db, user, tz="UTC")
    db.commit()
    pp.clear_dirty()
    _goal(db, user, "books_per_year", 12)
    db.commit()
    assert user.id in pp.take_due(now=10**12)


# ── finished needs reading data ──────────────────────────────────────────────

def test_finished_only_with_reading_sessions(db, admin_user, make_book):
    user, _ = admin_user
    marked = make_book(title="Only marked")
    read = make_book(title="Actually read")
    for b in (marked, read):
        db.add(UserBookStatus(user_id=user.id, book_id=b.id, status="read", finished_at=datetime(2026, 4, 2, 12)))
    _session(db, user.id, datetime(2026, 4, 1, 10), 45, read.id)
    db.flush()
    agg = pp.aggregate(db, user, UTC)
    assert agg.finished_by_day == {date(2026, 4, 2): [read.id]}
    link = _link(db, user, tz="UTC", share=True)
    day = _days(pp.build_envelope(db, user, link, full=True, today=date(2026, 4, 2)).body)["2026-04-02"]
    assert day["books_finished"] == 1 == len(day["finished"])
    assert day["finished"][0]["title"] == "Actually read" and day["finished"][0]["minutes_total"] == 45
