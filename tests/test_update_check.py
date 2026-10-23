"""Tests for the background update check (backend/services/update_check.py)
and the read-only /api/meta/update-check endpoint.

No test here touches the network: urlopen is patched everywhere it matters,
and conftest pins TOME_UPDATE_CHECK=false plus an .invalid URL.
"""
import asyncio
import io
import json
import logging
import random
import urllib.error
from unittest.mock import patch

import pytest
from starlette.testclient import TestClient

import backend.services.update_check as uc
from backend import __version__
from backend.core.config import settings
from backend.core.database import get_db

HOUR = 3600.0
TEST_URL = "http://update-check.invalid/latest.json"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    uc.reset_cache()
    monkeypatch.setattr(settings, "update_check_url", TEST_URL)
    yield
    uc.reset_cache()


class _Resp(io.BytesIO):
    def __init__(self, body: bytes, status: int = 200):
        super().__init__(body)
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def _json(obj, status=200):
    return _Resp(json.dumps(obj).encode(), status)


class _Sleeps:
    """Fake asyncio.sleep: records delays, stops the loop after `n` sleeps."""

    def __init__(self, n: int):
        self.n = n
        self.delays: list[float] = []

    async def __call__(self, d: float):
        self.delays.append(d)
        if len(self.delays) >= self.n:
            raise asyncio.CancelledError


def _run_loop(sleeps: _Sleeps, seed: int = 0):
    async def go():
        with pytest.raises(asyncio.CancelledError):
            await uc.update_check_loop(sleep=sleeps, rng=random.Random(seed))
    asyncio.run(go())


# ── Timing ────────────────────────────────────────────────────────────────────

def test_first_delay_and_cadence_in_range(monkeypatch):
    monkeypatch.setattr(uc, "check_once", lambda: True)
    for seed in range(50):
        sleeps = _Sleeps(4)
        _run_loop(sleeps, seed)
        first, *rest = sleeps.delays
        assert 5 * 60 <= first <= 60 * 60
        assert rest and all(22 * HOUR <= d <= 26 * HOUR for d in rest)


def test_backoff_1h_6h_then_daily_with_one_warning_and_one_recovery(monkeypatch, caplog):
    results = iter([False, False, False, False, True, True])
    monkeypatch.setattr(uc, "check_once", lambda: next(results))
    sleeps = _Sleeps(7)
    with caplog.at_level(logging.DEBUG, logger=uc.__name__):
        _run_loop(sleeps)
    after = sleeps.delays[1:]
    assert after[0] == 1 * HOUR
    assert after[1] == 6 * HOUR
    assert 22 * HOUR <= after[2] <= 26 * HOUR
    assert 22 * HOUR <= after[3] <= 26 * HOUR   # still failing: daily, not faster
    assert 22 * HOUR <= after[4] <= 26 * HOUR   # recovered
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    infos = [r for r in caplog.records if r.levelno == logging.INFO]
    assert len(warnings) == 1
    assert len(infos) == 1 and "working again" in infos[0].getMessage()
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


def test_backoff_resets_after_recovery():
    b = uc._Backoff(random.Random(1))
    assert b.after(False) == HOUR
    assert 22 * HOUR <= b.after(True) <= 26 * HOUR
    assert b.after(False) == HOUR


# ── The request ───────────────────────────────────────────────────────────────

def test_request_is_url_with_version_and_two_headers():
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["req"], captured["timeout"] = req, timeout
        return _json({"version": "99.0.0", "url": "https://github.com/bndct-devops/tome/releases/tag/v99.0.0"})

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        assert uc.check_once() is True
    req = captured["req"]
    assert req.full_url == f"{TEST_URL}?v={__version__}"
    assert req.get_method() == "GET"
    assert req.data is None
    assert captured["timeout"] == 10
    headers = {k.lower(): v for k, v in req.header_items()}
    assert headers == {"user-agent": f"Tome/{__version__}", "accept": "application/json"}


def test_success_fills_cache():
    rel = "https://github.com/bndct-devops/tome/releases/tag/v99.0.0"
    with patch("urllib.request.urlopen", return_value=_json({"version": "99.0.0", "url": rel})):
        assert uc.check_once()
    r = uc.cached_result()
    assert r == {"enabled": True, "current": __version__, "latest": "99.0.0",
                 "update_available": True, "url": rel}


