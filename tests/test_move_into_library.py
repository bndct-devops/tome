"""move_into_library must not leave a half-finished copy behind (#177).

``/bindery`` and ``/books`` are usually separate mounts, so ``shutil.move``
copies then unlinks. On NAS ACLs that grant create-but-not-delete the copy
succeeds and the unlink raises; the library must be left untouched.
"""
import shutil
from pathlib import Path

import pytest

from backend.core.config import Settings
from backend.services import organizer
from backend.services.organizer import move_into_library


def _copy_then_fail_unlink(src: str, dst: str) -> None:
    """Simulate a cross-device move whose source unlink is refused."""
    shutil.copy2(src, dst)
    raise PermissionError(13, "Permission denied", src)


def test_move_succeeds_normally(tmp_path: Path) -> None:
    src = tmp_path / "in" / "book.epub"
    src.parent.mkdir()
    src.write_bytes(b"epub")
    dest = tmp_path / "lib" / "Author" / "book.epub"
    (tmp_path / "lib").mkdir()

    move_into_library(src, dest)

    assert not src.exists()
    assert dest.read_bytes() == b"epub"


def test_failed_unlink_rolls_back_created_parent_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty dirs created for the move must go too: they keep the failed
    attempt's uid and would block a retry under a corrected PUID/PGID."""
    src = tmp_path / "in" / "book.epub"
    src.parent.mkdir()
    src.write_bytes(b"epub")
    lib = tmp_path / "lib"
    lib.mkdir()
    dest = lib / "Series" / "Sub" / "book.epub"

    monkeypatch.setattr(shutil, "move", _copy_then_fail_unlink)
    with pytest.raises(PermissionError):
        move_into_library(src, dest)

    assert not (lib / "Series").exists()
    assert lib.exists(), "the library root itself is never touched"
    assert src.exists()


def test_failure_keeps_preexisting_parent_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = tmp_path / "in" / "book.epub"
    src.parent.mkdir()
    src.write_bytes(b"epub")
    lib = tmp_path / "lib"
    (lib / "Series").mkdir(parents=True)
    (lib / "Series" / "other.epub").write_bytes(b"x")
    dest = lib / "Series" / "book.epub"

    monkeypatch.setattr(shutil, "move", _copy_then_fail_unlink)
    with pytest.raises(PermissionError):
        move_into_library(src, dest)

    assert (lib / "Series" / "other.epub").exists()
    assert not dest.exists()


def test_failed_unlink_removes_orphan_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = tmp_path / "in" / "book.epub"
    src.parent.mkdir()
    src.write_bytes(b"epub")
    dest = tmp_path / "lib" / "Author" / "book.epub"
    (tmp_path / "lib").mkdir()

    monkeypatch.setattr(shutil, "move", _copy_then_fail_unlink)

    with pytest.raises(PermissionError):
        move_into_library(src, dest)

    assert src.exists(), "source must be untouched"
    assert not dest.exists(), "orphaned library copy must be cleaned up"
    # A retry resolves to the same path again rather than 'book (2).epub'
    assert organizer.resolve_unique_path(tmp_path / "lib", Path("Author") / "book.epub") == dest


def test_failure_never_deletes_preexisting_dest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Defensive: if dest somehow already existed, a failed move must not remove it."""
    src = tmp_path / "in" / "book.epub"
    src.parent.mkdir()
    src.write_bytes(b"new")
    dest = tmp_path / "lib" / "book.epub"
    dest.parent.mkdir()
    dest.write_bytes(b"old")

    def _fail(src_: str, dst_: str) -> None:
        raise PermissionError(13, "Permission denied", src_)

    monkeypatch.setattr(shutil, "move", _fail)

    with pytest.raises(PermissionError):
        move_into_library(src, dest)

    assert dest.read_bytes() == b"old"
    assert src.exists()


def test_secret_key_unreadable_gives_actionable_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A secret.key created by another uid must fail with the PUID/PGID hint, not a bare traceback."""
    s = Settings(secret_key=None, data_dir=tmp_path)
    key = tmp_path / "secret.key"
    key.write_text("k")

    def _deny(*a, **k):
        raise PermissionError(13, "Permission denied", str(key))

    monkeypatch.setattr(Path, "read_text", _deny)

    with pytest.raises(RuntimeError) as ei:
        s.resolve_secret_key()
    assert "PUID/PGID" in str(ei.value)
    assert "secret.key" in str(ei.value)
