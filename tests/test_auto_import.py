"""Unattended Bindery import (backend/services/auto_import.py): the identity
gate on fetch candidates, files held by an open Bindery review, and the AI
identify path with its threshold and fallbacks. Metadata sources are mocked,
the provider is the FakeProvider, and every title is invented."""
import asyncio
import contextlib
import json
import os
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from backend.models.ai_usage import AIUsage
from backend.models.audit_log import AuditLog
from backend.models.book import Book
from backend.models.library import BookType
from backend.services import ai, auto_import, bindery_review, identity_match
from backend.services.ai import settings as ai_settings
from backend.services.ai.provider import AIProviderError
from backend.services.metadata_fetch import FetchResult, MetadataCandidate
from tests.ai_fake import FakeProvider
from tests.test_ai_identify import make_epub

KEY = "sk-ant-test-autoimport-00000000Ab12"


# ── fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _clean_lock():
    bindery_review.reset()
    auto_import.reset()
    yield
    bindery_review.reset()
    auto_import.reset()


@pytest.fixture()
def bindery(tmp_path: Path, monkeypatch) -> Path:
    incoming = tmp_path / "bindery"
    incoming.mkdir()
    (tmp_path / "library").mkdir()
    (tmp_path / "data" / "covers").mkdir(parents=True)
    monkeypatch.setattr("backend.core.config.settings.incoming_dir", incoming)
    monkeypatch.setattr("backend.core.config.settings.library_dir", tmp_path / "library")
    monkeypatch.setattr("backend.core.config.settings.data_dir", tmp_path / "data")
    return incoming


def cand(title: str, author: str | None, **kw) -> MetadataCandidate:
    base = dict(source="open_library", source_id=f"OL-{title[:8]}", title=title, author=author,
                description=f"About {title}.", publisher="Lantern Press", year=2019,
                isbn="9780000000002", language="en", tags=["Mystery"])
    base.update(kw)
    return MetadataCandidate(**base)


@pytest.fixture()
def sources(monkeypatch):
    """Mock metadata sources for auto-import and identify. Set
    ``state["candidates"]`` to what every fetch returns; ``state["calls"]``
    records each fetch's kwargs."""
    state: dict = {"candidates": [], "calls": []}

    async def fake_fetch(**kwargs):
        state["calls"].append(kwargs)
        return FetchResult(candidates=list(state["candidates"]), query_used=kwargs.get("title") or "")

    monkeypatch.setattr("backend.services.auto_import.fetch_candidates", fake_fetch)
    monkeypatch.setattr("backend.services.ai.identify.fetch_candidates", fake_fetch)
    return state


@pytest.fixture()
def novel_type(db: Session) -> BookType:
    bt = BookType(slug="autoimp-novel", label="Novel", icon="BookOpen", color="blue", sort_order=150)
    db.add(bt)
    db.flush()
    return bt


def run(db: Session) -> dict:
    return asyncio.run(auto_import.run_auto_import(lambda: contextlib.nullcontext(db)))


def only_book(db: Session) -> Book:
    books = db.query(Book).all()
    assert len(books) == 1
    return books[0]


def import_audit(db: Session, book_id: int) -> dict:
    row = (db.query(AuditLog)
           .filter(AuditLog.action == "auto_import.imported", AuditLog.resource_id == book_id)
           .one())
    return json.loads(row.details)


# ── agrees ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("identity,candidate,expected", [
    ({"title": "The Quiet Harbour", "author": "M. Vale"},
     {"title": "The Quiet Harbour", "author": "M. Vale"}, True),
    ({"title": "The Quiet Harbour", "author": "M. Vale"},
     {"title": "The Quiet Harbour: A Novel", "author": "Vale, M."}, True),
    ({"title": "Lanternfall, Vol. 3", "author": "Ida Brook"},
     {"title": "Lanternfall Volume 3", "author": "Ida Brook"}, True),
    ({"title": "The Quiet Harbour", "author": None},
     {"title": "The Quiet Harbour", "author": "Someone Else"}, True),
    ({"title": "The Quiet Harbour", "author": "M. Vale"},
     {"title": "The Quiet Harbour", "author": "M. Vale, J. Doe"}, True),
    ({"title": "Lanternfall, Vol. 3", "author": "Ida Brook"},
     {"title": "The Collected Grimwater Box Set", "author": "Ida Brook"}, False),
    ({"title": "The Quiet Harbour", "author": "M. Vale"},
     {"title": "The Quiet Harbour", "author": "Oswin Thale"}, False),
    ({"title": "Harbour", "author": None}, {"title": "Harboured Secrets", "author": None}, False),
    ({"title": "", "author": "M. Vale"}, {"title": "The Quiet Harbour", "author": "M. Vale"}, False),
])
def test_agrees(identity, candidate, expected):
    assert identity_match.agrees(identity, candidate) is expected