def test_null_version_keeps_previous_result():
    rel = "https://github.com/bndct-devops/tome/releases/tag/v99.0.0"
    with patch("urllib.request.urlopen", return_value=_json({"version": "99.0.0", "url": rel})):
        uc.check_once()
    with patch("urllib.request.urlopen", return_value=_json({"version": None, "url": None})) as m:
        assert uc.check_once() is True
    assert m.call_count == 1  # a null answer is not a failure: no GitHub fallback
    r = uc.cached_result()
    assert r["latest"] == "99.0.0" and r["url"] == rel


# ── Fallback ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("primary", [
    urllib.error.URLError("offline"),
    _Resp(b"<html>not json</html>"),
    _Resp(b"{}", status=503),
])
def test_fallback_to_github_when_primary_fails(primary):
    calls = []

    def fake_urlopen(req, timeout=None):
        calls.append(req)
        if len(calls) == 1:
            if isinstance(primary, Exception):
                raise primary
            return primary
        return _json({"tag_name": "v99.1.0"})

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        assert uc.check_once() is True
    assert len(calls) == 2
    assert calls[0].full_url.startswith(TEST_URL)
    assert calls[1].full_url == uc.GITHUB_RELEASES_API
    assert calls[1].get_header("User-agent") == "tome-update-check"
    r = uc.cached_result()
    assert r["latest"] == "99.1.0"
    assert r["url"] == uc.GITHUB_RELEASES_URL


def test_both_fail_keeps_previous_result():
    with patch("urllib.request.urlopen", return_value=_json({"version": "99.0.0", "url": None})):
        uc.check_once()
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("offline")) as m:
        assert uc.check_once() is False
    assert m.call_count == 2  # primary, then GitHub once
    assert uc.cached_result()["latest"] == "99.0.0"


# ── Endpoint ──────────────────────────────────────────────────────────────────

def test_endpoint_before_any_check(client: TestClient, monkeypatch):
    monkeypatch.setattr(settings, "update_check", True)
    with patch("urllib.request.urlopen") as m:
        data = client.get("/api/meta/update-check").json()
    m.assert_not_called()
    assert data == {"enabled": True, "current": __version__, "latest": None,
                    "update_available": False, "url": uc.GITHUB_RELEASES_URL}


def test_endpoint_returns_cache_without_calling_out(client: TestClient, monkeypatch):
    monkeypatch.setattr(settings, "update_check", True)
    rel = "https://github.com/bndct-devops/tome/releases/tag/v99.0.0"
    with patch("urllib.request.urlopen", return_value=_json({"version": "99.0.0", "url": rel})):
        uc.check_once()
    with patch("urllib.request.urlopen") as m:
        data = client.get("/api/meta/update-check").json()
        client.get("/api/meta/update-check")
    m.assert_not_called()
    assert data == {"enabled": True, "current": __version__, "latest": "99.0.0",
                    "update_available": True, "url": rel}


def test_endpoint_current_is_latest(client: TestClient, monkeypatch):
    monkeypatch.setattr(settings, "update_check", True)
    with patch("urllib.request.urlopen", return_value=_json({"version": __version__, "url": None})):
        uc.check_once()
    data = client.get("/api/meta/update-check").json()
    assert data["update_available"] is False


def test_endpoint_disabled(client: TestClient, monkeypatch):
    monkeypatch.setattr(settings, "update_check", False)
    with patch("urllib.request.urlopen", return_value=_json({"version": "99.0.0", "url": None})):
        uc.check_once()
    data = client.get("/api/meta/update-check").json()
    assert data["enabled"] is False
    assert data["latest"] is None
    assert data["update_available"] is False


# ── Lifespan guard ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("enabled", [False, True])
def test_lifespan_starts_loop_only_when_enabled(db, monkeypatch, enabled):
    started = []

    async def _idle():
        await asyncio.sleep(3600)

    def fake_loop(*a, **kw):  # records the start itself, not the task running
        started.append(True)
        return _idle()

    monkeypatch.setattr(uc, "update_check_loop", fake_loop)
    monkeypatch.setattr(settings, "update_check", enabled)
    from backend.main import create_app
    app = create_app()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    with patch("urllib.request.urlopen") as m:
        with TestClient(app):
            pass
    m.assert_not_called()
    assert started == ([True] if enabled else [])
