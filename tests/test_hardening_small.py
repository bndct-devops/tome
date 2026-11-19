"""Smaller hardening items: upload temp names and size cap, the Bindery path
check, security headers on every response, and safe restore extraction."""
import io
import tarfile
from pathlib import Path

import pytest
from fastapi import HTTPException
from starlette.datastructures import UploadFile
from starlette.testclient import TestClient

from backend.core.config import settings


# ── upload temp files ─────────────────────────────────────────────────────────

def _upload(name: str, data: bytes) -> UploadFile:
    return UploadFile(file=io.BytesIO(data), filename=name)


def test_same_name_uploads_get_separate_temp_files():
    from backend.api.books import _discard_upload, _save_upload

    a = _save_upload(_upload("The Quiet Orchard.epub", b"first"))
    b = _save_upload(_upload("The Quiet Orchard.epub", b"second"))
    try:
        assert a != b
        # The client's name is kept for everything that reads it later.
        assert a.name == b.name == "The Quiet Orchard.epub"
        assert a.read_bytes() == b"first"
        assert b.read_bytes() == b"second"
        tmp_root = (settings.incoming_dir / ".tmp").resolve()
        assert a.resolve().parent.parent == tmp_root
    finally:
        _discard_upload(a)
        _discard_upload(b)
    assert not a.parent.exists() and not b.parent.exists()


def test_upload_route_passes_the_original_name_and_cleans_up(client, monkeypatch):
    seen = []

    def fake_extract(path, covers_dir):
        seen.append(Path(path))
        return {"title": "Lanterns of Pell", "author": "Orra Vane"}

    monkeypatch.setattr("backend.api.books.extract_metadata", fake_extract)
    r = client.post("/api/books/upload",
                    files={"file": ("Lanterns of Pell.epub", b"epub-bytes", "application/epub+zip")})
    assert r.status_code == 201, r.text
    assert seen[0].name == "Lanterns of Pell.epub"
    tmp_root = settings.incoming_dir / ".tmp"
    assert not seen[0].parent.exists()
    assert not any(p.is_file() for p in tmp_root.rglob("*"))


def test_upload_over_the_cap_is_413_and_leaves_nothing(monkeypatch):
    from backend.api.books import _save_upload

    monkeypatch.setattr(settings, "max_upload_mb", 1)
    before = set((settings.incoming_dir / ".tmp").rglob("*")) if (settings.incoming_dir / ".tmp").exists() else set()
    with pytest.raises(HTTPException) as e:
        _save_upload(_upload("big.epub", b"x" * (1024 * 1024 + 1)))
    assert e.value.status_code == 413
    after = set((settings.incoming_dir / ".tmp").rglob("*"))
    assert after == before


def test_upload_cap_zero_means_no_limit(monkeypatch):
    from backend.api.books import _discard_upload, _save_upload

    monkeypatch.setattr(settings, "max_upload_mb", 0)
    p = _save_upload(_upload("big.epub", b"x" * (2 * 1024 * 1024)))
    assert p.stat().st_size == 2 * 1024 * 1024
    _discard_upload(p)


def test_request_over_the_cap_is_refused_before_the_route(client, monkeypatch):
    monkeypatch.setattr(settings, "max_upload_mb", 1)
    called = []
    monkeypatch.setattr("backend.api.books.extract_metadata",
                        lambda *a, **kw: called.append(1) or {"title": "x"})
    r = client.post("/api/books/upload",
                    files={"file": ("big.epub", b"x" * (2 * 1024 * 1024), "application/epub+zip")})
    assert r.status_code == 413
    assert "upload limit" in r.json()["detail"]
    assert called == []


