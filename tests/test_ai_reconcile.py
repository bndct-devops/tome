"""Merge duplicates (``backend/services/ai/reconcile.py``): candidates found in
code, the model confirms, merges recorded at the loser's first appearance so
``digest.profiles`` keeps the spoiler window. FakeProvider only; names are
made up."""
from __future__ import annotations

import json

import pytest
from sqlalchemy.orm import Session

from backend.core.security import hash_password
from backend.models.ai_chapter_digest import AIChapterDigest
from backend.models.ai_entity import AIEntity, AIEntityNote
from backend.models.ai_reconcile_verdict import AIReconcileVerdict
from backend.models.ai_usage import AIUsage
from backend.models.audit_log import AuditLog
from backend.models.book import Book, BookChapter
from backend.models.user import User
from backend.services import ai
from backend.services.ai import digest, reconcile, settings as ai_settings
from tests.ai_fake import FakeProvider

KEY = "sk-ant-test-reconcile-0000000000Ab12"
SERIES = "The Tidewright Cycle"
CHAPTERS = 4


# ── fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture()
def user(db: Session) -> User:
    u = User(username="reader", email="reader@example.com", hashed_password=hash_password("pw123456"),
             is_active=True, is_admin=True, role="admin", must_change_password=False)
    db.add(u)
    db.flush()
    ai_settings.set_user_key(db, u, KEY)
    db.flush()
    return u


@pytest.fixture()
def vols(db: Session, make_book) -> list[Book]:
    """Two volumes of one series, four chapters each, every chapter digested."""
    out = []
    for i in (1, 2):
        b = make_book(title=f"Tidewright {i}", series=SERIES, series_index=float(i))
        for idx in range(CHAPTERS):
            db.add(BookChapter(book_id=b.id, idx=idx, title=f"Chapter {idx + 1}",
                               start_fraction=idx / CHAPTERS, end_fraction=(idx + 1) / CHAPTERS))
            db.add(AIChapterDigest(book_id=b.id, chapter_idx=idx, model="claude-sonnet-5-5",
                                   summary=f"Summary {i}:{idx}", entries_json="[]"))
        out.append(b)
    db.flush()
    return out


def _entity(db: Session, book: Book, idx: int, name: str, *, kind: str = "person",
            identity: str = "", aliases: list[dict] | None = None) -> AIEntity:
    e = AIEntity(series_key=digest.series_key(book), kind=kind, name=name,
                 aliases_json=json.dumps(aliases or []), identity=identity or f"{name}, someone.",
                 first_book_id=book.id, first_chapter_idx=idx)
    db.add(e)
    db.flush()
    return e


def _note(db: Session, e: AIEntity, book: Book, idx: int, line: str) -> AIEntityNote:
    n = AIEntityNote(entity_id=e.id, book_id=book.id, chapter_idx=idx, line=line,
                     identity="", facts_json="{}", evidence="")
    db.add(n)
    db.flush()
    return n


def _pairs_sent(call: dict) -> list[tuple[int, int]]:
    text = call["user_content"][0]["text"]
    body = json.loads(text[text.index("["):])
    return [(p["a"]["id"], p["b"]["id"]) for p in body]


def _answer(verdicts: dict[frozenset, tuple[bool, float]]):
    """A FakeProvider callable answering each sent pair from ``verdicts``
    (default: not the same)."""
    def respond(feature, user_content, schema):
        call = {"user_content": user_content}
        return {"pairs": [{"a": a, "b": b, "same": verdicts.get(frozenset((a, b)), (False, 0.9))[0],
                           "confidence": verdicts.get(frozenset((a, b)), (False, 0.9))[1],
                           "why": "From the lines."} for a, b in _pairs_sent(call)]}
    return respond


def _profiles(db: Session, vols: list[Book], upto: int) -> list[digest.Profile]:
    db.expire_all()
    return digest.profiles(db, digest.Scope(book=vols[1], included=[vols[0]]), upto)


def _split_pair(db: Session, vols: list[Book]) -> tuple[AIEntity, AIEntity]:
    """The duplicate created by parallel reading: "Brannoc" (created FIRST,
    but first seen in vol 2 ch 2) and "Brannoc Hale" (vol 1 ch 0)."""
    v1, v2 = vols
    loser = _entity(db, v2, 2, "Brannoc", identity="A ferryman.",
                    aliases=[{"name": "the Ferryman", "book_id": v2.id, "chapter_idx": 3, "context_end": 3}])
    survivor = _entity(db, v1, 0, "Brannoc Hale", identity="A ferryman of the lower docks.")
    _note(db, survivor, v1, 0, "Brannoc Hale poles the ferry across.")
    _note(db, survivor, v1, 3, "He refuses the harbourmaster's bribe.")
    _note(db, survivor, v2, 0, "Brannoc Hale is back on the river.")
    _note(db, loser, v2, 2, "Brannoc loses his pole in the flood.")
    _note(db, loser, v2, 3, "Brannoc builds a new raft.")
    return survivor, loser


