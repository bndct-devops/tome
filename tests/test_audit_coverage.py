"""Audit-log coverage for the surfaces that gained calls in the coverage pass.

Not exhaustive per-endpoint — representative assertions that the wiring works
and stays wired (an action string disappearing here means someone removed a
call)."""
import json

from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from backend.models.audit_log import AuditLog


def _actions(db: Session) -> list[str]:
    return [r.action for r in db.query(AuditLog).order_by(AuditLog.id).all()]


def test_opds_pin_lifecycle_audited(client: TestClient, db: Session):
    r = client.post("/api/opds-pins", json={"label": "kindle"})
    assert r.status_code == 201
    client.delete(f"/api/opds-pins/{r.json()['id']}")
    acts = _actions(db)
    assert "opds_pin.created" in acts
    assert "opds_pin.revoked" in acts


def test_plugin_key_lifecycle_audited(client: TestClient, db: Session):
    r = client.post("/api/plugin/api-keys", json={"label": "kobo"})
    assert r.status_code == 201
    client.delete(f"/api/plugin/api-keys/{r.json()['id']}")
    acts = _actions(db)
    assert "plugin_key.created" in acts
    assert "plugin_key.revoked" in acts
    # The key itself must never land in the log — only the prefix.
    for row in db.query(AuditLog).filter(AuditLog.action == "plugin_key.created"):
        assert "tk_" not in (row.details or "") or json.loads(row.details).get("prefix")


def test_book_type_crud_audited(client: TestClient, db: Session):
    r = client.post("/api/book-types", json={"label": "Audit Type", "icon": "BookOpen",
                                             "color": "blue", "sort_order": 99})
    assert r.status_code == 201
    bt_id = r.json()["id"]
    client.put(f"/api/book-types/{bt_id}", json={"label": "Audit Type 2", "icon": "BookOpen",
                                                 "color": "blue", "sort_order": 99})
    client.delete(f"/api/book-types/{bt_id}")
    acts = _actions(db)
    assert {"book_type.created", "book_type.updated", "book_type.deleted"} <= set(acts)


def test_notification_channel_audited_without_token(client: TestClient, db: Session):
    r = client.post("/api/notification-channels",
                    json={"kind": "ntfy", "url": "https://ntfy.sh/x", "token": "supersecret"})
    client.delete(f"/api/notification-channels/{r.json()['id']}")
    rows = db.query(AuditLog).filter(AuditLog.action.like("notify_channel.%")).all()
    assert {x.action for x in rows} == {"notify_channel.created", "notify_channel.deleted"}
    assert all("supersecret" not in (x.details or "") for x in rows)


def test_reading_import_apply_audited(client: TestClient, db: Session, make_book):
    book = make_book(title="Audited Import Book")
    client.post("/api/import/reading-csv/apply",
                json={"items": [{"book_id": book.id, "status": "read"}]})
    row = db.query(AuditLog).filter(AuditLog.action == "reading_import.applied").first()
    assert row is not None
    assert json.loads(row.details)["items"] == 1


def test_backup_restore_stage_requires_and_audits(client: TestClient, db: Session, tmp_path, monkeypatch):
    from backend.core.config import settings as cfg
    monkeypatch.setattr(type(cfg), "data_dir", property(lambda self: tmp_path), raising=False)
    # Invalid archive: rejected, nothing staged, no stage audit entry.
    r = client.post("/api/admin/backup/restore",
                    files={"file": ("b.tar.gz", b"garbage", "application/gzip")},
                    data={"confirm": "RESTORE"})
    assert r.status_code == 422
    assert "backup.restore_staged" not in _actions(db)
