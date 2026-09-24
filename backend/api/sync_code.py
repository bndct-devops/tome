"""POST /api/sync-code - apply a KOReader sync code scanned by a phone.

The phone posts the raw QR page strings; the server decodes, dedups and writes
them for the signed-in user (JWT, device token or API token - the same auth
as every other /api endpoint) and returns an overview of what landed. See
backend/services/sync_code.py for the format and the write rules.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.core.database import get_db
from backend.core.security import get_current_user
from backend.models.user import User
from backend.services.sync_code import MAX_PAGES, SyncCodeError, apply_payload, assemble

router = APIRouter(tags=["sync-code"])


class SyncCodeRequest(BaseModel):
    pages: list[str] = Field(min_length=1, max_length=MAX_PAGES * 2)


@router.post("/sync-code")
def post_sync_code(
    body: SyncCodeRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        payload = assemble(body.pages)
    except SyncCodeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return apply_payload(db, user, payload)
