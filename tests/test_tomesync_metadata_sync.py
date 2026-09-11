"""TomeSync metadata sync (Tome -> KOReader custom metadata), issue #210.

Server half: ``POST /tome-sync/metadata`` answers only for (book, hash) pairs
Tome has on record, projects Tome's fields onto KOReader's custom-metadata
keys, and short-circuits unchanged books via a content fingerprint.
Plugin half: contract tests over the generated Lua (see the bottom half).
"""
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from backend.api.tome_sync import _main_impl_lua
from backend.core.config import settings
from backend.models.book import BookTag
from backend.models.library import Library
from backend.models.tome_sync import ApiKey
from backend.models.user import User
from backend.core.security import hash_password
from backend.services.device_metadata import (
    device_metadata, metadata_fingerprint, projected_props,
)
from backend.services.ko_hash import record_ko_hash


# ── helpers ──────────────────────────────────────────────────────────────────

def _api_key_for(db, user_id: int) -> dict:
    plaintext = ApiKey.generate()
    db.add(ApiKey(user_id=user_id, key_hash=ApiKey.hash_key(plaintext),
                  key_prefix=plaintext[:11], label="test"))
    db.flush()
    return {"Authorization": f"Bearer {plaintext}"}


def _member(db, name="member") -> User:
    u = User(username=name, email=f"{name}@x.test", hashed_password=hash_password("pw"),
             is_active=True, is_admin=False, role="member", must_change_password=False)
    db.add(u)
    db.flush()
    return u


def _post(client, hdr, items):
    return client.post("/api/tome-sync/metadata", headers=hdr, json={"items": items})


