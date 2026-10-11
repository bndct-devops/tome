"""Run 2c: AI job persistence, the digest estimate, monthly spend limits and
the jobs (Activity) API. FakeProvider only; books and names are made up."""
from __future__ import annotations

import json
import math
from contextlib import contextmanager
from datetime import datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from backend.models.ai_chapter_digest import AIChapterDigest
from backend.models.ai_job import AIJob
from backend.models.ai_usage import AIUsage
from backend.models.audit_log import AuditLog
from backend.models.tome_sync import ApiKey, TomeSyncPosition
from backend.models.user_book_status import UserBookStatus
from backend.services import ai, book_text
from backend.services.ai import budget, digest, digest_job, estimate, settings as ai_settings, usage
from backend.services.ai.provider import AIBudgetExceeded
from tests.ai_fake import FakeProvider
from tests.test_ai_digest import (  # noqa: F401  (pool is a fixture)
    CHAPTERS, _epub_book, _headers, _make_user, _pool_series, _read, _store_digest, _wait_finished,
    echo_digest, pool,
)

SONNET = "claude-sonnet-5-5"
EIGHT = [(f"Part {i + 1}", body) for i, (_t, body) in enumerate(CHAPTERS + CHAPTERS)]


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


def _mid(book, db, idx: int) -> float:
    ch = book_text.book_chapters(book, db)[idx]
    return (ch.start_fraction + ch.end_fraction) / 2


def _position(db: Session, user, book, pct: float) -> None:
    db.add(TomeSyncPosition(user_id=user.id, book_id=book.id, device="Kindle", percentage=pct))
    db.flush()


def _spend(db: Session, user, cost: float, *, key_source: str = "user", when: datetime | None = None,
           feature: str = "fix_book", book_id: int | None = None, job_id: str | None = None) -> AIUsage:
    row = AIUsage(user_id=user.id, feature=feature, model=SONNET, key_source=key_source,
                  input_tokens=100, output_tokens=10, cost_usd=cost, book_id=book_id, job_id=job_id,
                  created_at=when or datetime.utcnow())
    db.add(row)
    db.flush()
    return row


def _audit(db: Session, action: str) -> list[AuditLog]:
    return db.query(AuditLog).filter(AuditLog.action == action).order_by(AuditLog.id).all()


# ── persistence ──────────────────────────────────────────────────────────────

def test_interrupted_on_restart(db, make_book, tmp_path):
    user = _make_user(db, "restarted", "member")
    book = _epub_book(db, make_book, tmp_path, "Restarted")
    now = datetime.utcnow()
    for i, status in enumerate(("queued", "running", "stopping", "done", "cancelled")):
        db.add(AIJob(id=f"job{i:09d}", user_id=user.id, book_id=book.id, status=status,
                     created_at=now + timedelta(seconds=i)))
    db.flush()
    assert digest_job.mark_interrupted(db) == 3
    rows = {r.id: r for r in db.query(AIJob).all()}
    # A row left stopping without a reason was the user's cancel in progress.
    assert [rows[f"job{i:09d}"].status for i in range(5)] == [
        "interrupted", "interrupted", "cancelled", "done", "cancelled"]
    assert rows["job000000001"].stop_reason == digest_job.INTERRUPTED
    assert rows["job000000001"].finished_at is not None
    assert rows["job000000002"].stop_reason is None and rows["job000000002"].finished_at is not None
    assert rows["job000000003"].stop_reason is None
    # The latest row (cancelled) blocks the triggers; an interrupted latest does not.
    assert digest_job.last_cancelled(db, user.id, book.id)
    db.add(AIJob(id="job000000099", user_id=user.id, book_id=book.id, status="interrupted",
                 created_at=now + timedelta(seconds=10)))
    db.flush()
    assert not digest_job.last_cancelled(db, user.id, book.id)


def test_a_cancel_in_progress_still_blocks_the_triggers_after_restart(db, make_book, tmp_path):
    user = _make_user(db, "midcancel", "member")
    book = _epub_book(db, make_book, tmp_path, "Mid Cancel")
    db.add(AIJob(id="midcancel001", user_id=user.id, book_id=book.id, status="stopping",
                 created_at=datetime.utcnow()))
    db.add(AIJob(id="midstop00001", user_id=user.id, book_id=book.id, status="stopping",
                 stop_reason="Monthly AI budget reached (1.00 of 1.00 USD).",
                 created_at=datetime.utcnow() - timedelta(seconds=5)))
    db.flush()
    assert digest_job.last_cancelled(db, user.id, book.id)
    digest_job.mark_interrupted(db)
    assert db.get(AIJob, "midcancel001").status == "cancelled"
    assert db.get(AIJob, "midstop00001").status == "interrupted"          # a stop by Tome
    assert digest_job.last_cancelled(db, user.id, book.id)


