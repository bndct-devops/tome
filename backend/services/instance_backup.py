"""Instance backup & restore — the whole curation in one tarball.

Backup: a consistent SQLite snapshot (sqlite3 backup API — safe under WAL,
never a raw copy of a live DB), the cover cache, and a manifest. Library
files are NOT included: Tome references them on disk and they are the
operator's own data to back up.

Restore is two-phase by design: the upload is validated (tar shape,
manifest, the DB actually opens and passes integrity_check) and STAGED next
to the data dir; the swap happens at the next server start, before the
engine exists — never under a live connection pool. The current DB is kept
as tome.db.pre-restore-<ts> so even a restored-the-wrong-file mistake is
recoverable. Any failure during apply leaves the current DB untouched and
parks the staging file as .failed.
"""
from __future__ import annotations

import json
import logging
import shutil
import sqlite3
import tarfile
import tempfile
import time
from pathlib import Path

log = logging.getLogger(__name__)

STAGED_NAME = "restore_staged.tar.gz"
_MEMBER_DB = "tome.db"
_MEMBER_MANIFEST = "manifest.json"
_MEMBER_COVERS = "covers"


def create_backup_tarball(db_path: Path, covers_dir: Path, version: str) -> Path:
    """Build the tarball in a temp file and return its path (caller deletes)."""
    tmp_dir = Path(tempfile.mkdtemp(prefix="tome-backup-"))
    snap = tmp_dir / _MEMBER_DB
    # Consistent snapshot even mid-write: the sqlite backup API copies a
    # transactionally coherent image, which a plain file copy of a WAL db is not.
    src = sqlite3.connect(str(db_path))
    try:
        dst = sqlite3.connect(str(snap))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()

    manifest = {
        "app": "tome",
        "version": version,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    out = Path(tempfile.mkstemp(prefix="tome-backup-", suffix=".tar.gz")[1])
    with tarfile.open(out, "w:gz") as tar:
        tar.add(snap, arcname=_MEMBER_DB)
        mpath = tmp_dir / _MEMBER_MANIFEST
        mpath.write_text(json.dumps(manifest, indent=2))
        tar.add(mpath, arcname=_MEMBER_MANIFEST)
        if covers_dir.is_dir():
            tar.add(covers_dir, arcname=_MEMBER_COVERS)
    shutil.rmtree(tmp_dir, ignore_errors=True)
    return out


def validate_backup(path: Path) -> dict:
    """Raise ValueError if the archive isn't a healthy Tome backup; else a
    summary dict {version, created_at, users, books}."""
    try:
        with tarfile.open(path, "r:gz") as tar:
            names = set(tar.getnames())
            if _MEMBER_DB not in names or _MEMBER_MANIFEST not in names:
                raise ValueError("Not a Tome backup (missing tome.db or manifest.json)")
            manifest = json.loads(tar.extractfile(_MEMBER_MANIFEST).read().decode())
            if manifest.get("app") != "tome":
                raise ValueError("Not a Tome backup (manifest mismatch)")
            with tempfile.TemporaryDirectory(prefix="tome-restore-check-") as td:
                tar.extract(_MEMBER_DB, td)
                db_file = Path(td) / _MEMBER_DB
                con = sqlite3.connect(str(db_file))
                try:
                    ok = con.execute("PRAGMA integrity_check").fetchone()[0]
                    if ok != "ok":
                        raise ValueError(f"Database failed integrity check: {ok}")
                    users = con.execute("SELECT COUNT(*) FROM users").fetchone()[0]
                    books = con.execute("SELECT COUNT(*) FROM books").fetchone()[0]
                finally:
                    con.close()
    except (tarfile.TarError, OSError, json.JSONDecodeError, sqlite3.DatabaseError) as exc:
        raise ValueError(f"Unreadable backup archive: {exc}") from exc
    return {
        "version": manifest.get("version"),
        "created_at": manifest.get("created_at"),
        "users": users,
        "books": books,
    }


def staged_path(data_dir: Path) -> Path:
    return data_dir / STAGED_NAME


def apply_staged_restore_if_present(data_dir: Path, db_path: Path, covers_dir: Path) -> bool:
    """Called at startup BEFORE the engine exists. Returns True if a restore
    was applied. Failure never touches the current DB."""
    staged = staged_path(data_dir)
    if not staged.is_file():
        return False
    log.warning("Staged restore found — applying %s", staged)
    try:
        summary = validate_backup(staged)
        ts = time.strftime("%Y%m%d-%H%M%S")
        if db_path.exists():
            shutil.copy2(db_path, db_path.with_name(f"{db_path.name}.pre-restore-{ts}"))
        with tarfile.open(staged, "r:gz") as tar:
            with tempfile.TemporaryDirectory(prefix="tome-restore-") as td:
                tar.extract(_MEMBER_DB, td)
                # Replace the DB and drop stale WAL/SHM sidecars from the old one.
                shutil.move(str(Path(td) / _MEMBER_DB), str(db_path))
            for suffix in ("-wal", "-shm"):
                side = db_path.with_name(db_path.name + suffix)
                side.unlink(missing_ok=True)
            cover_members = [m for m in tar.getmembers()
                             if m.name.startswith(_MEMBER_COVERS + "/") and m.isfile()
                             and ".." not in m.name]
            if cover_members:
                covers_dir.mkdir(parents=True, exist_ok=True)
                for m in cover_members:
                    m.name = m.name[len(_MEMBER_COVERS) + 1:]
                    tar.extract(m, covers_dir)
        staged.unlink(missing_ok=True)
        # Leave a marker for the app to audit-log once the DB is up — this
        # runs before any engine exists, so it cannot write the entry itself.
        try:
            (data_dir / "restore_applied.json").write_text(json.dumps({
                "applied_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                **summary,
            }))
        except OSError:
            pass
        log.warning("Restore applied: %s users, %s books (backup from %s)",
                    summary["users"], summary["books"], summary["created_at"])
        return True
    except Exception:  # noqa: BLE001 — park it, never brick startup
        log.exception("Staged restore FAILED — keeping current database")
        try:
            staged.rename(staged.with_suffix(".failed"))
        except OSError:
            pass
        return False
