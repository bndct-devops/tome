"""Book.cover_color: the placeholder tint a cover tile holds while it loads.

Covers the legacy-schema migration, the colour function, the background
backfill (which must not touch updated_at) and the API fields.
"""
import io
import re
from datetime import datetime

import pytest
from PIL import Image
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.core.database import Base
from backend.models.book import Book
from backend.services.cover_color import (
    MAX_LIGHTNESS, MIN_LIGHTNESS, backfill_cover_colors, cover_color,
)

HEX = re.compile(r"^#[0-9a-f]{6}$")


def _lightness(hex_colour: str) -> float:
    r, g, b = (int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5))
    return (max(r, g, b) + min(r, g, b)) / 2


def _save(path, size, colour, fmt="JPEG"):
    Image.new("RGB", size, colour).save(path, fmt)
    return path


# ── migration ────────────────────────────────────────────────────────────────

def test_the_startup_migration_adds_the_column_to_a_legacy_books_table():
    """A books table from before the column: the column appears, NULL for
    every existing row, and the rows are otherwise untouched. Runs twice."""
    from backend.main import migrate_book_cover_color

    engine = create_engine("sqlite://")
    with engine.connect() as conn:
        conn.execute(text(
            "CREATE TABLE books (id INTEGER PRIMARY KEY, title TEXT NOT NULL, author TEXT, "
            "cover_path TEXT, content_hash VARCHAR(64), status VARCHAR(32) NOT NULL, "
            "added_at DATETIME NOT NULL, updated_at DATETIME NOT NULL)"))
        conn.execute(text(
            "INSERT INTO books (title, author, cover_path, status, added_at, updated_at) VALUES "
            "('Old One', 'A', 'a.jpg', 'active', '2026-01-01 10:00:00', '2026-02-01 10:00:00'), "
            "('Old Two', NULL, NULL, 'active', '2026-01-02 10:00:00', '2026-02-02 10:00:00')"))
        conn.commit()
        before = conn.execute(text("SELECT * FROM books ORDER BY id")).fetchall()

        migrate_book_cover_color(conn)
        migrate_book_cover_color(conn)

        cols = {r[1] for r in conn.execute(text("PRAGMA table_info(books)")).fetchall()}
        assert "cover_color" in cols
        rows = conn.execute(text(
            "SELECT id, title, author, cover_path, content_hash, status, added_at, updated_at, cover_color "
            "FROM books ORDER BY id")).fetchall()
        assert [tuple(r[:8]) for r in rows] == [tuple(r) for r in before]
        assert [r[8] for r in rows] == [None, None]
        # An older Tome writing to the migrated table does not name the column.
        conn.execute(text(
            "INSERT INTO books (title, status, added_at, updated_at) "
            "VALUES ('Older Write', 'active', '2026-03-01', '2026-03-01')"))
        assert conn.execute(text("SELECT cover_color FROM books WHERE title = 'Older Write'")).scalar() is None


def test_the_migration_skips_a_database_without_a_books_table():
    from backend.main import migrate_book_cover_color

    engine = create_engine("sqlite://")
    with engine.connect() as conn:
        migrate_book_cover_color(conn)
        assert conn.execute(text("PRAGMA table_info(books)")).fetchall() == []


# ── colour function ──────────────────────────────────────────────────────────

def test_a_cover_gives_its_dominant_colour(tmp_path):
    img = Image.new("RGB", (300, 450), (30, 60, 160))
    img.paste((230, 200, 40), (0, 0, 300, 60))  # a yellow title band
    path = tmp_path / "c.jpg"
    img.save(path, "JPEG")
    colour = cover_color(path)
    assert colour and HEX.match(colour)
    r, g, b = (int(colour[i:i + 2], 16) for i in (1, 3, 5))
    assert b > r and b > g  # the blue, not the band


def test_extremes_are_clamped_into_a_readable_band(tmp_path):
    white = cover_color(_save(tmp_path / "w.png", (200, 300), (255, 255, 255), "PNG"))
    black = cover_color(_save(tmp_path / "b.png", (200, 300), (0, 0, 0), "PNG"))
    assert white != "#ffffff" and black != "#000000"
    assert MIN_LIGHTNESS - 0.01 <= _lightness(black) and _lightness(white) <= MAX_LIGHTNESS + 0.01


def test_a_tiny_and_a_transparent_image_still_give_a_colour(tmp_path):
    assert HEX.match(cover_color(_save(tmp_path / "t.png", (1, 1), (120, 40, 40), "PNG")))
    Image.new("RGBA", (40, 60), (0, 0, 0, 0)).save(tmp_path / "clear.png")
    clear = cover_color(tmp_path / "clear.png")
    assert clear and _lightness(clear) > 0.5  # transparent reads as paper, not black


def test_a_broken_or_missing_file_gives_none(tmp_path):
    bad = tmp_path / "bad.jpg"
    bad.write_bytes(b"\xff\xd8\xff not really a jpeg")
    assert cover_color(bad) is None
    assert cover_color(tmp_path / "gone.jpg") is None


# ── backfill ─────────────────────────────────────────────────────────────────