def test_chunked_request_over_the_cap_is_cut_off(client, monkeypatch):
    monkeypatch.setattr(settings, "max_upload_mb", 1)
    boundary = "b0undary"
    head = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"big.epub\"\r\n"
            "Content-Type: application/epub+zip\r\n\r\n").encode()

    def body():
        yield head
        for _ in range(3):
            yield b"x" * (1024 * 1024)
        yield f"\r\n--{boundary}--\r\n".encode()

    r = client.post("/api/books/upload", content=body(),
                    headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    assert r.status_code == 413


def test_small_requests_pass_the_cap(client, monkeypatch):
    monkeypatch.setattr(settings, "max_upload_mb", 1)
    assert client.post("/api/notifications/read-all").status_code == 200


# ── Bindery path check ────────────────────────────────────────────────────────

def test_bindery_path_check_refuses_a_sibling_with_the_same_prefix():
    from backend.api.bindery import _safe_resolve

    incoming = settings.incoming_dir.resolve()
    sibling = incoming.parent / (incoming.name + "-other")
    with pytest.raises(HTTPException) as e:
        _safe_resolve(f"../{sibling.name}/secret.epub")
    assert e.value.status_code == 400
    assert _safe_resolve("inbox/book.epub") == incoming / "inbox" / "book.epub"


# ── headers on every response ─────────────────────────────────────────────────

EXPECTED = {
    "x-content-type-options": "nosniff",
    "referrer-policy": "same-origin",
    "x-frame-options": "SAMEORIGIN",
}


def _has_basic_headers(r) -> bool:
    return all(r.headers.get(k) == v for k, v in EXPECTED.items())


def test_api_responses_carry_the_basic_headers(client):
    assert _has_basic_headers(client.get("/api/health"))
    assert _has_basic_headers(client.get("/api/no-such-endpoint"))


@pytest.fixture()
def spa_client(tmp_path, db, admin_user):
    from backend.core.database import get_db
    from backend.main import create_app

    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>Tome</title>")
    (dist / "assets" / "app-abc.js").write_text("console.log(1)")
    app = create_app(frontend_dist=dist)

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.mark.parametrize("path", ["/", "/share/sometoken", "/reader/3", "/assets/app-abc.js"])
def test_pages_and_assets_carry_the_basic_headers(spa_client, path):
    r = spa_client.get(path)
    assert r.status_code == 200
    assert _has_basic_headers(r)


# ── restore extraction ────────────────────────────────────────────────────────

def test_restore_skips_a_cover_entry_that_escapes(tmp_path: Path):
    import sqlite3
    from backend.services.instance_backup import (
        apply_staged_restore_if_present, create_backup_tarball, staged_path,
    )

    def mini(path):
        con = sqlite3.connect(str(path))
        con.execute("CREATE TABLE users (id INTEGER PRIMARY KEY)")
        con.execute("CREATE TABLE books (id INTEGER PRIMARY KEY)")
        con.commit()
        con.close()

    data = tmp_path / "data"
    data.mkdir()
    db = data / "tome.db"
    mini(db)
    covers = data / "covers"
    covers.mkdir()
    src = tmp_path / "src.db"
    mini(src)
    src_covers = tmp_path / "src-covers"
    src_covers.mkdir()
    (src_covers / "1.jpg").write_bytes(b"ok")
    tarball = create_backup_tarball(src, src_covers, "1.0.0")

    # Re-pack with an extra member whose name is absolute once "covers/" is cut.
    evil_target = tmp_path / "escaped.txt"
    repacked = tmp_path / "repacked.tar.gz"
    with tarfile.open(tarball) as old, tarfile.open(repacked, "w:gz") as new:
        for m in old.getmembers():
            new.addfile(m, old.extractfile(m) if m.isfile() else None)
        info = tarfile.TarInfo("covers/" + str(evil_target))
        info.size = 4
        new.addfile(info, io.BytesIO(b"evil"))
    tarball.unlink()
    repacked.rename(staged_path(data))

    assert apply_staged_restore_if_present(data, db, covers) is True
    assert (covers / "1.jpg").read_bytes() == b"ok"
    assert not evil_target.exists()
