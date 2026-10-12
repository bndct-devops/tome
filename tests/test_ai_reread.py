"""Read again (``reread``): admin-only delete of a book's or a series' stored
notes followed by a fresh read. FakeProvider only; books and names are made
up."""
from __future__ import annotations

import json
from contextlib import contextmanager

import pytest
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from backend.models.ai_chapter_digest import AIChapterDigest
from backend.models.ai_entity import AIEntity, AIEntityNote
from backend.models.ai_recap import AIRecap
from backend.models.ai_reconcile_verdict import AIReconcileVerdict
from backend.models.audit_log import AuditLog
from backend.models.tome_sync import ApiKey, TomeSyncPosition
from backend.models.user import User
from backend.services import ai, book_text
from backend.services.ai import companion, digest, digest_job, reread
from tests.ai_fake import FakeProvider
from tests.test_ai_digest import _epub_book, _headers, _make_user, _read, _store_digest, echo_digest


@pytest.fixture(autouse=True)
def _jobs_inline(db: Session, monkeypatch):
    @contextmanager
    def same_session():
        s = Session(bind=db.get_bind(), join_transaction_mode="create_savepoint", autoflush=False)
        try:
            yield s
        finally:
            s.close()

    digest_job.reset()
    digest.reset_triggers()
    monkeypatch.setattr(digest_job, "_spawn_worker", lambda: None)
    monkeypatch.setattr(digest_job, "_session_factory", same_session)
    monkeypatch.setattr(digest_job, "_followups", dict(digest_job._followups))
    yield
    digest_job.reset()
    digest.reset_triggers()


def _position(db: Session, user: User, book, pct: float) -> None:
    db.add(TomeSyncPosition(user_id=user.id, book_id=book.id, percentage=pct, device="Kindle"))
    db.flush()


def _mid(book, db, idx: int, frac: float = 0.5) -> float:
    ch = book_text.book_chapters(book, db)[idx]
    return ch.start_fraction + (ch.end_fraction - ch.start_fraction) * frac


def _digest_all(db, book, upto: int) -> None:
    for i in range(upto):
        _store_digest(db, book, i, [])


def _series(db, make_book, tmp_path, user, name: str):
    """Vols 1 and 2 read by ``user``, vol 3 open; every chapter of vols 1-2
    and chapters 0-1 of vol 3 digested; a recap per volume; two entities
    with notes and a verdict on the series key."""
    vols = [_epub_book(db, make_book, tmp_path, f"{name} {i}", series=name, index=i) for i in (1, 2, 3)]
    _read(db, user, vols[0])
    _read(db, user, vols[1])
    _digest_all(db, vols[0], 4)
    _digest_all(db, vols[1], 4)
    _digest_all(db, vols[2], 2)
    for v in vols:
        db.add(AIRecap(book_id=v.id, chapter_idx=4, scope_hash=digest.Scope(book=v).scope_hash,
                       position_fraction=1.0, text="A recap.", model="m", digested=4, needed=4))
    key = digest.series_key(vols[0])
    a = AIEntity(series_key=key, kind="person", name="Wren Calloway")
    b = AIEntity(series_key=key, kind="person", name="Wren")
    db.add_all([a, b])
    db.flush()
    db.add_all([AIEntityNote(entity_id=a.id, book_id=vols[0].id, chapter_idx=0, line="Sails."),
                AIEntityNote(entity_id=b.id, book_id=vols[1].id, chapter_idx=1, line="Rows.")])
    db.add(AIReconcileVerdict(series_key=key, entity_a=a.id, entity_b=b.id, same=False, confidence=0.6))
    db.commit()
    digest_job.reset()   # the setup's _read queued finished-book jobs
    return vols


def _digests(db, book) -> int:
    return db.query(AIChapterDigest).filter_by(book_id=book.id).count()


def _recaps(db, book) -> int:
    return db.query(AIRecap).filter_by(book_id=book.id).count()


