"""Admin instance backup/restore endpoints.

Download is a live consistent snapshot; restore is validate-and-stage — the
actual swap happens at the next server start (see services/instance_backup).
"""
from __future__ import annotations

import logging
import os
import shutil
import tempfile
import time
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from backend import __version__
from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.core.database import get_db
from backend.core.permissions import is_admin
from backend.core.security import get_current_user
from backend.models.user import User
from backend.services.audit import audit
from backend.services.instance_backup import (
    create_backup_tarball,
    staged_path,
    validate_backup,
)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/backup", tags=["admin"])


def _require_admin(user: User) -> None:
    if not is_admin(user):
        raise HTTPException(status_code=403, detail="Admin only")


@router.get("/download")
def download_backup(
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_admin(current_user)
    path = create_backup_tarball(settings.db_path, settings.covers_dir, __version__)
    audit(db, "backup.downloaded", user_id=current_user.id,
          username=current_user.username,
          details={"size_bytes": path.stat().st_size})
    background.add_task(lambda: path.unlink(missing_ok=True))
    stamp = time.strftime("%Y%m%d-%H%M")
    return FileResponse(
        path,
        media_type="application/gzip",
        filename=f"tome-backup-{stamp}.tar.gz",
    )


@router.get("/restore")
def restore_status(current_user: User = Depends(get_current_user)) -> dict:
    _require_admin(current_user)
    staged = staged_path(settings.data_dir)
    if not staged.is_file():
        return {"staged": False}
    try:
        summary = validate_backup(staged)
    except ValueError:
        return {"staged": True, "summary": None}
    return {"staged": True, "summary": summary}


@router.post("/restore")
def stage_restore(
    file: UploadFile = File(...),
    confirm: str = Form(""),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    _require_admin(current_user)
    if confirm != "RESTORE":
        raise HTTPException(status_code=422, detail='Type RESTORE to confirm')

    tmp = Path(tempfile.mkstemp(prefix="tome-restore-upload-", suffix=".tar.gz")[1])
    try:
        with tmp.open("wb") as out:
            shutil.copyfileobj(file.file, out, length=1024 * 1024)
        try:
            summary = validate_backup(tmp)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        target = staged_path(settings.data_dir)
        shutil.move(str(tmp), str(target))
    finally:
        tmp.unlink(missing_ok=True)
    audit(db, "backup.restore_staged", user_id=current_user.id,
          username=current_user.username, details=summary)
    log.warning("Restore staged by %s: %s", current_user.username, summary)
    return {"staged": True, "requires_restart": True, "summary": summary}


@router.delete("/restore")
def unstage_restore(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    _require_admin(current_user)
    staged = staged_path(settings.data_dir)
    existed = staged.is_file()
    staged.unlink(missing_ok=True)
    if existed:
        audit(db, "backup.restore_cancelled", user_id=current_user.id,
              username=current_user.username)
    return {"ok": True, "was_staged": existed}
