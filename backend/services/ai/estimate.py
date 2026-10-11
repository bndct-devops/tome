"""Cost and time estimates for reading-companion digest work.

``estimate(db, user, book, scope, upto)`` prices the digests a job would make:
the missing chapters before the reader's position chapter ``upto`` of
``book`` (``scope="book"``), plus every chapter of the earlier volumes the
user marked read (``scope="series"``), grouped into units exactly as the job
will group them (``digest.plan_units``).

Per unit (one model call):

- input: ``words * TOKENS_PER_WORD`` tokens of chapter text (measured at
  about 2.3 for English prose and verse alike), plus ``PROMPT_TOKENS`` of
  prompt and context and ``HEADING_TOKENS`` per chapter, plus the names-only
  glossary state (``STATE_TOKENS``), written to the cache by the first call
  of a volume and read from it by the rest;
- output: ``OUT_PER_CHAPTER`` tokens per chapter plus ``OUT_PER_WORD`` per
  word of chapter text (a digest has a fixed part, the summary, and a part
  that grows with the chapter, the entries), times ``out_scale``: the median
  of actual over predicted output across the last ``HISTORY`` digest calls
  made with the chosen model that recorded the words they sent (1.0 before
  there is history; calls with next to no output are left out). History
  only ever raises it (``OUT_SCALE_BOUNDS`` starts at 1.0);
- seconds: ``CALL_OVERHEAD_SECONDS`` plus input over
  ``INPUT_TOKENS_PER_SECOND`` plus output over ``OUTPUT_TOKENS_PER_SECOND``,
  times ``time_scale``, the same kind of median over the last ``HISTORY``
  timed ``reading_digest`` calls with that model.

The cost is then raised by ``ESTIMATE_MARGIN``: the dialog's figure should
come out a little high rather than a little low. Cost comes from
``usage.PRICES``. Time: the job runs up to ``digest_job.WORKERS`` units at
once, but the units of one volume one after the other (each builds on the
glossary of the one before), so the estimate is the larger of
``ceil(calls / WORKERS) * mean call seconds`` and the longest volume's units
back to back.

``call_cost_guess`` is the conservative per-call figure ``run_feature``
checks the spend limits with.
"""
from __future__ import annotations

import math
from statistics import median
from typing import Any

from sqlalchemy.orm import Session

from backend.models.ai_usage import AIUsage
from backend.models.book import Book
from backend.models.user import User
from backend.services.ai import budget, registry, settings as ai_settings, usage

TOKENS_PER_WORD = 2.3
PROMPT_TOKENS = 1500          # system prompt, schema, context block
HEADING_TOKENS = 20           # per chapter: "=== Chapter N: Title ==="
STATE_TOKENS = 1500           # names-only glossary state (cached after the first call)
OUT_PER_CHAPTER = 120         # the summary and the schema framing
OUT_PER_WORD = 0.15           # the entries grow with the chapter
OUT_MIN_PER_CHAPTER = 60
INPUT_TOKENS_PER_SECOND = 8000.0
OUTPUT_TOKENS_PER_SECOND = 250.0
CALL_OVERHEAD_SECONDS = 4.0
ESTIMATE_MARGIN = 1.25
HISTORY = 50
OUT_SCALE_BOUNDS = (1.0, 4.0)   # history raises the cost estimate, never lowers it
TIME_SCALE_BOUNDS = (0.5, 4.0)  # how far history may bend the time defaults
_HISTORY_MIN_OUT = 50         # calls below this are front matter, not a sample

# Conservative output guesses for the spend-limit check (generous on purpose:
# the check runs before the call and must not let a call pass a limit). A
# reading feature (recap, recall, ask) is priced at its call's ``max_tokens``
# when the caller passes it: the most the call can be billed for.
METADATA_CALL_GUESS = 0.05
_GUESS_OUT = {"reading_digest": 700, "recap": 3000, "recall": 800, "ask": 1500}
_GUESS_OUT_DEFAULT = 2000
# Features whose chapter text is always sent marked for caching (written to
# the cache at the cache-write rate) when the caller does not say how much is.
_CACHES_TEXT = frozenset({"recall"})