# ── estimate ─────────────────────────────────────────────────────────────────

def test_estimate_counts_every_chapter_and_the_deletes(client, db, make_book, tmp_path):
    admin = _make_user(db, "rr_est", "admin")
    v1, v2, v3 = _series(db, make_book, tmp_path, admin, "Saltmere")
    _position(db, admin, v3, _mid(v3, db, 3))
    db.commit()
    h = _headers(admin)

    plain = client.get(f"/api/ai/books/{v3.id}/digest/estimate", headers=h).json()
    assert plain["book"]["chapters"] == 1            # chapters 0-1 stored, 2 missing
    assert plain["series"]["chapters"] == 1

    r = client.get(f"/api/ai/books/{v3.id}/reread/estimate", headers=h)
    assert r.status_code == 200, r.text
    out = r.json()
    assert set(out) == {"book", "series", "chapter_idx", "chapter_title", "deletes"}
    assert out["chapter_idx"] == 3
    assert out["book"]["chapters"] == 3 and out["book"]["scope"] == "book"
    assert out["book"]["volumes"] == [{"book_id": v3.id, "title": "Saltmere 3", "chapters": 3}]
    assert out["series"]["chapters"] == 11
    assert [v["chapters"] for v in out["series"]["volumes"]] == [4, 4, 3]
    assert out["series"]["est_cost_usd"] > out["book"]["est_cost_usd"] > 0
    for sc in ("book", "series"):
        assert set(out[sc]) == set(plain["book"])
        assert out[sc]["remaining_budget_usd"] is None and out[sc]["over_budget"] is False
    assert out["deletes"] == {"book": {"digests": 2, "recaps": 1},
                              "series": {"volumes": 3, "digests": 10, "recaps": 3, "entities": 2}}


def test_estimate_series_null_without_earlier_volumes(client, db, make_book, tmp_path):
    admin = _make_user(db, "rr_alone", "admin")
    book = _epub_book(db, make_book, tmp_path, "Lone Lantern")
    _digest_all(db, book, 4)
    _position(db, admin, book, _mid(book, db, 2))
    db.commit()
    out = client.get(f"/api/ai/books/{book.id}/reread/estimate", headers=_headers(admin)).json()
    assert out["series"] is None and out["book"]["chapters"] == 2
    assert out["deletes"]["book"] == {"digests": 4, "recaps": 0}


# ── refusals: nothing is deleted ─────────────────────────────────────────────

def test_member_gets_403_and_nothing_is_deleted(client, db, make_book, tmp_path):
    member = _make_user(db, "rr_member", "member")
    v1, v2, v3 = _series(db, make_book, tmp_path, member, "Brindle")
    _position(db, member, v3, _mid(v3, db, 2))
    db.commit()
    h = _headers(member)
    r = client.post(f"/api/ai/books/{v3.id}/reread", json={"scope": "series"}, headers=h)
    assert r.status_code == 403, r.text
    assert r.json() == {"detail": reread.FORBIDDEN_MESSAGE, "reason": "admin_only"}
    assert client.get(f"/api/ai/books/{v3.id}/reread/estimate", headers=h).status_code == 403
    assert _digests(db, v1) == 4 and _digests(db, v3) == 2
    assert db.query(AIEntity).count() == 2 and digest_job.running() == []


def test_budget_409_deletes_nothing(client, db, make_book, tmp_path):
    admin = _make_user(db, "rr_budget", "admin")
    v1, v2, v3 = _series(db, make_book, tmp_path, admin, "Copperfen")
    _position(db, admin, v3, _mid(v3, db, 2))
    admin.ai_monthly_limit_usd = 0.0001
    db.commit()
    fake = FakeProvider(responses=echo_digest)
    ai.set_provider_override(fake)
    r = client.post(f"/api/ai/books/{v3.id}/reread", json={"scope": "series"}, headers=_headers(admin))
    assert r.status_code == 409 and r.json()["reason"] == "budget", r.text
    assert _digests(db, v1) == 4 and _digests(db, v2) == 4 and _recaps(db, v1) == 1
    assert db.query(AIEntity).count() == 2 and db.query(AIReconcileVerdict).count() == 1
    assert digest_job.running() == [] and fake.calls == []
    assert db.query(AuditLog).filter_by(action="ai.reread").count() == 0