# ── tests ────────────────────────────────────────────────────────────────────

def test_split_pair_merges_at_the_losers_first_appearance(db, user, vols):
    survivor, loser = _split_pair(db, vols)
    fake = FakeProvider(responses=_answer({frozenset((survivor.id, loser.id)): (True, 0.95)}))
    ai.set_provider_override(fake)

    res = reconcile.run(db, user, vols[1], job_id="job12345")

    assert (res.candidates, res.merged, res.calls) == (1, 1, 1)
    assert res.cost_usd > 0
    assert fake.calls[0]["feature"] == "reading_digest"
    sent = fake.calls[0]["user_content"][0]["text"]
    assert "vol 2, ch 3" in sent and "vol 1, ch 1" in sent      # first seen + note labels
    assert "Brannoc loses his pole" in sent
    db.expire_all()
    assert loser.merged_into == survivor.id
    assert (loser.merged_book_id, loser.merged_chapter_idx, loser.merged_context_end) == (vols[1].id, 2, 2)
    assert survivor.merged_into is None
    aliases = {a["name"]: a for a in digest.entity_aliases(survivor)}
    assert aliases["Brannoc"] == {"name": "Brannoc", "book_id": vols[1].id, "chapter_idx": 2, "context_end": 2,
                                  "by": "reconcile"}
    # Learned after the merge point: kept where the loser learned it.
    assert aliases["the Ferryman"]["chapter_idx"] == 3
    # Notes stay on the loser.
    assert {n.entity_id for n in db.query(AIEntityNote).filter(AIEntityNote.chapter_idx.in_([2]),
                                                               AIEntityNote.book_id == vols[1].id)} == {loser.id}
    usage_row = db.query(AIUsage).one()
    assert (usage_row.job_id, usage_row.book_id, usage_row.feature) == ("job12345", vols[1].id, "reading_digest")
    log = db.query(AuditLog).filter(AuditLog.action == "ai.entities_reconciled").one()
    assert json.loads(log.details)["merged"] == 1


def test_profiles_keep_the_window(db, user, vols):
    survivor, loser = _split_pair(db, vols)
    ai.set_provider_override(FakeProvider(responses=_answer({frozenset((survivor.id, loser.id)): (True, 0.9)})))
    reconcile.run(db, user, vols[1])

    before = _profiles(db, vols, 2)            # reader in vol 2 ch 2 (chapters 0-1 read)
    assert [p.entity_id for p in before] == [survivor.id]
    assert before[0].aliases == []
    assert len(before[0].timeline) == 3

    after = _profiles(db, vols, 4)             # past the end of vol 2
    assert [p.entity_id for p in after] == [survivor.id]
    p = after[0]
    lines = [t["line"] for t in p.timeline]
    assert "Brannoc loses his pole in the flood." in lines and "Brannoc Hale poles the ferry across." in lines
    assert len(lines) == 5
    assert set(p.aliases) == {"Brannoc", "the Ferryman"}


def test_same_name_different_people_stay_apart(db, user, vols):
    v1, v2 = vols
    a = _entity(db, v1, 0, "Sela Wexmoor", identity="A net mender.")
    b = _entity(db, v2, 1, "Ivo Wexmoor", identity="Sela's brother, a smuggler.")
    _note(db, a, v1, 0, "Sela mends nets.")
    _note(db, b, v2, 1, "Ivo argues with his sister Sela.")
    fake = FakeProvider(responses=_answer({frozenset((a.id, b.id)): (False, 0.97)}))
    ai.set_provider_override(fake)

    res = reconcile.run(db, user, v2)

    assert (res.candidates, res.merged, res.calls) == (1, 0, 1)
    db.expire_all()
    assert a.merged_into is None and b.merged_into is None
    assert len(_profiles(db, vols, 4)) == 2


def test_low_confidence_stays_apart(db, user, vols):
    survivor, loser = _split_pair(db, vols)
    ai.set_provider_override(FakeProvider(responses=_answer({frozenset((survivor.id, loser.id)): (True, 0.6)})))

    res = reconcile.run(db, user, vols[1])

    assert (res.merged, res.calls) == (0, 1)
    db.expire_all()
    assert loser.merged_into is None