def call_cost_guess(feature: str, model: str, words: int, chapters: int = 1,
                    max_tokens: int | None = None, cached_words: int | None = None) -> float:
    """A conservative USD figure for one call of ``feature`` on ``model``
    sending ``words`` words (``chapters`` chapters for a digest call). A
    metadata feature is a fixed ``METADATA_CALL_GUESS``. With ``max_tokens``
    a digest call or a reading feature takes that as its output: the most it
    can be billed for, so calls running side by side cannot together pass a
    limit. ``cached_words`` of the ``words`` are sent marked for caching and
    priced at the cache-write rate (None: all of them for a feature in
    ``_CACHES_TEXT``, else none)."""
    f = registry.get_feature(feature)
    if f is None or f.kind == "metadata":
        return METADATA_CALL_GUESS
    per = _GUESS_OUT.get(feature, _GUESS_OUT_DEFAULT)
    out = per * max(1, chapters) + (500 if feature == "reading_digest" else 0)
    if max_tokens and (feature == "reading_digest" or f.kind == "reading"):
        out = max_tokens
    if cached_words is None:
        cached_words = words if feature in _CACHES_TEXT else 0
    cached_words = max(0, min(int(cached_words), int(words)))
    inp = int((words - cached_words) * TOKENS_PER_WORD) + PROMPT_TOKENS
    return usage.cost_usd(model, inp, out, cache_write_tokens=int(cached_words * TOKENS_PER_WORD))


def content_cost_guess(feature: str, model: str, user_content: list[dict[str, Any]] | str,
                       max_tokens: int) -> float:
    """``call_cost_guess`` for the blocks a call will send: the words of the
    blocks marked ``cache_control`` priced at the cache-write rate, the
    output at ``max_tokens``."""
    if isinstance(user_content, str):
        return call_cost_guess(feature, model, len(user_content.split()), max_tokens=max_tokens,
                               cached_words=0)
    words = cached = 0
    for b in user_content:
        if not isinstance(b, dict):
            continue
        n = len(str(b.get("text", "")).split())
        words += n
        if b.get("cache_control"):
            cached += n
    return call_cost_guess(feature, model, words, max_tokens=max_tokens, cached_words=cached)


# ── history ──────────────────────────────────────────────────────────────────

def predicted_output(words: int, chapters: int) -> int:
    """Output tokens the defaults predict for ``chapters`` chapters holding
    ``words`` words of text in one call."""
    return int(round(max(OUT_PER_CHAPTER * chapters + OUT_PER_WORD * words,
                         OUT_MIN_PER_CHAPTER * chapters)))


def predicted_seconds(input_tokens: int, output_tokens: int) -> float:
    return (CALL_OVERHEAD_SECONDS + input_tokens / INPUT_TOKENS_PER_SECOND
            + output_tokens / OUTPUT_TOKENS_PER_SECOND)


def _clamp(x: float, bounds: tuple[float, float]) -> float:
    lo, hi = bounds
    return min(hi, max(lo, x))


def history(db: Session, model: str) -> dict[str, Any]:
    """How this instance's own recent calls with ``model`` compare with the
    defaults: ``out_scale`` (actual over predicted output tokens, median over
    the last ``HISTORY`` ``reading_digest`` calls that recorded the words
    and chapters they sent, front matter left out) and ``time_scale``
    (actual over predicted seconds, median over the last ``HISTORY`` timed
    ``reading_digest`` calls). Both are 1.0 without history and are kept
    within ``OUT_SCALE_BOUNDS`` / ``TIME_SCALE_BOUNDS``."""
    from backend.services.ai.digest import FEATURE

    rows = (
        db.query(AIUsage.words_sent, AIUsage.chapters, AIUsage.output_tokens)
        .filter(AIUsage.feature == FEATURE, AIUsage.model == model,
                AIUsage.words_sent.isnot(None), AIUsage.words_sent > 0,
                AIUsage.output_tokens >= _HISTORY_MIN_OUT)
        .order_by(AIUsage.id.desc())
        .limit(HISTORY)
        .all()
    )
    out_ratios = [out / predicted_output(words, max(1, chapters or 1)) for words, chapters, out in rows]
    timed = (
        db.query(AIUsage.duration_ms, AIUsage.input_tokens, AIUsage.cache_read_tokens,
                 AIUsage.cache_write_tokens, AIUsage.output_tokens)
        .filter(AIUsage.feature == FEATURE, AIUsage.model == model,
                AIUsage.duration_ms.isnot(None), AIUsage.duration_ms > 0,
                AIUsage.output_tokens > 0)
        .order_by(AIUsage.id.desc())
        .limit(HISTORY)
        .all()
    )
    time_ratios = [ms / 1000.0 / predicted_seconds(inp + (cr or 0) + (cw or 0), out)
                   for ms, inp, cr, cw, out in timed]
    return {
        "out_scale": _clamp(float(median(out_ratios)), OUT_SCALE_BOUNDS) if out_ratios else 1.0,
        "time_scale": _clamp(float(median(time_ratios)), TIME_SCALE_BOUNDS) if time_ratios else 1.0,
        "samples": len(out_ratios),
        "timed_samples": len(time_ratios),
    }


