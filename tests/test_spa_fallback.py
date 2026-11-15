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


# --- the fallback only serves files inside the frontend folder ---------------

SECRET = "TOP-SECRET-OUTSIDE-DIST"


@pytest.fixture()
def raw_get(tmp_path, spa_client):
    """GET with the path sent byte for byte (no client-side normalising)."""
    (tmp_path / "secret.txt").write_text(SECRET)
    (tmp_path / "dist" / "robots.txt").write_text("inside")
    (tmp_path / "dist-other").mkdir()
    (tmp_path / "dist-other" / "secret.txt").write_text(SECRET)

    import anyio

    app = spa_client.app

    def get(raw_path: str):
        sent = []

        delivered = False

        async def receive():
            # The request once, then silence: a file response keeps listening
            # for a disconnect while it sends, as it would on a live socket.
            nonlocal delivered
            if delivered:
                await anyio.sleep_forever()
            delivered = True
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            sent.append(message)

        from urllib.parse import unquote

        scope = {
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": "GET", "scheme": "http", "server": ("testserver", 80),
            "client": ("testclient", 1), "root_path": "", "query_string": b"",
            "headers": [(b"host", b"testserver")],
            # What uvicorn hands the app: the percent-decoded path.
            "path": unquote(raw_path), "raw_path": raw_path.encode(),
        }
        anyio.run(app, scope, receive, send)
        status = next(m["status"] for m in sent if m["type"] == "http.response.start")
        body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
        return status, body.decode(errors="replace")

    return get


@pytest.mark.parametrize("path", [
    "/../secret.txt",
    "/assets/../../secret.txt",
    "/%2e%2e/secret.txt",
    "/..%2fsecret.txt",
    "/%2e%2e%2fsecret.txt",
    "/x/..%2f..%2fsecret.txt",
    "/../dist-other/secret.txt",
])
def test_fallback_never_leaves_the_frontend_folder(raw_get, path):
    status, body = raw_get(path)
    assert SECRET not in body
    assert status in (200, 404)
    if status == 200:
        assert body == INDEX


def test_fallback_refuses_an_absolute_path(raw_get, tmp_path):
    status, body = raw_get("//" + str(tmp_path / "secret.txt").lstrip("/"))
    assert SECRET not in body


def test_fallback_refuses_a_symlink_out_of_the_folder(raw_get, tmp_path):
    (tmp_path / "dist" / "link.txt").symlink_to(tmp_path / "secret.txt")
    status, body = raw_get("/link.txt")
    assert SECRET not in body


def test_fallback_still_serves_a_real_file(raw_get):
    status, body = raw_get("/robots.txt")
    assert (status, body) == (200, "inside")
