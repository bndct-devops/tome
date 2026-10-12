"""Merge duplicate glossary entities of a series ("Merge duplicates").

A series read with volumes in flight at the same time (or a model that did
not use a ``ref``) can leave one character as two entities: "Humphrey" and
"Humphrey Geller", "Sophie" and "Sophie Wexler". This pass finds such pairs
in code and lets the model confirm each one; only confirmed pairs merge.

1. Candidates, no model: two live entities (``merged_into`` None) of one
   series key and one kind whose names share a name token (3+ letters, not a
   ``digest._NAME_STOPWORDS`` word) across canonical name + aliases, or where
   one's canonical name is among the other's aliases. Strongest first,
   at most ``MAX_PAIRS`` per run, ``PAIRS_PER_CALL`` per call.
2. One ``reading_digest`` call per batch (billed like the digest, under the
   caller's ``job_id``): per pair both ids, names, aliases, identity, first
   seen and up to the first and last ``NOTE_LINES`` note lines of each. The
   model judges from that context alone whether the two are one. Every
   answered pair is remembered (``ai_reconcile_verdicts``) with a ``stamp``
   of what the model saw (both sides' note + alias counts); ``candidates``
   skips a pair whose stamp has not changed, before the ``MAX_PAIRS`` cap,
   so a pair called different is not re-sent (and re-billed) on every run.
3. Merge in code when ``same`` and ``confidence >= MIN_CONFIDENCE``, under
   the series lock digest writes take (``digest._series_lock``), on freshly
   loaded rows: the entity first seen earlier (reading order: volume
   ``series_index``, then chapter) survives; the loser points at it from the
   loser's own first appearance. Notes stay on the loser; ``digest.profiles``
   follows ``merged_into`` (``_resolve``) once the merge point is inside the
   reader's window, so a reader before the loser's first appearance sees only
   the survivor, and one past it sees one profile with both timelines.

Spoiler note: the model sees notes from anywhere in the series, and the
merge is recorded at the loser's first appearance. The prompt therefore asks
for "same" only for a duplicate entry (one entity entered twice under
spellings the text uses for it), never for a later reveal (a disguise or
secret identity), which would leak into earlier windows.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Callable

from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.models.ai_entity import AIEntity, AIEntityNote
from backend.models.ai_reconcile_verdict import AIReconcileVerdict
from backend.models.book import Book
from backend.models.user import User
from backend.services import ai, book_text
from backend.services.ai import digest, settings as ai_settings, usage
from backend.services.audit import audit

logger = logging.getLogger(__name__)

FEATURE = digest.FEATURE          # reading_digest (Sonnet)
PAIRS_PER_CALL = 40
MAX_PAIRS = 200                   # per run: bounds one run's cost
NOTE_LINES = 2                    # first and last note lines per entity
MIN_CONFIDENCE = 0.8
MAX_TOKENS = 6000
_NOTE_LINE_MAX = 240
_WHY_MAX = 200

SYSTEM_PROMPT = """\
You check a reading glossary for duplicate entries. The glossary was built \
chapter by chapter from a book series, and the same person, place or term was \
sometimes entered twice, under a short and a full name ("Humphrey" and \
"Humphrey Geller") or under two spellings the text uses for it.

You get pairs of entries. Each entry has an id, its name, the other names it \
goes by, a one-line identity, where it was first seen ("vol N, ch M") and a \
few lines saying what it did in particular chapters.

For each pair, judge from this context alone whether the two entries are one \
and the same entity:
- Two different people can share a name: a first name, a family name, a \
title. Relatives share a family name. Shared words alone prove nothing.
- Answer same: true only for a duplicate entry: one entity entered twice. \
If the context shows that two characters who seem different turn out to be \
one (a disguise, a secret identity, a reveal), answer false.
- When the lines contradict each other (different roles, places, relatives, \
both present in one scene as two people), answer false.
- Never use outside knowledge of the book or series; only what is given here.