# ── units ────────────────────────────────────────────────────────────────────

def _unit_figures(unit: Any, first_of_volume: bool, model: str, hist: dict[str, Any]) -> tuple[float, float]:
    """(USD, seconds) for one unit."""
    n = len(unit.idxs)
    inp = int(unit.words * TOKENS_PER_WORD) + PROMPT_TOKENS + HEADING_TOKENS * n
    out = int(round(predicted_output(unit.words, n) * hist["out_scale"]))
    cache_read = 0 if first_of_volume else STATE_TOKENS
    cache_write = STATE_TOKENS if first_of_volume else 0
    cost = usage.cost_usd(model, inp, out, cache_read_tokens=cache_read,
                          cache_write_tokens=cache_write) * ESTIMATE_MARGIN
    secs = predicted_seconds(inp + cache_read + cache_write, out) * hist["time_scale"]
    return cost, secs


def units_figures(units: list[Any], model: str, hist: dict[str, Any]) -> dict[str, Any]:
    """``{chapters, calls, words, est_cost_usd, est_seconds}`` for ``units``
    (``digest.Unit``s in job order)."""
    from backend.services.ai.digest_job import WORKERS

    seen: set[int] = set()
    cost = 0.0
    per_volume: dict[int, float] = {}
    all_secs: list[float] = []
    for u in units:
        c, s = _unit_figures(u, u.book_id not in seen, model, hist)
        seen.add(u.book_id)
        cost += c
        per_volume[u.book_id] = per_volume.get(u.book_id, 0.0) + s
        all_secs.append(s)
    calls = len(units)
    if calls:
        mean = sum(all_secs) / calls
        seconds = max(math.ceil(calls / WORKERS) * mean, max(per_volume.values()))
    else:
        seconds = 0.0
    return {
        "chapters": sum(len(u.idxs) for u in units),
        "calls": calls,
        "words": sum(u.words for u in units),
        "est_cost_usd": round(cost, 4),
        "est_seconds": int(math.ceil(seconds)),
    }


# ── the estimate ─────────────────────────────────────────────────────────────

def _for_scope(db: Session, sc: Any, scope: str, upto: int, model: str,
               hist: dict[str, Any], remaining: float | None) -> dict[str, Any]:
    from backend.services.ai import digest

    units = digest.plan_units(db, sc, upto)
    figs = units_figures(units, model, hist)
    titles = {v.id: v.title for v in sc.volumes()}
    volumes: list[dict[str, Any]] = []
    for u in units:
        if volumes and volumes[-1]["book_id"] == u.book_id:
            volumes[-1]["chapters"] += len(u.idxs)
        else:
            volumes.append({"book_id": u.book_id, "title": titles.get(u.book_id), "chapters": len(u.idxs)})
    return {
        "scope": scope,
        **figs,
        "model": model,
        "remaining_budget_usd": None if remaining is None else round(remaining, 4),
        "over_budget": remaining is not None and figs["est_cost_usd"] > remaining + 1e-9,
        "volumes": volumes,
    }


def estimate(db: Session, user: User, book: Book, scope: str, upto: int) -> dict[str, Any]:
    """``{scope, chapters, calls, words, est_seconds, est_cost_usd, model,
    remaining_budget_usd, over_budget, volumes: [{book_id, title,
    chapters}]}`` for the missing chapters before chapter ``upto`` of
    ``book`` (``"book"``), plus the read earlier volumes (``"series"``)."""
    from backend.services.ai import digest

    sc = digest.Scope(book=book) if scope == "book" else digest.user_scope(db, user, book)
    model = ai_settings.effective_model(db, digest.FEATURE)
    return _for_scope(db, sc, scope, upto, model, history(db, model), budget.remaining(db, user))


def estimates(db: Session, user: User, book: Book, upto: int) -> dict[str, Any]:
    """Both scopes: ``{book: {...}, series: {...} | None}``; ``series`` is
    None when the user has no read earlier volume in the scope."""
    from backend.services.ai import digest

    model = ai_settings.effective_model(db, digest.FEATURE)
    hist = history(db, model)
    remaining = budget.remaining(db, user)
    series_scope = digest.user_scope(db, user, book)
    return {
        "book": _for_scope(db, digest.Scope(book=book), "book", upto, model, hist, remaining),
        "series": (_for_scope(db, series_scope, "series", upto, model, hist, remaining)
                   if series_scope.included else None),
    }