def test_already_running_409(client, db, make_book, tmp_path):
    admin = _make_user(db, "rr_busy", "admin")
    other = _make_user(db, "rr_other", "member")
    v1, v2, v3 = _series(db, make_book, tmp_path, admin, "Hollowmere")
    _position(db, admin, v3, _mid(v3, db, 2))
    db.commit()
    h = _headers(admin)
    # Another user's job on an earlier volume reads a volume of the scope.
    digest_job.enqueue(user_id=other.id, book_id=v1.id, upto=4, current_book_only=True)
    r = client.post(f"/api/ai/books/{v3.id}/reread", json={"scope": "series"}, headers=h)
    assert r.status_code == 409 and r.json()["reason"] == "already_running", r.text
    # Book scope: v1 is not in it, and that job reads v1 only.
    assert client.post(f"/api/ai/books/{v3.id}/reread", json={"scope": "book"},
                       headers=h).status_code == 202
    # Now the admin's own job on v3 is live.
    r = client.post(f"/api/ai/books/{v3.id}/reread", json={"scope": "book"}, headers=h)
    assert r.status_code == 409 and r.json()["reason"] == "already_running"
    assert _digests(db, v1) == 4


# ── the read ─────────────────────────────────────────────────────────────────

def test_series_reread_deletes_the_series_and_reads_it_again(client, db, make_book, tmp_path, monkeypatch):
    from backend.services.ai import reconcile
    merged: list[int] = []
    monkeypatch.setattr(reconcile, "run", lambda db_, user_, book_, **kw: (
        merged.append(book_.id), reconcile.ReconcileResult(candidates=0))[1])
    admin = _make_user(db, "rr_series", "admin")
    v1, v2, v3 = _series(db, make_book, tmp_path, admin, "Gullhaven")
    _position(db, admin, v3, _mid(v3, db, 2))
    db.commit()
    # The position trigger had acted on v3 for this reader.
    digest._last_target[(admin.id, v3.id)] = 2
    digest._last_target[(admin.id, 999_999)] = 5
    fake = FakeProvider(responses=echo_digest)
    ai.set_provider_override(fake)

    r = client.post(f"/api/ai/books/{v3.id}/reread", json={"scope": "series"}, headers=_headers(admin))
    assert r.status_code == 202, r.text
    out = r.json()
    assert set(out) == {"job", "estimate", "coverage", "scope", "chapter_idx", "chapter_title"}
    assert out["scope"] == "series" and out["chapter_idx"] == 2
    assert out["coverage"] == {"digested": 0, "needed": 10, "failed": 0}
    assert out["estimate"]["chapters"] == 10 and out["job"]["scope"] == "series"
    # The read ends with the merge step, so duplicates need no second click.
    assert [f["name"] for f in out["job"]["followups"]] == ["reconcile"]
    assert out["job"]["kind"] == "reread"
    # Everything of the series is gone before the read starts.
    assert _digests(db, v1) == _digests(db, v2) == _digests(db, v3) == 0
    assert _recaps(db, v1) == _recaps(db, v2) == _recaps(db, v3) == 0
    assert db.query(AIEntity).count() == 0 and db.query(AIEntityNote).count() == 0
    assert db.query(AIReconcileVerdict).count() == 0
    assert (admin.id, v3.id) not in digest._last_target and (admin.id, 999_999) in digest._last_target
    row = db.query(AuditLog).filter_by(action="ai.reread").one()
    details = json.loads(row.details)
    assert row.resource_id == v3.id and details["scope"] == "series"
    assert details["digests"] == 10 and details["recaps"] == 3 and details["entities"] == 2
    assert details["volumes"] == 3 and sorted(details["book_ids"]) == sorted([v1.id, v2.id, v3.id])
    assert details["notes"] == 2 and details["verdicts"] == 1

    digest_job.run_pending()
    db.expire_all()
    assert _digests(db, v1) == 4 and _digests(db, v2) == 4 and _digests(db, v3) == 2
    assert fake.calls and all(c["feature"] == "reading_digest" for c in fake.calls)
    assert db.query(AIEntity).filter_by(series_key=digest.series_key(v3)).count() > 0
    assert merged == [v3.id]                      # the merge step ran after the read
    snap = client.get(f"/api/ai/digest-jobs/{out['job']['id']}", headers=_headers(admin)).json()
    assert snap["status"] == "done" and snap["followups"][0]["status"] == "done"