Answer every pair once with {a, b, same, confidence, why}: a and b are the \
two ids as given, confidence is 0 to 1 that your answer is right about them \
being the same, why is one short sentence."""


@dataclass
class ReconcileResult:
    candidates: int = 0
    merged: int = 0
    calls: int = 0
    cost_usd: float = 0.0


def answer_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "pairs": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "a": {"type": "integer"},
                        "b": {"type": "integer"},
                        "same": {"type": "boolean"},
                        "confidence": {"type": "number"},
                        "why": {"type": "string"},
                    },
                    "required": ["a", "b", "same", "confidence", "why"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["pairs"],
        "additionalProperties": False,
    }


# ── reading order ────────────────────────────────────────────────────────────

class _Order:
    """Reading-order keys: (volume series_index, book id, chapter idx)."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self._books: dict[int, Book | None] = {}

    def book(self, book_id: int | None) -> Book | None:
        if book_id is None:
            return None
        if book_id not in self._books:
            self._books[book_id] = self.db.get(Book, book_id)
        return self._books[book_id]

    def key(self, book_id: int | None, idx: int | None) -> tuple[float, int, int]:
        b = self.book(book_id)
        if b is None or idx is None:
            return (float("inf"), 0, 0)
        # An unnumbered volume (a side story) ranks after every numbered one.
        vol = float(b.series_index) if b.series_index is not None else float("inf")
        return (vol, b.id, idx)

    def first_key(self, e: AIEntity) -> tuple[float, int, int, int]:
        return self.key(e.first_book_id, e.first_chapter_idx) + (e.id,)

    def label(self, book_id: int | None, idx: int | None) -> str:
        """``vol 2, ch 12`` (``ch 12`` for a book outside a numbered series)."""
        if idx is None:
            return "unknown"
        b = self.book(book_id)
        ch = f"ch {idx + 1}"
        if b is None or b.series_index is None:
            return ch
        f = float(b.series_index)
        return f"vol {int(f) if f.is_integer() else f'{f:g}'}, {ch}"


# ── candidates ───────────────────────────────────────────────────────────────

def _names(e: AIEntity) -> list[str]:
    return [e.name] + [a["name"] for a in digest.entity_aliases(e)]


def name_tokens(name: str) -> set[str]:
    """The name's words that can tie two entries together: 3+ letters, not a
    title or particle (``digest._NAME_STOPWORDS``)."""
    return {t for t in book_text._WORD_RE.findall(digest.normalize_text(name))
            if len(t) >= 3 and t not in digest._NAME_STOPWORDS}


def _live_entities(db: Session, key: str) -> list[AIEntity]:
    return (db.query(AIEntity)
            .filter(AIEntity.series_key == key, AIEntity.merged_into.is_(None))
            .order_by(AIEntity.id).all())


# ── verdicts ─────────────────────────────────────────────────────────────────

def _pair(a: int, b: int) -> tuple[int, int]:
    return (min(a, b), max(a, b))


def stamps(db: Session, key: str, ents: list[AIEntity]) -> dict[int, int]:
    """entity id -> its note count (with the notes of entities merged into
    it) plus its alias count: a pair's stamp is the sum of both sides'."""
    absorbed = _absorbed(db, key)
    counts = dict(db.query(AIEntityNote.entity_id, func.count(AIEntityNote.id))
                  .join(AIEntity, AIEntity.id == AIEntityNote.entity_id)
                  .filter(AIEntity.series_key == key)
                  .group_by(AIEntityNote.entity_id).all())
    return {e.id: sum(counts.get(x, 0) for x in _family(e.id, absorbed)) + len(digest.entity_aliases(e))
            for e in ents}


def _verdicts(db: Session, key: str) -> dict[tuple[int, int], AIReconcileVerdict]:
    return {(v.entity_a, v.entity_b): v
            for v in db.query(AIReconcileVerdict).filter(AIReconcileVerdict.series_key == key)}


def _record(db: Session, key: str, parsed: Any, sent: dict[tuple[int, int], int]) -> None:
    """Remember the model's answer for every sent pair it answered, at the
    stamp the pair had when it was sent. Caller commits."""
    have = _verdicts(db, key)
    items = parsed.get("pairs") if isinstance(parsed, dict) else None
    done: set[tuple[int, int]] = set()
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        a, b = item.get("a"), item.get("b")
        if not all(isinstance(x, int) and not isinstance(x, bool) for x in (a, b)):
            continue
        pair = _pair(a, b)
        if pair not in sent or pair in done:
            continue
        done.add(pair)
        conf = item.get("confidence")
        conf = float(conf) if isinstance(conf, (int, float)) and not isinstance(conf, bool) else 0.0
        v = have.get(pair)
        if v is None:
            v = AIReconcileVerdict(series_key=key, entity_a=pair[0], entity_b=pair[1])
            db.add(v)
        v.same = item.get("same") is True
        v.confidence = conf
        v.stamp = sent[pair]
    db.flush()


def clear_verdicts(db: Session, series_key: str) -> int:
    """Forget every remembered verdict of a series (the next run judges every
    candidate again). Returns the rows deleted; caller commits."""
    n = (db.query(AIReconcileVerdict).filter(AIReconcileVerdict.series_key == series_key)
         .delete(synchronize_session=False))
    db.flush()
    return n


