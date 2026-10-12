"""Run 2e facts: the status vocabulary at fold time (rows stored before it
carry "escaped", "left", "drunk") and the short per-field history the
glossary shows under each fact. FakeProvider only; books and names are made
up."""
from __future__ import annotations

import json
from contextlib import contextmanager

import pytest
from sqlalchemy.orm import Session

from backend.models.ai_entity import AIEntity, AIEntityNote
from backend.models.tome_sync import TomeSyncPosition
from backend.models.user import User
from backend.services import book_text
from backend.services.ai import companion, digest, digest_job
from tests.test_ai_digest import EIGHT, _entry, _epub_book, _headers, _make_user, _store_digest


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


# Per chapter: the facts the note of Kael Doran carries.
NOTES: dict[int, dict[str, str]] = {
    0: {"status": "alive", "powers_rank": "Iron rank", "location": "the mill"},
    1: {"powers_rank": "Bronze rank", "status": "escaped"},           # stored before the vocabulary
    2: {"powers_rank": "Bronze rank", "status": "drunk", "location": "the inn"},
    3: {"powers_rank": "Silver rank; title Wolf Breaker", "status": "captured, in chains",
        "location": "the mill"},
    4: {"powers_rank": "Silver rank; title Wolf Breaker", "status": "left"},
    5: {"powers_rank": "Gold rank", "status": "dead", "location": "the sea"},   # past the reader
}


def _kael_book(db, make_book, tmp_path, title="Kael Facts"):
    book = _epub_book(db, make_book, tmp_path, title, chapters=EIGHT)
    kael = AIEntity(series_key=digest.series_key(book), kind="person", name="Kael Doran",
                    identity="A miller's son.", first_book_id=book.id, first_chapter_idx=0)
    db.add(kael)
    db.flush()
    for idx, facts in NOTES.items():
        _store_digest(db, book, idx, [{**_entry("Kael Doran", f"Did thing {idx}."), "entity_id": kael.id}])
        db.add(AIEntityNote(entity_id=kael.id, book_id=book.id, chapter_idx=idx, line=f"Did thing {idx}.",
                            facts_json=json.dumps(facts), evidence="Kael Doran"))
    db.flush()
    return book, kael


def _profile(db, book, upto: int) -> dict:
    [p] = digest.profiles(db, digest.Scope(book=book), upto)
    return p.to_dict()


def _hist(fact: dict) -> list[tuple[str, int]]:
    return [(h["value"], h["chapter_idx"]) for h in fact["history"]]


def test_profiles_keep_earlier_values_newest_first(db, make_book, tmp_path):
    book, _ = _kael_book(db, make_book, tmp_path)
    d = _profile(db, book, 5)                                    # chapters 0..4
    pr = d["facts"]["powers_rank"]
    assert (pr["value"], pr["chapter_idx"]) == ("Silver rank; title Wolf Breaker", 4)
    # A repeat folds into the previous value (where it was last set).
    assert _hist(pr) == [("Bronze rank", 2), ("Iron rank", 0)]
    assert all(set(h) == {"value", "book_id", "chapter_idx"} for h in pr["history"])
    # A value that comes back is the current one, never also an earlier one.
    loc = d["facts"]["location"]
    assert (loc["value"], loc["chapter_idx"]) == ("the mill", 3)
    assert _hist(loc) == [("the inn", 2)]


def test_history_holds_at_most_two_earlier_values(db, make_book, tmp_path):
    book, kael = _kael_book(db, make_book, tmp_path)
    for idx, rank in ((6, "Diamond rank"),):
        _store_digest(db, book, idx, [{**_entry("Kael Doran", "More."), "entity_id": kael.id}])
        db.add(AIEntityNote(entity_id=kael.id, book_id=book.id, chapter_idx=idx, line="More.",
                            facts_json=json.dumps({"powers_rank": rank}), evidence="Kael Doran"))
    db.flush()
    pr = _profile(db, book, 7)["facts"]["powers_rank"]
    assert pr["value"] == "Diamond rank"
    assert _hist(pr) == [("Gold rank", 5), ("Silver rank; title Wolf Breaker", 4)]


def test_status_outside_the_vocabulary_is_ignored_at_fold_time(db, make_book, tmp_path):
    book, _ = _kael_book(db, make_book, tmp_path)
    # Chapters 0..2: "escaped" and "drunk" never replace "alive".
    d = _profile(db, book, 3)
    assert d["status"] == "alive"
    assert d["facts"]["status"] == {"value": "alive", "book_id": book.id, "chapter_idx": 0, "history": []}
    # Chapters 0..4: "left" is ignored, the condition form counts.
    d = _profile(db, book, 5)
    assert d["status"] == "captured, in chains"
    assert d["facts"]["status"]["chapter_idx"] == 3
    assert _hist(d["facts"]["status"]) == [("alive", 0)]
    assert "escaped" not in str(d) and "drunk" not in str(d) and "left" not in str(d)


def test_history_never_reaches_past_the_window(db, make_book, tmp_path):
    book, _ = _kael_book(db, make_book, tmp_path)
    d = _profile(db, book, 2)                                    # chapters 0..1
    pr = d["facts"]["powers_rank"]
    assert pr["value"] == "Bronze rank" and _hist(pr) == [("Iron rank", 0)]
    assert "Silver" not in str(d) and "Gold" not in str(d) and "dead" not in str(d)
    assert _profile(db, book, 1)["facts"]["powers_rank"]["history"] == []


def test_glossary_endpoint_carries_history_with_places(client, db, make_book, tmp_path):
    user = _make_user(db, "facthist", "member")
    book, kael = _kael_book(db, make_book, tmp_path)
    _position(db, user, book, _mid(book, db, 5))                 # window: chapters 0..4
    db.commit()
    r = client.get(f"/api/ai/books/{book.id}/glossary", headers=_headers(user))
    assert r.status_code == 200
    [e] = r.json()["entries"]
    pr = e["facts"]["powers_rank"]
    assert pr["value"] == "Silver rank; title Wolf Breaker" and pr["chapter_idx"] == 4
    assert "history" not in pr                                   # the list stays lean
    assert e["status"] == "captured, in chains"
    assert "Gold rank" not in str(r.json()) and "the sea" not in str(r.json())
    assert "escaped" not in str(r.json()) and "drunk" not in str(r.json())

    # The opened entry carries the history, newest first, places titled.
    full = client.get(f"/api/ai/books/{book.id}/glossary/entry?entity_id={kael.id}",
                      headers=_headers(user)).json()["entry"]
    fpr = full["facts"]["powers_rank"]
    assert fpr["chapter_title"] == "Part 5"
    assert [(h["value"], h["chapter_idx"], h["chapter_title"]) for h in fpr["history"]] == [
        ("Bronze rank", 2, "Part 3"), ("Iron rank", 0, "Part 1")]
    assert [h["value"] for h in full["facts"]["status"]["history"]] == ["alive"]
    assert "Gold rank" not in str(full) and "escaped" not in str(full)


def test_old_shape_entries_have_no_facts(db, make_book, tmp_path):
    book = _epub_book(db, make_book, tmp_path, "Old Shape", chapters=EIGHT)
    _store_digest(db, book, 0, [_entry("Tobin Ash", "Rings the bell.")])
    entries = companion.glossary_entries(db, digest.Scope(book=book), 1,
                                         digest.scoped_digests(db, digest.Scope(book=book), 1))
    [t] = entries
    assert t["entity_id"] is None and t["facts"] == {} and t["status"] is None
