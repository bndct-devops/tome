"""Release detection: follow a series → "vol N is out" notifications.

Hardcover is mocked throughout (fetch_series_latest / search_series); the
poller's contract under test: priming is silent, growth notifies exactly once
and advances the watermark, failures keep the watermark, and the whole feature
403s when TOME_RELEASE_DETECTION is off.
"""
import pytest

from backend.core.config import settings
from backend.models.notification import Notification
from backend.models.wish import Wish
from backend.services import release_detection as rd


@pytest.fixture(autouse=True)
def _enable_detection(monkeypatch):
    monkeypatch.setattr(settings, "release_detection", True)
    monkeypatch.setattr(settings, "hardcover_token", "hc_test")


def _state(latest, title="Vol Next", date="2026-08-01"):
    return {"latest_index": latest, "latest_title": title,
            "release_date": date, "total": None, "name": "Re:ZERO"}


def _follow(db, user, latest_known=None, sid="4242"):
    w = Wish(user_id=user.id, kind="follow", status="open", title="Re:ZERO",
             series="Re:ZERO", source="hardcover", source_id=f"series:{sid}",
             external_series_id=sid, latest_known_index=latest_known)
    db.add(w)
    db.flush()
    return w


@pytest.mark.anyio
async def test_first_check_primes_silently(db, admin_user, monkeypatch):
    user, _ = admin_user
    w = _follow(db, user, latest_known=None)
    async def fake(sid): return _state(27.0)
    monkeypatch.setattr(rd, "fetch_series_latest", fake)

    out = await rd.check_follows(db, force=True)
    assert out == {"checked": 1, "notified": 0}
    assert w.latest_known_index == 27.0
    assert db.query(Notification).count() == 0


@pytest.mark.anyio
async def test_new_volume_notifies_once(db, admin_user, monkeypatch):
    user, _ = admin_user
    w = _follow(db, user, latest_known=27.0)
    async def fake(sid): return _state(28.0, title="Re:ZERO Vol. 28")
    monkeypatch.setattr(rd, "fetch_series_latest", fake)

    out = await rd.check_follows(db, force=True)
    assert out["notified"] == 1
    assert w.latest_known_index == 28.0
    n = db.query(Notification).filter(Notification.kind == "release_out").one()
    assert "Volume 28" in n.title and "Re:ZERO" in n.title

    # Second pass with the same tracker state: no duplicate alert.
    out2 = await rd.check_follows(db, force=True)
    assert out2["notified"] == 0
    assert db.query(Notification).filter(Notification.kind == "release_out").count() == 1


@pytest.mark.anyio
async def test_fetch_failure_keeps_watermark(db, admin_user, monkeypatch):
    user, _ = admin_user
    w = _follow(db, user, latest_known=27.0)
    async def fake(sid): return None
    monkeypatch.setattr(rd, "fetch_series_latest", fake)

    out = await rd.check_follows(db, force=True)
    assert out == {"checked": 0, "notified": 0}
    assert w.latest_known_index == 27.0
    assert w.last_checked_at is None      # retries next cycle


def test_follow_endpoints(client, db, admin_user, monkeypatch):
    async def fake_latest(sid): return _state(16.0)
    monkeypatch.setattr(rd, "fetch_series_latest", fake_latest)

    r = client.post("/api/wishlist/follow", json={"name": "The Good Guys", "source_id": "777"})
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["latest_known_index"] == 16.0   # primed at follow time

    # Duplicate follow → 409; list shows exactly one.
    assert client.post("/api/wishlist/follow",
                       json={"name": "The Good Guys", "source_id": "777"}).status_code == 409
    follows = client.get("/api/wishlist/follows").json()
    assert len(follows) == 1 and follows[0]["source_id"] == "series:777"

    # Follows don't leak into the plain wishlist.
    assert client.get("/api/wishlist").json() == []

    # Unfollow via the normal wish delete.
    assert client.delete(f"/api/wishlist/{out['id']}").status_code == 204
    assert client.get("/api/wishlist/follows").json() == []


def test_disabled_flag_403s(client, db, admin_user, monkeypatch):
    monkeypatch.setattr(settings, "release_detection", False)
    assert client.get("/api/wishlist/follows").status_code == 403
    assert client.post("/api/wishlist/follow", json={"name": "X", "source_id": "1"}).status_code == 403
    assert client.post("/api/admin/release-check").status_code == 403


def test_me_reports_release_detection_flag(client, admin_user, monkeypatch):
    """The web client reads the flag from /auth/me instead of probing the
    follows endpoint for a 403 on every page load."""
    assert client.get("/api/auth/me").json()["release_detection"] is True
    monkeypatch.setattr(settings, "release_detection", False)
    assert client.get("/api/auth/me").json()["release_detection"] is False