# ── candidates ───────────────────────────────────────────────────────────────

def candidates(db: Session, book: Book, order: _Order | None = None) -> list[tuple[AIEntity, AIEntity]]:
    """Pairs of live entities of ``book``'s series that may be one, strongest
    first: a canonical name among the other's names, then more shared
    tokens, then the earlier first sight. A pair with a remembered verdict
    at its current stamp is left out (before the cap). At most
    ``MAX_PAIRS``."""
    order = order or _Order(db)
    key = digest.series_key(book)
    ents = _live_entities(db, key)
    stamp = stamps(db, key, ents)
    judged = {p: v.stamp for p, v in _verdicts(db, key).items()}
    names = {e.id: {digest.normalize_name(n) for n in _names(e)} for e in ents}
    toks = {e.id: set().union(*(name_tokens(n) for n in _names(e))) for e in ents}
    by_token: dict[tuple[str, str], list[AIEntity]] = {}
    for e in ents:
        for t in toks[e.id]:
            by_token.setdefault((e.kind, t), []).append(e)
    seen: set[tuple[int, int]] = set()
    scored: list[tuple[tuple[Any, ...], AIEntity, AIEntity]] = []

    def consider(a: AIEntity, b: AIEntity) -> None:
        if a.id == b.id or a.kind != b.kind:
            return
        pair = (min(a.id, b.id), max(a.id, b.id))
        if pair in seen:
            return
        if judged.get(pair) == stamp[a.id] + stamp[b.id]:
            seen.add(pair)
            return
        alias_hit = (digest.normalize_name(a.name) in names[b.id]
                     or digest.normalize_name(b.name) in names[a.id])
        shared = len(toks[a.id] & toks[b.id])
        if not alias_hit and not shared:
            return
        seen.add(pair)
        x, y = sorted((a, b), key=order.first_key)
        scored.append(((not alias_hit, -shared, order.first_key(x), order.first_key(y)), x, y))

    for group in by_token.values():
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                consider(a, b)
    # A canonical name among the other's aliases with no 3-letter token
    # (a short or CJK name): matched by name, not token.
    by_name: dict[tuple[str, str], list[AIEntity]] = {}
    for e in ents:
        for n in names[e.id]:
            by_name.setdefault((e.kind, n), []).append(e)
    for e in ents:
        for other in by_name.get((e.kind, digest.normalize_name(e.name)), []):
            consider(e, other)
    scored.sort(key=lambda s: s[0])
    return [(x, y) for _, x, y in scored[:MAX_PAIRS]]


# ── what the model sees ──────────────────────────────────────────────────────

def _absorbed(db: Session, key: str) -> dict[int, list[int]]:
    """entity id -> ids merged into it (directly), for its note lines."""
    out: dict[int, list[int]] = {}
    for e in db.query(AIEntity).filter(AIEntity.series_key == key, AIEntity.merged_into.isnot(None)):
        out.setdefault(e.merged_into, []).append(e.id)
    return out


def _family(eid: int, absorbed: dict[int, list[int]]) -> set[int]:
    out, todo = set(), [eid]
    while todo:
        x = todo.pop()
        if x in out:
            continue
        out.add(x)
        todo.extend(absorbed.get(x, []))
    return out


def _note_lines(db: Session, e: AIEntity, absorbed: dict[int, list[int]], order: _Order) -> list[str]:
    ids = _family(e.id, absorbed)
    notes = [n for n in db.query(AIEntityNote).filter(AIEntityNote.entity_id.in_(sorted(ids)))
             if (n.line or "").strip()]
    notes.sort(key=lambda n: order.key(n.book_id, n.chapter_idx) + (n.id,))
    picked = notes if len(notes) <= 2 * NOTE_LINES else notes[:NOTE_LINES] + notes[-NOTE_LINES:]
    return [f"{order.label(n.book_id, n.chapter_idx)}: {' '.join(n.line.split())[:_NOTE_LINE_MAX]}"
            for n in picked]


def _entity_payload(db: Session, e: AIEntity, absorbed: dict[int, list[int]], order: _Order) -> dict[str, Any]:
    return {
        "id": e.id, "name": e.name, "kind": e.kind,
        "aliases": [a["name"] for a in digest.entity_aliases(e)],
        "identity": " ".join((e.identity or "").split()),
        "first_seen": order.label(e.first_book_id, e.first_chapter_idx),
        "lines": _note_lines(db, e, absorbed, order),
    }


