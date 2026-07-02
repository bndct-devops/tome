"""Reading DNA — a reader-personality summary derived from existing aggregates.

Five 0–100 trait scores (Length, Variety, Rhythm, Time, Pace), each over a
trailing window, plus a named archetype assembled from the two most-defining
traits. No new tables, no migration — pure computation over the same
reconciled reading data the stats page already uses.

Trait scoring convention: 0 = the "low" pole, 100 = the "high" pole, 50 =
neutral. The archetype's NOUN comes from the single most extreme trait, the
MODIFIER from the second; they always come from different axes so the name can
never contradict itself (no "Savorer Devourer"). Tone is calibrated so the low
pole flatters as much as the high pole — reading one author for a year is
"loyal devotion", not "narrow".
"""
from __future__ import annotations

import statistics
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from backend.core.permissions import book_visibility_filter
from backend.models.book import Book
from backend.models.user import User
from backend.models.user_book_status import UserBookStatus
from backend.services import reconciled_reading as rr
from backend.services.reading_day import date_modifier, effective_today

# Per-axis vocabulary, tone-checked. Tuple is (low-pole word, high-pole word).
_NOUN = {
    "length":  ("Sprinter", "Epic Specialist"),
    "variety": ("Devotee", "Wanderer"),
    "rhythm":  ("Spree Reader", "Constant Reader"),
    "time":    ("Dawn Reader", "Night Owl"),
    "pace":    ("Savorer", "Devourer"),
}
_MOD = {
    "length":  ("Bite-size", "Marathon"),
    "variety": ("Loyal", "Roving"),
    "rhythm":  ("Seasonal", "Steady"),
    "time":    ("Early-Bird", "Night-Owl"),
    "pace":    ("Slow-Burn", "Voracious"),
}
# Bar pole labels shown in the UI (low, high).
_LABELS = {
    "length":  ("Short reads", "Long epics"),
    "variety": ("Focused", "Eclectic"),
    "rhythm":  ("Sporadic", "Consistent"),
    "time":    ("Early bird", "Night owl"),
    "pace":    ("Savorer", "Speed demon"),
}
# Short tag for the one-line summary (low, high).
_PHRASE = {
    "length":  ("short reads", "long epics"),
    "variety": ("focused taste", "eclectic taste"),
    "rhythm":  ("reads in bursts", "consistent"),
    "time":    ("early bird", "night owl"),
    "pace":    ("savoured", "fast-paced"),
}
_ORDER = ["length", "variety", "rhythm", "time", "pace"]

# An axis must sit at least this far from neutral (out of 50) to name the reader.
_EXTREME_MIN = 12
_WPM_MIN_SECONDS = 300  # below ~5 min of read-time on a book, pace is just noise


def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def _lerp_score(v: float, lo: float, hi: float) -> float:
    """Map v in [lo, hi] onto 0..100, clamped."""
    if hi == lo:
        return 50.0
    return _clamp((v - lo) / (hi - lo) * 100.0)


