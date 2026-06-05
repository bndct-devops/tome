"""Send-to-Device names files the KOReader/OPDS way so TomeSync resolves them.

The bug: Send-to emailed files as a bare ``<title>.ext``, which `/tome-sync/resolve`
(built for KOReader's ``Author - Vol. X — Title.ext`` OPDS naming) couldn't reliably
match. Fix is server-only: name the attachment the KOReader way. The resolver is
unchanged — these tests drive the real endpoint to prove the new names resolve.
"""
import tempfile
from pathlib import Path

from backend.models.tome_sync import ApiKey
from backend.services.organizer import koreader_style_name
from backend.services.email import _build_book_message


def _api_key_for(db, user_id: int) -> str:
    plaintext = ApiKey.generate()
    db.add(ApiKey(user_id=user_id, key_hash=ApiKey.hash_key(plaintext),
                  key_prefix=plaintext[:11], label="test"))
    db.flush()
    return plaintext


def _resolve(client, key, filename):
    return client.get(
        "/api/tome-sync/resolve",
        params={"filename": filename},
        headers={"Authorization": f"Bearer {key}"},
    )


# ── helper formatting ─────────────────────────────────────────────────────────

def test_name_series_uses_author_volume_title():
    assert koreader_style_name("Isuna Hasekura", "Spice and Wolf", 1, "epub") == \
        "Isuna Hasekura - Vol. 1 — Spice and Wolf.epub"


def test_name_standalone_is_author_title():
    assert koreader_style_name("Frank Herbert", "Dune", None, "epub") == \
        "Frank Herbert - Dune.epub"


def test_name_decimal_volume_and_missing_author():
    assert koreader_style_name("A", "B", 10.5, "cbz") == "A - Vol. 10.5 — B.cbz"
    assert koreader_style_name(None, "Lonely", None, "epub") == "Unknown - Lonely.epub"


# ── resolve end-to-end (resolver unchanged) ───────────────────────────────────

def test_series_style_name_resolves(client, db, admin_user, make_book):
    user, _ = admin_user
    key = _api_key_for(db, user.id)
    book = make_book(title="Spice and Wolf", author="Isuna Hasekura",
                     series="Spice and Wolf", series_index=1)
    name = koreader_style_name(book.author, book.title, book.series_index, "epub")

    r = _resolve(client, key, name)
    assert r.status_code == 200
    assert r.json()["book_id"] == book.id


def test_standalone_style_name_resolves(client, db, admin_user, make_book):
    user, _ = admin_user
    key = _api_key_for(db, user.id)
    book = make_book(title="Dune", author="Frank Herbert")
    name = koreader_style_name(book.author, book.title, None, "epub")

    r = _resolve(client, key, name)
    assert r.status_code == 200
    assert r.json()["book_id"] == book.id


# ── send side wiring ──────────────────────────────────────────────────────────

def test_sent_attachment_uses_koreader_name():
    name = koreader_style_name("Frank Herbert", "Dune", None, "epub")
    with tempfile.NamedTemporaryFile(suffix=".epub") as tmp:
        tmp.write(b"enough bytes to be a file")
        tmp.flush()
        msg = _build_book_message("reader@example.com", "Dune", name, Path(tmp.name), "epub")

    # Subject stays the human title; the attachment carries the resolvable name.
    assert msg["Subject"] == "Dune"
    assert msg.get_payload()[1].get_filename() == "Frank Herbert - Dune.epub"