def test_a_stop_by_tome_does_not_block_the_triggers(db, make_book, tmp_path):
    user = _make_user(db, "tomestop", "member")
    book = _epub_book(db, make_book, tmp_path, "Tome Stop")
    now = datetime.utcnow()
    db.add(AIJob(id="stopping0001", user_id=user.id, book_id=book.id, status="stopping",
                 stop_reason="Monthly AI budget reached (1.00 of 1.00 USD).", created_at=now))
    db.flush()
    assert not digest_job.last_cancelled(db, user.id, book.id)
    db.add(AIJob(id="stopping0002", user_id=user.id, book_id=book.id, status="stopping",
                 created_at=now + timedelta(seconds=1)))
    db.flush()
    assert digest_job.last_cancelled(db, user.id, book.id)


# ── estimate ─────────────────────────────────────────────────────────────────

def _expected(words: int, chapters: int, out_scale: float = 1.0, time_scale: float = 1.0,
              first: bool = True) -> tuple[float, float]:
    inp = int(words * estimate.TOKENS_PER_WORD) + estimate.PROMPT_TOKENS + estimate.HEADING_TOKENS * chapters
    out = int(round(estimate.predicted_output(words, chapters) * out_scale))
    cache_read = 0 if first else estimate.STATE_TOKENS
    cache_write = estimate.STATE_TOKENS if first else 0
    cost = usage.cost_usd(SONNET, inp, out, cache_read_tokens=cache_read,
                          cache_write_tokens=cache_write) * estimate.ESTIMATE_MARGIN
    secs = estimate.predicted_seconds(inp + cache_read + cache_write, out) * time_scale
    return cost, secs


def test_estimate_defaults_without_history(db, make_book, tmp_path):
    user = _make_user(db, "estimator", "member")
    book = _epub_book(db, make_book, tmp_path, "Estimated")
    words = book_text.chapter_word_counts(book, db)
    est = estimate.estimate(db, user, book, "book", 4)
    total_words = sum(words.values())
    cost, secs = _expected(total_words, 4)
    assert est["scope"] == "book" and est["model"] == SONNET
    assert est["chapters"] == 4 and est["calls"] == 1 and est["words"] == total_words
    assert est["est_cost_usd"] == pytest.approx(round(cost, 4))
    assert est["est_seconds"] == math.ceil(secs)
    assert est["remaining_budget_usd"] is None and est["over_budget"] is False
    assert est["volumes"] == [{"book_id": book.id, "title": "Estimated", "chapters": 4}]
    # A stored chapter is not counted (and splits the unit).
    _store_digest(db, book, 1, [])
    est = estimate.estimate(db, user, book, "book", 4)
    assert est["chapters"] == 3 and est["calls"] == 2


def test_estimate_uses_the_history_medians(db, make_book, tmp_path):
    user = _make_user(db, "historian", "member")
    book = _epub_book(db, make_book, tmp_path, "New Book")
    # Three digest calls whose output came out at 1x, 2x and 3x the
    # prediction for the words and chapters they sent, plus a front-matter
    # call (15 tokens) and a call without words_sent (before the column)
    # that must not count as samples.
    pred = estimate.predicted_output(3000, 2)
    for mult in (1, 2, 3):
        db.add(AIUsage(user_id=user.id, feature="reading_digest", model=SONNET, key_source="user",
                       input_tokens=9000, output_tokens=pred * mult, cost_usd=0.01,
                       words_sent=3000, chapters=2, created_at=datetime.utcnow()))
    db.add(AIUsage(user_id=user.id, feature="reading_digest", model=SONNET, key_source="user",
                   input_tokens=500, output_tokens=15, cost_usd=0.001, words_sent=40, chapters=1,
                   created_at=datetime.utcnow()))
    db.add(AIUsage(user_id=user.id, feature="reading_digest", model=SONNET, key_source="user",
                   input_tokens=500, output_tokens=90_000, cost_usd=0.001, created_at=datetime.utcnow()))
    # Three timed calls at 1x, 3x and 2x the predicted seconds.
    for mult in (1, 3, 2):
        db.add(AIUsage(user_id=user.id, feature="reading_digest", model=SONNET, key_source="user",
                       input_tokens=8000, output_tokens=1000, cost_usd=0.01,
                       duration_ms=int(estimate.predicted_seconds(8000, 1000) * 1000 * mult),
                       created_at=datetime.utcnow()))
    # Another model's history is ignored.
    db.add(AIUsage(user_id=user.id, feature="reading_digest", model="claude-haiku-5-5", key_source="user",
                   input_tokens=1, output_tokens=1000, cost_usd=0.01, duration_ms=999_000,
                   created_at=datetime.utcnow()))
    db.flush()
    hist = estimate.history(db, SONNET)
    assert hist["out_scale"] == pytest.approx(2.0) and hist["time_scale"] == pytest.approx(2.0)
    assert hist["samples"] == 3 and hist["timed_samples"] == 3

    est = estimate.estimate(db, user, book, "book", 4)
    cost, secs = _expected(est["words"], 4, 2.0, 2.0)
    assert est["est_cost_usd"] == pytest.approx(round(cost, 4))
    assert est["est_seconds"] == math.ceil(secs)