def build_user_content(db: Session, pairs: list[tuple[AIEntity, AIEntity]],
                       absorbed: dict[int, list[int]], order: _Order) -> list[dict[str, Any]]:
    body = [{"a": _entity_payload(db, a, absorbed, order), "b": _entity_payload(db, b, absorbed, order)}
            for a, b in pairs]
    return [{"type": "text", "text": "Pairs to judge, as JSON:\n\n"
             + json.dumps(body, ensure_ascii=False, indent=1)}]


def _confirmed(parsed: Any, sent: set[tuple[int, int]]) -> list[tuple[int, int]]:
    """The sent pairs the model called the same with enough confidence, in
    answer order, each once."""
    out: list[tuple[int, int]] = []
    done: set[tuple[int, int]] = set()
    items = parsed.get("pairs") if isinstance(parsed, dict) else None
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        a, b = item.get("a"), item.get("b")
        if not all(isinstance(x, int) and not isinstance(x, bool) for x in (a, b)):
            continue
        pair = (min(a, b), max(a, b))
        if pair not in sent or pair in done:
            continue
        done.add(pair)
        conf = item.get("confidence")
        if item.get("same") is True and isinstance(conf, (int, float)) and not isinstance(conf, bool) \
                and conf >= MIN_CONFIDENCE:
            out.append(pair)
    return out


# ── merging ──────────────────────────────────────────────────────────────────

def _later(order: _Order, a: tuple[Any, Any, Any], b: tuple[Any, Any, Any]) -> bool:
    """Alias position ``a`` (book, idx, context_end) is known later than ``b``."""
    def point(p: tuple[Any, Any, Any]) -> tuple[float, int, int]:
        ce = p[2] if isinstance(p[2], int) and p[1] is not None and p[2] > p[1] else p[1]
        return order.key(p[0], ce)
    return point(a) > point(b)


def _put_alias(survivor: AIEntity, aliases: list[dict[str, Any]], name: str,
               pos: tuple[Any, Any, Any], order: _Order) -> bool:
    """Add ``name`` known from ``pos`` (book_id, chapter_idx, context_end) to
    ``aliases``, or move an existing one earlier. True on change."""
    k = digest.normalize_name(name)
    if not k or k == digest.normalize_name(survivor.name):
        return False
    cur = next((a for a in aliases if digest.normalize_name(a["name"]) == k), None)
    # ``by``: written by this pass, so a re-read of that chapter
    # (``digest._EntityWriter._reset_at``) keeps it.
    item = {"name": name, "book_id": pos[0], "chapter_idx": pos[1], "context_end": pos[2],
            "by": "reconcile"}
    if cur is None:
        if len(aliases) >= digest.MAX_ALIASES:
            return False
        aliases.append(item)
        return True
    old = (cur.get("book_id"), cur.get("chapter_idx"), cur.get("context_end"))
    if old[0] is not None and old[1] is not None and not _later(order, old, pos):
        return False
    cur.update({k2: v for k2, v in item.items() if k2 != "name"})
    return True


def _root(entities: dict[int, AIEntity], eid: int) -> int:
    seen: set[int] = set()
    while eid not in seen:
        seen.add(eid)
        e = entities.get(eid)
        if e is None or e.merged_into is None:
            return eid
        eid = e.merged_into
    return eid


def merge(db: Session, survivor: AIEntity, loser: AIEntity, order: _Order,
          entities: dict[int, AIEntity]) -> bool:
    """Point ``loser`` at ``survivor`` from the loser's first appearance.

    The merge point is ``(loser.first_book_id, loser.first_chapter_idx)``
    with ``merged_context_end`` that same chapter: ``digest._ce_known``
    returns True when ``ce <= pos[1]``, so the merge counts for every reader
    whose window holds the loser's first chapter (the loser has no note
    before it, so a reader before it sees only the survivor). The loser's
    name and aliases become the survivor's aliases, each known from the
    later of the merge point and where the loser learned it. Notes stay on
    the loser; ``digest.profiles`` follows ``merged_into``. Refused when
    either is merged already, the kinds differ, or the merge would close a
    cycle. Caller commits."""
    if survivor.id == loser.id or survivor.kind != loser.kind:
        return False
    if survivor.merged_into is not None or loser.merged_into is not None:
        return False
    if _root(entities, survivor.id) == loser.id:
        return False
    if loser.first_book_id is None or loser.first_chapter_idx is None:
        return False
    where = (loser.first_book_id, loser.first_chapter_idx, loser.first_chapter_idx)
    loser.merged_into = survivor.id
    loser.merged_book_id, loser.merged_chapter_idx = loser.first_book_id, loser.first_chapter_idx
    loser.merged_context_end = loser.first_chapter_idx
    # Marked so a re-read of that chapter (``_EntityWriter._reset_at``) keeps
    # this merge: no chapter's same_as proved it.
    loser.merged_by = "reconcile"
    aliases = digest.entity_aliases(survivor)
    changed = _put_alias(survivor, aliases, loser.name, where, order)
    for x in digest.entity_aliases(loser):
        pos = (x.get("book_id"), x.get("chapter_idx"), x.get("context_end"))
        if pos[0] is None or pos[1] is None or not _later(order, pos, where):
            pos = where
        changed = _put_alias(survivor, aliases, x["name"], pos, order) or changed
    if changed:
        survivor.aliases_json = json.dumps(aliases, ensure_ascii=False)
    db.flush()
    logger.info("glossary reconcile: entity %s merged into %s at book %s chapter %s",
                loser.id, survivor.id, loser.first_book_id, loser.first_chapter_idx)
    return True


