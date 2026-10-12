"""Reading companion features on top of the digests: recap (spoiler window,
cache, "Ask again", Previously on), glossary, Recall, the web endpoints on
/api/ai and the plugin endpoints on /api/tome-sync/ai. FakeProvider only;
books and names are made up."""
from __future__ import annotations

from collections import deque
from contextlib import contextmanager

import pytest
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from backend.models.ai_recap import AIRecap
from backend.models.ai_usage import AIUsage
from backend.models.library import Library
from backend.models.tome_sync import ApiKey, TomeSyncPosition
from backend.models.user import User
from backend.services import ai, book_text
from backend.services.ai import companion, digest, digest_job, recap, settings as ai_settings, volume_recap
from tests.ai_fake import FakeProvider
from tests.test_ai_digest import (
    _entry, _epub_book, _headers, _make_user, _read, _store_digest, echo_digest, make_epub,
)

RECAP_TEXT = {"paragraphs": ["Mara walked to the harbour.", "Tobin rang the bell — twice."]}


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


def _position(db: Session, user: User, book, pct: float, device: str = "Kindle") -> None:
    row = db.query(TomeSyncPosition).filter_by(user_id=user.id, book_id=book.id).first()
    if row is None:
        db.add(TomeSyncPosition(user_id=user.id, book_id=book.id, percentage=pct, device=device))
    else:
        row.percentage = pct
        row.device = device
    db.flush()


def _mid(book, db, idx: int, frac: float = 0.5) -> float:
    ch = book_text.book_chapters(book, db)[idx]
    return ch.start_fraction + (ch.end_fraction - ch.start_fraction) * frac


def _nohist(fact: dict) -> dict:
    """A fact without its ``history`` (asserted on its own)."""
    return {k: v for k, v in fact.items() if k != "history"}


def _all_text(call: dict) -> str:
    content = call["user_content"]
    return content if isinstance(content, str) else "\n".join(b["text"] for b in content)


def _digest_all(db, book, upto: int, entries_by_idx: dict[int, list[dict]] | None = None) -> None:
    for i in range(upto):
        _store_digest(db, book, i, (entries_by_idx or {}).get(i, []))


def mixed(feature, user_content, schema):
    if feature == "reading_digest":
        return echo_digest(feature, user_content, schema)
    if feature == "recap":
        return RECAP_TEXT
    raise AssertionError(feature)


# ── recap: spoiler window ────────────────────────────────────────────────────

def test_recap_never_sees_past_the_position(db, make_book, tmp_path):
    user = _make_user(db, "spoil", "member")
    book = _epub_book(db, make_book, tmp_path, "Window Recap")
    # Every chapter is digested (another reader finished the book).
    _digest_all(db, book, 4)
    pct = _mid(book, db, 2, 0.4)
    window = companion.pct_window(db, book, pct)
    assert window.chapter_idx == 2

    fake = FakeProvider(responses=[RECAP_TEXT])
    ai.set_provider_override(fake)
    res = recap.generate(db, user, book, window)
    assert not res.cached
    [call] = fake.calls
    assert call["feature"] == "recap"
    sent = _all_text(call)
    assert f"Summary {book.id}:0" in sent and f"Summary {book.id}:1" in sent
    assert f"Summary {book.id}:2" not in sent and f"Summary {book.id}:3" not in sent
    assert "Oren Pike" in sent                       # chapter 2, before the position
    assert "Mark2end" not in sent                   # chapter 2, after the position
    assert "Mark3end" not in sent and "without its captain" not in sent
    # The summaries block comes first and is marked for prompt caching.
    first = call["user_content"][0]
    assert first["cache_control"] == {"type": "ephemeral"}
    assert f"Summary {book.id}:0" in first["text"]
    # Em-dashes from the model are replaced with plain hyphens.
    assert res.row.text == "Mara walked to the harbour.\n\nTobin rang the bell - twice."
    # No locator: the synced fraction is backed off by the margin before the cut.
    assert res.row.position_fraction == pytest.approx(pct - book_text.POSITION_MARGIN, abs=0.01)
    assert res.row.position_fraction < pct


def test_recap_cache_is_never_served_ahead_of_the_reader(db, make_book, tmp_path):
    ahead = _make_user(db, "ahead", "member")
    behind = _make_user(db, "behind", "member")
    book = _epub_book(db, make_book, tmp_path, "Shared Cache")
    _digest_all(db, book, 2)
    fake = FakeProvider(responses=[RECAP_TEXT, RECAP_TEXT, RECAP_TEXT])
    ai.set_provider_override(fake)

    late = companion.pct_window(db, book, _mid(book, db, 2, 0.9))
    early = companion.pct_window(db, book, _mid(book, db, 2, 0.2))
    assert not recap.generate(db, ahead, book, late).cached
    # Same chapter, same scope, but the cached text reaches past this reader.
    assert not recap.generate(db, behind, book, early).cached
    assert len(fake.calls) == 2
    # The row now ends earlier: served from the cache to both readers.
    assert recap.generate(db, behind, book, early).cached
    assert recap.generate(db, ahead, book, late).cached
    assert len(fake.calls) == 2


def test_recap_written_from_fewer_digests_goes_stale(db, make_book, tmp_path):
    user = _make_user(db, "stale", "member")
    book = _epub_book(db, make_book, tmp_path, "Stale")
    _store_digest(db, book, 0, [])
    fake = FakeProvider(responses=[RECAP_TEXT, RECAP_TEXT])
    ai.set_provider_override(fake)
    window = companion.pct_window(db, book, _mid(book, db, 2))
    with pytest.raises(recap.RecapNotReady):
        recap.generate(db, user, book, window)
    assert recap.generate(db, user, book, window, partial=True).row.needed == 2
    assert recap.generate(db, user, book, window, partial=True).cached
    _store_digest(db, book, 1, [])
    res = recap.generate(db, user, book, window)
    assert not res.cached and res.row.digested == 2
    assert len(fake.calls) == 2


def test_recap_whole_book_has_no_current_chapter_text(db, make_book, tmp_path):
    user = _make_user(db, "whole", "member")
    book = _epub_book(db, make_book, tmp_path, "Whole Book")
    _digest_all(db, book, 4)
    fake = FakeProvider(responses=[RECAP_TEXT])
    ai.set_provider_override(fake)
    res = recap.generate(db, user, book, recap.whole_book_window(db, book))
    assert res.row.chapter_idx == 4
    sent = _all_text(fake.calls[0])
    assert all(f"Summary {book.id}:{i}" in sent for i in range(4))
    assert "Mark0end" not in sent and "Mark3end" not in sent   # summaries only, no raw text


def test_recap_series_scope_only_read_volumes(db, make_book, tmp_path):
    user = _make_user(db, "series", "member")
    v1 = _epub_book(db, make_book, tmp_path, "Isle One", series="Isle", index=1)
    v2 = _epub_book(db, make_book, tmp_path, "Isle Two", series="Isle", index=2)
    v3 = _epub_book(db, make_book, tmp_path, "Isle Three", series="Isle", index=3)
    for b in (v1, v2, v3):
        _digest_all(db, b, 4)
    _read(db, user, v1)
    fake = FakeProvider(responses=[RECAP_TEXT])
    ai.set_provider_override(fake)
    window = companion.pct_window(db, v3, _mid(v3, db, 1))
    recap.generate(db, user, v3, window)
    sent = _all_text(fake.calls[0])
    assert f"Summary {v1.id}:3" in sent
    assert f"Summary {v2.id}:0" not in sent          # volume 2 not marked read
    assert sent.index(f"Summary {v1.id}:0") < sent.index(f"Summary {v3.id}:0")


# ── web endpoints ────────────────────────────────────────────────────────────

def test_endpoint_permissions(client: TestClient, db: Session, admin_user, make_book, tmp_path):
    member = _make_user(db, "reader", "member")
    keyless = _make_user(db, "keyless", "member", key=False)
    guest = _make_user(db, "guest", "guest", key=False)
    book = _epub_book(db, make_book, tmp_path, "Perms")
    hidden = _epub_book(db, make_book, tmp_path, "Hidden")
    lib = Library(name="Private", is_public=False, owner_id=admin_user[0].id)
    lib.books.append(hidden)
    db.add(lib)
    for u in (member, keyless):
        _position(db, u, book, _mid(book, db, 2))
        _position(db, u, hidden, _mid(hidden, db, 2))
    db.commit()

    urls = [
        ("get", f"/api/ai/books/{book.id}/companion", None),
        ("post", f"/api/ai/books/{book.id}/recap", {}),
        ("get", f"/api/ai/books/{book.id}/glossary", None),
        ("post", f"/api/ai/books/{book.id}/recall", {"term": "Mara"}),
        ("get", f"/api/ai/books/{book.id}/previously", None),
        ("delete", f"/api/ai/books/{book.id}/recap", None),
        ("post", f"/api/ai/books/{book.id}/digest", {}),
    ]
    for method, url, body in urls:
        kw = {"json": body} if body is not None else {}
        assert getattr(client, method)(url, headers=_headers(guest), **kw).status_code == 403, url
        hidden_url = url.replace(f"/books/{book.id}/", f"/books/{hidden.id}/")
        assert getattr(client, method)(hidden_url, headers=_headers(member), **kw).status_code == 404, url

    # Feature off: 404.
    ai_settings.set_feature_enabled(db, "recap", False)
    db.commit()
    assert client.post(f"/api/ai/books/{book.id}/recap", json={},
                       headers=_headers(member)).status_code == 404
    ai_settings.set_feature_enabled(db, "recap", True)
    ai_settings.set_feature_enabled(db, "recall", False)
    db.commit()
    assert client.post(f"/api/ai/books/{book.id}/recall", json={"term": "Mara"},
                       headers=_headers(member)).status_code == 404
    ai_settings.set_feature_enabled(db, "recall", True)
    ai_settings.set_feature_enabled(db, "reading_digest", False)
    db.commit()
    assert client.get(f"/api/ai/books/{book.id}/glossary",
                      headers=_headers(member)).status_code == 404
    ai_settings.set_feature_enabled(db, "reading_digest", True)
    db.commit()

    # No key: 409 before anything is queued.
    r = client.post(f"/api/ai/books/{book.id}/recap", json={}, headers=_headers(keyless))
    assert r.status_code == 409
    r = client.post(f"/api/ai/books/{book.id}/digest", json={}, headers=_headers(keyless))
    assert r.status_code == 409
    assert digest_job.active_for(keyless.id, book.id) is None

    # Digests off: the explicit start is a 404 as well.
    ai_settings.set_feature_enabled(db, "reading_digest", False)
    db.commit()
    assert client.post(f"/api/ai/books/{book.id}/digest", json={},
                       headers=_headers(member)).status_code == 404
    ai_settings.set_feature_enabled(db, "reading_digest", True)
    db.commit()
    assert client.post(f"/api/ai/books/{book.id}/digest", json={"scope": "everything"},
                       headers=_headers(member)).status_code == 422

    # A chapter past the position: 422.
    r = client.post(f"/api/ai/books/{book.id}/recap", json={"chapter": 3}, headers=_headers(member))
    assert r.status_code == 422


def test_recap_202_then_200_then_cached_then_ask_again(client, db, make_book, tmp_path):
    user = _make_user(db, "flow", "member")
    book = _epub_book(db, make_book, tmp_path, "Flow")
    _position(db, user, book, _mid(book, db, 2))
    db.commit()
    fake = FakeProvider(responses=mixed)
    ai.set_provider_override(fake)
    h = _headers(user)

    r = client.post(f"/api/ai/books/{book.id}/recap", json={}, headers=h)
    assert r.status_code == 202, r.text
    body = r.json()
    # Nothing is queued: the reader is told digests are needed, with the
    # estimate for the confirmation, and starts them explicitly.
    est = body.pop("estimate")
    assert body == {"needs_digest": True,
                    "coverage": {"digested": 0, "needed": 2, "failed": 0}, "job": None,
                    "chapter_idx": 2, "chapter_title": "The Lighthouse"}
    assert est["series"] is None                      # no read earlier volume
    assert est["book"]["chapters"] == 2 and est["book"]["calls"] == 1
    assert est["book"]["est_cost_usd"] > 0 and est["book"]["over_budget"] is False
    assert est["book"]["volumes"] == [{"book_id": book.id, "title": "Flow", "chapters": 2}]
    assert digest_job.active_for(user.id, book.id) is None and fake.calls == []

    r = client.post(f"/api/ai/books/{book.id}/digest", json={"scope": "book"}, headers=h)
    assert r.status_code == 202, r.text
    started = r.json()
    assert started["job"]["upto_chapter"] == 2 and started["job"]["scope"] == "book"
    assert started["scope"] == "book" and started["chapter_idx"] == 2
    assert started["coverage"] == {"digested": 0, "needed": 2, "failed": 0}
    # The worker has not planned the job yet, so the response's job carries
    # the estimate's figures rather than "0 of 0".
    assert started["job"]["total"] == 2 and started["job"]["est_chapters"] == 2
    assert started["job"]["est_cost_usd"] == started["estimate"]["est_cost_usd"]
    assert started["job"]["est_seconds"] == started["estimate"]["est_seconds"]
    # While it runs, the recap says so and points at the job.
    r = client.post(f"/api/ai/books/{book.id}/recap", json={}, headers=h)
    assert r.status_code == 202 and r.json()["job"]["id"] == started["job"]["id"]

    digest_job.run_pending()
    assert len(fake.calls) == 1 and fake.calls[0]["feature"] == "reading_digest"   # one unit
    # Nothing left to read: the start is a 200 without a job.
    r = client.post(f"/api/ai/books/{book.id}/digest", json={}, headers=h)
    assert r.status_code == 200 and r.json()["job"] is None

    r = client.post(f"/api/ai/books/{book.id}/recap", json={}, headers=h)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["cached"] is False and out["chapter_idx"] == 2
    assert out["chapter_title"] == "The Lighthouse" and out["whole_book"] is False
    assert out["text"].startswith("Mara walked")
    assert out["scope"]["excluded_volumes"] == []
    assert len(fake.calls) == 2

    r = client.post(f"/api/ai/books/{book.id}/recap", json={}, headers=h)
    assert r.status_code == 200 and r.json()["cached"] is True
    assert len(fake.calls) == 2

    c = client.get(f"/api/ai/books/{book.id}/companion", headers=h).json()
    assert c["recap_cached"] is True

    r = client.delete(f"/api/ai/books/{book.id}/recap", headers=h)
    assert r.status_code == 200 and r.json() == {"deleted": True, "chapter_idx": 2}
    assert db.query(AIRecap).filter_by(book_id=book.id).count() == 0
    r = client.post(f"/api/ai/books/{book.id}/recap", json={}, headers=h)
    assert r.status_code == 200 and r.json()["cached"] is False
    assert len(fake.calls) == 3
    usage = db.query(AIUsage).filter_by(user_id=user.id).all()
    assert sorted(u.feature for u in usage) == ["reading_digest", "recap", "recap"]