def test_book_reread_leaves_other_volumes_alone(client, db, make_book, tmp_path):
    admin = _make_user(db, "rr_book", "admin")
    v1, v2, v3 = _series(db, make_book, tmp_path, admin, "Thornwick")
    _position(db, admin, v3, _mid(v3, db, 3))
    db.commit()
    fake = FakeProvider(responses=echo_digest)
    ai.set_provider_override(fake)
    r = client.post(f"/api/ai/books/{v3.id}/reread", json={"scope": "book"}, headers=_headers(admin))
    assert r.status_code == 202, r.text
    assert r.json()["estimate"]["chapters"] == 3 and r.json()["scope"] == "book"
    assert _digests(db, v3) == 0 and _recaps(db, v3) == 0
    assert _digests(db, v1) == 4 and _digests(db, v2) == 4 and _recaps(db, v1) == 1
    assert db.query(AIEntity).count() == 2 and db.query(AIEntityNote).count() == 2
    assert db.query(AIReconcileVerdict).count() == 1
    details = json.loads(db.query(AuditLog).filter_by(action="ai.reread").one().details)
    assert details["scope"] == "book" and details["digests"] == 2 and "entities" not in details

    digest_job.run_pending()
    db.expire_all()
    assert _digests(db, v3) == 3 and _digests(db, v1) == 4


def test_reread_without_a_position_reads_the_earlier_volumes(client, db, make_book, tmp_path):
    admin = _make_user(db, "rr_zero", "admin")
    v1, v2, v3 = _series(db, make_book, tmp_path, admin, "Mossgate")
    fake = FakeProvider(responses=echo_digest)
    ai.set_provider_override(fake)
    h = _headers(admin)
    est = client.get(f"/api/ai/books/{v3.id}/reread/estimate", headers=h).json()
    assert est["chapter_idx"] == 0 and est["book"]["chapters"] == 0 and est["series"]["chapters"] == 8

    # Book scope with nothing before the position: refused, nothing deleted.
    r = client.post(f"/api/ai/books/{v3.id}/reread", json={"scope": "book"}, headers=h)
    assert r.status_code == 409 and r.json()["reason"] == "nothing_to_read"
    assert _digests(db, v3) == 2

    r = client.post(f"/api/ai/books/{v3.id}/reread", json={"scope": "series"}, headers=h)
    assert r.status_code == 202, r.text
    assert r.json()["chapter_idx"] == 0 and r.json()["estimate"]["chapters"] == 8
    digest_job.run_pending()
    db.expire_all()
    assert _digests(db, v1) == 4 and _digests(db, v2) == 4 and _digests(db, v3) == 0


def _stale_refs(db, key: str) -> set[int]:
    """Entity ids in the entries_json of the series' digests that are not a
    live entity of the key."""
    live = {e.id for e in db.query(AIEntity).filter(AIEntity.series_key == key)}
    out: set[int] = set()
    for d in db.query(AIChapterDigest):
        for e in json.loads(d.entries_json or "[]"):
            eid = e.get("entity_id")
            if eid is not None and eid not in live:
                out.add(eid)
    return out


