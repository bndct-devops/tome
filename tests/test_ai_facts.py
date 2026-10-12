"""Run 2e facts: the status vocabulary at fold time (rows stored before it
carry "escaped", "left", "drunk") and the short per-field history the
glossary shows under each fact. Run 2f: rank and powers (a legacy
``powers_rank`` note is read as powers), and rank/status on the state
line. FakeProvider only; books and names are made up."""
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
from tests.test_ai_companion import plugin  # noqa: F401  (fixture)
from backend.models.ai_chapter_digest import AIChapterDigest
from backend.services import ai
from tests.ai_fake import FakeProvider
from tests.test_ai_digest import (EIGHT, _answer, _entry, _epub_book, _headers, _make_user, _state_lines,
                                  _store_digest)


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


# Per chapter: the facts the note of Kael Doran carries. Chapter 0 is stored
# the way every note was before the rank/powers split (``powers_rank``).
NOTES: dict[int, dict[str, str]] = {
    0: {"status": "alive", "rank": "First Circle", "powers_rank": "Ash tongue", "location": "the mill"},
    1: {"rank": "Second Circle", "powers": "Stone skin", "status": "escaped"},  # before the vocabulary
    2: {"rank": "Second Circle", "powers": "Stone skin", "status": "drunk", "location": "the inn"},
    3: {"rank": "Third Circle", "powers": "title Wolf Breaker", "status": "captured, in chains",
        "location": "the mill"},
    4: {"rank": "Third Circle", "powers": "Ember sight", "status": "left"},
    5: {"rank": "Fourth Circle", "powers": "Sea call", "status": "dead", "location": "the sea"},  # past the reader
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
    rk = d["facts"]["rank"]
    assert (rk["value"], rk["chapter_idx"]) == ("Third Circle", 4)
    # A repeat folds into the previous value (where it was last set).
    assert _hist(rk) == [("Second Circle", 2), ("First Circle", 0)]
    assert all(set(h) == {"value", "book_id", "chapter_idx"} for h in rk["history"])
    # Powers are additive: every distinct earlier value, newest first; the
    # legacy ``powers_rank`` of chapter 0 is read as powers.
    pw = d["facts"]["powers"]
    assert (pw["value"], pw["chapter_idx"]) == ("Ember sight", 4)
    assert _hist(pw) == [("title Wolf Breaker", 3), ("Stone skin", 2), ("Ash tongue", 0)]
    assert "powers_rank" not in d["facts"]
    # A value that comes back is the current one, never also an earlier one.
    loc = d["facts"]["location"]
    assert (loc["value"], loc["chapter_idx"]) == ("the mill", 3)
    assert _hist(loc) == [("the inn", 2)]


def _more_notes(db, book, kael, facts_by_idx: dict[int, dict[str, str]]) -> None:
    for idx, facts in facts_by_idx.items():
        _store_digest(db, book, idx, [{**_entry("Kael Doran", "More."), "entity_id": kael.id}])
        db.add(AIEntityNote(entity_id=kael.id, book_id=book.id, chapter_idx=idx, line="More.",
                            facts_json=json.dumps(facts), evidence="Kael Doran"))
    db.flush()


def test_history_holds_at_most_two_earlier_values(db, make_book, tmp_path):
    book, kael = _kael_book(db, make_book, tmp_path)
    _more_notes(db, book, kael, {6: {"rank": "Fifth Circle"}})
    rk = _profile(db, book, 7)["facts"]["rank"]
    assert rk["value"] == "Fifth Circle"
    assert _hist(rk) == [("Fourth Circle", 5), ("Third Circle", 4)]


def test_powers_keep_at_most_five_earlier_values(db, make_book, tmp_path):
    book, kael = _kael_book(db, make_book, tmp_path)
    _more_notes(db, book, kael, {6: {"powers": "Iron grip"}, 7: {"powers": "Night step"}})
    pw = _profile(db, book, 8)["facts"]["powers"]
    assert pw["value"] == "Night step"
    assert _hist(pw) == [("Iron grip", 6), ("Sea call", 5), ("Ember sight", 4),
                         ("title Wolf Breaker", 3), ("Stone skin", 2)]     # "Ash tongue" capped
    assert digest.POWERS_HISTORY == 5 and digest.FACT_HISTORY == 3


def test_a_note_with_both_keys_keeps_powers():
    assert digest.note_facts('{"powers": "new", "powers_rank": "old"}') == {"powers": "new"}
    assert digest.note_facts('{"powers_rank": "old", "rank": ""}') == {"powers": "old"}
    assert digest.note_facts("not json") == {} and digest.note_facts(None) == {}


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
    rk = d["facts"]["rank"]
    assert rk["value"] == "Second Circle" and _hist(rk) == [("First Circle", 0)]
    assert _hist(d["facts"]["powers"]) == [("Ash tongue", 0)]
    assert "Third" not in str(d) and "Fourth" not in str(d) and "dead" not in str(d)
    assert "Ember" not in str(d) and "Sea call" not in str(d)
    d1 = _profile(db, book, 1)
    assert d1["facts"]["rank"]["history"] == [] and d1["facts"]["powers"]["value"] == "Ash tongue"
    assert "powers_rank" not in d1["facts"]


def _check_list_entry(e: dict, book) -> None:
    rk = e["facts"]["rank"]
    assert rk["value"] == "Third Circle" and rk["chapter_idx"] == 4
    assert "history" not in rk and "history" not in e["facts"]["powers"]   # the list stays lean
    assert e["facts"]["powers"]["value"] == "Ember sight"
    assert "powers_rank" not in e["facts"]
    assert e["status"] == "captured, in chains"


def test_glossary_endpoint_carries_history_with_places(client, db, make_book, tmp_path):
    user = _make_user(db, "facthist", "member")
    book, kael = _kael_book(db, make_book, tmp_path)
    _position(db, user, book, _mid(book, db, 5))                 # window: chapters 0..4
    db.commit()
    r = client.get(f"/api/ai/books/{book.id}/glossary", headers=_headers(user))
    assert r.status_code == 200
    [e] = r.json()["entries"]
    _check_list_entry(e, book)
    assert "Fourth Circle" not in str(r.json()) and "the sea" not in str(r.json())
    assert "escaped" not in str(r.json()) and "drunk" not in str(r.json())
    assert "powers_rank" not in str(r.json())

    # The opened entry carries the history, newest first, places titled.
    full = client.get(f"/api/ai/books/{book.id}/glossary/entry?entity_id={kael.id}",
                      headers=_headers(user)).json()["entry"]
    frk = full["facts"]["rank"]
    assert frk["chapter_title"] == "Part 5"
    assert [(h["value"], h["chapter_idx"], h["chapter_title"]) for h in frk["history"]] == [
        ("Second Circle", 2, "Part 3"), ("First Circle", 0, "Part 1")]
    fpw = full["facts"]["powers"]
    assert [(h["value"], h["chapter_idx"], h["chapter_title"]) for h in fpw["history"]] == [
        ("title Wolf Breaker", 3, "Part 4"), ("Stone skin", 2, "Part 3"), ("Ash tongue", 0, "Part 1")]
    assert [h["value"] for h in full["facts"]["status"]["history"]] == ["alive"]
    assert "Fourth Circle" not in str(full) and "escaped" not in str(full)
    assert "powers_rank" not in str(full)


def test_plugin_glossary_carries_rank_and_powers(db, make_book, tmp_path, plugin):
    c, key_for = plugin
    user = _make_user(db, "factplug", "member")
    book, _ = _kael_book(db, make_book, tmp_path)
    h = key_for(user)
    db.commit()
    r = c.get(f"/api/tome-sync/ai/books/{book.id}/glossary?pct={_mid(book, db, 5)}", headers=h)
    assert r.status_code == 200, r.text
    [e] = r.json()["entries"]
    _check_list_entry(e, book)
    assert "powers_rank" not in str(r.json()) and "Fourth Circle" not in str(r.json())


def test_old_shape_entries_have_no_facts(db, make_book, tmp_path):
    book = _epub_book(db, make_book, tmp_path, "Old Shape", chapters=EIGHT)
    _store_digest(db, book, 0, [_entry("Tobin Ash", "Rings the bell.")])
    entries = companion.glossary_entries(db, digest.Scope(book=book), 1,
                                         digest.scoped_digests(db, digest.Scope(book=book), 1))
    [t] = entries
    assert t["entity_id"] is None and t["facts"] == {} and t["status"] is None


# ── rank and status on the state line sent to the model ─────────────────────

def _state(db, book, upto: int) -> list[dict]:
    prior = digest._prior_digests(db, book, upto)
    notes = digest._notes_for(db, prior)
    return digest.model_state(digest.seen_entities(
        prior, book.id, digest._entities_for(db, book, prior, notes), upto=upto, notes=notes))


def test_the_state_line_carries_rank_and_status_from_the_window_only(db, make_book, tmp_path):
    book, kael = _kael_book(db, make_book, tmp_path)
    # Digesting chapter 3: notes 0..2 only ("escaped"/"drunk" are not statuses).
    assert _state(db, book, 3) == [{"id": kael.id, "name": "Kael Doran", "kind": "person",
                                    "identity": "A miller's son.", "rank": "Second Circle",
                                    "status": "alive"}]
    [line] = _state(db, book, 4)
    assert (line["rank"], line["status"]) == ("Third Circle", "captured, in chains")
    # Chapter 3 written by a call that also read chapter 4: a call for
    # chapter 4 must not know it, so neither its rank nor its status.
    db.query(AIChapterDigest).filter_by(book_id=book.id, chapter_idx=3).update({"context_end_idx": 4})
    db.flush()
    [line] = _state(db, book, 4)
    assert (line.get("rank"), line.get("status")) == ("Second Circle", "alive")
    assert "Third" not in str(line)
    # Past that call's end it counts again.
    [line] = _state(db, book, 5)
    assert line["rank"] == "Third Circle"
    # A rank set in the chapter being digested or later never shows.
    assert "Fourth" not in str(_state(db, book, 5)) and "dead" not in str(_state(db, book, 5))
    # A line without any of them keeps the old shape.
    tobin = AIEntity(series_key=digest.series_key(book), kind="person", name="Tobin Ash",
                     identity="A bell ringer.", first_book_id=book.id, first_chapter_idx=0)
    db.add(tobin)
    db.flush()
    d0 = db.query(AIChapterDigest).filter_by(book_id=book.id, chapter_idx=0).one()
    d0.entries_json = json.dumps(json.loads(d0.entries_json)
                                 + [{"name": "Tobin Ash", "kind": "person", "line": "Rang.", "more": "",
                                     "evidence": "Tobin Ash", "entity_id": tobin.id}])
    db.flush()
    lines = _state(db, book, 1)
    assert lines[1] == {"id": tobin.id, "name": "Tobin Ash", "kind": "person", "identity": "A bell ringer."}


def test_a_digest_call_sends_rank_and_status_and_says_what_they_are(db, make_book, tmp_path):
    user = _make_user(db, "factstate", "member")
    book, kael = _kael_book(db, make_book, tmp_path)
    fake = FakeProvider(responses=[_answer()])
    ai.set_provider_override(fake)
    digest.digest_chapter(db, user, book, 6)
    [line] = _state_lines(fake.calls[0])
    assert line == {"id": kael.id, "name": "Kael Doran", "kind": "person", "identity": "A miller's son.",
                    "rank": "Fourth Circle", "status": "dead"}
    text = fake.calls[0]["user_content"][0]["text"]
    assert text.startswith(digest.STATE_HEADER)
    assert "rank and status, when present, are the entity's current values" in digest.STATE_HEADER
    assert "a later line with the same id is newer" in digest.STATE_HEADER


def test_earlier_volume_lines_keep_volume_start_values_and_a_change_is_listed_again(
        db, make_book, tmp_path):
    vol1 = _epub_book(db, make_book, tmp_path, "Circle One", series="Circle Saga", index=1, chapters=EIGHT)
    vol2 = _epub_book(db, make_book, tmp_path, "Circle Two", series="Circle Saga", index=2, chapters=EIGHT)
    key = digest.series_key(vol1)
    kael = AIEntity(series_key=key, kind="person", name="Kael Doran", identity="A miller's son.",
                    first_book_id=vol1.id, first_chapter_idx=0)
    mira = AIEntity(series_key=key, kind="person", name="Mira Holt", identity="A smith.",
                    first_book_id=vol1.id, first_chapter_idx=0)
    db.add_all([kael, mira])
    db.flush()

    def write(book, idx, notes: dict) -> None:
        _store_digest(db, book, idx, [{**_entry(e.name, "Was there."), "entity_id": e.id} for e in notes])
        for e, facts in notes.items():
            db.add(AIEntityNote(entity_id=e.id, book_id=book.id, chapter_idx=idx, line="Was there.",
                                facts_json=json.dumps(facts), evidence=e.name))
        db.flush()

    write(vol1, 0, {kael: {"rank": "First Circle", "status": "alive"}, mira: {"status": "alive"}})
    write(vol2, 0, {kael: {"rank": "Second Circle"}, mira: {"status": "alive"}})
    write(vol2, 1, {mira: {"status": "missing"}})

    start = {"id": kael.id, "name": "Kael Doran", "kind": "person", "identity": "A miller's son.",
             "rank": "First Circle", "status": "alive"}
    s0, s1, s2 = _state(db, vol2, 0), _state(db, vol2, 1), _state(db, vol2, 2)
    assert s0[0] == start
    # The earlier-volume part is byte-identical for every call of the volume.
    assert s1[:2] == s0[:2] and s2[:2] == s0[:2]
    # Kael's rank changed in this volume: listed again with the current values.
    assert s1[2:] == [{**start, "rank": "Second Circle"}]
    # Mira returns unchanged in chapter 0 (no second line), changed in chapter 1.
    assert s2[2:] == [{**start, "rank": "Second Circle"},
                      {"id": mira.id, "name": "Mira Holt", "kind": "person", "identity": "A smith.",
                       "status": "missing"}]