def test_estimate_history_is_bounded(db):
    """A wild sample cannot push the estimate past OUT_SCALE_BOUNDS, and
    history never lowers the output estimate below the defaults."""
    user = _make_user(db, "wild", "member")
    row = AIUsage(user_id=user.id, feature="reading_digest", model=SONNET, key_source="user",
                  input_tokens=1000, output_tokens=50_000, cost_usd=0.01, words_sent=300, chapters=1,
                  created_at=datetime.utcnow())
    db.add(row)
    db.flush()
    assert estimate.history(db, SONNET)["out_scale"] == estimate.OUT_SCALE_BOUNDS[1]
    row.output_tokens = 60                     # far below the prediction
    db.flush()
    assert estimate.history(db, SONNET)["out_scale"] == 1.0


def test_estimate_defaults_match_the_measured_runs():
    """The defaults reproduce the real calls of the run 2c smoke within the
    margin, erring high: Romeo and Juliet Acts I-III in one call (18548
    words) cost 0.1186 USD and took 21 s on Sonnet; a 12-chapter unit of
    235-word chapters cost 0.03 to 0.05 USD."""
    cost, secs = _expected(18548, 3)
    assert 0.1186 <= cost <= 0.1186 * 1.6
    assert 15 <= secs <= 30
    cost, _ = _expected(12 * 235, 12)
    assert 0.03 <= cost <= 0.06


def test_estimate_series_scope_and_parallel_time(db, make_book, tmp_path):
    user = _make_user(db, "seriesest", "member")
    vols = [_epub_book(db, make_book, tmp_path, f"Isle {i}", series="Isle", index=i) for i in (1, 2, 3)]
    for v in vols[:2]:
        _read(db, user, v)
    both = estimate.estimates(db, user, vols[2], 2)
    assert both["book"]["chapters"] == 2 and both["book"]["volumes"] == [
        {"book_id": vols[2].id, "title": "Isle 3", "chapters": 2}]
    series = both["series"]
    assert series["chapters"] == 10 and series["calls"] == 3
    assert [v["chapters"] for v in series["volumes"]] == [4, 4, 2]
    # Three units of three volumes run side by side: the time is one round,
    # the longest unit, not the sum.
    single = estimate.estimate(db, user, vols[0], "book", 4)
    assert series["est_seconds"] <= single["est_seconds"] + 1
    # No read earlier volume: no series estimate.
    assert estimate.estimates(db, user, vols[0], 2)["series"] is None


def test_estimate_reports_the_remaining_budget(db, make_book, tmp_path):
    user = _make_user(db, "budgeted", "member")
    book = _epub_book(db, make_book, tmp_path, "Budgeted")
    user.ai_monthly_limit_usd = 1.0
    _spend(db, user, 0.40)
    _spend(db, user, 5.00, when=datetime.utcnow() - timedelta(days=40))   # an earlier month
    est = estimate.estimate(db, user, book, "book", 4)
    assert est["remaining_budget_usd"] == pytest.approx(0.60)
    assert est["over_budget"] is False
    user.ai_monthly_limit_usd = 0.41
    db.flush()
    est = estimate.estimate(db, user, book, "book", 4)
    assert est["remaining_budget_usd"] == pytest.approx(0.01)
    assert est["over_budget"] is True


def test_estimate_endpoints(client, db, make_book, tmp_path):
    user = _make_user(db, "estapi", "member")
    v1 = _epub_book(db, make_book, tmp_path, "Shoal 1", series="Shoal", index=1)
    v2 = _epub_book(db, make_book, tmp_path, "Shoal 2", series="Shoal", index=2)
    _read(db, user, v1)
    _position(db, user, v2, _mid(v2, db, 3))
    db.commit()
    r = client.get(f"/api/ai/books/{v2.id}/digest/estimate", headers=_headers(user))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["chapter_idx"] == 3 and body["chapter_title"] == "The Return"
    assert body["book"]["chapters"] == 3 and body["series"]["chapters"] == 7
    assert body["series"]["volumes"][0] == {"book_id": v1.id, "title": "Shoal 1", "chapters": 4}

    plaintext = ApiKey.generate()
    db.add(ApiKey(user_id=user.id, key_hash=ApiKey.hash_key(plaintext), key_prefix=plaintext[:11],
                  label="test"))
    db.commit()
    ph = {"Authorization": f"Bearer {plaintext}"}
    r = client.get(f"/api/tome-sync/ai/books/{v2.id}/digest/estimate",
                   params={"pct": _mid(v2, db, 2)}, headers=ph)
    assert r.status_code == 200, r.text
    assert r.json()["chapter_idx"] == 2 and r.json()["book"]["chapters"] == 2
    r = client.get(f"/api/tome-sync/ai/books/{v2.id}/digest/estimate", headers=ph)
    assert r.status_code == 200 and r.json()["chapter_idx"] == 3     # the stored position
    guest = _make_user(db, "estguest", "guest", key=False)
    assert client.get(f"/api/ai/books/{v2.id}/digest/estimate",
                      headers=_headers(guest)).status_code == 403