def test_agrees_takes_candidate_objects():
    assert identity_match.agrees(auto_import.Identity("The Quiet Harbour", "M. Vale"),
                                 cand("The Quiet Harbour", "M. Vale"))
    # The identity's volume (series_index) meets the candidate's.
    assert not identity_match.agrees(auto_import.Identity("Lanternfall", "Ida Brook", 3.0),
                                     cand("Lanternfall", "Ida Brook", series_index=1.0))


# ── the gate ─────────────────────────────────────────────────────────────────

def test_disagreeing_candidate_is_not_applied(db: Session, bindery: Path, admin_user, sources):
    make_epub(bindery / "Lanternfall 03.epub", title="Lanternfall, Vol. 3", author="Ida Brook")
    sources["candidates"] = [cand("The Collected Grimwater Box Set", "Oswin Thale",
                                  series=None, isbn="9781111111111")]
    summary = run(db)
    assert summary["imported"] == 1 and summary["gated"] == 1 and summary["enriched"] == 0
    book = only_book(db)
    assert book.title == "Lanternfall, Vol. 3"
    assert book.author == "Ida Brook"
    assert book.isbn is None
    assert book.description is None
    assert book.is_reviewed is False
    assert not (bindery / "Lanternfall 03.epub").exists()


def test_agreeing_candidate_is_applied(db: Session, bindery: Path, admin_user, sources):
    make_epub(bindery / "quiet.epub", title="The Quiet Harbour", author="M. Vale")
    sources["candidates"] = [
        cand("The Collected Grimwater Box Set", "Oswin Thale"),
        cand("The Quiet Harbour", "M. Vale", series="Harbour Tales", series_index=2.0),
    ]
    summary = run(db)
    assert summary["enriched"] == 1 and summary["gated"] == 0
    book = only_book(db)
    assert book.title == "The Quiet Harbour"
    assert book.description == "About The Quiet Harbour."
    assert book.isbn == "9780000000002"
    assert book.series == "Harbour Tales"
    assert book.is_reviewed is False
    assert "ai_assisted" not in import_audit(db, book.id)


# ── review lock ──────────────────────────────────────────────────────────────

def test_held_file_is_skipped_until_released(db: Session, bindery: Path, admin_user, sources):
    make_epub(bindery / "Harbour Tales" / "quiet.epub", title="The Quiet Harbour", author="M. Vale")
    rel = "Harbour Tales/quiet.epub"
    bindery_review.touch([rel])

    summary = run(db)
    assert summary["held"] == 1 and summary["imported"] == 0
    assert (bindery / rel).exists()
    assert db.query(Book).count() == 0

    bindery_review.release([rel])
    summary = run(db)
    assert summary["held"] == 0 and summary["imported"] == 1
    assert not (bindery / rel).exists()
    assert only_book(db).title == "The Quiet Harbour"


# ── AI path ──────────────────────────────────────────────────────────────────

def responder(confidence: float, **overrides):
    def respond(feature, user_content, schema):
        text = user_content[0]["text"]
        payload = json.loads(text[text.index("{"):])
        return {"files": [{
            "file_id": f["file_id"],
            "title": "The Quiet Harbour",
            "author": "M. Vale",
            "series": "Harbour Tales",
            "series_index": 2,
            "content_type": "volume",
            "book_type_slug": "autoimp-novel",
            "language": "en",
            "year": 2019,
            "tags": [],
            "confidence": confidence,
            "evidence": "The title page names the book and its author.",
            "candidate_source_id": None,
            **overrides,
        } for f in payload["files"]]}
    return respond


@pytest.fixture()
def admin_with_key(db: Session, admin_user):
    user, _ = admin_user
    ai_settings.set_user_key(db, user, KEY)
    db.flush()
    return user


def test_ai_proposal_at_threshold_is_applied_and_audited(db: Session, bindery: Path, sources,
                                                         admin_with_key, novel_type):
    # The file itself says something misleading; the AI read the pages.
    make_epub(bindery / "scan_0042.epub", title="Untitled Scan", author="Unknown")
    sources["candidates"] = [cand("The Quiet Harbour", "M. Vale")]
    threshold = ai_settings.confidence_threshold(db)
    fake = FakeProvider(responses=responder(threshold))
    ai.set_provider_override(fake)

    summary = run(db)
    assert summary["ai_applied"] == 1 and summary["enriched"] == 1
    assert len(fake.calls) == 1 and fake.calls[0]["feature"] == "bindery_identify"
    book = only_book(db)
    assert (book.title, book.author, book.series, book.series_index) == \
        ("The Quiet Harbour", "M. Vale", "Harbour Tales", 2.0)
    assert book.book_type_id == novel_type.id
    assert book.description == "About The Quiet Harbour."  # gated fetch on the AI identity
    assert book.is_reviewed is False
    assert import_audit(db, book.id)["ai_assisted"] is True
    usage = db.query(AIUsage).filter(AIUsage.feature == "bindery_identify").all()
    assert len(usage) == 1 and usage[0].job_id is None