@pytest.fixture
def file_db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'tome.db'}")
    Base.metadata.create_all(bind=engine)
    covers = tmp_path / "covers"
    covers.mkdir()
    return engine, covers


def test_the_backfill_fills_covers_skips_the_rest_and_keeps_updated_at(file_db):
    engine, covers = file_db
    _save(covers / "red.jpg", (120, 180), (180, 30, 30))
    _save(covers / "green.jpg", (120, 180), (30, 150, 60))
    (covers / "broken.jpg").write_bytes(b"nope")
    stamp = datetime(2026, 5, 1, 12, 0, 0)
    with Session(engine) as s:
        for title, cover, colour in (
            ("Red", "red.jpg", None),
            ("Green", "green.jpg", None),
            ("No cover", None, None),
            ("File gone", "gone.jpg", None),
            ("Broken", "broken.jpg", None),
            ("Already", "red.jpg", "#123456"),
        ):
            s.add(Book(title=title, cover_path=cover, cover_color=colour, status="active",
                       added_at=stamp, updated_at=stamp))
        s.commit()

    assert backfill_cover_colors(engine, covers, pause=0) == 2

    with engine.connect() as conn:
        rows = {r[0]: (r[1], r[2]) for r in conn.execute(text("SELECT title, cover_color, updated_at FROM books"))}
    assert HEX.match(rows["Red"][0]) and HEX.match(rows["Green"][0])
    assert rows["Red"][0] != rows["Green"][0]
    assert rows["No cover"][0] is None and rows["File gone"][0] is None and rows["Broken"][0] is None
    assert rows["Already"][0] == "#123456"
    assert {r[1] for r in rows.values()} == {"2026-05-01 12:00:00.000000"}

    # Idempotent: a second run finds nothing to do and changes nothing.
    assert backfill_cover_colors(engine, covers, pause=0) == 0


def test_the_backfill_does_nothing_without_a_covers_folder(file_db, tmp_path):
    engine, _ = file_db
    assert backfill_cover_colors(engine, tmp_path / "missing", pause=0) == 0


def test_the_backfill_works_in_batches(file_db, monkeypatch):
    from backend.services import cover_color as cc

    engine, covers = file_db
    monkeypatch.setattr(cc, "BATCH", 2)
    _save(covers / "c.jpg", (60, 90), (40, 40, 160))
    with Session(engine) as s:
        for i in range(5):
            s.add(Book(title=f"B{i}", cover_path="c.jpg", status="active"))
        s.commit()
    assert backfill_cover_colors(engine, covers, pause=0) == 5


# ── setting it, and the API ──────────────────────────────────────────────────

@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    settings.covers_dir.mkdir(parents=True)
    return tmp_path


def _jpeg(colour) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (300, 450), colour).save(buf, "JPEG")
    return buf.getvalue()


def test_an_uploaded_cover_stores_its_colour(client, make_book, data_dir):
    book = make_book(title="Plain")
    r = client.post(f"/api/books/{book.id}/cover", files={"file": ("c.jpg", _jpeg((30, 60, 160)), "image/jpeg")})
    assert r.status_code == 200
    first = r.json()["cover_color"]
    assert HEX.match(first)
    r = client.post(f"/api/books/{book.id}/cover", files={"file": ("c.jpg", _jpeg((170, 40, 40)), "image/jpeg")})
    assert r.json()["cover_color"] != first  # same file name, new colour


def test_a_scanned_book_gets_its_colour(db, data_dir, tmp_path):
    from backend.services.scanner import _create_book_entry

    _save(settings.covers_dir / "scan.jpg", (120, 180), (30, 150, 60))
    f = tmp_path / "Scan.epub"
    f.write_bytes(b"x")
    book = _create_book_entry(f, {"title": "Scan", "cover_path": "scan.jpg"}, "h" * 64, "epub", 1, db, None)
    assert HEX.match(book.cover_color)


def test_list_detail_series_and_home_return_the_colour(client, db, make_book, admin_user):
    from backend.models.user_book_status import UserBookStatus

    user, _ = admin_user
    one = make_book(title="Vol 1", series="Tinted", series_index=1, cover_path="a.jpg")
    two = make_book(title="Vol 2", series="Tinted", series_index=2, cover_path="b.jpg")
    one.cover_color, two.cover_color = "#335577", "#775533"
    db.add(UserBookStatus(user_id=user.id, book_id=one.id, status="reading", progress_pct=0.2))
    db.flush()

    listed = {b["id"]: b["cover_color"] for b in client.get("/api/books").json()}
    assert listed[one.id] == "#335577"
    assert client.get(f"/api/books/{one.id}").json()["cover_color"] == "#335577"
    series = client.get("/api/books/series-detail", params={"name": "Tinted"}).json()
    assert [b["cover_color"] for b in series["books"]] == ["#335577", "#775533"]
    grid = {s["name"]: s["cover_color"] for s in client.get("/api/books/series").json()}
    assert grid["Tinted"] == "#335577"
    focus = client.get("/api/home/focus").json()
    assert focus["book"]["cover_color"] == "#335577"
    assert focus["reading"][0]["cover_color"] == "#335577"
    assert focus["upcoming"][0]["cover_color"] == "#775533"
