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
