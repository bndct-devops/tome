"""One identity gate: does a metadata record describe the same book?

Used wherever Tome would copy a fetched record onto a book without a person
looking first: unattended auto-import (``auto_import._enrich``) and the AI
"fix this book" second fetch (``ai.fix_book._refetch``). Both sides may be
dicts or objects with ``title`` / ``author`` and, optionally,
``series_index``. Series-agnostic otherwise.

The gate is deliberately strict: a wrong record applied unattended costs more
than a right record left for review.
"""
from __future__ import annotations

import difflib
import re
import unicodedata
from typing import Any, Mapping

from backend.services.metadata_rank import extract_vol_number

TITLE_RATIO = 0.6
AUTHOR_RATIO = 0.6
# A title that only shares letters (no whole word) must be this close: a
# spelling variant ("Harbour" / "Harbor"), not a word inside another word
# ("Ember" / "Remember Me").
TITLE_RATIO_NO_SHARED_WORD = 0.85
# Containment without a subtitle separator: the shorter title must carry at
# least this share of the longer one's words ("Dune" is not "Dune Messiah").
CONTAINMENT_TOKEN_SHARE = 0.6

_COLLECTION_RE = re.compile(r"\b(?:box\s*set|boxset|omnibus|collection|bundle|anthology)\b")
# Where a subtitle starts: "Title: Sub", "Title - Sub", "Title (Series #2)", "Title, Vol. 3".
_SUBTITLE_SEP_RE = re.compile(r"\s*(?::|\s[-–—]\s|\(|,)")
_TRAILING_NUMBER_RE = re.compile(r"(?:^|\s)(\d{1,4})$")
_WEAK_WORDS = frozenset({"the", "a", "an", "of", "and", "vol", "volume", "book", "part"})


def norm(text: str | None) -> str:
    """Casefolded, accents stripped (NFKD), every run of non-word characters
    (Unicode ``\\w``, so CJK and Cyrillic survive) to one space."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).casefold()
    text = re.sub(r"[^\w]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _field(obj: Any, name: str) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(name)
    return getattr(obj, name, None)


def _text(obj: Any, name: str) -> str | None:
    v = _field(obj, name)
    return v if isinstance(v, str) else None


def _ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio()


def _contains(a: str, b: str) -> bool:
    """Word-bounded containment either way ("harbour" is not in "harboured")."""
    if not a or not b:
        return False
    pa, pb = f" {a} ", f" {b} "
    return pa in pb or pb in pa


def _volume(obj: Any, raw_title: str, normed: str) -> float | None:
    """The volume a record claims: a "Vol. 3" / "Volume 3" / "v003" in the
    title, else a trailing number ("Lanternfall 13"), else ``series_index``."""
    n = extract_vol_number(raw_title)
    if n is not None:
        return float(n)
    m = _TRAILING_NUMBER_RE.search(normed)
    if m and normed != m.group(1):
        return float(m.group(1))
    si = _field(obj, "series_index")
    if isinstance(si, (int, float)) and not isinstance(si, bool):
        return float(si)
    return None


def _main_title(raw: str) -> str:
    """The part of a title before its first subtitle separator."""
    return norm(_SUBTITLE_SEP_RE.split(raw, maxsplit=1)[0])


def _titles_agree(raw_a: str, a: str, raw_b: str, b: str) -> bool:
    if a == b:
        return True
    if _contains(a, b):
        short, long_raw, long_norm = (a, raw_b, b) if len(a) <= len(b) else (b, raw_a, a)
        if _main_title(long_raw) == short:
            return True
        if len(short.split()) >= CONTAINMENT_TOKEN_SHARE * len(long_norm.split()):
            return True
        return False
    ratio = _ratio(a, b)
    if ratio >= TITLE_RATIO_NO_SHARED_WORD:
        return True
    shared = {w for w in set(a.split()) & set(b.split()) if not w.isdigit() and w not in _WEAK_WORDS}
    return bool(shared) and ratio >= TITLE_RATIO


def _authors_agree(a: str, b: str) -> bool:
    if _contains(a, b):
        return True
    sa, sb = " ".join(sorted(a.split())), " ".join(sorted(b.split()))
    return max(_ratio(a, b), _ratio(sa, sb)) >= AUTHOR_RATIO


def agrees(identity: Any, candidate: Any) -> bool:
    """True when ``candidate`` describes the same book as ``identity``.

    - A title that normalises to nothing or to digits only is not comparable.
    - Volume: each side's number (title, else ``series_index``); both known
      and different = False.
    - A candidate that is a box set / omnibus / collection / bundle /
      anthology when the identity is not = False.
    - Title: equal; or word-bounded containment where the extra text starts
      at a subtitle separator (":", " - ", "(", ",") or the shorter title has
      at least 60% of the longer one's words; or ratio >= 0.6 with a shared
      word; or ratio >= 0.85 (spelling variants).
    - Author: missing on either side passes; else word-bounded containment or
      ratio >= 0.6 on the text or its sorted words ("Vale, M." / "M. Vale").
    """
    raw_it = _text(identity, "title") or ""
    raw_ct = _text(candidate, "title") or ""
    it, ct = norm(raw_it), norm(raw_ct)
    if not it or not ct or it.replace(" ", "").isdigit() or ct.replace(" ", "").isdigit():
        return False

    iv, cv = _volume(identity, raw_it, it), _volume(candidate, raw_ct, ct)
    if iv is not None and cv is not None and iv != cv:
        return False

    if _COLLECTION_RE.search(ct) and not _COLLECTION_RE.search(it):
        return False

    if not _titles_agree(raw_it, it, raw_ct, ct):
        return False

    ia, ca = norm(_text(identity, "author")), norm(_text(candidate, "author"))
    if not ia or not ca:
        return True
    return _authors_agree(ia, ca)