def test_call_cost_guess():
    assert estimate.call_cost_guess("fix_book", "claude-opus-5-5", 50_000) == estimate.METADATA_CALL_GUESS
    small = estimate.call_cost_guess("reading_digest", SONNET, 1000, 1)
    big = estimate.call_cost_guess("reading_digest", SONNET, 1000, 12)
    assert 0 < small < big
    assert estimate.call_cost_guess("reading_digest", "claude-haiku-5-5", 1000) < small
    # A digest call's output term is its max_tokens: the most it can be billed for.
    capped = estimate.call_cost_guess("reading_digest", SONNET, 1000, 2, max_tokens=6500)
    assert capped == pytest.approx(usage.cost_usd(
        SONNET, int(1000 * estimate.TOKENS_PER_WORD) + estimate.PROMPT_TOKENS, 6500))
    assert estimate.call_cost_guess("fix_book", SONNET, 1000, max_tokens=9999) == estimate.METADATA_CALL_GUESS


def test_run_feature_records_words_and_chapters(db):
    user = _make_user(db, "wordcount", "member")
    ai.set_provider_override(FakeProvider(responses=lambda *a: {}))
    ai.run_feature(db, user, "reading_digest", system="s", user_content="u", chapters=3, words=4200)
    ai.run_feature(db, user, "fix_book", system="s", user_content="u")
    rows = db.query(AIUsage).filter(AIUsage.user_id == user.id).order_by(AIUsage.id).all()
    assert [(r.words_sent, r.chapters) for r in rows] == [(4200, 3), (None, None)]
    assert budget.reserved() == 0.0


# ── spend limits ─────────────────────────────────────────────────────────────

def test_run_feature_stops_at_the_user_limit(db):
    user = _make_user(db, "capped", "member")
    fake = FakeProvider(responses=[{"x": 1}])
    ai.set_provider_override(fake)
    user.ai_monthly_limit_usd = 0.10
    _spend(db, user, 0.08)
    with pytest.raises(AIBudgetExceeded) as exc:
        ai.run_feature(db, user, "fix_book", system="s", user_content="u")
    assert exc.value.message == "Monthly AI budget reached (0.08 of 0.10 USD). Raise it in Settings > AI."
    assert exc.value.status_code == 409 and exc.value.reason == "budget" and exc.value.which == "user"
    assert fake.calls == []
    user.ai_monthly_limit_usd = 0.20
    ai.run_feature(db, user, "fix_book", system="s", user_content="u")
    assert len(fake.calls) == 1


def test_instance_limit_applies_to_instance_key_calls_only(db, admin_user):
    admin, _ = admin_user
    member = _make_user(db, "ownkey", "member")                 # has their own key
    ai_settings.set_instance_key(db, "sk-ant-test-instance-000000000000Zz11")
    ai_settings.set_instance_monthly_budget(db, 0.05)
    _spend(db, admin, 0.04, key_source="instance")
    fake = FakeProvider(responses=lambda *a: {})
    ai.set_provider_override(fake)
    with pytest.raises(AIBudgetExceeded) as exc:
        ai.run_feature(db, admin, "fix_book", system="s", user_content="u")
    assert exc.value.which == "instance" and "0.04 of 0.05 USD" in exc.value.message
    ai.run_feature(db, member, "fix_book", system="s", user_content="u")   # own key: not capped
    assert len(fake.calls) == 1
    b = budget.status(db, admin)
    assert b.key_source == "instance" and b.remaining == pytest.approx(0.01)
    assert budget.status(db, member).remaining is None


def test_post_digest_over_budget_is_409_budget(client, db, make_book, tmp_path):
    user = _make_user(db, "overbudget", "member")
    book = _epub_book(db, make_book, tmp_path, "Over Budget")
    _position(db, user, book, _mid(book, db, 3))
    user.ai_monthly_limit_usd = 0.001
    db.commit()
    r = client.post(f"/api/ai/books/{book.id}/digest", json={"scope": "book"}, headers=_headers(user))
    assert r.status_code == 409, r.text
    body = r.json()
    assert body["reason"] == "budget" and "Raise it in Settings > AI." in body["detail"]
    assert digest_job._queue == type(digest_job._queue)()
    # The recap's 202 says so in its estimate.
    r = client.post(f"/api/ai/books/{book.id}/recap", json={}, headers=_headers(user))
    assert r.status_code == 202 and r.json()["estimate"]["book"]["over_budget"] is True
    # A key without a limit problem still gets the plain 409 without a reason.
    other = _make_user(db, "nokeyhere", "member", key=False)
    _position(db, other, book, _mid(book, db, 3))
    db.commit()
    r = client.post(f"/api/ai/books/{book.id}/digest", json={}, headers=_headers(other))
    assert r.status_code == 409 and "reason" not in r.json()


