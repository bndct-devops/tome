"""Cross-source candidate merging + relevance ranking for metadata fetching.

Two jobs, both previously scattered or missing:

- ``merge_candidates``: the same book returned by two sources used to occupy two
  of the five result slots, each *partially* filled (Hardcover knows series but
  not language; Google knows language but not series). Duplicates are detected
  by ISBN or by fuzzy title+author, and merged field-by-field into one complete
  candidate.
- ``score_candidate`` / ``rank_candidates``: one scoring brain for every
  consumer. Before this, only the bulk-review endpoint scored candidates —
  single fetch, bulk auto-apply, Bindery and auto-import all blindly trusted
  whatever the highest-priority source returned first.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

_SOURCE_PRIORITY = {"hardcover": 0, "google_books": 1, "open_library": 2, "anilist": 3}

_ART_WORDS = ("manga", "manhwa", "manhua", "webtoon", "comic", "graphic")
_PROSE_WORDS = ("novel", "book")


def classify_media_hint(hint: str | None) -> str | None:
    """Coarse edition class from a BookType slug — custom slugs included.

    Slugs are user-defined ("Webtoons", "graphic_novel", "LightNovels"), so
    exact matching only covers the seeded four. Normalize and substring-match
    instead. Returns "art" (sequential art), "light_novel" (a prose subclass —
    the only one that gets the retrieval-side query bias), "prose", or None.
    Art words win: "graphic_novel" is art despite containing "novel".
    """
    if not hint:
        return None
    h = re.sub(r"[^a-z]", "", hint.lower())
    if any(w in h for w in _ART_WORDS):
        return "art"
    if h == "ln" or ("light" in h and "novel" in h):
        return "light_novel"
    if any(w in h for w in _PROSE_WORDS):
        return "prose"
    return None


def extract_vol_number(title: str) -> int | None:
    """Volume number from a title: 'v001', 'Vol. 1', 'Vol 1', 'Volume 1'."""
    m = re.search(r"\bv(?:ol(?:ume)?\.?\s*)(\d+)\b", title, re.IGNORECASE)
    if m:
        return int(m.group(1))
    m = re.search(r"\bv(\d{2,4})\b", title, re.IGNORECASE)  # bare manga "vNNN"
    if m:
        return int(m.group(1))
    return None


@dataclass
class ScoreContext:
    """What we know about the book being matched (all optional but title)."""
    title: str | None = None
    author: str | None = None
    isbn: str | None = None
    year: int | None = None
    language: str | None = None
    series: str | None = None
    series_index: float | None = None
    # BookType slug ("manga", "light_novel", …) — lets ranking reject the wrong
    # EDITION of the right series (the manga adaptation of a light novel).
    media_hint: str | None = None


def score_candidate(candidate, ctx: ScoreContext) -> int:
    """Relevance of one candidate against the book context. Higher is better.

    Ported verbatim from the old books.py `_score_candidate` (same weights, so
    bulk-review behaviour is unchanged) with the Book model swapped for a plain
    context — the fetch service can't import models.
    """
    from backend.services.metadata_fetch import _clean_title

    score = 0
    if candidate.description:
        score += 2
    if candidate.cover_url:
        score += 1
    if candidate.isbn and ctx.isbn and candidate.isbn == ctx.isbn:
        score += 4
    if candidate.year and ctx.year and candidate.year == ctx.year:
        score += 1

    clean_book_title = _clean_title(ctx.title).lower() if ctx.title else ""
    if candidate.title and clean_book_title:
        ratio = difflib.SequenceMatcher(None, candidate.title.lower(), clean_book_title).ratio()
        if ratio > 0.85:
            score += 3
        elif ratio > 0.6:
            score += 1
    if candidate.author and ctx.author:
        ratio = difflib.SequenceMatcher(None, candidate.author.lower(), ctx.author.lower()).ratio()
        if ratio > 0.7:
            score += 2

    # Volume matching — critical for series where every title shares the name.
    book_vol = extract_vol_number(ctx.title) if ctx.title else None
    if book_vol is None and ctx.series_index is not None and float(ctx.series_index).is_integer():
        book_vol = int(ctx.series_index)
    if book_vol is not None:
        cand_vol = None
        if candidate.series_index is not None:
            cand_vol = int(candidate.series_index) if candidate.series_index == int(candidate.series_index) else None
        if cand_vol is None:
            cand_vol = extract_vol_number(candidate.title) if candidate.title else None
        if cand_vol == book_vol:
            score += 8
        else:
            score -= 4

    book_lang = (ctx.language or "en").lower()[:2]
    cand_lang = (candidate.language or "en").lower()[:2]
    if cand_lang == book_lang:
        score += 2
    elif cand_lang != "en":
        score -= 3

    if candidate.title:
        ct = candidate.title.lower()
        if "omnibus" in ct:
            score -= 3
        if "ace's story" in ct or "film:" in ct or "color walk" in ct:
            score -= 5

    # Wrong edition type of the right series: a light novel must not match the
    # manga adaptation (Hardcover titles them "… (Manga), Vol. N") and vice versa.
    hint_cls = classify_media_hint(ctx.media_hint)
    if hint_cls and candidate.title:
        ct = candidate.title.lower()
        cand_manga = "(manga)" in ct
        cand_ln = "(light novel)" in ct or "light novel" in ct
        if hint_cls in ("prose", "light_novel") and cand_manga:
            score -= 5
        elif hint_cls == "art" and cand_ln:
            score -= 5

    if candidate.source == "hardcover":
        score += 6

    return score


_MERGE_FIELDS = (
    "author", "description", "cover_url", "publisher", "year",
    "page_count", "isbn", "language", "series", "series_index",
)


def _fuzzy_key(candidate) -> tuple[str, str]:
    """Cross-source identity when ISBN is missing: normalized title + author surname."""
    t = re.sub(r"[^\w\s]", "", (candidate.title or "").lower())
    t = re.sub(r"\s+", " ", t).strip()
    a = (candidate.author or "").lower().split(",")[0].strip()
    surname = a.split()[-1] if a else ""
    return (t, surname)


def merge_candidates(candidates: list) -> list:
    """Collapse the same book seen by several sources into one candidate.

    Identity: shared ISBN, or fuzzy (normalized title, author surname). The
    highest-priority source's candidate is the base; missing fields are filled
    from the duplicates and tags are unioned — so the merged result is more
    complete than any single source's answer. Input order (source priority) is
    preserved for the survivors.
    """
    merged: list = []
    by_isbn: dict[str, int] = {}
    by_fuzzy: dict[tuple[str, str], int] = {}

    def _langs_compatible(a, b) -> bool:
        # A fuzzy title+author match across DIFFERENT languages is a different
        # EDITION (e.g. the Italian Fellowship of the Ring) — merging it would
        # poison the base candidate's language/ISBN. Unknown matches anything.
        la = (a.language or "").lower()[:2]
        lb = (b.language or "").lower()[:2]
        return not la or not lb or la == lb

    for c in candidates:
        idx = None
        if c.isbn and c.isbn in by_isbn:
            idx = by_isbn[c.isbn]
        else:
            fk = _fuzzy_key(c)
            if fk[0] and fk in by_fuzzy and _langs_compatible(merged[by_fuzzy[fk]], c):
                idx = by_fuzzy[fk]

        if idx is None:
            merged.append(c)
            idx = len(merged) - 1
        else:
            base = merged[idx]
            for f in _MERGE_FIELDS:
                if getattr(base, f) in (None, "") and getattr(c, f) not in (None, ""):
                    setattr(base, f, getattr(c, f))
            existing = {t.lower() for t in base.tags}
            base.tags.extend(t for t in c.tags if t.lower() not in existing)

        c2 = merged[idx]
        if c2.isbn:
            by_isbn[c2.isbn] = idx
        fk = _fuzzy_key(c2)
        if fk[0]:
            by_fuzzy[fk] = idx
    return merged


def rank_candidates(candidates: list, ctx: ScoreContext) -> list:
    """Sort by relevance, ties broken by source priority (stable)."""
    return sorted(
        candidates,
        key=lambda c: (-score_candidate(c, ctx), _SOURCE_PRIORITY.get(c.source, 9)),
    )
