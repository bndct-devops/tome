"""The SPA fallback serves index.html for app routes but never for machine paths.

Clients such as the TomeSync KOReader plugin read a 404 under /api as "this
server has no such feature"; an HTML page with 200 there decodes to garbage.
"""
import pytest
from starlette.testclient import TestClient

from backend.core.database import get_db

INDEX = "<!doctype html><title>Tome</title><div id=root></div>"


@pytest.fixture()
def spa_client(tmp_path, db, admin_user):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(INDEX)

    from backend.main import create_app

    app = create_app(frontend_dist=dist)

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        c.headers["Authorization"] = f"Bearer {admin_user[1]}"
        yield c
    app.dependency_overrides.clear()


def _is_json_404(r):
    return (
        r.status_code == 404
        and r.headers["content-type"].startswith("application/json")
        and r.json() == {"detail": "Not Found"}
    )


@pytest.mark.parametrize("method", ["GET", "PUT", "POST", "DELETE", "PATCH"])
def test_unknown_api_path_is_json_404(spa_client, method):
    assert _is_json_404(spa_client.request(method, "/api/tome-sync/no-such-endpoint"))


@pytest.mark.parametrize("path", ["/api", "/api/", "/opds/no-such-feed"])
def test_bare_prefix_and_unknown_opds_are_json_404(spa_client, path):
    assert _is_json_404(spa_client.get(path))


def test_known_api_route_wrong_method_is_405(spa_client):
    assert spa_client.put("/api/health").status_code == 405


def test_real_api_route_still_works(spa_client):
    r = spa_client.get("/api/health")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")


@pytest.mark.parametrize("path", ["/", "/books/12", "/settings", "/apiary", "/opdsx"])
def test_unknown_app_path_serves_index(spa_client, path):
    r = spa_client.get(path)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert r.text == INDEX