def test_a_job_stops_when_the_budget_runs_out(db, make_book, tmp_path):
    user = _make_user(db, "runsout", "member")
    book = _epub_book(db, make_book, tmp_path, "Runs Out")
    fake = FakeProvider(responses=echo_digest)
    ai.set_provider_override(fake)
    snap = digest_job.enqueue(user_id=user.id, book_id=book.id, upto=4)
    user.ai_monthly_limit_usd = 0.0
    db.commit()
    digest_job.run_pending()
    job = digest_job.get(snap["id"])
    assert job["status"] == "stopped" and job["stop_reason"].startswith("Monthly AI budget reached")
    assert fake.calls == [] and job["done"] == 0
    assert db.get(AIJob, snap["id"]).status == "stopped"
    # A stop by Tome is not a cancel: once the limit is raised the trigger works again.
    assert not digest_job.last_cancelled(db, user.id, book.id)


def test_triggers_do_not_enqueue_over_budget(db, make_book, tmp_path):
    user = _make_user(db, "triggercap", "member")
    book = _epub_book(db, make_book, tmp_path, "Trigger Cap", chapters=EIGHT, series="Trigger Cap", index=1)
    user.ai_monthly_limit_usd = 0.0
    db.commit()
    assert digest.on_position_advanced(db, user, book, _mid(book, db, 4)) is None
    db.add(UserBookStatus(user_id=user.id, book_id=book.id, status="read", progress_pct=1.0))
    db.commit()
    assert list(digest_job._queue) == []
    # With room under the limit both act.
    user.ai_monthly_limit_usd = 50.0
    db.commit()
    digest.reset_triggers()
    assert digest.on_position_advanced(db, user, book, _mid(book, db, 4)) is not None