def test_recap_partial_after_a_failed_chapter(client, db, make_book, tmp_path):
    user = _make_user(db, "partial", "member")
    book = _epub_book(db, make_book, tmp_path, "Partial")
    _position(db, user, book, _mid(book, db, 2))
    _store_digest(db, book, 0, [])
    db.commit()
    ai.set_provider_override(FakeProvider(responses=[RECAP_TEXT]))
    h = _headers(user)
    assert client.post(f"/api/ai/books/{book.id}/recap", json={}, headers=h).status_code == 202
    r = client.post(f"/api/ai/books/{book.id}/recap", json={"partial": True}, headers=h)
    assert r.status_code == 200 and r.json()["partial"] is True
    assert r.json()["missing_volumes"] == [
        {"book_id": book.id, "title": "Partial", "series_index": None, "missing": 1, "needed": 2}]


def test_entries_to_dicts_places_carry_series_index(db, make_book, tmp_path):
    user = _make_user(db, "places", "member")
    lone = _epub_book(db, make_book, tmp_path, "Lone")
    scope = digest.user_scope(db, user, lone)
    entries = digest.fold_entries([_store_digest(db, lone, 0, [_entry("Mara Vell", "A pilot.")])])
    [d] = companion.entries_to_dicts(db, scope, entries)
    assert d["first_seen"]["series_index"] is None and d["last_seen"]["series_index"] is None

    v1 = _epub_book(db, make_book, tmp_path, "Reef One", series="Reef", index=1)
    v2 = _epub_book(db, make_book, tmp_path, "Reef Two", series="Reef", index=2.5)
    _read(db, user, v1)
    scope = digest.user_scope(db, user, v2)
    entries = digest.fold_entries([_store_digest(db, v1, 0, [_entry("Mara Vell", "A pilot.")]),
                                   _store_digest(db, v2, 0, [_entry("Mara Vell", "Back home.")])])
    [d] = companion.entries_to_dicts(db, scope, entries)
    assert d["first_seen"]["book_id"] == v1.id and d["first_seen"]["series_index"] == 1.0
    assert d["last_seen"]["book_id"] == v2.id and d["last_seen"]["series_index"] == 2.5


def test_partial_recap_after_a_book_only_job_reports_missing_volumes(db, make_book, tmp_path):
    user = _make_user(db, "gaps", "member")
    v1 = _epub_book(db, make_book, tmp_path, "Gap One", series="Gap", index=1)
    v2 = _epub_book(db, make_book, tmp_path, "Gap Two", series="Gap", index=2)
    v3 = _epub_book(db, make_book, tmp_path, "Gap Three", series="Gap", index=3)
    _read(db, user, v1)
    _read(db, user, v2)
    _store_digest(db, v2, 0, [])
    db.commit()
    digest_job.reset()      # drop the whole-volume jobs marking v1/v2 read queued
    fake = FakeProvider(responses=mixed)
    ai.set_provider_override(fake)
    snap = digest_job.enqueue(user_id=user.id, book_id=v3.id, upto=2, current_book_only=True)
    digest_job.run_pending()
    assert digest_job.get(snap["id"])["status"] == "done"

    window = companion.pct_window(db, v3, _mid(v3, db, 2))
    scope = digest.user_scope(db, user, v3)
    res = recap.generate(db, user, v3, window, partial=True)
    body = recap.to_dict(res, window, scope)
    expected = [
        {"book_id": v1.id, "title": "Gap One", "series_index": 1.0, "missing": 4, "needed": 4},
        {"book_id": v2.id, "title": "Gap Two", "series_index": 2.0, "missing": 3, "needed": 4},
    ]
    assert body["partial"] is True and not body["cached"]
    assert body["missing_volumes"] == expected      # the current book: nothing missing, not listed
    assert v3.id not in {v["book_id"] for v in body["missing_volumes"]}

    # Cached: the same list, from the stored row.
    res2 = recap.generate(db, user, v3, window, partial=True)
    assert res2.cached
    assert recap.to_dict(res2, window, scope)["missing_volumes"] == expected

    # A row from before the column reports [].
    res2.row.missing_json = None
    assert recap.to_dict(res2, window, scope)["missing_volumes"] == []


def test_fully_digested_recap_reports_no_missing_volumes(db, make_book, tmp_path):
    user = _make_user(db, "nogaps", "member")
    v1 = _epub_book(db, make_book, tmp_path, "Full One", series="Full", index=1)
    v2 = _epub_book(db, make_book, tmp_path, "Full Two", series="Full", index=2)
    _read(db, user, v1)
    _digest_all(db, v1, 4)
    _digest_all(db, v2, 2)
    ai.set_provider_override(FakeProvider(responses=[RECAP_TEXT]))
    window = companion.pct_window(db, v2, _mid(v2, db, 2))
    res = recap.generate(db, user, v2, window)
    body = recap.to_dict(res, window, digest.user_scope(db, user, v2))
    assert body["partial"] is False and body["missing_volumes"] == []


def test_companion_payload(client, db, make_book, tmp_path):
    user = _make_user(db, "companion", "member")
    keyless = _make_user(db, "nokey", "member", key=False)
    book = _epub_book(db, make_book, tmp_path, "Companion")
    pdf = make_book(title="Manual", file_format="pdf")
    nomap = make_book(title="No Map", file_path=str(make_epub(tmp_path / "nomap.epub")))
    h = _headers(user)
    db.commit()

    c = client.get(f"/api/ai/books/{book.id}/companion", headers=h).json()
    assert c["available"] is False and c["reason"] == "no_position"
    assert client.get(f"/api/ai/books/{pdf.id}/companion", headers=h).json()["reason"] == "not_epub"
    assert client.get(f"/api/ai/books/{nomap.id}/companion", headers=h).json()["reason"] == "no_chapters"
    r = client.post(f"/api/ai/books/{nomap.id}/recap", json={}, headers=h)
    assert r.status_code == 422
    assert client.get(f"/api/ai/books/{book.id}/companion",
                      headers=_headers(keyless)).json()["reason"] == "no_key"

    _position(db, user, book, _mid(book, db, 2), device="web")
    _store_digest(db, book, 0, [_entry("Mara Vell", "A woman at the harbour.")])
    db.commit()
    c = client.get(f"/api/ai/books/{book.id}/companion", headers=h).json()
    assert c["available"] is True and c["reason"] is None
    assert c["position"]["chapter_idx"] == 2 and c["position"]["source"] == "web"
    assert c["position"]["chapter_title"] == "The Lighthouse"
    assert c["chapters"] == 4
    assert c["coverage"] == {"digested": 1, "needed": 2, "failed": 0}
    assert c["glossary_count"] == 1 and c["recap_cached"] is False
    assert c["features"] == {"recap": True, "recall": True, "glossary": True, "ask": True}
    assert c["job"] is None                         # GET companion never queues work
    assert c["last_job"] is None

    # The latest ended job comes back as last_job (the page offers Continue).
    from datetime import datetime, timedelta
    from backend.models.ai_job import AIJob
    now = datetime.utcnow()
    db.add(AIJob(id="older0000001", user_id=user.id, book_id=book.id, status="done",
                 scope="book", kind="digest", created_at=now - timedelta(hours=2)))
    db.add(AIJob(id="latest000001", user_id=user.id, book_id=book.id, status="cancelled",
                 scope="series", kind="digest", total=10, done=4, created_at=now - timedelta(hours=1)))
    db.commit()
    c = client.get(f"/api/ai/books/{book.id}/companion", headers=h).json()
    assert c["job"] is None
    assert c["last_job"]["id"] == "latest000001"
    assert c["last_job"]["status"] == "cancelled" and c["last_job"]["scope"] == "series"

    ai_settings.set_feature_enabled(db, "reading_digest", False)
    db.commit()
    c = client.get(f"/api/ai/books/{book.id}/companion", headers=h).json()
    assert c["available"] is False and c["reason"] == "feature_off"


def test_glossary_endpoint(client, db, make_book, tmp_path):
    user = _make_user(db, "glossary", "member")
    v1 = _epub_book(db, make_book, tmp_path, "Cove One", series="Cove", index=1)
    v2 = _epub_book(db, make_book, tmp_path, "Cove Two", series="Cove", index=2)
    v3 = _epub_book(db, make_book, tmp_path, "Cove Three", series="Cove", index=3)
    _read(db, user, v1)
    _digest_all(db, v1, 4, {1: [_entry("Mara Vell", "A ferry pilot.")]})
    _store_digest(db, v3, 0, [_entry("mara  vell", "Back at the harbour."),
                              _entry("Tobin Ash", "A bell ringer.")])
    _store_digest(db, v3, 2, [_entry("Oren Pike", "The keeper.")])   # ahead of the reader
    _position(db, user, v3, _mid(v3, db, 2))
    db.commit()
    r = client.get(f"/api/ai/books/{v3.id}/glossary", headers=_headers(user))
    assert r.status_code == 200, r.text
    g = r.json()
    assert g["chapter_idx"] == 2
    assert [e["name"] for e in g["entries"]] == ["Mara Vell", "Tobin Ash"]
    mara = g["entries"][0]
    assert mara["line"] == "Back at the harbour."
    assert mara["first_seen"] == {"book_id": v1.id, "book_title": "Cove One", "series_index": 1.0,
                                  "chapter_idx": 1, "chapter_title": "The Bell"}
    assert mara["last_seen"]["book_id"] == v3.id and mara["last_seen"]["series_index"] == 3.0
    assert g["scope"]["excluded_volumes"] == [
        {"book_id": v2.id, "title": "Cove Two", "series_index": 2.0, "reason": "not_read"}]
    assert g["coverage"] == {"digested": 5, "needed": 6, "failed": 0}    # v3 chapter 1 missing
    assert g["job"] is None                       # reading the glossary never queues
    assert digest_job.active_for(user.id, v3.id) is None
    assert g["rev"]

    # An explicit earlier chapter: fold before it only.
    g0 = client.get(f"/api/ai/books/{v3.id}/glossary?chapter=0", headers=_headers(user)).json()
    assert [e["name"] for e in g0["entries"]] == ["Mara Vell"]
    assert g0["entries"][0]["line"] == "A ferry pilot."


def test_glossary_get_never_queues(client, db, make_book, tmp_path, plugin):
    user = _make_user(db, "glossread", "member")
    book = _epub_book(db, make_book, tmp_path, "Read Only")
    _store_digest(db, book, 0, [_entry("Mara Vell", "A ferry pilot.")])
    _position(db, user, book, _mid(book, db, 3))
    c, key_for = plugin
    hp = key_for(user)
    db.commit()
    fake = FakeProvider(responses=echo_digest)
    ai.set_provider_override(fake)
    # Web (the old ?queue=true is ignored) and plugin: read-only.
    for url, h in ((f"/api/ai/books/{book.id}/glossary", _headers(user)),
                   (f"/api/ai/books/{book.id}/glossary?queue=true", _headers(user)),
                   (f"/api/tome-sync/ai/books/{book.id}/glossary", hp)):
        g = (c if "tome-sync" in url else client).get(url, headers=h).json()
        assert g["coverage"] == {"digested": 1, "needed": 3, "failed": 0}, url
        assert g["job"] is None, url
        assert [e["name"] for e in g["entries"]] == ["Mara Vell"]
    assert digest_job.active_for(user.id, book.id) is None and digest_job._queue == deque()
    assert fake.calls == []
    # A job the reader started shows up in the payload.
    started = client.post(f"/api/ai/books/{book.id}/digest", json={}, headers=_headers(user)).json()
    g = client.get(f"/api/ai/books/{book.id}/glossary", headers=_headers(user)).json()
    assert g["job"]["id"] == started["job"]["id"]


# ── Recall ───────────────────────────────────────────────────────────────────

def _recall_book(db, make_book, tmp_path, user, title="Recall"):
    book = _epub_book(db, make_book, tmp_path, title)
    _store_digest(db, book, 0, [_entry("Mara Vell", "A woman who walks to the harbour.")])
    _store_digest(db, book, 1, [_entry("Tobin Ash", "Rings the ferry bell.")])
    _store_digest(db, book, 3, [_entry("Lost Captain", "Never came back.", kind="person")])
    _position(db, user, book, _mid(book, db, 2, 0.6))
    db.commit()
    return book


def test_recall_hit_needs_no_model_call(client, db, make_book, tmp_path):
    user = _make_user(db, "hit", "member")
    book = _recall_book(db, make_book, tmp_path, user)
    fake = FakeProvider(responses=[])
    ai.set_provider_override(fake)
    for term in ("Mara", "mara vell", "Tobin's", "  TOBIN  ASH "):
        r = client.post(f"/api/ai/books/{book.id}/recall", json={"term": term}, headers=_headers(user))
        assert r.status_code == 200, r.text
        out = r.json()
        assert out["found"] is True and out["live"] is False, term
        assert out["entry"]["first_seen"]["chapter_idx"] in (0, 1)
    assert fake.calls == []


def test_recall_miss_makes_one_cited_call(client, db, make_book, tmp_path):
    user = _make_user(db, "miss", "member")
    book = _recall_book(db, make_book, tmp_path, user, "Recall Miss")
    fake = FakeProvider(responses=[{
        "found": True, "name": "Oren Pike", "kind": "person",
        "line": "The lighthouse keeper.", "more": "Keeps a ledger of ships.",
        "evidence": "the keeper,  OREN Pike, kept a ledger",
    }])
    ai.set_provider_override(fake)
    r = client.post(f"/api/ai/books/{book.id}/recall", json={"term": "Oren Pike"},
                    headers=_headers(user))
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["found"] is True and out["live"] is True
    assert out["entry"]["line"] == "The lighthouse keeper."
    assert out["entry"]["first_seen"]["chapter_idx"] == 2
    [call] = fake.calls
    assert call["feature"] == "recall"
    sent = _all_text(call)
    assert "Oren Pike" in sent and "Mara Vell" in sent      # chapter text + glossary
    assert "Mark2end" not in sent and "Mark3end" not in sent
    assert "Lost Captain" not in sent and "without its captain" not in sent
    assert db.query(AIUsage).filter_by(user_id=user.id, feature="recall").count() == 1