def test_no_candidates_no_call(db, user, vols):
    v1, v2 = vols
    _entity(db, v1, 0, "Mara Vell")
    _entity(db, v1, 1, "Vell Harbour", kind="place")        # shares a token, other kind
    _entity(db, v2, 0, "Oren Pike")
    _entity(db, v2, 1, "The Old King")                      # stopwords only
    _entity(db, v2, 2, "Old King Tam")
    fake = FakeProvider(responses=_answer({}))
    ai.set_provider_override(fake)

    res = reconcile.run(db, user, v2)

    assert (res.candidates, res.calls, res.merged) == (0, 0, 0)
    assert fake.calls == []
    assert db.query(AuditLog).filter(AuditLog.action == "ai.entities_reconciled").count() == 0


def test_alias_hit_without_a_token(db, user, vols):
    v1, v2 = vols
    a = _entity(db, v1, 0, "Quillon Ash", aliases=[{"name": "Qu", "book_id": v1.id, "chapter_idx": 0,
                                                   "context_end": 0}])
    b = _entity(db, v2, 0, "Qu")
    pairs = reconcile.candidates(db, v2)
    assert [(x.id, y.id) for x, y in pairs] == [(a.id, b.id)]


def test_merged_entities_are_not_candidates_again(db, user, vols):
    survivor, loser = _split_pair(db, vols)
    fake = FakeProvider(responses=_answer({frozenset((survivor.id, loser.id)): (True, 0.9)}))
    ai.set_provider_override(fake)
    reconcile.run(db, user, vols[1])

    res = reconcile.run(db, user, vols[1])

    assert (res.candidates, res.calls) == (0, 0)
    assert len(fake.calls) == 1


def test_chain_resolves(db, user, vols):
    """A -> B from an earlier merge, then B -> C: one profile, C's aliases
    carry A's name, and A's notes reach C."""
    v1, v2 = vols
    c = _entity(db, v1, 0, "Dagny Rook", identity="A lamplighter.")
    b = _entity(db, v1, 2, "Dagny", identity="A lamplighter.")
    a = _entity(db, v2, 1, "Lamp Dagny", identity="A lamplighter.")
    _note(db, c, v1, 0, "Dagny Rook lights the quay.")
    _note(db, b, v1, 2, "Dagny trims the wicks.")
    _note(db, a, v2, 1, "Lamp Dagny runs out of oil.")
    entities = {e.id: e for e in (a, b, c)}
    order = reconcile._Order(db)
    assert reconcile.merge(db, b, a, order, entities)           # earlier pass: A -> B
    assert [n for n in digest.entity_aliases(b) if n["name"] == "Lamp Dagny"]
    fake = FakeProvider(responses=_answer({frozenset((b.id, c.id)): (True, 0.9)}))
    ai.set_provider_override(fake)

    res = reconcile.run(db, user, v2)

    assert _pairs_sent(fake.calls[0]) == [(c.id, b.id)]          # A is merged: not a candidate
    assert "Lamp Dagny runs out of oil." in fake.calls[0]["user_content"][0]["text"]
    assert res.merged == 1
    db.expire_all()
    assert (a.merged_into, b.merged_into, c.merged_into) == (b.id, c.id, None)
    assert {x["name"] for x in digest.entity_aliases(c)} == {"Dagny", "Lamp Dagny"}
    ps = _profiles(db, vols, 4)
    assert [p.entity_id for p in ps] == [c.id]
    assert len(ps[0].timeline) == 3
    # No cycle: the survivor's root may not be the loser, merged ones refuse.
    entities = {e.id: e for e in (a, b, c)}
    assert not reconcile.merge(db, a, c, order, entities)
    assert not reconcile.merge(db, c, b, order, entities)


def test_batches_and_stop(db, user, vols, monkeypatch):
    v1, v2 = vols
    x1 = _entity(db, v1, 0, "Tamsin Reed")
    x2 = _entity(db, v2, 0, "Tamsin")
    y1 = _entity(db, v1, 1, "Corvin Hask")
    y2 = _entity(db, v2, 1, "Corvin")
    monkeypatch.setattr(reconcile, "PAIRS_PER_CALL", 1)
    fake = FakeProvider(responses=_answer({frozenset((x1.id, x2.id)): (True, 0.9),
                                           frozenset((y1.id, y2.id)): (True, 0.9)}))
    ai.set_provider_override(fake)
    progress: list[tuple[int, int]] = []

    res = reconcile.run(db, user, v2, on_progress=lambda d, t: progress.append((d, t)),
                        should_stop=lambda: len(fake.calls) >= 1)

    assert (res.candidates, res.calls, res.merged) == (2, 1, 1)
    assert progress == [(1, 2)]


# ── remembered verdicts ──────────────────────────────────────────────────────

