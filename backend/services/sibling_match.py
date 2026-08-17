"""Sibling identity — inherit a canonical series identity from the library.

Scribe's single biggest quality trick, ported server-side: volume 12 of a
series whose volumes 1-11 already exist should inherit their exact series
name, author, book type and language instead of trusting whatever the file's
metadata or an external source says. This kills series-name drift ("Frieren -
Beyond Journey's End" vs "Frieren: Beyond Journey's End") and wrong-author
variants at ingest time.

Reviewed books outvote unreviewed ones when deriving the identity, so one bad
early auto-import can't poison later volumes once a human has confirmed any
volume of the series.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from backend.models.book import Book

# Same tier the duplicate finder and metadata ranking use for "same title".
MATCH_THRESHOLD = 0.85
# Below this, an author sharing a fuzzy series name is treated as a different
# series entirely (two authors can both write a "Genesis").
AUTHOR_CONFLICT_BELOW = 0.5

# Confidence tiers for applying an external metadata candidate (see
# backend/main.py auto-import). Scores come from metadata_rank.score_candidate.
FULL_APPLY_AT = 6      # overwrite title/author, fill the rest, take tags+cover
FILL_ONLY_AT = 3       # fill-if-empty description/publisher/year ONLY
                       # below: discard the candidate entirely


def apply_tier(score: int) -> str:
    """'full' | 'fill' | 'discard' for a candidate score."""
    if score >= FULL_APPLY_AT:
        return "full"
    if score >= FILL_ONLY_AT:
        return "fill"
    return "discard"


def _norm(s: str) -> str:
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _most_common(values: list) -> object | None:
    vals = [v for v in values if v is not None and v != ""]
    if not vals:
        return None
    return max(set(vals), key=vals.count)


@dataclass
class SeriesIdentity:
    series: str
    author: str | None
    book_type_id: int | None
    language: str | None
    library_ids: list[int] = field(default_factory=list)
    volume_count: int = 0
    from_reviewed: bool = False


def find_series_identity(
    db: Session,
    series: str | None,
    author: str | None = None,
) -> SeriesIdentity | None:
    """Fuzzy-match an incoming series name against series already in the DB
    and return their canonical identity, or None when nothing matches.

    Only ever matches on a parsed/extracted series name — guessing a series
    from a bare title is how wrong matches happen, so we don't.
    """
    if not series or not series.strip():
        return None
    target = _norm(series)
    if not target:
        return None

    names = [
        row[0]
        for row in db.query(Book.series)
        .filter(Book.series.isnot(None), Book.status == "active")
        .distinct()
        .all()
    ]
    best_name: str | None = None
    best_ratio = 0.0
    for name in names:
        ratio = difflib.SequenceMatcher(None, target, _norm(name)).ratio()
        if ratio > best_ratio:
            best_name, best_ratio = name, ratio
    if best_name is None or best_ratio < MATCH_THRESHOLD:
        return None

    siblings = (
        db.query(Book)
        .filter(Book.series == best_name, Book.status == "active")
        .all()
    )
    if not siblings:
        return None
    reviewed = [b for b in siblings if b.is_reviewed]
    pool = reviewed or siblings

    canonical_author = _most_common([b.author for b in pool])
    # Same series name, clearly different author → different series; refuse
    # the match rather than silently rewrite the attribution.
    if author and canonical_author:
        if difflib.SequenceMatcher(None, _norm(author), _norm(canonical_author)).ratio() < AUTHOR_CONFLICT_BELOW:
            return None

    return SeriesIdentity(
        series=best_name,
        author=canonical_author,
        book_type_id=_most_common([b.book_type_id for b in pool]),
        language=_most_common([b.language for b in pool]),
        library_ids=sorted({lib.id for b in pool for lib in b.libraries}),
        volume_count=len(siblings),
        from_reviewed=bool(reviewed),
    )