def test_recall_caches_the_chapter_text_not_the_term(client, db, make_book, tmp_path):
    """Live Recall puts the chapter text first, marked for prompt caching, and
    the term after it, so a second lookup at the same position reuses the
    cached prefix (the smoke run paid ~14k uncached input tokens per lookup)."""
    user = _make_user(db, "cachey", "member")
    book = _recall_book(db, make_book, tmp_path, user, "Recall Cache")
    fake = FakeProvider(responses=lambda *_a: {"found": False, "name": "", "kind": "term",
                                               "line": "", "more": "", "evidence": ""})
    ai.set_provider_override(fake)
    for term in ("Oren Pike", "lighthouse"):
        r = client.post(f"/api/ai/books/{book.id}/recall", json={"term": term},
                        headers=_headers(user))
        assert r.status_code == 200, r.text
    first, second = fake.calls
    assert first["user_content"][0] == second["user_content"][0]
    assert first["user_content"][0]["cache_control"] == {"type": "ephemeral"}
    assert all("cache_control" not in b for b in first["user_content"][1:])
    assert '"term": "Oren Pike"' in first["user_content"][1]["text"]
    assert '"term": "lighthouse"' in second["user_content"][1]["text"]


def test_recall_drops_an_uncited_answer(client, db, make_book, tmp_path):
    user = _make_user(db, "uncited", "member")
    book = _recall_book(db, make_book, tmp_path, user, "Recall Uncited")
    ai.set_provider_override(FakeProvider(responses=[{
        "found": True, "name": "Oren Pike", "kind": "person", "line": "Secretly the captain.",
        "more": "", "evidence": "Oren Pike was the captain all along",
    }]))
    out = client.post(f"/api/ai/books/{book.id}/recall", json={"term": "Oren"},
                      headers=_headers(user)).json()
    assert out["found"] is False and out["live"] is True and "entry" not in out


def test_recall_absent_term_is_not_found_without_a_call(client, db, make_book, tmp_path):
    user = _make_user(db, "absent", "member")
    book = _recall_book(db, make_book, tmp_path, user, "Recall Absent")
    fake = FakeProvider(responses=[])
    ai.set_provider_override(fake)
    for term in ("Friar Laurence", "Lost Captain", "captain"):   # unknown, or only after the position
        out = client.post(f"/api/ai/books/{book.id}/recall", json={"term": term},
                          headers=_headers(user)).json()
        assert out["found"] is False and "entry" not in out, term
        assert out["message"] == "Nothing about that before this page."
    assert fake.calls == []
    assert client.post(f"/api/ai/books/{book.id}/recall", json={"term": " ... "},
                       headers=_headers(user)).status_code == 422


# ── Previously on ────────────────────────────────────────────────────────────

def test_previously_404_without_an_earlier_volume(client, db, make_book, tmp_path):
    user = _make_user(db, "first", "member")
    book = _epub_book(db, make_book, tmp_path, "Solo", series="Solo", index=1)
    db.commit()
    assert client.get(f"/api/ai/books/{book.id}/previously", headers=_headers(user)).status_code == 404


def test_finishing_a_volume_writes_previously_on(client, db, make_book, tmp_path):
    user = _make_user(db, "finish", "member")
    v1 = _epub_book(db, make_book, tmp_path, "Bay One", series="Bay", index=1)
    v2 = _epub_book(db, make_book, tmp_path, "Bay Two", series="Bay", index=2)
    db.commit()
    h = _headers(user)
    # Nothing written yet.
    assert client.get(f"/api/ai/books/{v2.id}/previously", headers=h).status_code == 404

    r = client.put(f"/api/books/{v1.id}/status", json={"status": "read"}, headers=h)
    assert r.status_code == 200
    fake = FakeProvider(responses=mixed)
    ai.set_provider_override(fake)
    digest_job.run_pending()
    assert [c["feature"] for c in fake.calls] == ["reading_digest", "recap"]   # one unit of 4
    assert "Mark3end" not in _all_text(fake.calls[-1])   # summaries only

    r = client.get(f"/api/ai/books/{v2.id}/previously", headers=h)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["book_id"] == v1.id and out["title"] == "Bay One" and out["chapter_idx"] == 4
    assert out["text"].startswith("Mara walked")

    c = client.get(f"/api/ai/books/{v2.id}/companion", headers=h).json()
    assert c["previously"] == {"book_id": v1.id, "title": "Bay One"}
    # Past chapter 0 of volume 2, the companion no longer offers it.
    _position(db, user, v2, _mid(v2, db, 1))
    db.commit()
    assert client.get(f"/api/ai/books/{v2.id}/companion", headers=h).json()["previously"] is None

    # Another reader who has not marked volume 1 read does not get it.
    other = _make_user(db, "other", "member")
    db.commit()
    assert client.get(f"/api/ai/books/{v2.id}/previously", headers=_headers(other)).status_code == 404


def test_previously_on_is_skipped_when_the_next_volume_is_under_way(db, make_book, tmp_path):
    user = _make_user(db, "binge", "member")
    v1 = _epub_book(db, make_book, tmp_path, "Gulf One", series="Gulf", index=1)
    v2 = _epub_book(db, make_book, tmp_path, "Gulf Two", series="Gulf", index=2)
    v3 = _epub_book(db, make_book, tmp_path, "Gulf Three", series="Gulf", index=3)
    for b in (v1, v2, v3):
        _digest_all(db, b, 4)
    _read(db, user, v1)
    _read(db, user, v2)
    db.commit()
    digest_job.reset()
    fake = FakeProvider(responses=mixed)
    ai.set_provider_override(fake)

    # Volume 2 is already read: the recap of volume 1 could never be shown.
    snap = digest_job.enqueue(user_id=user.id, book_id=v1.id, upto=4, followups=["previously_recap"])
    digest_job.run_pending()
    [result] = digest_job.get(snap["id"])["followups"]
    assert result["status"] == "skipped"
    assert fake.calls == [] and db.query(AIRecap).filter_by(book_id=v1.id).count() == 0

    # Volume 3 not started: volume 2's recap is written.
    snap = digest_job.enqueue(user_id=user.id, book_id=v2.id, upto=4, followups=["previously_recap"])
    digest_job.run_pending()
    assert digest_job.get(snap["id"])["followups"] == [{"name": "previously_recap", "status": "done"}]
    assert [c["feature"] for c in fake.calls] == ["recap"]

    # Volume 3 is the last one so far: its recap is written for a volume 4 that may come.
    _read(db, user, v3)
    db.commit()
    digest_job.run_pending()
    assert [c["feature"] for c in fake.calls] == ["recap", "recap"]


def test_scope_and_previously_skip_a_volume_the_user_cannot_see(client, db, admin_user, make_book, tmp_path):
    member = _make_user(db, "outsider", "member")
    v1 = _epub_book(db, make_book, tmp_path, "Secret Sea One", series="Secret Sea", index=1)
    v2 = _epub_book(db, make_book, tmp_path, "Secret Sea Two", series="Secret Sea", index=2)
    lib = Library(name="Private", is_public=False, owner_id=admin_user[0].id)
    lib.books.append(v1)
    db.add(lib)
    _read(db, member, v1)
    _store_digest(db, v1, 0, [_entry("Hidden Harbourmaster", "Only in the private volume.")],
                  summary="HIDDEN-VOLUME summary.")
    db.add(AIRecap(book_id=v1.id, chapter_idx=4, scope_hash=digest.user_scope(db, member, v1).scope_hash,
                   position_fraction=1.0, text="HIDDEN recap.", model="m", digested=4, needed=4))
    _position(db, member, v2, 0.01)
    db.commit()
    digest_job.reset()

    scope = digest.user_scope(db, member, v2)
    assert scope.included == [] and scope.excluded == []
    assert "Secret Sea One" not in str(scope.to_dict())
    assert recap.previous_volume(db, member, v2) is None

    h = _headers(member)
    g = client.get(f"/api/ai/books/{v2.id}/glossary?queue=false", headers=h).json()
    assert "Hidden Harbourmaster" not in str(g) and "Secret Sea One" not in str(g)
    assert client.get(f"/api/ai/books/{v2.id}/previously", headers=h).status_code == 404
    assert client.get(f"/api/ai/books/{v2.id}/companion", headers=h).json()["previously"] is None

    fake = FakeProvider(responses=[RECAP_TEXT])
    ai.set_provider_override(fake)
    _store_digest(db, v2, 0, [])
    recap.generate(db, member, v2, companion.pct_window(db, v2, _mid(v2, db, 1)), partial=True)
    sent = _all_text(fake.calls[0])
    assert f"Summary {v1.id}:" not in sent and "HIDDEN" not in sent and "Secret Sea One" not in sent


# ── plugin endpoints ─────────────────────────────────────────────────────────

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


def test_plugin_status_and_auth(db, make_book, tmp_path, plugin):
    c, key_for = plugin
    member = _make_user(db, "kindle", "member")
    guest = _make_user(db, "kguest", "guest", key=False)
    keyless = _make_user(db, "knokey", "member", key=False)
    book = _epub_book(db, make_book, tmp_path, "Plugin Auth")
    hm, hg, hk = key_for(member), key_for(guest), key_for(keyless)
    db.commit()

    assert c.get("/api/tome-sync/ai/status", headers=hm).json() == {
        "recap": True, "recall": True, "glossary": True, "ask": True}
    assert c.get("/api/tome-sync/ai/status", headers=hg).json() == {
        "recap": False, "recall": False, "glossary": False, "ask": False}
    assert c.get("/api/tome-sync/ai/status", headers=hk).json() == {
        "recap": False, "recall": False, "glossary": True, "ask": False}
    assert c.get("/api/tome-sync/ai/status").status_code == 422          # no header
    assert c.get("/api/tome-sync/ai/status",
                 headers={"Authorization": "Bearer tk_nope"}).status_code == 401
    # A web JWT is not a plugin key.
    assert c.get(f"/api/tome-sync/ai/books/{book.id}/glossary?pct=0.5",
                 headers=_headers(member)).status_code == 401
    assert c.get(f"/api/tome-sync/ai/books/{book.id}/glossary?pct=0.5", headers=hg).status_code == 403
    assert c.get("/api/tome-sync/ai/books/999999/glossary?pct=0.5", headers=hm).status_code == 404


def test_plugin_endpoints_404_on_a_hidden_book_and_403_for_guest(db, admin_user, make_book, tmp_path, plugin):
    c, key_for = plugin
    member = _make_user(db, "kmember", "member")
    guest = _make_user(db, "kguest2", "guest", key=False)
    book = _epub_book(db, make_book, tmp_path, "Plugin Visible")
    hidden = _epub_book(db, make_book, tmp_path, "Plugin Hidden")
    lib = Library(name="Private", is_public=False, owner_id=admin_user[0].id)
    lib.books.append(hidden)
    db.add(lib)
    hm, hg = key_for(member), key_for(guest)
    db.commit()
    fake = FakeProvider(responses=mixed)
    ai.set_provider_override(fake)

    base = f"/api/tome-sync/ai/books/{hidden.id}"
    assert c.get(f"{base}/glossary?pct=0.5", headers=hm).status_code == 404
    assert c.post(f"{base}/recap", json={"pct": 0.5}, headers=hm).status_code == 404
    assert c.post(f"{base}/recall", json={"term": "Mara", "pct": 0.5}, headers=hm).status_code == 404
    assert c.post(f"{base}/digest", json={"pct": 0.5}, headers=hm).status_code == 404
    assert fake.calls == [] and digest_job.active_for(member.id, hidden.id) is None

    base = f"/api/tome-sync/ai/books/{book.id}"
    assert c.post(f"{base}/recap", json={"pct": 0.5}, headers=hg).status_code == 403
    assert c.post(f"{base}/recall", json={"term": "Mara", "pct": 0.5}, headers=hg).status_code == 403
    assert c.post(f"{base}/digest", json={"pct": 0.5}, headers=hg).status_code == 403
    assert fake.calls == []


def test_plugin_glossary_rev_and_position_response(db, make_book, tmp_path, plugin):
    c, key_for = plugin
    user = _make_user(db, "revs", "member")
    book = _epub_book(db, make_book, tmp_path, "Plugin Rev")
    h = key_for(user)
    _store_digest(db, book, 0, [_entry("Mara Vell", "At the harbour.")])
    db.commit()
    pct = _mid(book, db, 2)

    r = c.get(f"/api/tome-sync/ai/books/{book.id}/glossary?pct={pct}", headers=h)
    assert r.status_code == 200, r.text
    g1 = r.json()
    assert g1["chapter_idx"] == 2 and [e["name"] for e in g1["entries"]] == ["Mara Vell"]

    r = c.put(f"/api/tome-sync/position/{book.id}", json={"percentage": pct}, headers=h)
    assert r.status_code == 200
    assert r.json()["glossary_rev"] == g1["rev"]

    _store_digest(db, book, 1, [_entry("Tobin Ash", "Rings the bell.")])
    db.commit()
    g2 = c.get(f"/api/tome-sync/ai/books/{book.id}/glossary?pct={pct}", headers=h).json()
    assert g2["rev"] != g1["rev"] and g2["count"] == 2
    r = c.put(f"/api/tome-sync/position/{book.id}", json={"percentage": pct}, headers=h)
    assert r.json()["glossary_rev"] == g2["rev"]
    # Without pct the stored position is used.
    assert c.get(f"/api/tome-sync/ai/books/{book.id}/glossary", headers=h).json()["rev"] == g2["rev"]

    ai_settings.set_feature_enabled(db, "reading_digest", False)
    db.commit()
    r = c.put(f"/api/tome-sync/position/{book.id}", json={"percentage": pct}, headers=h)
    assert r.status_code == 200 and r.json()["glossary_rev"] is None
    assert c.get(f"/api/tome-sync/ai/books/{book.id}/glossary?pct={pct}", headers=h).status_code == 404