def test_ai_candidate_pick_is_used_and_keeps_the_ai_identity(db: Session, bindery: Path, sources,
                                                             admin_with_key, novel_type):
    make_epub(bindery / "scan_0043.epub", title="Untitled Scan", author="Unknown")
    sources["candidates"] = [cand("The Quiet Harbour (Harbour Tales, #2)", "M. Vale",
                                  source_id="OL777W", description="Picked by the model.")]
    ai.set_provider_override(FakeProvider(responses=responder(0.99, candidate_source_id="OL777W")))

    summary = run(db)
    assert summary["ai_applied"] == 1 and summary["enriched"] == 1
    book = only_book(db)
    assert book.title == "The Quiet Harbour"  # not overwritten by the candidate title
    assert book.description == "Picked by the model."


def test_ai_proposal_below_threshold_uses_the_fetch_path(db: Session, bindery: Path, sources,
                                                         admin_with_key, novel_type):
    make_epub(bindery / "lantern.epub", title="Lanternfall", author="Ida Brook")
    sources["candidates"] = [cand("Lanternfall", "Ida Brook")]
    ai.set_provider_override(FakeProvider(responses=responder(0.5)))

    summary = run(db)
    assert summary["ai_applied"] == 0 and summary["enriched"] == 1
    book = only_book(db)
    assert book.title == "Lanternfall" and book.author == "Ida Brook"
    assert book.series is None
    assert book.description == "About Lanternfall."
    assert "ai_assisted" not in import_audit(db, book.id)


def test_ai_error_falls_back_to_the_fetch_path(db: Session, bindery: Path, sources,
                                               admin_with_key):
    make_epub(bindery / "lantern.epub", title="Lanternfall", author="Ida Brook")
    sources["candidates"] = [cand("Lanternfall", "Ida Brook")]
    ai.set_provider_override(FakeProvider(error=AIProviderError("upstream overloaded")))

    summary = run(db)
    assert summary["ai_error"] == "AIProviderError"
    assert summary["imported"] == 1 and summary["ai_applied"] == 0 and summary["enriched"] == 1
    book = only_book(db)
    assert book.title == "Lanternfall"
    assert "ai_assisted" not in import_audit(db, book.id)


def test_no_key_never_calls_the_model(db: Session, bindery: Path, sources, admin_user):
    make_epub(bindery / "lantern.epub", title="Lanternfall", author="Ida Brook")
    sources["candidates"] = []
    fake = FakeProvider(responses=responder(0.99))
    ai.set_provider_override(fake)
    summary = run(db)
    assert summary["imported"] == 1
    assert fake.calls == []


def test_already_imported_file_is_not_sent_to_the_model(db: Session, bindery: Path, sources,
                                                        admin_with_key, novel_type):
    path = make_epub(bindery / "lantern.epub", title="Lanternfall", author="Ida Brook")
    copy = bindery / "again" / "lantern.epub"
    copy.parent.mkdir()
    copy.write_bytes(path.read_bytes())
    fake = FakeProvider(responses=responder(0.99))
    ai.set_provider_override(fake)
    summary = run(db)  # one copy imported, the other is the same bytes
    assert summary["imported"] == 1 and summary["duplicates"] == 1
    assert len(fake.calls) == 1
    assert db.query(Book).count() == 1
    summary = run(db)
    assert summary["duplicates"] == 1 and summary["imported"] == 0
    assert len(fake.calls) == 1  # the leftover duplicate costs nothing


def test_ai_year_and_language_are_applied(db: Session, bindery: Path, sources,
                                          admin_with_key, novel_type):
    make_epub(bindery / "scan_0044.epub", title="Untitled Scan", author="Unknown")
    ai.set_provider_override(FakeProvider(responses=responder(0.99, year=2011, language="de")))
    run(db)
    book = only_book(db)
    assert (book.year, book.language) == (2011, "de")


# ── billing guards ───────────────────────────────────────────────────────────

def _real_extract():
    from backend.services import metadata
    return metadata.extract_metadata


def test_failing_ingest_is_not_identified_again(db: Session, bindery: Path, sources,
                                                admin_with_key, novel_type, monkeypatch):
    make_epub(bindery / "scan_0045.epub", title="Untitled Scan", author="Unknown")
    fake = FakeProvider(responses=responder(0.99))
    ai.set_provider_override(fake)
    real = _real_extract()

    def broken(*_a, **_k):
        raise RuntimeError("corrupt archive")
    monkeypatch.setattr("backend.services.metadata.extract_metadata", broken)

    assert run(db)["errors"] == 1
    assert run(db)["errors"] == 1
    assert len(fake.calls) == 1  # the second tick retried the ingest, not the model
    assert (bindery / "scan_0045.epub").exists()

    monkeypatch.setattr("backend.services.metadata.extract_metadata", real)
    summary = run(db)
    assert summary["imported"] == 1 and summary["ai_applied"] == 1
    assert len(fake.calls) == 1
    assert only_book(db).title == "The Quiet Harbour"


