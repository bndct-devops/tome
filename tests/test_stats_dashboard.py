"""Tests for the per-user stats dashboard persistence endpoints."""
from fastapi.testclient import TestClient


def test_dashboard_empty_until_saved(client: TestClient):
    r = client.get("/api/stats/dashboard")
    assert r.status_code == 200
    assert r.json() == {"data": None}


def test_dashboard_roundtrip(client: TestClient):
    board = {
        "tabs": [{"id": "overview", "label": "Overview", "tiles": [], "layout": []}],
        "activeTabId": "overview",
        "pad": "lot",
        "days": 30,
    }
    r = client.put("/api/stats/dashboard", json={"data": board})
    assert r.status_code == 200
    assert r.json() == {"ok": True}

    r = client.get("/api/stats/dashboard")
    assert r.status_code == 200
    assert r.json()["data"] == board


def test_dashboard_overwrite_replaces(client: TestClient):
    client.put("/api/stats/dashboard", json={"data": {"v": 1}})
    client.put("/api/stats/dashboard", json={"data": {"v": 2}})
    r = client.get("/api/stats/dashboard")
    assert r.json()["data"] == {"v": 2}


def test_dashboard_size_cap(client: TestClient):
    huge = {"blob": "x" * (300 * 1024)}
    r = client.put("/api/stats/dashboard", json={"data": huge})
    assert r.status_code == 413


def test_dashboard_requires_auth(client: TestClient):
    client.headers.pop("Authorization")
    assert client.get("/api/stats/dashboard").status_code == 401
    assert client.put("/api/stats/dashboard", json={"data": {}}).status_code == 401