def test_limit_setters_and_audit(client, db, admin_user):
    admin, _ = admin_user
    member = _make_user(db, "limiter", "member")
    other = _make_user(db, "limited", "member")
    db.commit()
    h = _headers(member)

    r = client.put("/api/ai/limits", json={"user_monthly_usd": 5}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["limits"]["user_monthly_usd"] == 5.0
    assert len(_audit(db, "ai.budget_changed")) == 1
    client.put("/api/ai/limits", json={"user_monthly_usd": 5}, headers=h)   # unchanged: no row
    r = client.put("/api/ai/limits", json={"user_monthly_usd": None}, headers=h)
    assert r.json()["limits"]["user_monthly_usd"] is None
    rows = _audit(db, "ai.budget_changed")
    assert len(rows) == 2
    assert client.put("/api/ai/limits", json={"user_monthly_usd": -1}, headers=h).status_code == 422

    # Admin: a cap on any member, apart from the member's own limit; members may not.
    assert client.put(f"/api/ai/users/{other.id}/limit", json={"user_monthly_usd": 2},
                      headers=h).status_code == 403
    r = client.put(f"/api/ai/users/{other.id}/limit", json={"user_monthly_usd": 2})
    assert r.status_code == 200 and r.json() == {"user_id": other.id, "username": "limited",
                                                 "monthly_limit_usd": None, "admin_limit_usd": 2.0,
                                                 "effective_limit_usd": 2.0}
    db.refresh(other)
    assert other.ai_admin_limit_usd == 2.0 and other.ai_monthly_limit_usd is None
    assert client.put("/api/ai/users/999999/limit", json={"user_monthly_usd": 2}).status_code == 404

    # Instance limit through PUT /instance; leaving the field out keeps it.
    r = client.put("/api/ai/instance", json={"budget_instance_monthly_usd": 20})
    assert r.status_code == 200 and r.json()["budget_instance_monthly_usd"] == 20.0
    r = client.put("/api/ai/instance", json={"confidence_threshold": 0.9})
    assert r.json()["budget_instance_monthly_usd"] == 20.0
    r = client.put("/api/ai/instance", json={"budget_instance_monthly_usd": None})
    assert r.json()["budget_instance_monthly_usd"] is None
    rows = _audit(db, "ai.budget_changed")
    assert len(rows) == 5
    details = [json.loads(r.details) for r in rows]
    assert details[0] == {"limit": "user", "user_id": member.id, "old": None, "new": 5.0}
    assert details[1] == {"limit": "user", "user_id": member.id, "old": 5.0, "new": None}
    assert details[2] == {"limit": "admin", "user_id": other.id, "old": None, "new": 2.0}
    assert details[3] == {"limit": "instance", "old": None, "new": 20.0}
    assert details[4] == {"limit": "instance", "old": 20.0, "new": None}
    assert rows[2].user_id == admin.id
    # Members cannot change the instance limit.
    assert client.put("/api/ai/instance", json={"budget_instance_monthly_usd": 1},
                      headers=h).status_code == 403


def test_a_member_cannot_lift_an_admin_cap(client, db, admin_user):
    capped = _make_user(db, "admincapped", "member")
    db.commit()
    h = _headers(capped)
    assert client.put(f"/api/ai/users/{capped.id}/limit", json={"user_monthly_usd": 1}).status_code == 200
    # The member's own edits (raise, clear) touch only their own limit.
    r = client.put("/api/ai/limits", json={"user_monthly_usd": 50}, headers=h)
    assert r.json()["limits"]["user_monthly_usd"] == 50.0
    assert r.json()["limits"]["admin_monthly_usd"] == 1.0
    assert r.json()["limits"]["effective_monthly_usd"] == 1.0
    r = client.put("/api/ai/limits", json={"user_monthly_usd": None}, headers=h)
    assert r.json()["limits"]["effective_monthly_usd"] == 1.0
    db.refresh(capped)
    assert budget.effective_user_limit(capped) == 1.0
    # A lower own limit wins.
    client.put("/api/ai/limits", json={"user_monthly_usd": 0.5}, headers=h)
    db.refresh(capped)
    assert budget.effective_user_limit(capped) == 0.5
    _spend(db, capped, 0.97)
    db.commit()
    client.put("/api/ai/limits", json={"user_monthly_usd": None}, headers=h)
    db.refresh(capped)
    ai.set_provider_override(FakeProvider(responses=[{"x": 1}]))
    with pytest.raises(AIBudgetExceeded) as exc:
        ai.run_feature(db, capped, "fix_book", system="s", user_content="u")
    assert exc.value.which == "user" and exc.value.limit == 1.0


def test_reservations_count_against_the_limits(db, admin_user):
    """Calls in flight hold their guess: a second call cannot pass on the
    headroom the first already took, for the user and for the instance key."""
    admin, _ = admin_user
    user = _make_user(db, "reserver", "member")
    user.ai_monthly_limit_usd = 0.10
    db.flush()
    t1 = budget.check_and_reserve(db, user, "user", 0.06)
    try:
        with pytest.raises(AIBudgetExceeded):
            budget.check_and_reserve(db, user, "user", 0.06)
        assert budget.remaining(db, user) == pytest.approx(0.04)
    finally:
        budget.release(t1)
    budget.release(budget.check_and_reserve(db, user, "user", 0.06))
    # The instance limit counts every user's instance-key reservations.
    ai_settings.set_instance_key(db, "sk-ant-test-instance-000000000000Zz11")
    ai_settings.set_instance_monthly_budget(db, 0.10)
    other = _make_user(db, "reserver2", "member")
    t2 = budget.reserve(other.id, "instance", 0.08)
    try:
        with pytest.raises(AIBudgetExceeded) as exc:
            budget.check_and_reserve(db, admin, "instance", 0.05)
        assert exc.value.which == "instance"
    finally:
        budget.release(t2)
    assert budget.reserved() == 0.0


def test_parallel_digest_calls_stay_within_the_limit(pool, tmp_path, monkeypatch):
    """Four workers start calls at once: each call reserves its max_tokens
    guess, so together they cannot pass the limit (before the ledger, four
    calls each saw 0 spent and all went out)."""
    import threading
    import time as _time

    from backend.models.user import User

    monkeypatch.setattr(digest, "CHUNK_CHAPTERS", 2)
    user_id, ids = _pool_series(pool, tmp_path, 5)
    limit = 0.15                            # room for two calls' guesses, not four
    with pool() as s:
        s.get(User, user_id).ai_monthly_limit_usd = limit
        s.commit()
    in_flight = {"now": 0, "max": 0}
    lk = threading.Lock()

    def on_call(call):
        with lk:
            in_flight["now"] += 1
            in_flight["max"] = max(in_flight["max"], in_flight["now"])
        _time.sleep(0.3)                    # long enough for the workers to overlap
        with lk:
            in_flight["now"] -= 1

    # Billed output stays under the call's max_tokens, as a real answer must.
    fake = FakeProvider(responses=echo_digest, on_call=on_call, input_tokens=5000, output_tokens=6000)
    ai.set_provider_override(fake)
    snap = digest_job.enqueue(user_id=user_id, book_id=ids[-1], upto=0)
    job = _wait_finished(snap["id"])
    with pool() as s:
        spent = sum(r.cost_usd for r in s.query(AIUsage).all())
    one_call = usage.cost_usd(SONNET, 5000, 6000)
    assert in_flight["max"] > 1                                   # the calls did overlap
    assert job["status"] == "stopped" and job["stop_reason"].startswith("Monthly AI budget reached")
    assert spent <= limit + one_call
    assert spent <= limit + 1e-9                                  # the guesses are real upper bounds here
    assert budget.reserved() == 0.0


# ── jobs API ─────────────────────────────────────────────────────────────────

def test_jobs_api_groups_by_book_and_scopes_to_the_user(client, db, make_book, tmp_path, admin_user):
    alice = _make_user(db, "alice", "member")
    bob = _make_user(db, "bob", "member")
    a = _epub_book(db, make_book, tmp_path, "Alpha", series="Alpha", index=1)
    b = _epub_book(db, make_book, tmp_path, "Beta")
    db.commit()
    ai.set_provider_override(FakeProvider(responses=echo_digest))

    done = digest_job.enqueue(user_id=alice.id, book_id=a.id, upto=4)
    digest_job.run_pending()
    _spend(db, alice, 0.25, feature="recap", book_id=b.id)              # a direct call, no job
    _spend(db, alice, 9.00, feature="recap", book_id=b.id,
           when=datetime.utcnow() - timedelta(days=45))                # outside the window
    bobs = digest_job.enqueue(user_id=bob.id, book_id=a.id, upto=2)     # queued: running
    db.commit()

    r = client.get("/api/ai/jobs", headers=_headers(alice))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["running"] == []
    assert [g["book_id"] for g in body["books"]] == [b.id, a.id]        # last activity first
    gb, ga = body["books"]
    assert gb["jobs"] == [] and gb["calls"] == 1 and gb["cost_usd"] == pytest.approx(0.25)
    assert ga["title"] == "Alpha" and ga["series"] == "Alpha" and ga["series_index"] == 1
    [job] = ga["jobs"]
    assert job["id"] == done["id"] and job["status"] == "done" and job["total"] == 4
    assert job["calls"] == 1 and ga["calls"] == 1 and ga["chapters_digested"] == 4
    assert ga["cost_usd"] == pytest.approx(job["cost_usd"]) and job["cost_usd"] > 0
    assert job["est_cost_usd"] > 0 and job["actual_seconds"] is not None
    assert "username" not in job
    assert client.get(f"/api/ai/jobs?user_id={bob.id}", headers=_headers(alice)).status_code == 403
    assert client.get(f"/api/ai/jobs?user_id={alice.id}", headers=_headers(alice)).status_code == 200

    r = client.get("/api/ai/jobs", headers=_headers(bob)).json()
    assert [j["id"] for j in r["running"]] == [bobs["id"]]
    assert r["running"][0]["book_title"] == "Alpha" and r["running"][0]["status"] == "queued"
    assert [g["book_id"] for g in r["books"]] == [a.id]

    # Admin: everyone, with usernames, or one user.
    r = client.get("/api/ai/jobs").json()
    ga = next(g for g in r["books"] if g["book_id"] == a.id)
    assert {j["username"] for j in ga["jobs"]} == {"alice", "bob"}
    assert [j["id"] for j in r["running"]] == [bobs["id"]] and r["running"][0]["username"] == "bob"
    r = client.get(f"/api/ai/jobs?user_id={bob.id}").json()
    assert [g["book_id"] for g in r["books"]] == [a.id] and len(r["books"][0]["jobs"]) == 1
    guest = _make_user(db, "jobguest", "guest", key=False)
    assert client.get("/api/ai/jobs", headers=_headers(guest)).status_code == 403


def test_jobs_api_days_window(client, db, make_book, tmp_path):
    user = _make_user(db, "olden", "member")
    book = _epub_book(db, make_book, tmp_path, "Olden")
    db.add(AIJob(id="oldjob000001", user_id=user.id, book_id=book.id, status="done",
                 created_at=datetime.utcnow() - timedelta(days=40)))
    db.commit()
    assert client.get("/api/ai/jobs", headers=_headers(user)).json()["books"] == []
    r = client.get("/api/ai/jobs?days=60", headers=_headers(user)).json()
    assert [j["id"] for j in r["books"][0]["jobs"]] == ["oldjob000001"]


def test_job_calls_listing(client, db, make_book, tmp_path):
    owner = _make_user(db, "callowner", "member")
    stranger = _make_user(db, "callstranger", "member")
    book = _epub_book(db, make_book, tmp_path, "Calls")
    db.commit()
    ai.set_provider_override(FakeProvider(responses=echo_digest, input_tokens=2000, output_tokens=300,
                                          cache_read_tokens=500))
    snap = digest_job.enqueue(user_id=owner.id, book_id=book.id, upto=4)
    digest_job.run_pending()
    r = client.get(f"/api/ai/jobs/{snap['id']}/calls", headers=_headers(owner))
    assert r.status_code == 200, r.text
    [call] = r.json()["calls"]
    assert call["feature"] == "reading_digest" and call["model"] == SONNET
    assert call["input_tokens"] == 2000 and call["output_tokens"] == 300
    assert call["cache_read_tokens"] == 500 and call["book_id"] == book.id
    assert call["cost_usd"] == pytest.approx(usage.cost_usd(SONNET, 2000, 300, 500))
    assert client.get(f"/api/ai/jobs/{snap['id']}/calls", headers=_headers(stranger)).status_code == 404
    assert client.get(f"/api/ai/jobs/{snap['id']}/calls").status_code == 200          # admin
    assert client.get("/api/ai/jobs/nope/calls", headers=_headers(owner)).status_code == 404


def test_jobs_api_keeps_the_spend_of_a_deleted_book(client, db, make_book, tmp_path):
    """Deleting a book cascades its job rows; the usage rows stay (book_id
    NULL) and show under one "Deleted book" group (book_id None)."""
    user = _make_user(db, "deleter", "member")
    keep = _epub_book(db, make_book, tmp_path, "Kept")
    db.commit()
    _spend(db, user, 0.20, feature="reading_digest", job_id="gonejob00001")   # its job row is gone
    _spend(db, user, 0.05, feature="reading_digest", job_id="gonejob00001")
    _spend(db, user, 0.10, feature="recap", book_id=keep.id)
    db.commit()
    body = client.get("/api/ai/jobs", headers=_headers(user)).json()
    groups = {g["book_id"]: g for g in body["books"]}
    assert set(groups) == {None, keep.id}
    gone = groups[None]
    assert gone["title"] is None and gone["jobs"] == []
    assert gone["calls"] == 2 and gone["cost_usd"] == pytest.approx(0.25)
    other = _make_user(db, "notdeleter", "member")
    db.commit()
    assert client.get("/api/ai/jobs", headers=_headers(other)).json()["books"] == []


def test_plugin_can_cancel_its_job(client, db, make_book, tmp_path):
    owner = _make_user(db, "plugstop", "member")
    stranger = _make_user(db, "plugstranger", "member")
    book = _epub_book(db, make_book, tmp_path, "Plug Stop")
    db.commit()
    headers = {}
    for u in (owner, stranger):
        plaintext = ApiKey.generate()
        db.add(ApiKey(user_id=u.id, key_hash=ApiKey.hash_key(plaintext), key_prefix=plaintext[:11],
                      label="test"))
        headers[u.id] = {"Authorization": f"Bearer {plaintext}"}
    db.commit()
    snap = digest_job.enqueue(user_id=owner.id, book_id=book.id, upto=4)
    r = client.post(f"/api/tome-sync/ai/jobs/{snap['id']}/cancel", headers=headers[stranger.id])
    assert r.status_code == 404
    assert digest_job.get(snap["id"])["status"] == "queued"
    r = client.post(f"/api/tome-sync/ai/jobs/{snap['id']}/cancel", headers=headers[owner.id])
    assert r.status_code == 200, r.text
    assert r.json()["id"] == snap["id"] and r.json()["status"] == "cancelled"
    assert digest_job.last_cancelled(db, owner.id, book.id)
    assert client.post("/api/tome-sync/ai/jobs/nope/cancel", headers=headers[owner.id]).status_code == 404


def test_estimate_counts_the_users_other_live_jobs(client, db, make_book, tmp_path, monkeypatch):
    user = _make_user(db, "busyreader", "member")
    book = _epub_book(db, make_book, tmp_path, "Busy")
    _position(db, user, book, _mid(book, db, 3))
    user.ai_monthly_limit_usd = 1.0
    db.commit()
    live = [
        {"id": "other0000001", "user_id": user.id, "book_id": book.id + 1000,
         "est_cost_usd": 0.70, "cost_usd": 0.10},                 # 0.60 still to spend
        {"id": "other0000002", "user_id": user.id, "book_id": book.id + 1001,
         "est_cost_usd": 0.05, "cost_usd": 0.20},                 # over its estimate: 0
        {"id": "same00000001", "user_id": user.id, "book_id": book.id,
         "est_cost_usd": 5.0, "cost_usd": 0.0},                   # this book: coalesces, left out
    ]
    monkeypatch.setattr(digest_job, "running", lambda user_id=None: list(live))
    body = client.get(f"/api/ai/books/{book.id}/digest/estimate", headers=_headers(user)).json()
    assert body["book"]["remaining_budget_usd"] == pytest.approx(0.40)
    live[0]["est_cost_usd"] = 1.10                                 # 1.00 still to spend
    body = client.get(f"/api/ai/books/{book.id}/digest/estimate", headers=_headers(user)).json()
    assert body["book"]["remaining_budget_usd"] == 0.0 and body["book"]["over_budget"] is True
    r = client.post(f"/api/ai/books/{book.id}/digest", json={"scope": "book"}, headers=_headers(user))
    assert r.status_code == 409 and r.json()["reason"] == "budget"