def test_failed_mark_and_cache_expire(db: Session, bindery: Path, sources,
                                      admin_with_key, novel_type, monkeypatch):
    make_epub(bindery / "scan_0046.epub", title="Untitled Scan", author="Unknown")
    fake = FakeProvider(responses=responder(0.99))
    ai.set_provider_override(fake)
    clock = [1000.0]
    monkeypatch.setattr(auto_import, "_now", lambda: clock[0])

    def broken(*_a, **_k):
        raise RuntimeError("corrupt archive")
    monkeypatch.setattr("backend.services.metadata.extract_metadata", broken)
    run(db)
    clock[0] += auto_import.PROPOSAL_TTL_SECONDS + 1
    run(db)
    assert len(fake.calls) == 2


def test_hold_taken_during_ingest_leaves_the_file(db: Session, bindery: Path, sources,
                                                  admin_with_key, novel_type, monkeypatch):
    make_epub(bindery / "scan_0047.epub", title="Untitled Scan", author="Unknown")
    fake = FakeProvider(responses=responder(0.99))
    ai.set_provider_override(fake)
    real = _real_extract()

    def extract_then_review(path, covers):
        bindery_review.touch(["scan_0047.epub"])  # a review opens mid-ingest
        return real(path, covers)
    monkeypatch.setattr("backend.services.metadata.extract_metadata", extract_then_review)

    summary = run(db)
    assert summary["held"] == 1 and summary["imported"] == 0
    assert (bindery / "scan_0047.epub").exists()
    assert db.query(Book).count() == 0

    monkeypatch.setattr("backend.services.metadata.extract_metadata", real)
    bindery_review.release(["scan_0047.epub"])
    summary = run(db)
    assert summary["imported"] == 1 and summary["ai_applied"] == 1
    assert len(fake.calls) == 1  # the cached proposal, no second identify


def test_file_changed_since_phase_0_waits(db: Session, bindery: Path, admin_user, sources,
                                          monkeypatch):
    path = make_epub(bindery / "lantern.epub", title="Lanternfall", author="Ida Brook")
    real = _real_extract()

    def extract_while_copying(p, covers):
        st = os.stat(path)
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
        return real(p, covers)
    monkeypatch.setattr("backend.services.metadata.extract_metadata", extract_while_copying)

    summary = run(db)
    assert summary["deferred"] == 1 and summary["imported"] == 0
    assert path.exists() and db.query(Book).count() == 0

    monkeypatch.setattr("backend.services.metadata.extract_metadata", real)
    summary = run(db)
    assert summary["imported"] == 1 and summary["deferred"] == 0


def test_identify_runs_per_batch_and_keeps_earlier_batches(db: Session, bindery: Path, sources,
                                                           admin_with_key, novel_type, monkeypatch):
    from backend.services.ai import identify
    monkeypatch.setattr(identify, "BATCH_SIZE", 1)
    make_epub(bindery / "a_scan.epub", title="Untitled Scan A", author="Unknown")
    make_epub(bindery / "b_scan.epub", title="Untitled Scan B", author="Unknown")
    respond = responder(0.99)

    def on_call(call):
        if len(fake.calls) == 2:
            raise AIProviderError("upstream overloaded")
    fake = FakeProvider(responses=respond, on_call=on_call)
    ai.set_provider_override(fake)

    summary = run(db)
    assert len(fake.calls) == 2
    assert summary["ai_error"] == "AIProviderError"
    assert summary["imported"] == 2 and summary["ai_applied"] == 1
    titles = sorted(b.title for b in db.query(Book).all())
    assert titles == ["The Quiet Harbour", "Untitled Scan B"]


def test_vanished_file_is_not_sent(db: Session, bindery: Path, sources,
                                   admin_with_key, novel_type, monkeypatch):
    from backend.services.ai import identify
    monkeypatch.setattr(identify, "BATCH_SIZE", 1)
    make_epub(bindery / "a_scan.epub", title="Untitled Scan A", author="Unknown")
    gone = make_epub(bindery / "b_scan.epub", title="Untitled Scan B", author="Unknown")

    def on_call(call):
        gone.unlink(missing_ok=True)  # removed while the first batch runs
    fake = FakeProvider(responses=responder(0.99), on_call=on_call)
    ai.set_provider_override(fake)

    summary = run(db)
    assert len(fake.calls) == 1
    assert summary["imported"] == 1