def test_series_reread_clears_volumes_outside_the_scope(client, db, make_book, tmp_path):
    admin = _make_user(db, "rr_whole", "admin")
    other = _make_user(db, "rr_whole_o", "member")
    v1, v2, v3 = _series(db, make_book, tmp_path, admin, "Brackwater")
    key = digest.series_key(v3)
    a, b = db.query(AIEntity).order_by(AIEntity.id).all()
    # A prequel the admin never marked read, and a later volume another user
    # read: both digested, their entries pointing at the series' entities.
    prequel = _epub_book(db, make_book, tmp_path, "Brackwater 0", series="Brackwater", index=0.5)
    later = _epub_book(db, make_book, tmp_path, "Brackwater 4", series="Brackwater", index=4)
    _read(db, other, later)
    for v in (prequel, later):
        for i in range(4):
            _store_digest(db, v, i, [{"name": "Wren Calloway", "kind": "person", "line": "Sails on.",
                                      "evidence": "x", "entity_id": a.id},
                                     {"name": "Wren", "kind": "person", "line": "Rows.",
                                      "evidence": "x", "entity_id": b.id}])
    db.add(AIRecap(book_id=later.id, chapter_idx=4, scope_hash=digest.Scope(book=later).scope_hash,
                   position_fraction=1.0, text="A recap.", model="m", digested=4, needed=4))
    _position(db, admin, v3, _mid(v3, db, 2))
    db.commit()
    digest_job.reset()
    h = _headers(admin)

    est = client.get(f"/api/ai/books/{v3.id}/reread/estimate", headers=h).json()
    assert est["deletes"]["series"] == {"volumes": 5, "digests": 18, "recaps": 4, "entities": 2}
    assert [v["book_id"] for v in est["series"]["volumes"]] == [v1.id, v2.id, v3.id]

    fake = FakeProvider(responses=echo_digest)
    ai.set_provider_override(fake)
    r = client.post(f"/api/ai/books/{v3.id}/reread", json={"scope": "series"}, headers=h)
    assert r.status_code == 202, r.text
    db.expire_all()
    assert _digests(db, prequel) == 0 and _digests(db, later) == 0 and _recaps(db, later) == 0
    assert db.query(AIChapterDigest).count() == 0 and _stale_refs(db, key) == set()
    details = json.loads(db.query(AuditLog).filter_by(action="ai.reread").one().details)
    assert details["volumes"] == 5 and details["digests"] == 18 and details["recaps"] == 4
    assert sorted(details["book_ids"]) == sorted([prequel.id, v1.id, v2.id, v3.id, later.id])

    digest_job.run_pending()
    db.expire_all()
    assert _digests(db, v1) == 4 and _digests(db, later) == 0 and _digests(db, prequel) == 0
    refs = {e.get("entity_id") for d in db.query(AIChapterDigest)
            for e in json.loads(d.entries_json or "[]")}
    assert refs - {None}, "the re-read stored entity ids"
    assert _stale_refs(db, key) == set()


def test_series_reread_without_a_read_earlier_volume_is_refused(client, db, make_book, tmp_path):
    admin = _make_user(db, "rr_unread", "admin")
    reader = _make_user(db, "rr_unread_r", "member")
    v1, v2, v3 = _series(db, make_book, tmp_path, reader, "Cinderholt")
    _position(db, admin, v3, _mid(v3, db, 2))
    db.commit()
    fake = FakeProvider(responses=echo_digest)
    ai.set_provider_override(fake)
    h = _headers(admin)
    assert client.get(f"/api/ai/books/{v3.id}/reread/estimate", headers=h).json()["series"] is None
    r = client.post(f"/api/ai/books/{v3.id}/reread", json={"scope": "series"}, headers=h)
    assert r.status_code == 409, r.text
    assert r.json() == {"detail": reread.NO_EARLIER_MESSAGE, "reason": "nothing_to_read"}
    assert _digests(db, v1) == 4 and _digests(db, v3) == 2 and _recaps(db, v1) == 1
    assert db.query(AIEntity).count() == 2 and db.query(AIReconcileVerdict).count() == 1
    assert db.query(AuditLog).filter_by(action="ai.reread").count() == 0 and fake.calls == []


