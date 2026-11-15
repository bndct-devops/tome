"""The web UI's pages carry a Content-Security-Policy that keeps script inside
a book from running (the reader's section iframes are blob: documents, which
inherit the page's policy)."""
import base64
import hashlib

import pytest
from starlette.testclient import TestClient

from backend.core.config import settings
from backend.core.database import get_db
from backend.core.headers import build_csp, csp_enabled, inline_script_hashes

BOOT = "\n      var t = localStorage.getItem('tome_theme')\n    "
INDEX = (
    "<!doctype html><html><head><title>Tome</title>"
    f"<script>{BOOT}</script>"
    '<script type="module" crossorigin src="/assets/index-abc.js"></script>'
    "</head><body><div id=root></div></body></html>"
)


def _sha(body: str) -> str:
    return "'sha256-" + base64.b64encode(hashlib.sha256(body.encode()).digest()).decode() + "'"


@pytest.fixture()
def client(tmp_path, db, admin_user):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(INDEX)
    (dist / "robots.txt").write_text("inside")

    from backend.main import create_app

    app = create_app(frontend_dist=dist)

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        c.headers["Authorization"] = f"Bearer {admin_user[1]}"
        yield c
    app.dependency_overrides.clear()


def _directives(policy: str) -> dict[str, list[str]]:
    out = {}
    for part in policy.split(";"):
        words = part.split()
        if words:
            out[words[0]] = words[1:]
    return out


@pytest.mark.parametrize("path", ["/", "/reader/12", "/share/abc", "/index.html"])
def test_page_carries_the_policy(client, path):
    r = client.get(path)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    d = _directives(r.headers["content-security-policy"])
    # No inline script except the app's own (by hash), no blob: script, no eval.
    assert d["script-src"] == ["'self'", _sha(BOOT)]
    assert d["object-src"] == ["'none'"]
    assert d["frame-ancestors"] == ["'self'"]
    assert "'unsafe-inline'" not in d["script-src"]
    assert "'unsafe-eval'" not in d["script-src"]


def test_api_and_plain_files_have_no_policy(client):
    assert "content-security-policy" not in client.get("/api/health").headers
    r = client.get("/robots.txt")
    assert r.text == "inside"
    assert "content-security-policy" not in r.headers


@pytest.mark.parametrize("value", ["off", "OFF", "false", "0"])
def test_off_removes_the_header(client, monkeypatch, value):
    monkeypatch.setattr(settings, "content_security_policy", value)
    r = client.get("/")
    assert r.status_code == 200
    assert "content-security-policy" not in r.headers


def test_enabled_values():
    assert csp_enabled(None)
    assert csp_enabled("on")
    assert csp_enabled("")
    assert not csp_enabled(" off ")


def test_hashes_skip_external_scripts_and_dedupe():
    html = '<script src="/a.js"></script><script>x()</script><SCRIPT>x()</SCRIPT>'
    assert inline_script_hashes(html) == [_sha("x()")]
    assert "script-src 'self';" in build_csp()
