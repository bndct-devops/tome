"""Text responses are gzipped for clients that ask; files and images never."""
import io

from PIL import Image

from backend.core.compression import compressible
from backend.core.config import settings


def test_compressible_types():
    assert compressible("application/json")
    assert compressible("text/html; charset=utf-8")
    assert compressible("application/atom+xml;profile=opds-catalog")
    assert not compressible("image/webp")
    assert not compressible("application/epub+zip")
    assert not compressible("application/zip")
    assert not compressible("text/event-stream")


def test_json_is_gzipped_when_asked(client):
    r = client.get("/api/openapi.json", headers={"Accept-Encoding": "gzip"})
    assert r.headers.get("content-encoding") == "gzip"
    plain = client.get("/api/openapi.json", headers={"Accept-Encoding": "identity"})
    assert "content-encoding" not in plain.headers


def test_cover_is_not_gzipped(client, make_book, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    settings.covers_dir.mkdir(parents=True)
    buf = io.BytesIO()
    Image.effect_noise((300, 450), 64).convert("RGB").save(buf, "JPEG")
    (settings.covers_dir / "c.jpg").write_bytes(buf.getvalue())
    book = make_book(title="C", cover_path="c.jpg")
    r = client.get(f"/api/books/{book.id}/cover", headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200
    assert "content-encoding" not in r.headers
    assert r.content == buf.getvalue()


def _mini_app():
    """A bare Starlette app behind the middleware, for response shapes the
    API does not have a handy endpoint for."""
    from starlette.applications import Starlette
    from starlette.responses import PlainTextResponse, RedirectResponse, Response, StreamingResponse
    from starlette.routing import Route

    from backend.core.compression import TextGZipMiddleware

    async def stream(request):
        async def gen():
            for i in range(50):
                yield f"line {i} " * 40 + "\n"
        return StreamingResponse(gen(), media_type="text/plain")

    routes = [
        Route("/redirect", lambda r: RedirectResponse("/elsewhere")),
        Route("/xml", lambda r: Response("<x/>" * 2000, media_type="application/opensearchdescription+xml")),
        Route("/png", lambda r: Response(b"\x89PNG" + b"\0" * 5000, media_type="image/png")),
        Route("/small", lambda r: PlainTextResponse("tiny")),
        Route("/stream", stream),
        Route("/partial", lambda r: Response("x" * 5000, status_code=206, media_type="text/plain")),
    ]
    app = Starlette(routes=routes)
    app.add_middleware(TextGZipMiddleware, minimum_size=1024)
    return app


def test_non_text_and_special_responses_pass_through():
    """Every response the middleware leaves alone still arrives whole, with
    its headers (Starlette 1.8 broke the earlier subclass on exactly these)."""
    from starlette.testclient import TestClient

    c = TestClient(_mini_app())
    gz = {"Accept-Encoding": "gzip"}
    r = c.get("/redirect", headers=gz, follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == "/elsewhere"
    for path in ("/xml", "/png", "/partial"):
        r = c.get(path, headers=gz)
        assert r.status_code in (200, 206), path
        assert "content-encoding" not in r.headers, path
    assert c.get("/png", headers=gz).content.startswith(b"\x89PNG")
    small = c.get("/small", headers=gz)
    assert small.text == "tiny" and "content-encoding" not in small.headers


def test_streamed_text_is_gzipped_and_decodes():
    from starlette.testclient import TestClient

    c = TestClient(_mini_app())
    r = c.get("/stream", headers={"Accept-Encoding": "gzip"})
    assert r.headers.get("content-encoding") == "gzip"
    assert "content-length" not in r.headers
    assert r.text == "".join(f"line {i} " * 40 + "\n" for i in range(50))