def compute_reading_dna(db: Session, user: User, tz_offset: int) -> dict:
    now = datetime.utcnow()
    # Plain local offset — used ONLY for the hour-of-day trait, where 1am must
    # read as hour 1. Day-identity buckets use the reading-day modifier below.
    offset_hours = -(tz_offset // 60)
    tzm = f"{offset_hours:+d} hours"
    day_mod = date_modifier(tz_offset)
    covered = rr.covered_book_ids(db, user.id)

    traits: dict[str, float] = {}

    # ── Time (early bird ↔ night owl) — trailing 365d hour distribution ──────────
    hour_dow = rr.hour_dow(db, user.id, tzm, covered, now - timedelta(days=365), None)
    hour_secs: dict[int, int] = {}
    for (_d, h), (secs, _sess) in hour_dow.items():
        hour_secs[h] = hour_secs.get(h, 0) + secs
    total_t = sum(hour_secs.values())
    if total_t > 0:
        # Circular mean with a 4am reading-day boundary (hours 0–3 roll to 24–27),
        # so a 1am session reads as "late", not "early morning".
        num = sum((h if h >= 4 else h + 24) * s for h, s in hour_secs.items())
        mean_hour = num / total_t
        traits["time"] = _lerp_score(mean_hour, 8, 24)  # 8am→early, midnight→night

    # ── Rhythm (sporadic ↔ consistent) — active-day density, last 120d ───────────
    # Reading-day buckets (4h rollover) so a midnight session doesn't split into
    # two active days, and the user's own "today" — not the UTC date.
    adays = rr.active_days(db, user.id, day_mod, covered)
    if adays:
        today = effective_today(tz_offset)
        window = 120
        first = min(adays)
        span = min(window, (today - first).days + 1)
        if span >= 14:  # need a couple of weeks before judging consistency
            recent = sum(1 for d in adays if 0 <= (today - d).days < window)
            traits["rhythm"] = _clamp(recent / span * 100)

    # ── Finished books power Length, Pace, Variety ───────────────────────────────
    finished = (
        db.query(Book.id, Book.author, Book.book_type_id, Book.word_count)
        .join(UserBookStatus, UserBookStatus.book_id == Book.id)
        .filter(
            UserBookStatus.user_id == user.id,
            UserBookStatus.status == "read",
            Book.status == "active",
            book_visibility_filter(db, user),
        )
        .all()
    )

    # Length (short ↔ long) — median finished-book word count.
    word_counts = [int(r.word_count) for r in finished if r.word_count]
    if len(word_counts) >= 3:
        # Calibrated to human norms: ~50k light novel → short, ~90k novel → neutral,
        # 150k+ → long epic. (Not to any one library's data.)
        traits["length"] = _lerp_score(statistics.median(word_counts), 30_000, 150_000)

    # Pace (savorer ↔ speed demon) — true WPM = words ÷ reconciled read-time.
    if word_counts:
        book_secs = rr.book_seconds(db, user.id, day_mod, covered, None, None)
        words_sum = secs_sum = counted = 0
        for r in finished:
            if not r.word_count:
                continue
            secs = int(book_secs.get(r.id, (0, 0, 0))[0])
            if secs >= _WPM_MIN_SECONDS:
                words_sum += int(r.word_count)
                secs_sum += secs
                counted += 1
        if counted >= 3 and secs_sum > 0:
            # ~250 reconciled WPM is an average reader (→ neutral); 140 savours,
            # 360+ is a speed demon. Human-calibrated, not seed-calibrated.
            traits["pace"] = _lerp_score(words_sum * 60 / secs_sum, 140, 360)

    # Variety (focused ↔ eclectic) — spread across authors & book types.
    if len(finished) >= 4:
        authors: dict[str, int] = {}
        types: dict[int, int] = {}
        for r in finished:
            a = (r.author or "").strip().lower()
            if a:
                authors[a] = authors.get(a, 0) + 1
            if r.book_type_id is not None:
                types[r.book_type_id] = types.get(r.book_type_id, 0) + 1

        def _spread(counts: dict) -> float | None:
            tot = sum(counts.values())
            if tot == 0:
                return None
            hhi = sum((c / tot) ** 2 for c in counts.values())  # 1 = one bucket
            return 1 - hhi

        divs = [d for d in (_spread(authors), _spread(types)) if d is not None]
        if divs:
            traits["variety"] = _clamp(sum(divs) / len(divs) * 100)

    # ── Assemble the card ────────────────────────────────────────────────────────
    trait_list = [
        {"key": k, "score": round(traits[k]), "low": _LABELS[k][0], "high": _LABELS[k][1]}
        for k in _ORDER
        if k in traits
    ]

    ranked = sorted(traits.items(), key=lambda kv: abs(kv[1] - 50), reverse=True)

    archetype: str | None = None
    if ranked and abs(ranked[0][1] - 50) >= _EXTREME_MIN:
        n_axis, n_val = ranked[0]
        noun = _NOUN[n_axis][1 if n_val >= 50 else 0]
        if len(ranked) >= 2 and abs(ranked[1][1] - 50) >= _EXTREME_MIN:
            m_axis, m_val = ranked[1]
            archetype = f"{_MOD[m_axis][1 if m_val >= 50 else 0]} {noun}"
        else:
            archetype = noun
    elif trait_list:
        archetype = "The Generalist"

    if archetype == "The Generalist":
        summary = "A bit of everything, at your own pace."
    else:
        tags = [
            _PHRASE[axis][1 if val >= 50 else 0]
            for axis, val in ranked[:3]
            if abs(val - 50) >= 8
        ]
        summary = (" · ".join(tags)[:1].upper() + " · ".join(tags)[1:] + ".") if tags else None

    # No window_days field: nothing consumed it, and no single window is honest —
    # time uses 365d, rhythm 120d, and length/pace/variety are all-time.
    return {
        "ready": bool(trait_list),
        "archetype": archetype,
        "summary": summary,
        "traits": trait_list,
    }