def test_plugin_recap_and_recall_by_pct(db, make_book, tmp_path, plugin):
    c, key_for = plugin
    user = _make_user(db, "device", "member")
    book = _epub_book(db, make_book, tmp_path, "Plugin Recap")
    h = key_for(user)
    db.commit()
    fake = FakeProvider(responses=mixed)
    ai.set_provider_override(fake)
    pct = _mid(book, db, 1, 0.5)

    r = c.post(f"/api/tome-sync/ai/books/{book.id}/recap", json={"pct": pct}, headers=h)
    assert r.status_code == 202, r.text
    assert r.json()["needs_digest"] is True and r.json()["job"] is None
    r = c.post(f"/api/tome-sync/ai/books/{book.id}/digest", json={"pct": pct}, headers=h)
    assert r.status_code == 202, r.text
    job = r.json()["job"]
    assert job["upto_chapter"] == 1 and job["scope"] == "book"
    assert c.get(f"/api/tome-sync/ai/jobs/{job['id']}", headers=h).json()["status"] == "queued"
    other = key_for(_make_user(db, "nosy", "member"))
    db.commit()
    assert c.get(f"/api/tome-sync/ai/jobs/{job['id']}", headers=other).status_code == 404
    digest_job.run_pending()
    assert c.get(f"/api/tome-sync/ai/jobs/{job['id']}", headers=h).json()["status"] == "done"

    r = c.post(f"/api/tome-sync/ai/books/{book.id}/recap", json={"pct": pct}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["chapter_idx"] == 1 and r.json()["cached"] is False
    assert r.json()["missing_volumes"] == []
    assert "Mark1end" not in _all_text(fake.calls[-1])
    assert "Tobin Ash" in _all_text(fake.calls[-1])

    fake.responses = lambda *_a: {"found": False, "name": "", "kind": "term", "line": "",
                                  "more": "", "evidence": ""}
    r = c.post(f"/api/tome-sync/ai/books/{book.id}/recall", json={"term": "Oren Pike", "pct": pct},
               headers=h)
    assert r.status_code == 200 and r.json()["found"] is False   # chapter 2: not yet
    n = len(fake.calls)
    r = c.post(f"/api/tome-sync/ai/books/{book.id}/recall", json={"term": "lighthouse", "pct": pct},
               headers=h)
    assert r.status_code == 200 and r.json()["found"] is False and r.json()["live"] is True
    assert len(fake.calls) == n + 1


def test_recap_cache_key_includes_the_scope(db, make_book, tmp_path):
    """The recap cache key includes the scope: a reader with a read earlier
    volume does not get the recap written for a reader without it."""
    a = _make_user(db, "scope_a", "member")
    b = _make_user(db, "scope_b", "member")
    v1 = _epub_book(db, make_book, tmp_path, "Fen One", series="Fen", index=1)
    v2 = _epub_book(db, make_book, tmp_path, "Fen Two", series="Fen", index=2)
    _digest_all(db, v1, 4)
    _digest_all(db, v2, 2)
    _read(db, a, v1)
    fake = FakeProvider(responses=[RECAP_TEXT, RECAP_TEXT])
    ai.set_provider_override(fake)
    w = companion.pct_window(db, v2, _mid(v2, db, 2))
    recap.generate(db, a, v2, w)
    assert not recap.generate(db, b, v2, w).cached
    assert db.query(AIRecap).filter_by(book_id=v2.id).count() == 2
    assert f"Summary {v1.id}:0" in _all_text(fake.calls[0])
    assert f"Summary {v1.id}:0" not in _all_text(fake.calls[1])


# ── recap as volumes ─────────────────────────────────────────────────────────

VOLUME_TEXT = {"paragraphs": ["Mara sailed out.", "The storm passed.", "Tobin stayed.",
                              "The bell was rung — at last."]}


def _volume_series(db, make_book, tmp_path, user, name: str):
    """Vols 1 and 2 read, reader on vol 3 (no position)."""
    vols = [_epub_book(db, make_book, tmp_path, f"{name} {i}", series=name, index=i) for i in (1, 2, 3)]
    _read(db, user, vols[0])
    _read(db, user, vols[1])
    return vols


def test_recap_volumes_list_statuses_and_no_position(client, db, make_book, tmp_path):
    user = _make_user(db, "vols", "member")
    v1, v2, v3 = _volume_series(db, make_book, tmp_path, user, "Reef")
    own = lambda v: digest.Scope(book=v).scope_hash
    # vol 1: ready (all digested, full row); vol 2: partial row.
    _digest_all(db, v1, 4)
    db.add(AIRecap(book_id=v1.id, chapter_idx=4, scope_hash=own(v1), position_fraction=1.0,
                   text="Vol one recap.", model="m", digested=4, needed=4))
    _digest_all(db, v2, 2)
    db.add(AIRecap(book_id=v2.id, chapter_idx=4, scope_hash=own(v2), position_fraction=1.0,
                   text="Vol two partial.", model="m", digested=2, needed=4))
    db.commit()
    digest_job.reset()   # the setup's _read queued finished-book jobs
    fake = FakeProvider(responses=[])
    ai.set_provider_override(fake)

    r = client.get(f"/api/ai/books/{v3.id}/recap/volumes", headers=_headers(user))
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["can_write"] is True
    assert [v["book_id"] for v in out["volumes"]] == [v1.id, v2.id]
    a, b = out["volumes"]
    assert a["status"] == "ready" and a["text"] == "Vol one recap." and a["missing_chapters"] == 0
    assert a["chapters"] == 4 and a["series_index"] == 1 and a["created_at"]
    assert b["status"] == "partial" and b["digested"] == 2 and b["missing_chapters"] == 2
    assert out["current"] == {"book_id": v3.id, "title": "Reef 3", "series_index": 3,
                              "has_position": False, "recap_cached": False}
    assert fake.calls == [] and digest_job.active_for(user.id, v1.id) is None

    # Remove vol 1's row: missing, text null.
    db.query(AIRecap).filter_by(book_id=v1.id).delete()
    db.commit()
    a = client.get(f"/api/ai/books/{v3.id}/recap/volumes", headers=_headers(user)).json()["volumes"][0]
    assert a["status"] == "missing" and a["text"] is None and a["created_at"] is None
    assert a["digested"] == 4 and a["needed"] == 4 and a["missing_chapters"] == 0

    # With a position on vol 3, current reports it.
    _position(db, user, v3, _mid(v3, db, 1))
    db.commit()
    cur = client.get(f"/api/ai/books/{v3.id}/recap/volumes", headers=_headers(user)).json()["current"]
    assert cur["has_position"] is True and cur["recap_cached"] is False


def test_recap_volume_post_writes_from_summaries_and_caches(client, db, make_book, tmp_path):
    user = _make_user(db, "volpost", "member")
    v1, v2, v3 = _volume_series(db, make_book, tmp_path, user, "Cove")
    _digest_all(db, v1, 4)
    db.commit()
    fake = FakeProvider(responses=[VOLUME_TEXT])
    ai.set_provider_override(fake)
    h = _headers(user)

    r = client.post(f"/api/ai/books/{v3.id}/recap/volumes/{v1.id}", json={}, headers=h)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["status"] == "ready" and out["book_id"] == v1.id
    assert out["text"].endswith("The bell was rung - at last.")
    [call] = fake.calls
    assert call["feature"] == "recap" and call["system"] == volume_recap.VOLUME_PROMPT
    sent = _all_text(call)
    assert all(f"Summary {v1.id}:{i}" in sent for i in range(4))
    assert not any(f"Mark{i}end" in sent for i in range(4))     # summaries only
    assert f"Summary {v2.id}" not in sent
    usage_row = db.query(AIUsage).order_by(AIUsage.id.desc()).first()
    assert usage_row.feature == "recap" and usage_row.book_id == v1.id
    row = db.query(AIRecap).filter_by(book_id=v1.id).one()
    assert row.chapter_idx == 4 and row.position_fraction == 1.0
    assert row.scope_hash == digest.Scope(book=v1).scope_hash

    g = client.get(f"/api/ai/books/{v3.id}/recap/volumes", headers=h).json()
    assert g["volumes"][0]["status"] == "ready"
    # Second POST: served, no call.
    assert client.post(f"/api/ai/books/{v3.id}/recap/volumes/{v1.id}", json={}, headers=h).status_code == 200
    assert len(fake.calls) == 1

    # DELETE ("Ask again"), then it is missing.
    r = client.delete(f"/api/ai/books/{v3.id}/recap/volumes/{v1.id}", headers=h)
    assert r.json() == {"deleted": True}
    assert client.delete(f"/api/ai/books/{v3.id}/recap/volumes/{v1.id}", headers=h).json() == {"deleted": False}
    g = client.get(f"/api/ai/books/{v3.id}/recap/volumes", headers=h).json()
    assert g["volumes"][0]["status"] == "missing"


def test_recap_volume_202_when_undigested_then_force_partial(client, db, make_book, tmp_path):
    user = _make_user(db, "volpart", "member")
    v1, v2, v3 = _volume_series(db, make_book, tmp_path, user, "Spit")
    _digest_all(db, v2, 2)
    db.commit()
    digest_job.reset()   # the setup's _read queued finished-book jobs
    fake = FakeProvider(responses=[VOLUME_TEXT])
    ai.set_provider_override(fake)
    h = _headers(user)

    r = client.post(f"/api/ai/books/{v3.id}/recap/volumes/{v2.id}", json={}, headers=h)
    assert r.status_code == 202, r.text
    out = r.json()
    assert out["needs_digest"] is True
    assert out["coverage"]["digested"] == 2 and out["coverage"]["needed"] == 4
    assert out["estimate"]["scope"] == "book" and out["estimate"]["chapters"] == 2
    assert fake.calls == [] and digest_job.active_for(user.id, v2.id) is None

    r = client.post(f"/api/ai/books/{v3.id}/recap/volumes/{v2.id}", json={"force": True}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "partial" and r.json()["digested"] == 2 and r.json()["missing_chapters"] == 2
    assert len(fake.calls) == 1

    # Not an included earlier volume: 404 (vol 3 itself, and an unread vol).
    assert client.post(f"/api/ai/books/{v3.id}/recap/volumes/{v3.id}", json={}, headers=h).status_code == 404
    assert client.post(f"/api/ai/books/{v2.id}/recap/volumes/{v2.id}", json={}, headers=h).status_code == 404


def test_recap_volumes_permissions_and_keyless(client, db, admin_user, make_book, tmp_path):
    user = _make_user(db, "volperm", "member")
    keyless = _make_user(db, "volnokey", "member", key=False)
    guest = _make_user(db, "volguest", "guest", key=False)
    v1, v2, v3 = _volume_series(db, make_book, tmp_path, user, "Sand")
    _read(db, keyless, v1)
    hidden = _epub_book(db, make_book, tmp_path, "Sand Hidden")
    lib = Library(name="Private", is_public=False, owner_id=admin_user[0].id)
    lib.books.append(hidden)
    db.add(lib)
    _digest_all(db, v1, 4)
    db.commit()
    ai.set_provider_override(FakeProvider(responses=[]))

    for method, url, kw in [
        ("get", f"/api/ai/books/{v3.id}/recap/volumes", {}),
        ("post", f"/api/ai/books/{v3.id}/recap/volumes/{v1.id}", {"json": {}}),
        ("delete", f"/api/ai/books/{v3.id}/recap/volumes/{v1.id}", {}),
    ]:
        assert getattr(client, method)(url, headers=_headers(guest), **kw).status_code == 403
    assert client.get(f"/api/ai/books/{hidden.id}/recap/volumes", headers=_headers(user)).status_code == 404

    # No key: listed read-only, writing is a 409.
    r = client.get(f"/api/ai/books/{v3.id}/recap/volumes", headers=_headers(keyless))
    assert r.status_code == 200 and r.json()["can_write"] is False
    assert [v["book_id"] for v in r.json()["volumes"]] == [v1.id]
    assert client.post(f"/api/ai/books/{v3.id}/recap/volumes/{v1.id}", json={},
                       headers=_headers(keyless)).status_code == 409

    ai_settings.set_feature_enabled(db, "recap", False)
    db.commit()
    assert client.get(f"/api/ai/books/{v3.id}/recap/volumes", headers=_headers(user)).status_code == 404
    ai_settings.set_feature_enabled(db, "recap", True)
    ai_settings.set_feature_enabled(db, "reading_digest", False)
    db.commit()
    assert client.get(f"/api/ai/books/{v3.id}/recap/volumes", headers=_headers(user)).status_code == 404


def test_companion_series_recaps_flag(client, db, make_book, tmp_path):
    user = _make_user(db, "volflag", "member")
    v1, v2, v3 = _volume_series(db, make_book, tmp_path, user, "Dune")
    db.commit()
    h = _headers(user)
    c = client.get(f"/api/ai/books/{v3.id}/companion", headers=h).json()
    assert c["reason"] == "no_position" and c["series_recaps"] is True
    assert client.get(f"/api/ai/books/{v1.id}/companion", headers=h).json()["series_recaps"] is False
    _position(db, user, v3, _mid(v3, db, 1))
    db.commit()
    c = client.get(f"/api/ai/books/{v3.id}/companion", headers=h).json()
    assert c["available"] is True and c["series_recaps"] is True
    ai_settings.set_feature_enabled(db, "recap", False)
    db.commit()
    assert client.get(f"/api/ai/books/{v3.id}/companion", headers=h).json()["series_recaps"] is False


def test_finished_book_hook_recap_shows_as_ready_volume(client, db, make_book, tmp_path):
    user = _make_user(db, "volhook", "member")
    v1 = _epub_book(db, make_book, tmp_path, "Marsh One", series="Marsh", index=1)
    v2 = _epub_book(db, make_book, tmp_path, "Marsh Two", series="Marsh", index=2)
    db.commit()
    h = _headers(user)
    assert client.put(f"/api/books/{v1.id}/status", json={"status": "read"}, headers=h).status_code == 200
    fake = FakeProvider(responses=mixed)
    ai.set_provider_override(fake)
    digest_job.run_pending()
    assert [c["feature"] for c in fake.calls] == ["reading_digest", "recap"]

    out = client.get(f"/api/ai/books/{v2.id}/recap/volumes", headers=h).json()
    [vol] = out["volumes"]
    assert vol["book_id"] == v1.id and vol["status"] == "ready" and vol["text"].startswith("Mara walked")
    # POST serves it without another call.
    r = client.post(f"/api/ai/books/{v2.id}/recap/volumes/{v1.id}", json={}, headers=h)
    assert r.status_code == 200 and len(fake.calls) == 2


def test_plugin_recap_volumes_mirror(db, admin_user, make_book, tmp_path, plugin):
    c, key_for = plugin
    user = _make_user(db, "volplug", "member")
    guest = _make_user(db, "volplugg", "guest", key=False)
    v1, v2, v3 = _volume_series(db, make_book, tmp_path, user, "Fjord")
    hidden = _epub_book(db, make_book, tmp_path, "Fjord Hidden")
    lib = Library(name="Private", is_public=False, owner_id=admin_user[0].id)
    lib.books.append(hidden)
    db.add(lib)
    _digest_all(db, v1, 4)
    h, hg = key_for(user), key_for(guest)
    db.commit()
    fake = FakeProvider(responses=[VOLUME_TEXT])
    ai.set_provider_override(fake)
    base = f"/api/tome-sync/ai/books/{v3.id}/recap/volumes"

    r = c.get(base, headers=h)
    assert r.status_code == 200, r.text
    assert [(v["book_id"], v["status"]) for v in r.json()["volumes"]] == [(v1.id, "missing"), (v2.id, "missing")]
    assert c.get(base, headers=hg).status_code == 403
    assert c.get(f"/api/tome-sync/ai/books/{hidden.id}/recap/volumes", headers=h).status_code == 404
    assert c.post(f"{base}/{v2.id}", json={}, headers=h).status_code == 202
    r = c.post(f"{base}/{v1.id}", json={}, headers=h)
    assert r.status_code == 200 and r.json()["status"] == "ready"
    assert c.post(f"{base}/{v1.id}", headers=h).status_code == 200      # no body
    assert len(fake.calls) == 1
    assert c.delete(f"{base}/{v1.id}", headers=h).json() == {"deleted": True}


# ── recap as volumes: "Write all" ────────────────────────────────────────────

def _write_all_series(db, make_book, tmp_path, user, name: str):
    """Vols 1-4 read, reader on vol 5. Vol 1: digested, no row (writes). Vol
    2: digested, fresh row (fresh). Vol 3: half digested, no row (unread).
    Vol 4: digested, stale row written from 2 summaries (writes)."""
    vols = [_epub_book(db, make_book, tmp_path, f"{name} {i}", series=name, index=i) for i in range(1, 6)]
    for v in vols[:4]:
        _read(db, user, v)
    own = lambda v: digest.Scope(book=v).scope_hash
    v1, v2, v3, v4, _ = vols
    _digest_all(db, v1, 4)
    _digest_all(db, v2, 4)
    db.add(AIRecap(book_id=v2.id, chapter_idx=4, scope_hash=own(v2), position_fraction=1.0,
                   text="Vol two recap.", model="m", digested=4, needed=4))
    _digest_all(db, v3, 2)
    _digest_all(db, v4, 4)
    db.add(AIRecap(book_id=v4.id, chapter_idx=4, scope_hash=own(v4), position_fraction=1.0,
                   text="Vol four stale.", model="m", digested=2, needed=4))
    db.commit()
    digest_job.reset()   # the setup's _read queued finished-book jobs
    return vols


@pytest.fixture()
def reconcile_calls(monkeypatch):
    """Stub the merging step: records each call, merges nothing."""
    from backend.services.ai import reconcile
    calls: list[dict] = []

    def fake_run(db, user, book, *, job_id=None, **kw):
        calls.append({"book_id": book.id, "job_id": job_id})
        return reconcile.ReconcileResult(candidates=2, merged=1)

    monkeypatch.setattr(reconcile, "run", fake_run)
    return calls


def write_all_answers(feature, user_content, schema):
    if feature == "reading_digest":
        return echo_digest(feature, user_content, schema)
    if feature == "recap":
        return VOLUME_TEXT
    raise AssertionError(feature)


def _recap_calls(fake) -> list[dict]:
    return [c for c in fake.calls if c["system"] == volume_recap.VOLUME_PROMPT]


def test_write_all_job_reads_merges_and_writes(client, db, make_book, tmp_path, reconcile_calls):
    user = _make_user(db, "wall", "member")
    v1, v2, v3, v4, v5 = _write_all_series(db, make_book, tmp_path, user, "Isle")
    fake = FakeProvider(responses=write_all_answers)
    ai.set_provider_override(fake)
    h = _headers(user)

    g = client.get(f"/api/ai/books/{v5.id}/recap/volumes", headers=h).json()
    wa = g["write_all"]
    # v1 (no row), v3 (two chapters to read first), v4 (stale); v2 is fresh.
    assert wa["count"] == 3 and wa["est_cost_usd"] > 0
    assert wa["reading"]["chapters"] == 2 and wa["reading"]["volumes"] == 1
    assert wa["reading"]["est_cost_usd"] > 0 and wa["reading"]["est_seconds"] > 0
    assert wa["job"] is None and wa["last"] is None and wa["remaining_budget_usd"] is None
    assert fake.calls == []

    r = client.post(f"/api/ai/books/{v5.id}/recap/volumes", json={}, headers=h)
    assert r.status_code == 202, r.text
    job = r.json()["job"]
    assert set(r.json()) == {"job"} and job["scope"] == "series" and job["upto_chapter"] == 0
    assert [f["name"] for f in job["followups"]] == ["reconcile", "volume_recaps"]
    assert fake.calls == []
    # One job per book at a time.
    r = client.post(f"/api/ai/books/{v5.id}/recap/volumes", json={}, headers=h)
    assert r.status_code == 409 and r.json()["reason"] == "already_running"
    assert client.get(f"/api/ai/books/{v5.id}/recap/volumes", headers=h).json()["write_all"]["job"]["id"] == job["id"]

    digest_job.run_pending()
    snap = client.get(f"/api/ai/digest-jobs/{job['id']}", headers=h).json()
    assert snap["status"] == "done", snap
    assert (snap["step"], snap["step_done"], snap["step_total"]) == ("writing", 3, 3)
    assert (snap["done"], snap["total"]) == (2, 2)
    rec, wr = snap["followups"]
    assert rec["name"] == "reconcile" and rec["status"] == "done" and rec["merged"] == 1
    assert wr["name"] == "volume_recaps" and wr["status"] == "done"
    assert wr["written"] == [v1.id, v3.id, v4.id] and wr["failed"] == []
    assert [(x["book_id"], x["reason"]) for x in wr["skipped"]] == [(v2.id, "fresh")]
    assert reconcile_calls == [{"book_id": v5.id, "job_id": job["id"]}]
    assert len(_recap_calls(fake)) == 3
    # Reading came before merging and writing; nothing of the open book was read.
    first_recap = fake.calls.index(_recap_calls(fake)[0])
    assert all(c["system"] != volume_recap.VOLUME_PROMPT for c in fake.calls[:first_recap])
    assert digest.get_digest(db, v5.id, 0) is None

    vols = client.get(f"/api/ai/books/{v5.id}/recap/volumes", headers=h).json()
    assert [(v["book_id"], v["status"]) for v in vols["volumes"]] == [
        (v1.id, "ready"), (v2.id, "ready"), (v3.id, "ready"), (v4.id, "ready")]
    wa = vols["write_all"]
    assert wa["job"] is None and wa["last"]["id"] == job["id"] and wa["last"]["status"] == "done"
    assert (wa["count"], wa["est_cost_usd"], wa["reading"]["chapters"]) == (0, 0.0, 0)
    db.expire_all()
    assert db.query(AIRecap).filter_by(book_id=v4.id).one().digested == 4

    # Nothing to read or write: straight to the steps, no recap call.
    r = client.post(f"/api/ai/books/{v5.id}/recap/volumes", json={}, headers=h)
    assert r.status_code == 202
    digest_job.run_pending()
    snap = client.get(f"/api/ai/digest-jobs/{r.json()['job']['id']}", headers=h).json()
    assert snap["status"] == "done" and snap["total"] == 0
    assert snap["followups"][1]["written"] == [] and snap["step_total"] == 0
    assert len(_recap_calls(fake)) == 3


def test_write_all_stops_on_budget_keeping_earlier_writes(client, db, make_book, tmp_path, monkeypatch,
                                                          reconcile_calls):
    from backend.services.ai.provider import AIBudgetExceeded
    user = _make_user(db, "wallbud", "member")
    v1, v2, v3, v4, v5 = _write_all_series(db, make_book, tmp_path, user, "Holm")
    fake = FakeProvider(responses=write_all_answers)
    ai.set_provider_override(fake)
    real = ai.run_feature
    recaps = {"n": 0}

    def limited(db_, user_, feature, *a, **kw):
        if feature == "recap":
            recaps["n"] += 1
            if recaps["n"] > 1:
                raise AIBudgetExceeded("Monthly AI budget reached (1.00 of 1.00 USD).", spent=1.0, limit=1.0)
        return real(db_, user_, feature, *a, **kw)

    monkeypatch.setattr(ai, "run_feature", limited)
    h = _headers(user)
    r = client.post(f"/api/ai/books/{v5.id}/recap/volumes", json={}, headers=h)
    assert r.status_code == 202, r.text
    digest_job.run_pending()
    snap = client.get(f"/api/ai/digest-jobs/{r.json()['job']['id']}", headers=h).json()
    assert snap["status"] == "stopped" and snap["stop_reason"].startswith("Monthly AI budget reached")
    wr = snap["followups"][1]
    assert wr["status"] == "failed" and wr["written"] == [v1.id]
    assert (snap["step"], snap["step_done"], snap["step_total"]) == ("writing", 1, 3)
    db.expire_all()
    assert db.query(AIRecap).filter_by(book_id=v1.id).count() == 1
    assert db.query(AIRecap).filter_by(book_id=v3.id).count() == 0
    assert db.query(AIRecap).filter_by(book_id=v4.id).one().text == "Vol four stale."
    vols = client.get(f"/api/ai/books/{v5.id}/recap/volumes", headers=h).json()
    assert vols["write_all"]["count"] == 2 and vols["write_all"]["last"]["status"] == "stopped"
    # The job is over: another one may start.
    assert client.post(f"/api/ai/books/{v5.id}/recap/volumes", json={}, headers=h).status_code == 202


def test_write_all_409s_guest_and_keyless(client, db, make_book, tmp_path):
    user = _make_user(db, "wallrun", "member")
    keyless = _make_user(db, "wallnokey", "member", key=False)
    guest = _make_user(db, "wallguest", "guest", key=False)
    v1, v2, v3 = _volume_series(db, make_book, tmp_path, user, "Skerry")
    _read(db, keyless, v1)
    _digest_all(db, v1, 4)
    db.commit()
    digest_job.reset()
    fake = FakeProvider(responses=[])
    ai.set_provider_override(fake)
    url = f"/api/ai/books/{v3.id}/recap/volumes"

    # Any active job for the book blocks it (and "Merge duplicates").
    digest_job.enqueue(user_id=user.id, book_id=v3.id, upto=0)
    r = client.post(url, json={}, headers=_headers(user))
    assert r.status_code == 409 and r.json()["reason"] == "already_running"
    assert "already running" in r.json()["detail"]
    r = client.post(f"/api/ai/books/{v3.id}/reconcile", headers=_headers(user))
    assert r.status_code == 409 and r.json()["reason"] == "already_running"
    digest_job.reset()
    assert client.post(url, json={}, headers=_headers(guest)).status_code == 403
    r = client.post(url, json={}, headers=_headers(keyless))
    assert r.status_code == 409 and r.json().get("reason") != "already_running"

    # Over a monthly limit: reading (vol 2) + recaps exceed what is left.
    user.ai_monthly_limit_usd = 0.0001
    db.commit()
    wa = client.get(url, headers=_headers(user)).json()["write_all"]
    assert wa["remaining_budget_usd"] is not None and wa["remaining_budget_usd"] < 0.001
    r = client.post(url, json={}, headers=_headers(user))
    assert r.status_code == 409 and r.json()["reason"] == "budget"
    assert digest_job.active_for(user.id, v3.id) is None
    assert fake.calls == []


def test_plugin_write_all_and_reconcile_mirror(db, make_book, tmp_path, plugin, reconcile_calls):
    c, key_for = plugin
    user = _make_user(db, "wallplug", "member")
    guest = _make_user(db, "wallplugg", "guest", key=False)
    v1, v2, v3 = _volume_series(db, make_book, tmp_path, user, "Sound")
    _digest_all(db, v1, 4)
    h, hg = key_for(user), key_for(guest)
    db.commit()
    digest_job.reset()
    fake = FakeProvider(responses=write_all_answers)
    ai.set_provider_override(fake)
    base = f"/api/tome-sync/ai/books/{v3.id}/recap/volumes"

    wa = c.get(base, headers=h).json()["write_all"]
    assert wa["count"] == 2 and wa["reading"]["chapters"] == 4
    assert c.post(base, headers=hg).status_code == 403
    r = c.post(base, headers=h)
    assert r.status_code == 202, r.text
    job_id = r.json()["job"]["id"]
    assert c.post(base, headers=h).status_code == 409
    digest_job.run_pending()
    snap = c.get(f"/api/tome-sync/ai/jobs/{job_id}", headers=h).json()
    assert snap["status"] == "done" and snap["step"] == "writing"
    assert snap["followups"][1]["written"] == [v1.id, v2.id]
    assert c.get(base, headers=h).json()["write_all"]["last"]["id"] == job_id

    r = c.post(f"/api/tome-sync/ai/books/{v3.id}/reconcile", headers=h)
    assert r.status_code == 202, r.text
    assert [f["name"] for f in r.json()["job"]["followups"]] == ["reconcile"]
    assert c.post(f"/api/tome-sync/ai/books/{v3.id}/reconcile", headers=hg).status_code == 403
    digest_job.run_pending()
    snap = c.get(f"/api/tome-sync/ai/jobs/{r.json()['job']['id']}", headers=h).json()
    assert snap["status"] == "done" and snap["step"] == "merging" and snap["total"] == 0
    assert len(reconcile_calls) == 2


def test_a_finished_book_is_digested_to_its_last_chapter(client, db, make_book, tmp_path):
    """A device can leave a finished book's position short of the end (97%),
    and the chapter the position is in is never digested. Marked read, an
    explicit start and its estimate cover every chapter."""
    user = _make_user(db, "finisher", "member")
    book = _epub_book(db, make_book, tmp_path, "Finished Short")
    chapters = book_text.book_chapters(book, db)
    _position(db, user, book, (chapters[-1].start_fraction + 1.0) / 2)   # inside the last chapter
    db.commit()
    h = _headers(user)
    est = client.get(f"/api/ai/books/{book.id}/digest/estimate", headers=h).json()
    assert est["book"]["chapters"] == len(chapters) - 1                 # not read: the last one waits
    _read(db, user, book)
    db.commit()
    est = client.get(f"/api/ai/books/{book.id}/digest/estimate", headers=h).json()
    assert est["book"]["chapters"] == len(chapters)
    r = client.post(f"/api/ai/books/{book.id}/digest", json={"scope": "book"}, headers=h)
    assert r.status_code == 202, r.text
    assert r.json()["job"]["upto_chapter"] == len(chapters)
    assert r.json()["coverage"]["needed"] == len(chapters)


# ── glossary v2: entity profiles ─────────────────────────────────────────────

def _entity_book(db, make_book, tmp_path, user, title="Profiles"):
    """An eight-chapter standalone with the entity Mara Vell (notes in
    chapters 0, 1, 2 and 5) and an old-style row for Tobin Ash in chapter 1
    (no notes). The reader is in chapter 4: chapters 0..3 are the window."""
    import json as _json

    from backend.models.ai_entity import AIEntity, AIEntityNote
    from tests.test_ai_digest import EIGHT

    book = _epub_book(db, make_book, tmp_path, title, chapters=EIGHT)
    mara = AIEntity(series_key=digest.series_key(book), kind="person", name="Mara Vell",
                    aliases_json=_json.dumps([{"name": "The Pilot", "book_id": book.id, "chapter_idx": 1},
                                              {"name": "Vell the Dead", "book_id": book.id, "chapter_idx": 5}]),
                    identity="A ferry pilot from the harbour.", first_book_id=book.id, first_chapter_idx=0)
    db.add(mara)
    db.flush()
    ref = {"entity_id": mara.id}
    _store_digest(db, book, 0, [{**_entry("Mara Vell", "Walks to the harbour."), **ref}])
    _store_digest(db, book, 1, [{**_entry("The Pilot", "Hears the bell."), **ref},
                                _entry("Tobin Ash", "Rings the bell.")])
    _store_digest(db, book, 2, [{**_entry("Mara Vell", "Takes the ferry out."), **ref}])
    _store_digest(db, book, 3, [])
    _store_digest(db, book, 5, [{**_entry("Mara Vell", "Drowns."), **ref}])
    for idx, line, facts in ((0, "Walks to the harbour.", {"status": "alive", "location": "the harbour"}),
                             (1, "Hears the bell.", {"location": "the bell tower"}),
                             (2, "Takes the ferry out.", {"location": "the ferry", "appearance": "grey coat"}),
                             (5, "Drowns.", {"status": "dead", "location": "the sea"})):
        db.add(AIEntityNote(entity_id=mara.id, book_id=book.id, chapter_idx=idx, line=line,
                            facts_json=_json.dumps(facts), evidence="Mara Vell walked"))
    _position(db, user, book, _mid(book, db, 4))
    db.flush()
    return book, mara


def test_glossary_v2_fields_on_web_and_plugin(client, db, make_book, tmp_path, plugin):
    user = _make_user(db, "profweb", "member")
    book, mara = _entity_book(db, make_book, tmp_path, user)
    c, key_for = plugin
    hp = key_for(user)
    db.commit()
    web = client.get(f"/api/ai/books/{book.id}/glossary", headers=_headers(user)).json()
    plug = c.get(f"/api/tome-sync/ai/books/{book.id}/glossary", headers=hp).json()
    assert web["entries"] == plug["entries"] and web["rev"] == plug["rev"]
    assert web["chapter_idx"] == 4
    assert [e["name"] for e in web["entries"]] == ["Mara Vell", "Tobin Ash"]
    assert web["count"] == 2
    m = web["entries"][0]
    v1_keys = {"name", "kind", "line", "more", "evidence", "first_seen", "last_seen", "chapters"}
    v2_keys = {"entity_id", "aliases", "identity", "status", "facts", "timeline", "timeline_truncated"}
    assert v1_keys | v2_keys <= set(m)
    assert m["entity_id"] == mara.id and m["kind"] == "person"
    assert m["identity"] == m["more"] == "A ferry pilot from the harbour."
    assert m["line"] == "Takes the ferry out."              # the latest line inside the window
    assert m["aliases"] == ["The Pilot"]                    # the chapter-5 alias is past the reader
    assert m["first_seen"]["chapter_idx"] == 0 and m["first_seen"]["book_title"] == "Profiles"
    assert m["last_seen"]["chapter_idx"] == 2 and m["chapters"] == 3
    assert m["evidence"] == "x"
    # Facts: latest value inside the window wins; chapter 5 never leaks.
    assert m["status"] == "alive"
    # The list carries no title strings in facts and timeline (payload size).
    assert _nohist(m["facts"]["location"]) == {"value": "the ferry", "book_id": book.id,
                                               "series_index": None, "chapter_idx": 2}
    # The list carries no fact history; the opened entry does (newest first,
    # never past the window).
    assert "history" not in m["facts"]["location"]
    opened = client.get(f"/api/ai/books/{book.id}/glossary/entry?entity_id={mara.id}",
                        headers=_headers(user)).json()["entry"]
    assert [h["value"] for h in opened["facts"]["location"]["history"]][:1] == ["the bell tower"]
    assert m["facts"]["status"]["chapter_idx"] == 0
    assert m["facts"]["appearance"]["value"] == "grey coat"
    assert "dead" not in str(web) and "the sea" not in str(web) and "Drowns" not in str(web)
    # Timeline: oldest first, resolved places.
    assert [t["line"] for t in m["timeline"]] == [
        "Walks to the harbour.", "Hears the bell.", "Takes the ferry out."]
    assert m["timeline"][1] == {"book_id": book.id, "series_index": None, "chapter_idx": 1,
                                "line": "Hears the bell."}
    assert m["timeline_truncated"] is False

    # The opened entry: titled places, same window, web and plugin alike.
    for e in (client.get(f"/api/ai/books/{book.id}/glossary/entry?entity_id={mara.id}",
                         headers=_headers(user)).json()["entry"],
              c.get(f"/api/tome-sync/ai/books/{book.id}/glossary/entry?entity_id={mara.id}",
                    headers=hp).json()["entry"]):
        assert _nohist(e["facts"]["location"]) == {"value": "the ferry", "book_id": book.id,
                                                   "series_index": None, "chapter_idx": 2,
                                                   "chapter_title": "Part 3"}
        assert e["timeline"][1] == {"book_id": book.id, "series_index": None, "chapter_idx": 1,
                                    "chapter_title": "Part 2", "line": "Hears the bell."}
        assert "Drowns" not in str(e) and "the sea" not in str(e)
    # The old-style row (no notes): identity is its line, no facts.
    t = web["entries"][1]
    assert t["entity_id"] is None and t["identity"] == t["line"] == "Rings the bell."
    assert t["facts"] == {} and t["status"] is None and t["aliases"] == []
    assert [x["line"] for x in t["timeline"]] == ["Rings the bell."]

    # An earlier explicit chapter (web): the window shrinks, facts follow it.
    g1 = client.get(f"/api/ai/books/{book.id}/glossary?chapter=1", headers=_headers(user)).json()
    [m1] = g1["entries"]
    assert {k: _nohist(f) for k, f in m1["facts"].items()} == {
        "status": {"value": "alive", "book_id": book.id, "series_index": None, "chapter_idx": 0},
        "location": {"value": "the harbour", "book_id": book.id, "series_index": None, "chapter_idx": 0}}
    assert m1["aliases"] == [] and m1["line"] == "Walks to the harbour."


def test_glossary_v2_timeline_cap(db, make_book, tmp_path, monkeypatch):
    user = _make_user(db, "profcap", "member")
    book, _mara = _entity_book(db, make_book, tmp_path, user)
    monkeypatch.setattr(companion, "TIMELINE_CAP", 2)
    window = companion.web_window(db, user, book, None)
    m = companion.glossary_payload(db, user, book, window)["entries"][0]
    assert [t["line"] for t in m["timeline"]] == ["Hears the bell.", "Takes the ferry out."]
    assert m["timeline_truncated"] is True
    assert m["chapters"] == 3 and m["first_seen"]["chapter_idx"] == 0


def test_glossary_v2_without_profiles_keeps_the_shape(db, make_book, tmp_path, monkeypatch):
    user = _make_user(db, "profshim", "member")
    book, _mara = _entity_book(db, make_book, tmp_path, user)
    monkeypatch.delattr(digest, "profiles", raising=False)
    window = companion.web_window(db, user, book, None)
    entries = companion.glossary_payload(db, user, book, window)["entries"]
    assert [e["name"] for e in entries] == ["Mara Vell", "The Pilot", "Tobin Ash"]   # the plain fold
    for e in entries:
        assert e["entity_id"] is None and e["facts"] == {} and e["identity"] == e["line"]
        assert e["timeline_truncated"] is False


def test_glossary_rev_changes_when_a_note_is_added(db, make_book, tmp_path, plugin):
    from backend.models.ai_entity import AIEntity, AIEntityNote

    user = _make_user(db, "profrev", "member")
    book, _mara = _entity_book(db, make_book, tmp_path, user)
    c, key_for = plugin
    h = key_for(user)
    db.commit()
    pct = _mid(book, db, 4)
    g1 = c.get(f"/api/tome-sync/ai/books/{book.id}/glossary?pct={pct}", headers=h).json()
    assert c.put(f"/api/tome-sync/position/{book.id}", json={"percentage": pct},
                 headers=h).json()["glossary_rev"] == g1["rev"]

    tobin = AIEntity(series_key=digest.series_key(book), kind="person", name="Tobin Ash",
                     identity="The bell ringer.", first_book_id=book.id, first_chapter_idx=3)
    db.add(tobin)
    db.flush()
    db.add(AIEntityNote(entity_id=tobin.id, book_id=book.id, chapter_idx=3, line="Rings again.",
                        facts_json="{}", evidence="Tobin Ash rang"))
    db.commit()
    g2 = c.get(f"/api/tome-sync/ai/books/{book.id}/glossary?pct={pct}", headers=h).json()
    assert g2["rev"] != g1["rev"]
    assert c.put(f"/api/tome-sync/position/{book.id}", json={"percentage": pct},
                 headers=h).json()["glossary_rev"] == g2["rev"]
    [t] = [e for e in g2["entries"] if e["name"] == "Tobin Ash"]
    assert t["entity_id"] == tobin.id and t["line"] == "Rings again."
    assert t["timeline"][-1]["chapter_idx"] == 3
    assert t["first_seen"]["chapter_idx"] == 1       # the old row still counts for first seen

    # A note past the window does not change it.
    db.add(AIEntityNote(entity_id=tobin.id, book_id=book.id, chapter_idx=5, line="Leaves.",
                        facts_json="{}", evidence="Tobin Ash left"))
    db.commit()
    assert c.get(f"/api/tome-sync/ai/books/{book.id}/glossary?pct={pct}",
                 headers=h).json()["rev"] == g2["rev"]


# ── run 2d review: a finished book is the whole book ────────────────────────

def _multi_digests(db, book, n: int) -> None:
    """Every chapter digested by one multi-chapter call that read 0..n-1
    (``context_end_idx`` = the last chapter): unusable for a reader inside
    that range, usable for one past it."""
    for i in range(n):
        row = _store_digest(db, book, i, [_entry(f"Name{i} Person", f"Line {i}.")])
        row.context_end_idx = n - 1
    db.flush()


def test_finished_book_with_a_position_in_the_last_chapter_is_the_whole_book(
        client, db, make_book, tmp_path, plugin):
    """The smoke defect: marked read, device position at 100% inside the last
    chapter. The page shows the whole book: full coverage, every glossary
    entry, nothing left to digest, the whole-book recap (the volume list's
    row), and Ask reads every chapter."""
    user = _make_user(db, "finished", "member")
    book = _epub_book(db, make_book, tmp_path, "Finished Whole")
    n = len(book_text.book_chapters(book, db))
    _multi_digests(db, book, n)
    _read(db, user, book)
    _position(db, user, book, 1.0)
    c, key_for = plugin
    hp = key_for(user)
    db.commit()
    digest_job.reset()
    h = _headers(user)
    # The rows of one multi-chapter call are usable at the whole book.
    assert all(digest.row_usable(d, n) for d in db.query(digest.AIChapterDigest).filter_by(book_id=book.id))
    assert not digest.row_usable(db.query(digest.AIChapterDigest).filter_by(book_id=book.id,
                                                                           chapter_idx=0).one(), n - 1)

    window = companion.web_window(db, user, book, None)
    assert window.finished and window.chapter_idx == n and window.chapter is None
    assert companion.window_text(book, window, db) == ""
    assert companion.digest_upto(db, user, book, window) == n
    # The plugin's fraction for a read book: the whole book too.
    pw = companion.pct_window(db, book, 1.0, user=user)
    assert pw.finished and pw.chapter_idx == n
    assert companion.pct_window(db, book, 1.0).chapter_idx == n - 1     # without the user: the chapter

    comp = client.get(f"/api/ai/books/{book.id}/companion", headers=h).json()
    assert comp["available"] is True
    assert comp["position"]["finished"] is True and comp["position"]["chapter_idx"] == n
    assert comp["position"]["chapter_title"] is None
    assert comp["coverage"] == {"digested": n, "needed": n, "failed": 0}
    assert comp["glossary_count"] == n

    g = client.get(f"/api/ai/books/{book.id}/glossary", headers=h).json()
    assert [e["name"] for e in g["entries"]] == [f"Name{i} Person" for i in range(n)]
    gp = c.get(f"/api/tome-sync/ai/books/{book.id}/glossary?pct=1.0", headers=hp).json()
    assert gp["entries"] == g["entries"] and gp["rev"] == g["rev"]
    assert c.put(f"/api/tome-sync/position/{book.id}", json={"percentage": 1.0},
                 headers=hp).json()["glossary_rev"] == g["rev"]

    est = client.get(f"/api/ai/books/{book.id}/digest/estimate", headers=h).json()
    assert est["book"]["chapters"] == 0 and est["chapter_idx"] == n
    r = client.post(f"/api/ai/books/{book.id}/digest", json={"scope": "book"}, headers=h)
    assert r.status_code == 200 and r.json()["coverage"] == {"digested": n, "needed": n, "failed": 0}

    fake = FakeProvider(responses=[VOLUME_TEXT])
    ai.set_provider_override(fake)
    r = client.post(f"/api/ai/books/{book.id}/recap", json={}, headers=h)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["whole_book"] is True and out["chapter_idx"] == n and out["partial"] is False
    [call] = fake.calls
    assert call["system"] == volume_recap.VOLUME_PROMPT
    sent = _all_text(call)
    assert all(f"Summary {book.id}:{i}" in sent for i in range(n)) and "Mark0end" not in sent
    [row] = db.query(AIRecap).filter_by(book_id=book.id).all()
    assert row.chapter_idx == n and row.scope_hash == digest.Scope(book=book).scope_hash
    # Served again without a call; the companion says cached.
    again = client.post(f"/api/ai/books/{book.id}/recap", json={}, headers=h).json()
    assert again["cached"] is True and again["text"] == out["text"] and len(fake.calls) == 1
    assert client.get(f"/api/ai/books/{book.id}/companion", headers=h).json()["recap_cached"] is True
    # Plugin recap at 100% for the read book: the same row.
    rp = c.post(f"/api/tome-sync/ai/books/{book.id}/recap", json={"pct": 1.0}, headers=hp)
    assert rp.status_code == 200 and rp.json()["cached"] is True and len(fake.calls) == 1

    # Recall: no current-chapter text, a hit is served from the fold.
    rc = client.post(f"/api/ai/books/{book.id}/recall", json={"term": f"Name{n - 1} Person"}, headers=h)
    assert rc.status_code == 200 and rc.json()["found"] is True and rc.json()["live"] is False
    miss = client.post(f"/api/ai/books/{book.id}/recall", json={"term": "lighthouse"}, headers=h).json()
    assert miss["found"] is False and len(fake.calls) == 1


def test_finished_book_whole_recap_is_the_volume_row(client, db, make_book, tmp_path):
    """The finished book's own page and the next volume's list share one row:
    written on either side, served on the other without a call."""
    user = _make_user(db, "shared", "member")
    v1, v2, v3 = _volume_series(db, make_book, tmp_path, user, "Shared")
    _digest_all(db, v1, 4)
    _position(db, user, v1, 1.0)
    db.commit()
    digest_job.reset()
    fake = FakeProvider(responses=[VOLUME_TEXT])
    ai.set_provider_override(fake)
    h = _headers(user)
    r = client.post(f"/api/ai/books/{v3.id}/recap/volumes/{v1.id}", json={}, headers=h)
    assert r.status_code == 200 and len(fake.calls) == 1
    own = client.post(f"/api/ai/books/{v1.id}/recap", json={}, headers=h).json()
    assert own["cached"] is True and own["whole_book"] is True and own["text"] == r.json()["text"]
    assert len(fake.calls) == 1


# ── run 2d review: volume recap writes once ─────────────────────────────────

def test_volume_recap_with_a_failed_placeholder_is_written_once(client, db, make_book, tmp_path):
    from backend.models.ai_chapter_digest import AIChapterDigest

    user = _make_user(db, "volfail", "member")
    v1, v2, v3 = _volume_series(db, make_book, tmp_path, user, "Fail")
    _digest_all(db, v1, 3)
    db.add(AIChapterDigest(book_id=v1.id, chapter_idx=3, model=digest.FAILED_MODEL, summary="",
                           entries_json="[]"))
    db.commit()
    digest_job.reset()
    fake = FakeProvider(responses=[VOLUME_TEXT] * 5)
    ai.set_provider_override(fake)
    h = _headers(user)
    url = f"/api/ai/books/{v3.id}/recap/volumes/{v1.id}"
    for _ in range(3):
        r = client.post(url, json={}, headers=h)
        assert r.status_code == 200 and r.json()["status"] == "partial"
    assert len(fake.calls) == 1
    # force does not rewrite when nothing new was digested either.
    assert client.post(url, json={"force": True}, headers=h).status_code == 200
    assert len(fake.calls) == 1
    row_id = db.query(AIRecap).filter_by(book_id=v1.id).one().id

    # "Ask again": the placeholder goes, the row stays until a new one is written.
    r = client.post(url, json={"again": True}, headers=h)
    assert r.status_code == 202 and r.json()["needs_digest"] is True
    assert r.json()["coverage"] == {"digested": 3, "needed": 4, "failed": 0}
    assert len(fake.calls) == 1
    db.expire_all()
    assert db.query(AIRecap).filter_by(book_id=v1.id).one().id == row_id
    # The chapter is read now: usable grew, so the next POST rewrites in place.
    _store_digest(db, v1, 3, [])
    db.commit()
    r = client.post(url, json={}, headers=h)
    assert r.status_code == 200 and r.json()["status"] == "ready" and len(fake.calls) == 2
    db.expire_all()
    [row] = db.query(AIRecap).filter_by(book_id=v1.id).all()
    assert row.id == row_id and row.digested == 4
    # Again on a ready row with nothing failed: one more call, same row.
    assert client.post(url, json={"again": True}, headers=h).status_code == 200
    assert len(fake.calls) == 3
    db.expire_all()
    assert [r.id for r in db.query(AIRecap).filter_by(book_id=v1.id)] == [row_id]


def test_volume_ask_again_keeps_the_previously_on_row(client, db, make_book, tmp_path):
    user = _make_user(db, "volprev", "member")
    keyless = _make_user(db, "volprevnokey", "member", key=False)
    v1, v2, v3 = _volume_series(db, make_book, tmp_path, user, "Prev")
    _read(db, keyless, v1)
    _read(db, keyless, v2)
    _digest_all(db, v1, 4)
    _digest_all(db, v2, 4)
    user_hash = digest.user_scope(db, user, v2).scope_hash
    assert user_hash != digest.Scope(book=v2).scope_hash
    db.add(AIRecap(book_id=v2.id, chapter_idx=4, scope_hash=user_hash, position_fraction=1.0,
                   text="Hook recap.", model="m", digested=8, needed=8))
    db.commit()
    digest_job.reset()
    fake = FakeProvider(responses=[VOLUME_TEXT])
    ai.set_provider_override(fake)
    h = _headers(user)
    url = f"/api/ai/books/{v3.id}/recap/volumes/{v2.id}"
    assert recap.previously_for(db, user, v3)[1].text == "Hook recap."

    # DELETE: only the own-scope row (there is none); a keyless user gets 409.
    assert client.delete(url, headers=h).json() == {"deleted": False}
    assert client.delete(url, headers=_headers(keyless)).status_code == 409
    item = client.post(url, json={}, headers=h).json()
    assert item["text"] == "Hook recap." and fake.calls == []
    # Ask again writes the own-scope row; the hook's row stays for "Previously on".
    r = client.post(url, json={"again": True}, headers=h)
    assert r.status_code == 200 and r.json()["text"].startswith("Mara sailed out.")
    assert len(fake.calls) == 1
    db.expire_all()
    assert db.query(AIRecap).filter_by(book_id=v2.id, scope_hash=user_hash).one().text == "Hook recap."
    assert recap.previously_for(db, user, v3)[1].text == "Hook recap."
    # DELETE now removes the own-scope row only.
    assert client.delete(url, headers=h).json() == {"deleted": True}
    db.expire_all()
    assert db.query(AIRecap).filter_by(book_id=v2.id).count() == 1


def test_volume_recap_in_flight_is_a_409(client, db, make_book, tmp_path, plugin):
    user = _make_user(db, "volbusy", "member")
    v1, v2, v3 = _volume_series(db, make_book, tmp_path, user, "Busy")
    _digest_all(db, v1, 4)
    c, key_for = plugin
    hp = key_for(user)
    db.commit()
    digest_job.reset()
    fake = FakeProvider(responses=[VOLUME_TEXT])
    ai.set_provider_override(fake)
    with companion.in_flight("volume_recap", user.id, v1.id, "busy"):
        r = client.post(f"/api/ai/books/{v3.id}/recap/volumes/{v1.id}", json={}, headers=_headers(user))
        assert r.status_code == 409 and r.json()["reason"] == "already_running"
        r = c.post(f"/api/tome-sync/ai/books/{v3.id}/recap/volumes/{v1.id}", json={}, headers=hp)
        assert r.status_code == 409
    assert fake.calls == []
    assert client.post(f"/api/ai/books/{v3.id}/recap/volumes/{v1.id}", json={},
                       headers=_headers(user)).status_code == 200


# ── run 2d review: spend-limit guesses ──────────────────────────────────────

def test_reading_guesses_price_max_tokens_and_cache_writes(db, make_book, tmp_path, monkeypatch):
    from backend.services.ai import estimate, usage

    opus, sonnet = "claude-opus-5-5", "claude-sonnet-5-5"
    tpw = estimate.TOKENS_PER_WORD
    # A reading feature with max_tokens: output priced at max_tokens.
    assert estimate.call_cost_guess("recap", opus, 1000, max_tokens=16_000) == pytest.approx(
        usage.cost_usd(opus, int(1000 * tpw) + estimate.PROMPT_TOKENS, 16_000))
    # Cached words at the cache-write rate.
    assert estimate.call_cost_guess("recap", opus, 1000, max_tokens=100, cached_words=600) == pytest.approx(
        usage.cost_usd(opus, int(400 * tpw) + estimate.PROMPT_TOKENS, 100,
                       cache_write_tokens=int(600 * tpw)))
    blocks = [{"type": "text", "text": "a " * 500, "cache_control": {"type": "ephemeral"}},
              {"type": "text", "text": "b " * 100}]
    assert estimate.content_cost_guess("ask", sonnet, blocks, 8000) == pytest.approx(
        estimate.call_cost_guess("ask", sonnet, 600, max_tokens=8000, cached_words=500))
    assert volume_recap.MAX_TOKENS == recap.MAX_TOKENS == 16_000

    # The calls pass their own guesses: recap caches its summaries block, the
    # volume recap caches nothing.
    seen: list[dict] = []
    real = ai.run_feature

    def spy(db_, user_, feature, **kw):
        seen.append({"feature": feature, **kw})
        return real(db_, user_, feature, **kw)

    monkeypatch.setattr(ai, "run_feature", spy)
    user = _make_user(db, "guess", "member")
    v1, v2, v3 = _volume_series(db, make_book, tmp_path, user, "Guess")
    _digest_all(db, v1, 4)
    db.commit()
    digest_job.reset()
    ai.set_provider_override(FakeProvider(responses=[VOLUME_TEXT, RECAP_TEXT]))
    volume_recap.generate_volume(db, user, v1)
    _store_digest(db, v3, 0, [])
    recap.generate(db, user, v3, companion.pct_window(db, v3, _mid(v3, db, 1)), partial=True)
    vol_call, recap_call = seen
    assert all("cache_control" not in b for b in vol_call["user_content"])
    model = ai_settings.effective_model(db, "recap")
    assert vol_call["cost_guess"] == pytest.approx(
        estimate.content_cost_guess("recap", model, vol_call["user_content"], 16_000))
    assert recap_call["user_content"][0]["cache_control"] == {"type": "ephemeral"}
    assert recap_call["cost_guess"] == pytest.approx(
        estimate.content_cost_guess("recap", model, recap_call["user_content"], 16_000))
    assert recap_call["cost_guess"] > estimate.call_cost_guess(
        "recap", model, sum(len(b["text"].split()) for b in recap_call["user_content"]),
        max_tokens=16_000, cached_words=0)


def test_companion_ask_cost_hint(client, db, make_book, tmp_path):
    from backend.services.ai import ask, estimate

    user = _make_user(db, "hint", "member")
    book = _epub_book(db, make_book, tmp_path, "Hint")
    _position(db, user, book, _mid(book, db, 2))
    db.commit()
    c = client.get(f"/api/ai/books/{book.id}/companion", headers=_headers(user)).json()
    model = ai_settings.effective_model(db, "ask")
    want = round(estimate.call_cost_guess("ask", model, ask.ASK_MAX_WORDS,
                                          max_tokens=ask.ANSWER_MAX_TOKENS, cached_words=0), 2)
    assert c["ask_cost_hint_usd"] == want > 0
    assert c["position"]["finished"] is False
    ai_settings.set_feature_enabled(db, "ask", False)
    db.commit()
    assert client.get(f"/api/ai/books/{book.id}/companion",
                      headers=_headers(user)).json()["ask_cost_hint_usd"] is None


# ── run 2d review: glossary payload size + entry endpoint ───────────────────

def _big_series_glossary(db, make_book, tmp_path, user, tiers):
    """A standalone with 40 chapters and a cast in ``tiers``: ``[(count,
    notes_each, facts_each, fact_chars)]``. Lines run about 15 words (the
    digest allows 25), identities about 12. The reader finished the book."""
    import json as _json
    import random

    from backend.models.ai_entity import AIEntity, AIEntityNote
    from tests.test_ai_digest import EIGHT

    rng = random.Random(7)
    vocab = [("".join(rng.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(rng.randint(2, 9))))
             for _ in range(4000)]

    def words(k: int) -> str:     # varied prose, so the gzip figure is not flattered
        return " ".join(rng.choice(vocab) for _ in range(k))

    book = _epub_book(db, make_book, tmp_path, "Big Cast", chapters=EIGHT * 5)
    n = len(book_text.book_chapters(book, db))
    for i in range(n):
        _store_digest(db, book, i, [])
    key = digest.series_key(book)
    ents, notes = [], []
    for count, notes_each, facts_each, fact_chars in tiers:
        tier = [AIEntity(series_key=key, kind="person", name=f"Person Number{len(ents) + i}",
                         aliases_json="[]", identity=words(12),
                         first_book_id=book.id, first_chapter_idx=0) for i in range(count)]
        db.add_all(tier)
        db.flush()
        for e in tier:
            for c in range(notes_each):
                facts = {f: words(max(1, fact_chars // 6)) for f in list(digest.FACT_FIELDS)[:facts_each]}
                notes.append(AIEntityNote(entity_id=e.id, book_id=book.id, chapter_idx=c % n,
                                          line=words(15), facts_json=_json.dumps(facts),
                                          evidence="Person Number walked"))
        ents.extend(tier)
    db.add_all(notes)
    _read(db, user, book)
    db.flush()
    return book, ents


# A 1300-entry series: a main cast with long timelines and every fact, a
# secondary cast, and many people seen once or twice.
REALISTIC_CAST = [(50, 40, 6, 40), (250, 5, 3, 25), (1000, 2, 1, 15)]


def test_glossary_list_is_small_and_the_entry_is_full(client, db, make_book, tmp_path, plugin):
    user = _make_user(db, "bigcast", "member")
    book, ents = _big_series_glossary(db, make_book, tmp_path, user, REALISTIC_CAST)
    c, key_for = plugin
    hp = key_for(user)
    db.commit()
    digest_job.reset()
    r = client.get(f"/api/ai/books/{book.id}/glossary", headers=_headers(user))
    assert r.status_code == 200
    g = r.json()
    size = len(r.content)
    assert r.headers["content-encoding"] == "gzip"
    with client.stream("GET", f"/api/ai/books/{book.id}/glossary", headers=_headers(user)) as s:
        wire = len(b"".join(s.iter_raw()))
    plain = client.get(f"/api/ai/books/{book.id}/glossary",
                       headers={**_headers(user), "Accept-Encoding": "identity"})
    assert "content-encoding" not in plain.headers and plain.json() == g
    print(f"glossary list payload: {g['count']} entries, {size} bytes JSON, {wire} bytes gzipped")
    assert g["count"] == 1300 and size < 1_600_000 and wire < 500_000
    e = g["entries"][0]
    assert len(e["timeline"]) == companion.TIMELINE_CAP == 3 and e["timeline_truncated"] is True
    assert all("chapter_title" not in t for t in e["timeline"])
    assert all(set(f) == {"value", "book_id", "series_index", "chapter_idx"}
               for f in e["facts"].values())
    assert e["first_seen"]["chapter_title"] == "Part 1"          # v1 places keep their titles
    rp = c.get(f"/api/tome-sync/ai/books/{book.id}/glossary", headers=hp)
    assert rp.headers["content-encoding"] == "gzip" and rp.json()["entries"] == g["entries"]

    # The opened entry: titled, the long timeline (cap 200), web and plugin alike.
    eid = ents[5].id
    full = client.get(f"/api/ai/books/{book.id}/glossary/entry?entity_id={eid}",
                      headers=_headers(user)).json()["entry"]
    assert full["entity_id"] == eid and len(full["timeline"]) == 40   # one line per chapter
    assert full["timeline_truncated"] is False
    assert full["timeline"][0]["chapter_title"] == "Part 1"
    assert all("chapter_title" in f for f in full["facts"].values())
    assert set(full) >= {"name", "kind", "line", "more", "evidence", "first_seen", "last_seen", "chapters",
                         "entity_id", "aliases", "identity", "status", "facts", "timeline",
                         "timeline_truncated"}
    by_name = client.get(f"/api/ai/books/{book.id}/glossary/entry",
                         params={"name": "person  number5", "kind": "person"},
                         headers=_headers(user)).json()["entry"]
    assert by_name == full
    plug = c.get(f"/api/tome-sync/ai/books/{book.id}/glossary/entry?entity_id={eid}", headers=hp)
    assert plug.status_code == 200 and plug.json()["entry"] == full
    assert client.get(f"/api/ai/books/{book.id}/glossary/entry?entity_id=999999",
                      headers=_headers(user)).status_code == 404
    assert client.get(f"/api/ai/books/{book.id}/glossary/entry?name=Person%20Number5&kind=place",
                      headers=_headers(user)).status_code == 404
    r = client.get(f"/api/ai/books/{book.id}/glossary/entry", headers=_headers(user))
    assert r.status_code == 422 and isinstance(r.json()["detail"], str)


def test_glossary_entry_timeline_cap_and_window(client, db, make_book, tmp_path, monkeypatch):
    user = _make_user(db, "entrycap", "member")
    book, mara = _entity_book(db, make_book, tmp_path, user)
    db.commit()
    monkeypatch.setattr(companion, "ENTRY_TIMELINE_CAP", 2)
    e = client.get(f"/api/ai/books/{book.id}/glossary/entry?entity_id={mara.id}",
                   headers=_headers(user)).json()["entry"]
    assert [t["line"] for t in e["timeline"]] == ["Hears the bell.", "Takes the ferry out."]
    assert e["timeline_truncated"] is True
    assert "Drowns" not in str(e)                               # chapter 5 is past the reader
    # By alias, inside the window only.
    assert client.get(f"/api/ai/books/{book.id}/glossary/entry?name=The%20Pilot",
                      headers=_headers(user)).json()["entry"]["entity_id"] == mara.id
    assert client.get(f"/api/ai/books/{book.id}/glossary/entry?name=Vell%20the%20Dead",
                      headers=_headers(user)).status_code == 404


def test_plain_fold_entry_merges_only_into_a_profile_of_the_same_kind(db, make_book, tmp_path):
    user = _make_user(db, "kinds", "member")
    book, mara = _entity_book(db, make_book, tmp_path, user)
    # An old-style row naming a PLACE "Mara Vell" (a ship, say): its own entry.
    row = db.query(digest.AIChapterDigest).filter_by(book_id=book.id, chapter_idx=3).one()
    import json as _json
    row.entries_json = _json.dumps([{"name": "Mara Vell", "kind": "place", "line": "A ship.",
                                     "more": "", "evidence": "x"}])
    db.flush()
    window = companion.web_window(db, user, book, None)
    entries = companion.glossary_payload(db, user, book, window)["entries"]
    named = [(e["name"], e["kind"], e["entity_id"]) for e in entries if e["name"] == "Mara Vell"]
    assert ("Mara Vell", "person", mara.id) in named
    assert ("Mara Vell", "place", None) in named
    [person] = [e for e in entries if e["kind"] == "person" and e["name"] == "Mara Vell"]
    assert person["line"] == "Takes the ferry out." and "A ship." not in str(person)


def test_window_note_stamp_counts_notes_in_the_window(db, make_book, tmp_path):
    from backend.models.ai_entity import AIEntityNote

    user = _make_user(db, "stamp", "member")
    book, mara = _entity_book(db, make_book, tmp_path, user)
    scope = digest.user_scope(db, user, book)
    # Notes in chapters 0, 1, 2 (inside upto=4); chapter 5 is outside.
    s4 = companion._window_note_stamp(db, scope, 4)
    assert s4.startswith("3:")
    assert companion._window_note_stamp(db, scope, 6).startswith("4:")
    assert companion._window_note_stamp(db, scope, 0) == ""
    db.add(AIEntityNote(entity_id=mara.id, book_id=book.id, chapter_idx=3, line="More.",
                        facts_json="{}", evidence="Mara Vell walked"))
    db.flush()
    assert companion._window_note_stamp(db, scope, 4) != s4


# ── plugin snapshot (run 2b: the offline companion on the device) ────────────

def test_plugin_snapshot_shape_and_spoiler_window(db, make_book, tmp_path, plugin):
    user = _make_user(db, "snap", "member")
    book, mara = _entity_book(db, make_book, tmp_path, user, title="Snapshot")
    # Kell Ward appears only in chapter 6, past the reader (chapter 4).
    _store_digest(db, book, 6, [_entry("Kell Ward", "Arrives late.")])
    scope = digest.user_scope(db, user, book)
    cov = digest.coverage(db, scope, 4)
    db.add(AIRecap(book_id=book.id, chapter_idx=4, scope_hash=scope.scope_hash,
                   position_fraction=book_text.book_chapters(book, db)[4].start_fraction,
                   text="So far: Mara went to sea.", model="m",
                   digested=digest.usable(cov), needed=cov["needed"]))
    c, key_for = plugin
    h = key_for(user)
    db.commit()
    fake = FakeProvider(responses=[])
    ai.set_provider_override(fake)

    pct = _mid(book, db, 4)
    r = c.get(f"/api/tome-sync/ai/books/{book.id}/snapshot?pct={pct}", headers=h)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/json")
    assert "content-encoding" not in r.headers          # no Accept-Encoding: gzip, plain JSON
    s = r.json()
    assert set(s) == {"rev", "as_of", "book", "chapters", "entries", "recaps", "generated_at"}
    assert s["as_of"] == {"chapter_idx": 4, "pct": pytest.approx(pct)}
    assert s["book"] == {"id": book.id, "title": "Snapshot", "series": None, "series_index": None,
                         "series_key": f"book:{book.id}"}
    chapters = book_text.book_chapters(book, db)
    assert [ch["idx"] for ch in s["chapters"]] == [ch.idx for ch in chapters]
    assert set(s["chapters"][0]) == {"idx", "title", "start_fraction", "end_fraction"}
    assert s["chapters"][3]["end_fraction"] == pytest.approx(chapters[3].end_fraction)
    gloss = c.get(f"/api/tome-sync/ai/books/{book.id}/glossary?pct={pct}", headers=h).json()
    assert s["rev"] == gloss["rev"]
    extra = {"alias_places", "identity_at"}
    assert [{k: v for k, v in e.items() if k not in extra} for e in s["entries"]] == gloss["entries"]
    # The position response carries the same rev the snapshot is stamped with.
    pr = c.put(f"/api/tome-sync/position/{book.id}", json={"percentage": pct}, headers=h)
    assert pr.json()["glossary_rev"] == s["rev"]

    names = [e["name"] for e in s["entries"]]
    assert "Mara Vell" in names and "Tobin Ash" in names and "Kell Ward" not in names
    m = next(e for e in s["entries"] if e["name"] == "Mara Vell")
    assert m["aliases"] == ["The Pilot"]                 # "Vell the Dead" is learned in chapter 5
    assert m["status"] == "alive" and m["facts"]["location"]["value"] == "the ferry"
    assert set(m["facts"]["location"]) == {"value", "book_id", "series_index", "chapter_idx"}
    assert [t["line"] for t in m["timeline"]] == ["Walks to the harbour.", "Hears the bell.",
                                                  "Takes the ferry out."]
    assert all(t["chapter_idx"] < 4 for t in m["timeline"])
    assert m["first_seen"]["chapter_idx"] == 0 and m["last_seen"]["chapter_idx"] == 2
    # Where each alias and the identity were learned, cut at the window (the
    # device hides them on a re-read before those places).
    assert m["alias_places"] == [{"name": "The Pilot", "book_id": book.id, "series_index": None,
                                  "chapter_idx": 1}]
    assert m["identity_at"] == {"book_id": book.id, "series_index": None, "chapter_idx": 0}
    t = next(e for e in s["entries"] if e["name"] == "Tobin Ash")
    assert t["alias_places"] == [] and t["identity_at"]["chapter_idx"] == 1
    assert "Vell the Dead" not in r.text
    assert "Drowns" not in r.text and "Arrives late" not in r.text and "the sea" not in r.text

    assert s["recaps"]["volumes"] == []
    cur = s["recaps"]["current"]
    assert cur["text"] == "So far: Mara went to sea." and cur["chapter_idx"] == 4 and cur["created_at"]

    # Earlier in the book (a re-read): the cached recap at chapter 4 is not served, the
    # glossary shrinks.
    early = c.get(f"/api/tome-sync/ai/books/{book.id}/snapshot?pct={_mid(book, db, 1)}",
                  headers=h).json()
    assert early["as_of"]["chapter_idx"] == 1 and early["recaps"]["current"] is None
    assert [e["name"] for e in early["entries"]] == ["Mara Vell"]
    [em] = early["entries"]
    assert em["aliases"] == [] and em["alias_places"] == []        # "The Pilot" is chapter 1
    # Without pct: the stored position (moved back to chapter 2, so it differs
    # from the pct asked above).
    back = _mid(book, db, 2)
    assert c.put(f"/api/tome-sync/position/{book.id}", json={"percentage": back},
                 headers=h).status_code == 200
    stored = c.get(f"/api/tome-sync/ai/books/{book.id}/snapshot", headers=h).json()
    assert stored["as_of"] == {"chapter_idx": 2, "pct": pytest.approx(back)}
    assert stored["rev"] not in (s["rev"], early["rev"])
    sm = next(e for e in stored["entries"] if e["name"] == "Mara Vell")
    assert [t["chapter_idx"] for t in sm["timeline"]] == [0, 1]
    assert stored["recaps"]["current"] is None
    assert fake.calls == [] and digest_job.active_for(user.id, book.id) is None


def test_plugin_snapshot_finished_book_is_the_whole_window(db, make_book, tmp_path, plugin):
    user = _make_user(db, "snapdone", "member")
    book = _epub_book(db, make_book, tmp_path, "Snap Done")
    n = len(book_text.book_chapters(book, db))
    _digest_all(db, book, n, {0: [_entry("Mara Vell", "At the harbour.")],
                              n - 1: [_entry("Kell Ward", "Arrives at the end.")]})
    db.add(AIRecap(book_id=book.id, chapter_idx=n, scope_hash=digest.Scope(book=book).scope_hash,
                   position_fraction=1.0, text="The whole book.", model="m", digested=n, needed=n))
    _read(db, user, book)
    _position(db, user, book, _mid(book, db, 1))     # a device left mid-book
    c, key_for = plugin
    h = key_for(user)
    db.commit()
    digest_job.reset()   # _read queued the finished-book job
    fake = FakeProvider(responses=[])
    ai.set_provider_override(fake)

    for url in (f"/api/tome-sync/ai/books/{book.id}/snapshot?pct={_mid(book, db, 1)}",
                f"/api/tome-sync/ai/books/{book.id}/snapshot"):
        s = c.get(url, headers=h).json()
        assert s["as_of"]["chapter_idx"] == n == len(s["chapters"])
        assert [e["name"] for e in s["entries"]] == ["Mara Vell", "Kell Ward"]
        cur = s["recaps"]["current"]
        assert cur is not None and cur["text"] == "The whole book." and cur["chapter_idx"] == n
    assert fake.calls == []


def test_plugin_snapshot_volumes_and_zero_window(db, make_book, tmp_path, plugin):
    user = _make_user(db, "snapvol", "member")
    v1, v2, v3 = _volume_series(db, make_book, tmp_path, user, "Strand")
    v4 = _epub_book(db, make_book, tmp_path, "Strand 4", series="Strand", index=4)
    own = lambda v: digest.Scope(book=v).scope_hash
    _digest_all(db, v1, 4, {0: [_entry("Mara Vell", "At the harbour.")]})
    db.add(AIRecap(book_id=v1.id, chapter_idx=4, scope_hash=own(v1), position_fraction=1.0,
                   text="Vol one recap.", model="m", digested=4, needed=4))
    _digest_all(db, v2, 2)
    db.add(AIRecap(book_id=v2.id, chapter_idx=4, scope_hash=own(v2), position_fraction=1.0,
                   text="Vol two partial.", model="m", digested=2, needed=4))
    _digest_all(db, v4, 4)      # unread later volume: never in the snapshot
    db.add(AIRecap(book_id=v4.id, chapter_idx=4, scope_hash=own(v4), position_fraction=1.0,
                   text="Vol four recap.", model="m", digested=4, needed=4))
    c, key_for = plugin
    h = key_for(user)
    db.commit()
    digest_job.reset()   # the setup's _read queued finished-book jobs
    fake = FakeProvider(responses=[])
    ai.set_provider_override(fake)

    r = c.get(f"/api/tome-sync/ai/books/{v3.id}/snapshot", headers=h)    # no position on vol 3
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["as_of"] == {"chapter_idx": 0, "pct": None}
    assert s["book"]["series"] == "Strand" and s["book"]["series_index"] == 3
    assert s["book"]["series_key"] == "strand"
    assert [v["book_id"] for v in s["recaps"]["volumes"]] == [v1.id]
    [vol] = s["recaps"]["volumes"]
    assert set(vol) == {"book_id", "title", "series_index", "text", "created_at"}
    assert vol["text"] == "Vol one recap." and vol["series_index"] == 1 and vol["created_at"]
    assert s["recaps"]["current"] is None
    assert [e["name"] for e in s["entries"]] == ["Mara Vell"]
    assert s["entries"][0]["first_seen"]["book_id"] == v1.id
    assert "Vol four" not in r.text and "Vol two partial" not in r.text
    assert fake.calls == []


def test_plugin_snapshot_auth_gzip_and_feature_off(db, admin_user, make_book, tmp_path, plugin,
                                                   monkeypatch):
    c, key_for = plugin
    member = _make_user(db, "snapm", "member")
    guest = _make_user(db, "snapg", "guest", key=False)
    book = _epub_book(db, make_book, tmp_path, "Snap Auth")
    hidden = _epub_book(db, make_book, tmp_path, "Snap Hidden")
    lib = Library(name="Snap Private", is_public=False, owner_id=admin_user[0].id)
    lib.books.append(hidden)
    db.add(lib)
    _store_digest(db, book, 0, [_entry("Mara Vell", "At the harbour.")])
    hm, hg = key_for(member), key_for(guest)
    db.commit()
    url = f"/api/tome-sync/ai/books/{book.id}/snapshot?pct={_mid(book, db, 2)}"

    assert c.get(url, headers=hg).status_code == 403
    assert c.get(url).status_code == 422                   # no key header
    assert c.get(f"/api/tome-sync/ai/books/{hidden.id}/snapshot", headers=hm).status_code == 404
    assert c.get("/api/tome-sync/ai/books/999999/snapshot", headers=hm).status_code == 404

    monkeypatch.setattr(companion, "GZIP_MIN_BYTES", 0)
    r = c.get(url, headers={**hm, "Accept-Encoding": "gzip"})
    assert r.status_code == 200 and r.headers.get("content-encoding") == "gzip"
    assert r.json()["entries"][0]["name"] == "Mara Vell"   # the client decodes it
    r = c.get(url, headers={**hm, "Accept-Encoding": "identity"})
    assert r.status_code == 200 and "content-encoding" not in r.headers

    ai_settings.set_feature_enabled(db, "reading_digest", False)
    db.commit()
    assert c.get(url, headers=hm).status_code == 404
