"""GET /books/{id}/cover: small WebP copies (``w``) and cache headers (``v``).

Grids ask for a 400 px copy versioned by the book's ``updated_at`` and keep it
for a year, so the version must move whenever the cover does, also when a
re-upload reuses the cover's file name.
"""
import io

import pytest
from PIL import Image

from backend.core.config import settings


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    settings.covers_dir.mkdir(parents=True)
    return tmp_path


def _jpeg(width: int, height: int, color=(200, 30, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, "JPEG")
    return buf.getvalue()


def test_thumbnail_is_small_webp(client, make_book, data_dir):
    (settings.covers_dir / "big.jpg").write_bytes(_jpeg(1200, 1800))
    book = make_book(title="Big", cover_path="big.jpg")

    r = client.get(f"/api/books/{book.id}/cover?w=400")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/webp"
    assert Image.open(io.BytesIO(r.content)).size == (400, 600)

    # Odd widths snap up to an allowed one; past the largest, the original.
    assert Image.open(io.BytesIO(client.get(f"/api/books/{book.id}/cover?w=100").content)).width == 160
    assert client.get(f"/api/books/{book.id}/cover?w=900").headers["content-type"] == "image/jpeg"


def test_narrow_cover_served_as_is(client, make_book, data_dir):
    (settings.covers_dir / "small.jpg").write_bytes(_jpeg(300, 450))
    book = make_book(title="Small", cover_path="small.jpg")
    r = client.get(f"/api/books/{book.id}/cover?w=400")
    assert r.headers["content-type"] == "image/jpeg"
    assert not settings.cover_thumbs_dir.exists()


def test_cache_headers(client, make_book, data_dir):
    (settings.covers_dir / "c.jpg").write_bytes(_jpeg(600, 900))
    book = make_book(title="C", cover_path="c.jpg")
    versioned = client.get(f"/api/books/{book.id}/cover?w=400&v=2026-01-01T00:00:00")
    assert "immutable" in versioned.headers["cache-control"]
    plain = client.get(f"/api/books/{book.id}/cover")
    assert "immutable" not in plain.headers["cache-control"]
    assert "max-age=300" in plain.headers["cache-control"]


def test_replaced_cover_gets_new_thumbnail_and_version(client, make_book, data_dir):
    book = make_book(title="Swap")
    first = client.post(f"/api/books/{book.id}/cover",
                        files={"file": ("a.jpg", _jpeg(800, 1200, (255, 0, 0)), "image/jpeg")})
    assert first.status_code == 200
    v1 = client.get("/api/books").json()
    v1 = next(b for b in v1 if b["id"] == book.id)["updated_at"]
    red = Image.open(io.BytesIO(client.get(f"/api/books/{book.id}/cover?w=400").content)).convert("RGB")

    # Same file name on the second upload: the version must still move.
    second = client.post(f"/api/books/{book.id}/cover",
                         files={"file": ("b.jpg", _jpeg(800, 1200, (0, 0, 255)), "image/jpeg")})
    assert second.status_code == 200
    v2 = next(b for b in client.get("/api/books").json() if b["id"] == book.id)["updated_at"]
    assert v1 and v2 and v2 != v1

    blue = Image.open(io.BytesIO(client.get(f"/api/books/{book.id}/cover?w=400").content)).convert("RGB")
    assert red.getpixel((10, 10))[0] > 200
    assert blue.getpixel((10, 10))[2] > 200
    assert len(list(settings.cover_thumbs_dir.glob(f"{book.id}_400_*.webp"))) == 1


def test_broken_cover_falls_back_to_original(client, make_book, data_dir):
    (settings.covers_dir / "bad.jpg").write_bytes(b"not an image")
    book = make_book(title="Bad", cover_path="bad.jpg")
    r = client.get(f"/api/books/{book.id}/cover?w=400")
    assert r.status_code == 200
    assert r.content == b"not an image"
