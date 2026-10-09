"""Telemetry consent: what would be sent, and the admin's decision about it.

There is no send endpoint on purpose. The only sends are the daily background
check and the one right after a grant, both gated on ``telemetry.may_send``.
"""
import threading

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.core.database import SessionLocal, get_db
from backend.core.security import get_current_admin
from backend.models.user import User
from backend.services import telemetry
from backend.services.audit import audit

router = APIRouter(prefix="/admin/telemetry", tags=["admin"])


class ConsentBody(BaseModel):
    decision: str  # "granted" | "declined"


@router.get("")
def get_telemetry(db: Session = Depends(get_db), _: User = Depends(get_current_admin)) -> dict:
    """Consent state plus the exact report a send would carry."""
    return {"consent": telemetry.consent_state(db), "report": telemetry.build_report(db)}


@router.post("/consent")
def set_consent(
    body: ConsentBody,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> dict:
    if body.decision not in ("granted", "declined"):
        raise HTTPException(status_code=422, detail="decision must be granted or declined")
    if body.decision == "granted" and telemetry.consent_state(db)["state"] == "env_off":
        raise HTTPException(status_code=409, detail="Telemetry is disabled by TOME_TELEMETRY")
    state = telemetry.record_consent(db, body.decision)
    audit(
        db, "telemetry.consent", user_id=admin.id, username=admin.username,
        resource_type="instance", details={"decision": body.decision, "schema": telemetry.REPORT_SCHEMA},
        ip=request.client.host if request.client else None,
    )
    if body.decision == "granted":
        # One report at opt-in, off the request thread. The sender re-checks
        # consent itself, so this can never send after a later decline.
        threading.Thread(target=_send_once, daemon=True, name="telemetry-first-send").start()
    return {"consent": state}


def _send_once() -> None:
    with SessionLocal() as db:
        telemetry.send_if_due(db)
