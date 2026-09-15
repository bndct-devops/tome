"""GET /api/meta/features: UI flags for the frontend."""
from fastapi.testclient import TestClient

from backend.core.config import settings


def test_native_app_off_by_default(client: TestClient):
    r = client.get("/api/meta/features")
    assert r.status_code == 200
    assert r.json() == {"native_app": False}


def test_native_app_flag_on(client: TestClient, monkeypatch):
    monkeypatch.setattr(settings, "native_app", True)
    assert client.get("/api/meta/features").json() == {"native_app": True}


def test_pairing_and_devices_work_with_flag_off(client: TestClient):
    # The flag only hides UI: the app keeps pairing against a server without it.
    assert settings.native_app is False
    issued = client.post("/api/auth/quick-connect/issue")
    assert issued.status_code == 200
    body = {"code": issued.json()["code"], "poll_token": issued.json()["poll_token"],
            "device": {"name": "Phone"}}
    assert client.post("/api/auth/quick-connect/poll", json=body).json()["status"] == "authorized"
    assert [d["name"] for d in client.get("/api/auth/devices").json()] == ["Phone"]


def test_features_requires_auth(client: TestClient):
    client.headers.pop("Authorization", None)
    assert client.get("/api/meta/features").status_code == 401