def test_start_failure_after_the_delete_says_so(client, db, make_book, tmp_path, monkeypatch):
    from backend.services.ai.provider import AINotConfigured

    admin = _make_user(db, "rr_fail", "admin")
    v1, v2, v3 = _series(db, make_book, tmp_path, admin, "Lanternfall")
    _position(db, admin, v3, _mid(v3, db, 2))
    db.commit()
    ai.set_provider_override(FakeProvider(responses=echo_digest))

    def boom(*_a, **_k):
        raise AINotConfigured("No AI key is configured. Add an Anthropic key in Settings.")

    monkeypatch.setattr(companion, "start_digest", boom)
    r = client.post(f"/api/ai/books/{v3.id}/reread", json={"scope": "book"}, headers=_headers(admin))
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail.startswith("The stored notes were deleted but the read could not start")
    assert detail.endswith("Use Read the missing chapters to start it.")
    assert "No AI key is configured" in detail and "reason" not in r.json()
    db.expire_all()
    assert _digests(db, v3) == 0 and _recaps(db, v3) == 0 and _digests(db, v1) == 4
    assert digest_job.running() == []


# ── plugin mirror ────────────────────────────────────────────────────────────

@pytest.fixture()
def plugin(db: Session):
    from backend.core.database import get_db
    from backend.main import create_app

    app = create_app()
    app.dependency_overrides[get_db] = lambda: (yield db)

    def key_for(user: User) -> dict[str, str]:
        plaintext = ApiKey.generate()
        db.add(ApiKey(user_id=user.id, key_hash=ApiKey.hash_key(plaintext),
                      key_prefix=plaintext[:11], label="test"))
        db.flush()
        return {"Authorization": f"Bearer {plaintext}"}

    with TestClient(app, raise_server_exceptions=True) as c:
        yield c, key_for
    app.dependency_overrides.clear()


def test_plugin_reread_mirror(plugin, db, make_book, tmp_path):
    c, key_for = plugin
    admin = _make_user(db, "rr_plug", "admin")
    member = _make_user(db, "rr_plug_m", "member")
    v1, v2, v3 = _series(db, make_book, tmp_path, admin, "Fernreach")
    ha, hm = key_for(admin), key_for(member)
    db.commit()
    fake = FakeProvider(responses=echo_digest)
    ai.set_provider_override(fake)
    pct = _mid(v3, db, 2)

    assert c.get(f"/api/tome-sync/ai/books/{v3.id}/reread/estimate", params={"pct": pct},
                 headers=hm).status_code == 403
    r = c.get(f"/api/tome-sync/ai/books/{v3.id}/reread/estimate", params={"pct": pct}, headers=ha)
    assert r.status_code == 200, r.text
    assert r.json()["book"]["chapters"] == 2 and r.json()["deletes"]["book"]["digests"] == 2

    assert c.post(f"/api/tome-sync/ai/books/{v3.id}/reread", json={"pct": pct, "scope": "book"},
                  headers=hm).status_code == 403
    assert _digests(db, v3) == 2
    r = c.post(f"/api/tome-sync/ai/books/{v3.id}/reread", json={"pct": pct, "scope": "book"}, headers=ha)
    assert r.status_code == 202, r.text
    assert r.json()["chapter_idx"] == 2 and _digests(db, v3) == 0 and _digests(db, v1) == 4