@pytest.fixture
def covers(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.core.config.settings.data_dir", tmp_path / "data")
    settings.covers_dir.mkdir(parents=True, exist_ok=True)
    return settings.covers_dir


# ── projection ───────────────────────────────────────────────────────────────

def test_projection_uses_koreader_keys_and_drops_empties(db, make_book):
    book = make_book(title="  Vol 2  ", author="A. Writer", series="Saga", series_index=2.0,
                     language="en", description="", tags=["Fantasy", " Isekai ", "fantasy"])
    props = projected_props(book)
    assert props == {
        "title": "Vol 2", "authors": "A. Writer", "series": "Saga", "series_index": 2,
        "language": "en", "keywords": "Fantasy\nIsekai\nfantasy",
    }
    assert "description" not in props          # empty string -> not sent
    assert set(props) <= {"title", "authors", "series", "series_index", "language",
                          "keywords", "description"}


def test_projection_fractional_index_and_index_without_series(db, make_book):
    assert projected_props(make_book(series="S", series_index=1.5))["series_index"] == 1.5
    assert "series_index" not in projected_props(make_book(series=None, series_index=3))


def test_fingerprint_tracks_tags_and_cover_bytes_not_updated_at(db, make_book, covers):
    book = make_book(title="FP", tags=["a"])
    fp0 = metadata_fingerprint(book)
    assert metadata_fingerprint(book) == fp0          # stable

    # Tag-only edit: the books row is untouched, the fingerprint still moves.
    book.tags.append(BookTag(book_id=book.id, tag="b", source="user"))
    db.flush()
    fp1 = metadata_fingerprint(book)
    assert fp1 != fp0

    # Cover appears, then is replaced under the same filename.
    cover = covers / "book_fp_cover.jpg"
    cover.write_bytes(b"\xff\xd8one")
    book.cover_path = cover.name
    db.flush()
    fp2 = metadata_fingerprint(book)
    assert fp2 != fp1 and device_metadata(book)["cover"] is True
    cover.write_bytes(b"\xff\xd8two-bytes")
    os.utime(cover, ns=(1, 1))
    assert metadata_fingerprint(book) != fp2

    # Missing cover file -> no cover, no crash.
    book.cover_path = "gone.jpg"
    assert device_metadata(book)["cover"] is False


# ── endpoint ─────────────────────────────────────────────────────────────────

def test_answers_only_for_hash_tome_has_on_record(client, db, make_book, admin_user):
    user, _ = admin_user
    hdr = _api_key_for(db, user.id)
    good = make_book(title="Good", series="S", series_index=1)
    other = make_book(title="Other")
    record_ko_hash(db, good.id, "a" * 32)
    record_ko_hash(db, other.id, "b" * 32)
    db.commit()

    r = _post(client, hdr, [
        {"book_id": good.id, "ko_md5": "a" * 32},          # right pair
        {"book_id": good.id, "ko_md5": "b" * 32},          # other book's hash -> rejected
        {"book_id": other.id, "ko_md5": "c" * 32},         # unknown hash -> rejected
        {"book_id": 999999, "ko_md5": "a" * 32},           # unknown book -> rejected
    ])
    assert r.status_code == 200, r.text
    body = r.json()
    assert [b["book_id"] for b in body["books"]] == [good.id]
    assert body["rejected"] == [
        {"book_id": good.id, "ko_md5": "b" * 32},
        {"book_id": other.id, "ko_md5": "c" * 32},
        {"book_id": 999999, "ko_md5": "a" * 32},
    ]
    assert body["unchanged"] == []
    entry = body["books"][0]
    assert entry["ko_md5"] == "a" * 32
    assert entry["props"] == {"title": "Good", "authors": "Test Author", "series": "S",
                              "series_index": 1, "language": "en"}
    assert entry["cover"] is False
    assert entry["fingerprint"] == metadata_fingerprint(good)


def test_fingerprint_roundtrip_reports_unchanged_until_an_edit(client, db, make_book, admin_user):
    user, _ = admin_user
    hdr = _api_key_for(db, user.id)
    book = make_book(title="Round")
    record_ko_hash(db, book.id, "d" * 32)
    db.commit()

    first = _post(client, hdr, [{"book_id": book.id, "ko_md5": "d" * 32}]).json()
    fp = first["books"][0]["fingerprint"]

    again = _post(client, hdr, [{"book_id": book.id, "ko_md5": "d" * 32, "fingerprint": fp}]).json()
    assert again == {"books": [], "rejected": [],
                     "unchanged": [{"book_id": book.id, "ko_md5": "d" * 32}]}

    book.title = "Round, revised"
    db.commit()
    third = _post(client, hdr, [{"book_id": book.id, "ko_md5": "d" * 32, "fingerprint": fp}]).json()
    assert third["unchanged"] == [] and third["books"][0]["props"]["title"] == "Round, revised"
    assert third["books"][0]["fingerprint"] != fp


def test_same_book_two_device_files_answered_per_hash(client, db, make_book, admin_user):
    user, _ = admin_user
    hdr = _api_key_for(db, user.id)
    book = make_book(title="Twice")
    record_ko_hash(db, book.id, "a" * 32, kind="raw")
    record_ko_hash(db, book.id, "b" * 32, kind="baked")
    db.commit()
    body = _post(client, hdr, [
        {"book_id": book.id, "ko_md5": "a" * 32},
        {"book_id": book.id, "ko_md5": "b" * 32},
        {"book_id": book.id, "ko_md5": "c" * 32},
    ]).json()
    assert [(b["book_id"], b["ko_md5"]) for b in body["books"]] == [(book.id, "a" * 32), (book.id, "b" * 32)]
    assert body["rejected"] == [{"book_id": book.id, "ko_md5": "c" * 32}]


def test_invisible_and_inactive_books_are_rejected(client, db, make_book, admin_user):
    admin, _ = admin_user
    member = _member(db)
    hdr = _api_key_for(db, member.id)

    private = Library(name="Admin only", is_public=False, owner_id=admin.id)
    db.add(private)
    db.flush()
    hidden = make_book(title="Hidden")
    hidden.libraries.append(private)
    record_ko_hash(db, hidden.id, "e" * 32)

    deleted = make_book(title="Deleted")
    deleted.status = "deleted"
    record_ko_hash(db, deleted.id, "f" * 32)

    public = make_book(title="Public")           # admin upload, no library -> visible
    record_ko_hash(db, public.id, "0" * 32)
    db.commit()

    body = _post(client, hdr, [
        {"book_id": hidden.id, "ko_md5": "e" * 32},
        {"book_id": deleted.id, "ko_md5": "f" * 32},
        {"book_id": public.id, "ko_md5": "0" * 32},
    ]).json()
    assert [r["book_id"] for r in body["rejected"]] == [hidden.id, deleted.id]
    assert [b["book_id"] for b in body["books"]] == [public.id]


def test_requires_api_key_and_caps_batch(client, db, make_book, admin_user):
    user, _ = admin_user
    assert client.post("/api/tome-sync/metadata", json={"items": []}).status_code in (401, 422)
    hdr = _api_key_for(db, user.id)
    assert _post(client, hdr, []).json() == {"books": [], "unchanged": [], "rejected": []}
    # Lua rapidjson encodes an empty table as {} - must not 422.
    r = client.post("/api/tome-sync/metadata", headers=hdr, json={"items": {}})
    assert r.status_code == 200
    # Over the cap: the tail is silently dropped (plugin chunks well below it).
    items = [{"book_id": 1, "ko_md5": "1" * 32}] * 150
    assert len(_post(client, hdr, items).json()["rejected"]) == 100


def test_cover_flag_and_keywords_on_the_wire(client, db, make_book, admin_user, covers):
    user, _ = admin_user
    hdr = _api_key_for(db, user.id)
    (covers / "c.jpg").write_bytes(b"\xff\xd8\xff")
    book = make_book(title="Covered", tags=["x", "y"], description="Long text",
                     cover_path="c.jpg")
    record_ko_hash(db, book.id, "9" * 32)
    db.commit()
    entry = _post(client, hdr, [{"book_id": book.id, "ko_md5": "9" * 32}]).json()["books"][0]
    assert entry["cover"] is True
    assert entry["props"]["keywords"] == "x\ny"
    assert entry["props"]["description"] == "Long text"


# ── plugin (generated Lua) contract ──────────────────────────────────────────

def _impl() -> str:
    return _main_impl_lua("https://tome.example.org", "tk_testkey", "tester")


def _body(lua: str, func: str) -> str:
    start = lua.find(f"\nfunction TomeSync:{func}")
    assert start != -1, f"missing function {func}"
    return lua[start:lua.find("\nfunction ", start + 1)]


def test_plugin_compiles_under_luajit():
    luajit = shutil.which("luajit")
    if luajit is None:
        pytest.skip("luajit not installed")
    with tempfile.NamedTemporaryFile(suffix=".lua", delete=False, mode="w") as f:
        f.write(_impl())
        path = f.name
    try:
        r = subprocess.run([luajit, "-bl", path], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
    finally:
        Path(path).unlink(missing_ok=True)


def test_plugin_has_metadata_sync_surface():
    lua = _impl()
    for fn in ("_syncMetadata", "_syncMetadataImpl", "_applyDeviceMetadata", "_originalProps"):
        assert f"\nfunction TomeSync:{fn}" in lua, fn
    assert '"/tome-sync/metadata"' in lua
    # Off by default, per-device, lives with the other boolean preferences.
    assert 'G_reader_settings:isTrue("tomesync_meta_sync")' in lua
    # The per-file ledger is a data table -> dedicated state file, pruned with book_map.
    assert '"tomesync_meta_ledger"' in lua
    assert 'self.state:readSetting("tomesync_meta_ledger")' in _body(lua, "init")
    assert "meta_ledger" in _body(lua, "_pruneState")
    assert 'G_reader_settings:saveSetting("tomesync_meta_ledger"' not in lua


def test_plugin_writes_koreader_custom_metadata_not_the_file():
    apply = _body(_impl(), "_applyDeviceMetadata")
    # KOReader's own override layer: custom_props + a copy of the original
    # doc_props (the "reset" button indexes it), flushed via DocSettings.
    assert 'saveSetting("doc_props"' in apply
    assert 'saveSetting("custom_props"' in apply
    assert "flushCustomMetadata(" in apply
    assert "flushCustomCover(" in apply
    # Read-back through KOReader's own lookup: a stale copy shadowing the
    # read-only fallback location must count as a failure, not success.
    assert "shadowed by a stale sidecar copy" in apply
    # The book file is never opened for writing.
    assert 'io.open(path, "w' not in apply and 'io.open(path, "a' not in apply
    # Displays are told: cover-browser DB row dropped + generic metadata event.
    assert 'Event:new("InvalidateMetadataCache"' in apply
    assert 'Event:new("BookMetadataChanged")' in _body(_impl(), "_syncMetadataImpl")


def test_plugin_reapplies_after_a_device_side_edit():
    """The fingerprint short-circuit must not let a device edit/reset stick:
    the sidecar signature is recorded after a write and checked before the
    fingerprint is trusted."""
    lua = _impl()
    assert "\nlocal function sidecarSig(path)" in lua
    assert "sidecarSig(path)" in _body(lua, "_applyDeviceMetadata")
    impl = _body(lua, "_syncMetadataImpl")
    assert "led.sig == sidecarSig(c.path)" in impl


def test_plugin_reverts_its_writes_when_tome_stops_vouching():
    """A file synced earlier that is later rejected (replaced under the same
    name, book deleted in Tome) must not keep the old book's metadata."""
    lua = _impl()
    revoke = _body(lua, "_revokeDeviceMetadata")
    assert "for k in pairs(prev_keys)" in revoke          # only keys we own
    assert "findCustomCoverFile(path)" in revoke and "os.remove(cover)" in revoke
    impl = _body(lua, "_syncMetadataImpl")
    rejected = impl[impl.find("each(resp.rejected"):impl.find("each(resp.books")]
    assert "_revokeDeviceMetadata" in rejected
    assert "prev.keys" in rejected and "prev.cover" in rejected


def test_plugin_skips_open_book_and_gates_on_setting():
    impl = _body(_impl(), "_syncMetadataImpl")
    # The book currently open in the reader is left for the next run - its
    # in-memory doc_props would otherwise disagree with the sidecar.
    assert "self.ui.document.file" in impl
    sync = _body(_impl(), "_syncMetadata")
    assert 'G_reader_settings:isTrue("tomesync_meta_sync")' in sync
    # Automatic runs are debounced (launch + NetworkConnected fire together);
    # the manual action is not.
    assert "os.time() - meta_last_auto < 120" in sync
    assert "if not interactive then" in sync


def test_plugin_reports_old_server_distinctly():
    impl = _body(_impl(), "_syncMetadataImpl")
    assert "code == 404 or code == 405" in impl
    assert "does not support metadata sync yet" in impl


def test_plugin_menu_and_triggers():
    lua = _impl()
    menu = _body(lua, "_menuItems")
    assert "Apply Tome metadata to this device" in menu     # settings toggle
    assert "Apply Tome metadata now" in menu                 # always-visible action
    assert "_syncMetadata(" in _body(lua, "init")
    assert "_syncMetadata(" in _body(lua, "onNetworkConnected")
    assert "_syncMetadata(" in _body(lua, "_sweepLibraryImpl")