def test_rejected_pair_is_not_sent_again(db, user, vols):
    v1, v2 = vols
    a = _entity(db, v1, 0, "Sela Wexmoor", identity="A net mender.")
    b = _entity(db, v2, 1, "Ivo Wexmoor", identity="Sela's brother.")
    _note(db, a, v1, 0, "Sela mends nets.")
    _note(db, b, v2, 1, "Ivo argues with his sister Sela.")
    fake = FakeProvider(responses=_answer({frozenset((a.id, b.id)): (False, 0.95)}))
    ai.set_provider_override(fake)
    reconcile.run(db, user, v2)
    v = db.query(AIReconcileVerdict).one()
    assert (v.entity_a, v.entity_b, v.same, v.confidence, v.stamp) == (a.id, b.id, False, 0.95, 2)

    res = reconcile.run(db, user, v2)

    assert (res.candidates, res.calls) == (0, 0)
    assert len(fake.calls) == 1
    # Cleared: judged again.
    assert reconcile.clear_verdicts(db, digest.series_key(v2)) == 1
    assert len(reconcile.candidates(db, v2)) == 1


def test_a_new_note_reopens_a_rejected_pair(db, user, vols):
    v1, v2 = vols
    a = _entity(db, v1, 0, "Sela Wexmoor")
    b = _entity(db, v2, 1, "Ivo Wexmoor")
    _note(db, a, v1, 0, "Sela mends nets.")
    fake = FakeProvider(responses=_answer({}))
    ai.set_provider_override(fake)
    reconcile.run(db, user, v2)
    assert reconcile.candidates(db, v2) == []

    _note(db, b, v2, 3, "Ivo sails for the cape.")

    res = reconcile.run(db, user, v2)
    assert (res.candidates, res.calls) == (1, 1)
    assert _pairs_sent(fake.calls[1]) == [(a.id, b.id)]
    assert db.query(AIReconcileVerdict).one().stamp == 2


def test_cap_does_not_starve_a_pair_behind_rejected_ones(db, user, vols, monkeypatch):
    v1, v2 = vols
    x1 = _entity(db, v1, 0, "Tamsin Reed")
    x2 = _entity(db, v2, 0, "Tamsin Vale")
    y1 = _entity(db, v1, 1, "Corvin Hask")
    y2 = _entity(db, v2, 1, "Corvin")
    monkeypatch.setattr(reconcile, "MAX_PAIRS", 1)
    fake = FakeProvider(responses=_answer({frozenset((y1.id, y2.id)): (True, 0.9)}))
    ai.set_provider_override(fake)

    first = reconcile.run(db, user, v2)
    second = reconcile.run(db, user, v2)

    assert _pairs_sent(fake.calls[0]) == [(x1.id, x2.id)]
    assert _pairs_sent(fake.calls[1]) == [(y1.id, y2.id)]
    assert (first.merged, second.merged) == (0, 1)
    db.expire_all()
    assert y2.merged_into == y1.id
    merged_verdict = db.query(AIReconcileVerdict).filter_by(entity_a=y1.id, entity_b=y2.id).one()
    assert merged_verdict.same is True


# ── ordering and re-reads ────────────────────────────────────────────────────

def test_unnumbered_volume_ranks_last(db, user, vols, make_book):
    side = make_book(title="Tidewright Tales", series=SERIES, series_index=None)
    early = _entity(db, side, 0, "Mirela")                  # first seen in the side story
    numbered = _entity(db, vols[1], 3, "Mirela Dunn")       # vol 2, last chapter
    _note(db, early, side, 0, "Mirela sings.")
    _note(db, numbered, vols[1], 3, "Mirela Dunn sings at the inn.")
    ai.set_provider_override(FakeProvider(responses=_answer({frozenset((early.id, numbered.id)): (True, 0.9)})))

    res = reconcile.run(db, user, vols[1])

    assert res.merged == 1
    db.expire_all()
    assert early.merged_into == numbered.id and numbered.merged_into is None


def test_reconcile_aliases_survive_a_re_read(db, user, vols):
    survivor, loser = _split_pair(db, vols)
    v2 = vols[1]
    # An alias a digest of vol 2 ch 2 learned for the survivor: a re-read of
    # that chapter clears it.
    survivor.aliases_json = json.dumps([{"name": "Old Hale", "book_id": v2.id, "chapter_idx": 2,
                                         "context_end": 2}])
    db.flush()
    ai.set_provider_override(FakeProvider(responses=_answer({frozenset((survivor.id, loser.id)): (True, 0.9)})))
    reconcile.run(db, user, v2)
    db.expire_all()
    assert {a["name"] for a in digest.entity_aliases(survivor)} == {"Old Hale", "Brannoc", "the Ferryman"}

    digest._EntityWriter(db, v2).write_chapter(2, "The river is high.", [], [])
    db.flush()
    db.expire_all()

    aliases = {a["name"]: a for a in digest.entity_aliases(survivor)}
    assert set(aliases) == {"Brannoc", "the Ferryman"}
    assert aliases["Brannoc"]["by"] == "reconcile"
    assert loser.merged_into == survivor.id
