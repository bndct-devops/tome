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
from backend.services.ai import companion, digest, digest_job, recap, settings as ai_settings
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
    assert c["features"] == {"recap": True, "recall": True, "glossary": True}
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
        "recap": True, "recall": True, "glossary": True}
    assert c.get("/api/tome-sync/ai/status", headers=hg).json() == {
        "recap": False, "recall": False, "glossary": False}
    assert c.get("/api/tome-sync/ai/status", headers=hk).json() == {
        "recap": False, "recall": False, "glossary": True}
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
