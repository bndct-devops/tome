"""Auto-import leaves files alone while they may still be copying.

A file dropped into the Bindery over a network share appears at full name
long before its last byte lands. Hashing and moving it then imports a broken
book, so files modified in the last AUTO_IMPORT_SETTLE_SECONDS are skipped
until a later scan.
"""
import os
import time

from backend import main


def _touch(path, age_seconds: float) -> None:
    path.write_bytes(b"PK\x03\x04 invented")
    t = time.time() - age_seconds
    os.utime(path, (t, t))


def test_recent_files_are_skipped_until_they_settle(tmp_path):
    settle = main.AUTO_IMPORT_SETTLE_SECONDS
    fresh = tmp_path / "Glass Orchard - Invented Author.epub"
    settled = tmp_path / "sub" / "Hollow Meridian - Invented Author.cbz"
    settled.parent.mkdir()
    _touch(fresh, 1)
    _touch(settled, settle + 5)

    found = main._bindery_files_to_import(tmp_path)
    assert settled in found
    assert fresh not in found

    _touch(fresh, settle + 1)
    assert fresh in main._bindery_files_to_import(tmp_path)


def test_hidden_and_unsupported_files_are_still_skipped(tmp_path):
    old = main.AUTO_IMPORT_SETTLE_SECONDS + 60
    _touch(tmp_path / "notes.txt", old)
    hidden = tmp_path / ".partial" / "Paper Lanterns.epub"
    hidden.parent.mkdir()
    _touch(hidden, old)
    _touch(tmp_path / "Paper Lanterns.epub", old)
    assert main._bindery_files_to_import(tmp_path) == [tmp_path / "Paper Lanterns.epub"]