def _apply(db: Session, key: str, pairs: list[tuple[int, int]], order: _Order) -> int:
    """Merge the confirmed pairs on fresh rows under the series lock."""
    if not pairs:
        return 0
    merged = 0
    with digest._series_lock(key):
        try:
            db.expire_all()
            entities = {e.id: e for e in db.query(AIEntity).filter(AIEntity.series_key == key)}
            for a_id, b_id in pairs:
                a, b = entities.get(a_id), entities.get(b_id)
                if a is None or b is None:
                    continue
                survivor, loser = sorted((a, b), key=order.first_key)
                if merge(db, survivor, loser, order, entities):
                    merged += 1
            db.commit()
        except Exception:
            db.rollback()
            raise
    return merged


# ── the pass ─────────────────────────────────────────────────────────────────

def run(db: Session, user: User, book: Book, *, job_id: str | None = None,
        on_progress: Callable[[int, int], None] | None = None,
        should_stop: Callable[[], bool] | None = None) -> ReconcileResult:
    """Find, confirm and merge duplicate entities of ``book``'s series.

    ``on_progress(done_calls, total_calls)`` runs after each call;
    ``should_stop()`` is checked before each call (a cancel stops after the
    current call, its merges stored). Raises the ``AIError`` family from
    ``ai.run_feature``; merges of earlier calls stay stored."""
    order = _Order(db)
    key = digest.series_key(book)
    pairs = candidates(db, book, order)
    result = ReconcileResult(candidates=len(pairs))
    batches = [pairs[i:i + PAIRS_PER_CALL] for i in range(0, len(pairs), PAIRS_PER_CALL)]
    try:
        for n, batch in enumerate(batches):
            if should_stop is not None and should_stop():
                break
            # Pairs of this batch may have merged in an earlier one.
            db.expire_all()
            live = [(a, b) for a, b in batch if a.merged_into is None and b.merged_into is None]
            if live:
                # What the model sees, for the remembered verdicts (a merge's
                # verdict included): a later note or alias re-opens the pair.
                st = stamps(db, key, [e for p in live for e in p])
                sent = {_pair(a.id, b.id): st[a.id] + st[b.id] for a, b in live}
                absorbed = _absorbed(db, key)
                content = build_user_content(db, live, absorbed, order)
                res = ai.run_feature(db, user, FEATURE, system=SYSTEM_PROMPT, user_content=content,
                                     schema=answer_schema(), max_tokens=MAX_TOKENS, job_id=job_id,
                                     book_id=book.id)
                result.calls += 1
                result.cost_usd += usage.cost_usd(res.model, res.input_tokens, res.output_tokens,
                                                  res.cache_read_tokens, res.cache_write_tokens)
                _record(db, key, res.parsed, sent)
                db.commit()
                result.merged += _apply(db, key, _confirmed(res.parsed, set(sent)), order)
            if on_progress is not None:
                on_progress(n + 1, len(batches))
    finally:
        result.cost_usd = round(result.cost_usd, 6)
        if result.calls:
            audit(db, "ai.entities_reconciled", user_id=user.id, username=user.username,
                  resource_type="book", resource_id=book.id, resource_title=book.title,
                  details={"candidates": result.candidates, "merged": result.merged,
                           "calls": result.calls, "cost_usd": result.cost_usd,
                           "model": ai_settings.effective_model(db, FEATURE)})
    return result
