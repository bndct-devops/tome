"""
External metadata fetching.
Sources: Hardcover (primary), Google Books, Open Library — all queried in parallel.
Returns a list of MetadataCandidate objects for the user to review.
"""
import asyncio
import copy
import logging
import re
import time
from dataclasses import dataclass, field

import httpx

from backend.core.config import settings

logger = logging.getLogger(__name__)

GOOGLE_BOOKS_URL = "https://www.googleapis.com/books/v1/volumes"
OPEN_LIBRARY_SEARCH = "https://openlibrary.org/search.json"
OPEN_LIBRARY_WORK = "https://openlibrary.org{key}.json"
OPEN_LIBRARY_COVER = "https://covers.openlibrary.org/b/id/{cover_id}-L.jpg"
HARDCOVER_URL = "https://api.hardcover.app/v1/graphql"
ANILIST_URL = "https://graphql.anilist.co"

_MAX_RESULTS = 5


@dataclass
class MetadataCandidate:
    source: str          # "hardcover" | "google_books" | "open_library"
    source_id: str       # Google volumeId or OL work key
    title: str
    author: str | None = None
    description: str | None = None
    cover_url: str | None = None
    publisher: str | None = None
    year: int | None = None
    page_count: int | None = None
    isbn: str | None = None
    language: str | None = None
    tags: list[str] = field(default_factory=list)
    series: str | None = None
    series_index: float | None = None


@dataclass
class FetchResult:
    candidates: list[MetadataCandidate]
    query_used: str  # the query sent to Hardcover (for display / manual re-search)
    # Per-source outcome: ok | empty | rate_limited | timeout | error | disabled.
    # Surfaced to the UI so a rate-limited fetch can't masquerade as "no results".
    sources: dict = field(default_factory=dict)


class RateLimited(Exception):
    """A source told us to back off (429 / exhausted quota)."""


# Short-lived per-source result cache. Re-opening the fetch dialog, retrying
# after a rate limit, or bulk-fetching a series (whose volumes share the
# series-level AniList lookup) should not re-hit the external APIs. Keyed by
# (source, query); successful results only — failures always retry live.
# Single-process in-memory, same assumption as the bake job.
_CACHE_TTL = 900.0   # 15 minutes
_CACHE_MAX = 512
_source_cache: dict[tuple, tuple[float, list[MetadataCandidate]]] = {}


def _cache_get(key: tuple) -> list[MetadataCandidate] | None:
    hit = _source_cache.get(key)
    if not hit or hit[0] < time.monotonic():
        _source_cache.pop(key, None)
        return None
    # Deep copy both ways — merge_candidates MUTATES candidates (field fill).
    return copy.deepcopy(hit[1])


def _cache_put(key: tuple, candidates: list[MetadataCandidate]) -> None:
    if len(_source_cache) >= _CACHE_MAX:
        for k in sorted(_source_cache, key=lambda k: _source_cache[k][0])[: _CACHE_MAX // 4]:
            _source_cache.pop(k, None)
    _source_cache[key] = (time.monotonic() + _CACHE_TTL, copy.deepcopy(candidates))


# In-flight request dedup: bulk-fetching a series fires the SAME series-level
# query for every volume concurrently — the TTL cache alone can't help because
# none of them has finished yet. Followers await the leader's future instead
# of going out themselves.
_inflight: dict[tuple, "asyncio.Future"] = {}


async def _attempt_source(name: str, factory) -> tuple[list[MetadataCandidate], str]:
    """One source, one retry on rate-limit/timeout; never raises (except
    cancellation) — failures degrade to ([], honest_status)."""
    for attempt in (1, 2):
        try:
            res = await factory()
            return res, ("ok" if res else "empty")
        except RateLimited:
            if attempt == 1:
                await asyncio.sleep(1.5)
                continue
            logger.warning("%s still rate-limited after retry", name)
            return [], "rate_limited"
        except httpx.TimeoutException:
            if attempt == 1:
                continue
            logger.warning("%s timed out twice", name)
            return [], "timeout"
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("%s metadata fetch failed: %s", name, exc)
            return [], "error"
    return [], "error"


async def _call_with_retry(name: str, factory, cache_key=None) -> tuple[list[MetadataCandidate], str]:
    """Run one source with retry, short-TTL caching and in-flight dedup.

    ``factory`` builds a fresh coroutine per attempt. Returns (candidates,
    status). ``cache_key`` (usually the query string) enables the cache —
    empty-but-successful results are cached too, so a book no source knows
    isn't re-searched every time — and makes concurrent identical calls share
    one outbound request.
    """
    key = (name, cache_key) if cache_key is not None else None
    if key is not None:
        cached = _cache_get(key)
        if cached is not None:
            return cached, ("ok" if cached else "empty")
        pending = _inflight.get(key)
        if pending is not None:
            res, status = await pending
            return copy.deepcopy(res), status

    fut: asyncio.Future | None = None
    if key is not None:
        fut = asyncio.get_running_loop().create_future()
        _inflight[key] = fut
    try:
        res, status = await _attempt_source(name, factory)
        if key is not None and status in ("ok", "empty"):
            _cache_put(key, res)
        if fut is not None:
            fut.set_result((copy.deepcopy(res), status))
        return res, status
    except BaseException:
        # cancellation — release followers with an honest error, don't wedge them
        if fut is not None and not fut.done():
            fut.set_result(([], "error"))
        raise
    finally:
        if key is not None:
            _inflight.pop(key, None)


async def fetch_candidates(
    title: str,
    author: str | None = None,
    isbn: str | None = None,
    series: str | None = None,
    series_index: float | None = None,
    query_override: str | None = None,
    year: int | None = None,
    language: str | None = None,
    media_hint: str | None = None,
) -> FetchResult:
    """Query Hardcover, Google Books and OpenLibrary in parallel; return up to
    _MAX_RESULTS candidates, cross-source merged and relevance-ranked.

    Duplicates across sources are collapsed into ONE candidate whose missing
    fields are filled from the others (Hardcover has series but no language;
    Google has language but no series — merged, you get both). The result is
    sorted by the same score the bulk-review UI uses, so ``candidates[0]`` is
    the best match on every consumer path, not "whatever Hardcover said first".
    ``year``/``language`` are optional ranking context.
    """
    from backend.services.metadata_rank import ScoreContext, merge_candidates, rank_candidates, classify_media_hint

    # When the user has typed a manual query, ignore the stored ISBN entirely —
    # treat it like Plex "Fix Match": search only by what was typed.
    effective_isbn = None if query_override else isbn
    query = _build_query(title, author, effective_isbn, series, series_index, query_override)
    hc_query = _build_hardcover_query(title, author, effective_isbn, series, series_index, query_override, media_hint)

    hint_cls = classify_media_hint(media_hint)

    async with httpx.AsyncClient(timeout=10) as client:
        async def _hc_disabled() -> tuple[list[MetadataCandidate], str]:
            return [], "disabled"

        async def _al_skipped() -> tuple[list[MetadataCandidate], str]:
            return [], "skipped"

        hc_call = (
            _call_with_retry("hardcover", lambda: _hardcover(
                client, title, author, effective_isbn, series, series_index, query_override, media_hint),
                cache_key=hc_query)
            if settings.hardcover_token else _hc_disabled()
        )
        # AniList is manga-specialised (no key needed) — only worth a call for
        # sequential-art book types. It knows series, staff, genres and covers
        # far better than the prose-first sources, but at SERIES level.
        al_query = query_override or series or title
        al_call = (
            _call_with_retry("anilist", lambda: _anilist(client, al_query), cache_key=al_query)
            if hint_cls == "art" else _al_skipped()
        )
        (hc, hc_status), (gb, gb_status), (ol, ol_status), (al, al_status) = await asyncio.gather(
            hc_call,
            _call_with_retry("google_books", lambda: _google_books(client, query, effective_isbn),
                             cache_key=(query, effective_isbn)),
            _call_with_retry("open_library", lambda: _open_library(client, query, effective_isbn),
                             cache_key=(query, effective_isbn)),
            al_call,
        )

        # Series-aware fallback: when the per-book title is obscure, retry empty
        # sources with a "{series} Vol N" query — including Hardcover, which the
        # old fallback skipped entirely.
        if (
            not query_override and not isbn
            and series and series_index is not None
            and not _title_is_series_variant(title, series)
        ):
            fallback_query = _build_series_query(series, series_index, author)
            if not gb and not ol:
                (gb, gb_status), (ol, ol_status) = await asyncio.gather(
                    _call_with_retry("google_books", lambda: _google_books(client, fallback_query, None),
                                     cache_key=(fallback_query, None)),
                    _call_with_retry("open_library", lambda: _open_library(client, fallback_query, None),
                                     cache_key=(fallback_query, None)),
                )
            if not hc and hc_status not in ("disabled", "rate_limited"):
                hc, hc_status = await _call_with_retry("hardcover", lambda: _hardcover(
                    client, title, author, None, series, series_index, fallback_query),
                    cache_key=fallback_query)

        # Suspect-ISBN fallback: a stored ISBN belonging to the WRONG edition
        # (the manga's ISBN on a light novel — old auto-apply versions caused
        # exactly this) monopolises retrieval, and ranking can't demote what
        # search never returned. If every Hardcover hit violates the media
        # hint, re-query by title/series with the edition bias and prepend.
        def _violates_hint(c: MetadataCandidate) -> bool:
            if not hint_cls or not c.title:
                return False
            ct = c.title.lower()
            if hint_cls in ("prose", "light_novel"):
                return "(manga)" in ct
            if hint_cls == "art":
                return "(light novel)" in ct
            return False

        if (
            not query_override and effective_isbn and media_hint
            and hc and all(_violates_hint(c) for c in hc)
        ):
            hc2, _st = await _call_with_retry("hardcover", lambda: _hardcover(
                client, title, author, None, series, series_index, None, media_hint),
                cache_key=_build_hardcover_query(title, author, None, series, series_index, None, media_hint))
            if hc2:
                hc = [*hc2, *hc]

    merged = merge_candidates([*hc, *gb, *ol, *al])
    if al:
        _fill_from_anilist(merged, al)
    ranked = rank_candidates(merged, ScoreContext(
        title=title, author=author, isbn=effective_isbn,
        year=year, language=language, series=series, series_index=series_index,
        media_hint=media_hint,
    ))
    sources = {"hardcover": hc_status, "google_books": gb_status, "open_library": ol_status}
    if al_status != "skipped":
        sources["anilist"] = al_status
    return FetchResult(
        candidates=ranked[:_MAX_RESULTS],
        query_used=hc_query,
        sources=sources,
    )


# ── AniList (manga) ───────────────────────────────────────────────────────────

_ANILIST_QUERY = """
query ($search: String) {
  Page(perPage: 3) {
    media(search: $search, type: MANGA) {
      id
      title { english romaji }
      description(asHtml: false)
      coverImage { extraLarge large }
      startDate { year }
      genres
      countryOfOrigin
      staff(perPage: 6) { edges { role node { name { full } } } }
    }
  }
}
"""


def _strip_anilist_markup(text: str | None) -> str | None:
    """AniList descriptions carry light HTML (<br>, <i>) and source credits."""
    if not text:
        return None
    text = re.sub(r"<br\s*/?>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\(Source:[^)]*\)\s*$", "", text.strip()).strip()
    return text or None


async def _anilist(client: httpx.AsyncClient, search: str) -> list[MetadataCandidate]:
    """Search AniList for manga SERIES metadata (no API key required).

    AniList models series, not volumes — the candidate carries series-level
    description/staff/genres/cover. It both appears as its own candidate and
    back-fills missing fields on volume-level hits via ``_fill_from_anilist``.
    """
    resp = await client.post(ANILIST_URL, json={
        "query": _ANILIST_QUERY, "variables": {"search": search},
    })
    if resp.status_code == 429:
        raise RateLimited("anilist 429")
    resp.raise_for_status()
    media = (resp.json().get("data") or {}).get("Page", {}).get("media") or []

    out: list[MetadataCandidate] = []
    for m in media:
        titles = m.get("title") or {}
        name = titles.get("english") or titles.get("romaji")
        if not name:
            continue
        author = None
        for edge in (m.get("staff") or {}).get("edges") or []:
            role = (edge.get("role") or "").lower()
            if "story" in role or "original creator" in role:
                author = ((edge.get("node") or {}).get("name") or {}).get("full")
                break
        cover = (m.get("coverImage") or {})
        out.append(MetadataCandidate(
            source="anilist",
            source_id=str(m.get("id")),
            title=name,
            author=author,
            description=_strip_anilist_markup(m.get("description")),
            cover_url=cover.get("extraLarge") or cover.get("large"),
            year=(m.get("startDate") or {}).get("year"),
            tags=list(m.get("genres") or []),
            series=name,
        ))
    return out


def _norm_series(name: str | None) -> str:
    n = re.sub(r"[^\w\s]", "", (name or "").lower())
    return re.sub(r"\s+", " ", n).strip()


def _fill_from_anilist(candidates: list[MetadataCandidate], al: list[MetadataCandidate]) -> None:
    """Back-fill missing description/author/tags on volume-level candidates from
    the matching AniList series. Manga volumes are the books most often missing
    a description in the prose-first sources; the series blurb beats nothing.
    Covers are NOT filled — AniList's is the series (vol 1) art.
    """
    by_series = {_norm_series(a.title): a for a in al}
    for c in candidates:
        if c.source == "anilist":
            continue
        key = _norm_series(c.series)
        a = by_series.get(key)
        if a is None and c.title:
            # no series on the candidate — try "title starts with series name"
            nt = _norm_series(c.title)
            a = next((v for k, v in by_series.items() if k and nt.startswith(k)), None)
        if a is None:
            continue
        if not c.description and a.description:
            c.description = a.description
        if not c.author and a.author:
            c.author = a.author
        if a.tags:
            existing = {t.lower() for t in c.tags}
            c.tags.extend(t for t in a.tags if t.lower() not in existing)


# ── Hardcover ─────────────────────────────────────────────────────────────────

def _build_hardcover_query(
    title: str,
    author: str | None,
    isbn: str | None,
    series: str | None,
    series_index: float | None,
    query_override: str | None = None,
    media_hint: str | None = None,
) -> str:
    """Build the query string sent to Hardcover's Typesense search."""
    if query_override:
        return query_override
    clean_title = _clean_title(title)
    if isbn:
        return isbn
    if series and series_index is not None and _title_is_series_variant(title, series):
        clean = _clean_series_name(series)
        vol = int(series_index) if series_index == int(series_index) else series_index
        # Retrieval-side edition bias: Hardcover titles LN editions
        # "... Vol. N (light novel)" and manga ones "(Manga)". For a
        # series-variant query ("Slime 13") Typesense often surfaces ONLY the
        # wrong edition — ranking can't fix what search never returned.
        from backend.services.metadata_rank import classify_media_hint
        if classify_media_hint(media_hint) == "light_novel":
            return f"{clean} {vol} light novel"
        return f"{clean} {vol}"
    vol_match = re.search(r'\bv(\d{2,4})\b', title, re.IGNORECASE)
    if vol_match:
        vol_num = int(vol_match.group(1))
        return f"{clean_title} {vol_num}"
    if author:
        return f"{clean_title} {author}"
    return clean_title


async def _hardcover(
    client: httpx.AsyncClient,
    title: str,
    author: str | None,
    isbn: str | None,
    series: str | None,
    series_index: float | None,
    query_override: str | None = None,
    media_hint: str | None = None,
) -> list[MetadataCandidate]:
    """Fetch candidates from Hardcover.app GraphQL API."""
    token = settings.hardcover_token
    if not token:
        return []

    query_str = _build_hardcover_query(title, author, isbn, series, series_index, query_override, media_hint)

    graphql_query = """
    query SearchBook($q: String!, $perPage: Int!) {
        search(query: $q, query_type: "Book", per_page: $perPage) {
            ids
            results
        }
    }
    """

    headers = {"authorization": token}
    # Failures propagate — _call_with_retry classifies them (retry on 429/timeout)
    # so the UI can distinguish "rate limited" from "book doesn't exist".
    resp = await client.post(
        HARDCOVER_URL,
        json={"query": graphql_query, "variables": {"q": query_str, "perPage": _MAX_RESULTS}},
        headers=headers,
    )
    if resp.status_code == 429:
        raise RateLimited("hardcover")
    resp.raise_for_status()
    data = resp.json()

    search = data.get("data", {}).get("search", {})
    hits = search.get("results", {}).get("hits", [])
    hc_ids = search.get("ids", [])

    if not hits:
        return []

    candidates: list[MetadataCandidate] = []
    for hit in hits:
        doc = hit.get("document", {})
        c = _parse_hardcover(doc)
        if _is_useful(c):
            candidates.append(c)

    # Post-filter: discard candidates that don't match the series name.
    # This catches cases where Hardcover's fuzzy search returns unrelated books.
    if series and candidates:
        series_lower = series.lower()
        # Use significant words (3+ chars) from series name for matching
        series_words = {w for w in series_lower.split() if len(w) >= 3}
        filtered = []
        for c in candidates:
            c_title_lower = c.title.lower()
            # Match if the series name appears in the title, OR if most series words do
            if series_lower in c_title_lower:
                filtered.append(c)
            elif series_words and sum(1 for w in series_words if w in c_title_lower) >= len(series_words) * 0.6:
                filtered.append(c)
        if filtered:
            candidates = filtered
        # If filter removed everything, keep originals — better than nothing

    if candidates and hc_ids:
        await _fetch_hardcover_details(client, candidates, hc_ids, headers)

    return candidates


async def search_series(q: str) -> list[dict]:
    """Search Hardcover for SERIES entities (not books).

    Returns dicts with a canonical series id, name, author, and the *true*
    volume count — ``primary_books_count`` (distinct volumes), not the
    edition-inflated ``books_count``. E.g. "The Good Guys" by Eric Ugland comes
    back with total=16, not 25. Empty when no token / no hits.
    """
    token = settings.hardcover_token
    if not token:
        return []

    graphql_query = """
    query SearchSeries($q: String!, $perPage: Int!) {
        search(query: $q, query_type: "Series", per_page: $perPage) {
            results
        }
    }
    """
    # Second query: the series search doc carries no cover, so fetch each
    # series' first volume's cover (a series is naturally represented by vol 1).
    covers_query = """
    query SeriesCovers($ids: [Int!]!) {
        series(where: {id: {_in: $ids}}) {
            id
            book_series(order_by: {position: asc}, limit: 1) {
                book { image { url } }
            }
        }
    }
    """
    headers = {"authorization": token}
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                HARDCOVER_URL,
                json={"query": graphql_query, "variables": {"q": q, "perPage": _MAX_RESULTS}},
                headers=headers,
            )
            if resp.status_code == 429:
                logger.warning("Hardcover rate limited (series)")
                return []
            resp.raise_for_status()
            data = resp.json()

            hits = data.get("data", {}).get("search", {}).get("results", {}).get("hits", [])
            out: list[dict] = []
            for hit in hits:
                doc = hit.get("document", {})
                name = doc.get("name")
                sid = doc.get("id")
                if not name or sid is None:
                    continue
                total = doc.get("primary_books_count") or None
                out.append({
                    "source": "hardcover",
                    "source_id": str(sid),
                    "name": name,
                    "author": doc.get("author_name"),
                    "total": int(total) if total else None,
                    "slug": doc.get("slug"),
                    "cover_url": None,
                })

            # Best-effort vol-1 covers in one batched query.
            ids = [int(e["source_id"]) for e in out if e["source_id"].isdigit()]
            if ids:
                try:
                    cresp = await client.post(
                        HARDCOVER_URL,
                        json={"query": covers_query, "variables": {"ids": ids}},
                        headers=headers,
                    )
                    cresp.raise_for_status()
                    cdata = cresp.json()
                    covers: dict[str, str] = {}
                    for s in (cdata.get("data", {}).get("series") or []):
                        bs = s.get("book_series") or []
                        if bs:
                            img = (bs[0].get("book") or {}).get("image") or {}
                            if img.get("url"):
                                covers[str(s.get("id"))] = img["url"]
                    for e in out:
                        e["cover_url"] = covers.get(e["source_id"])
                except Exception as exc:
                    logger.warning("Hardcover series covers failed: %s", exc)
    except Exception as exc:
        logger.warning("Hardcover series request failed: %s", exc)
        return []

    return out


def _parse_hardcover(doc: dict) -> MetadataCandidate:
    # Extract primary author — skip illustrators/editors
    author_name: str | None = None
    author_names = doc.get("author_names", [])
    contribution_types = doc.get("contribution_types", [])
    for i, name in enumerate(author_names):
        ctype = contribution_types[i] if i < len(contribution_types) else None
        if ctype is None or ctype == "Author":
            author_name = name
            break
    if not author_name and author_names:
        author_name = author_names[0]

    # Prefer ISBN-13 (13 digits)
    isbn_val: str | None = None
    for i_val in doc.get("isbns", []):
        s = str(i_val)
        if len(s) == 13:
            isbn_val = s
            break
    if not isbn_val:
        for i_val in doc.get("isbns", []):
            s = str(i_val)
            if len(s) == 10:
                isbn_val = s
                break

    image = doc.get("image") or {}
    cover_url = image.get("url") if image else None

    return MetadataCandidate(
        source="hardcover",
        source_id=str(doc.get("id", "")),
        title=doc.get("title", ""),
        author=author_name,
        description=_clean_html(doc.get("description", "")),
        cover_url=cover_url,
        publisher=None,  # populated by _fetch_hardcover_publishers
        year=doc.get("release_year"),
        page_count=doc.get("pages"),
        isbn=isbn_val,
        language=None,
        tags=doc.get("genres", []),
    )


async def _fetch_hardcover_details(
    client: httpx.AsyncClient,
    candidates: list[MetadataCandidate],
    hc_ids: list[str],
    headers: dict,
) -> None:
    """Fetch publisher and series info from Hardcover for each candidate, in-place.

    The search response's ``ids`` array holds INTS while document ids parse as
    STRINGS — compare as strings, or nothing ever matches (this exact mismatch
    made the details call a silent no-op in production: Hardcover series and
    publisher were never filled).
    """
    id_to_candidate = {c.source_id: c for c in candidates}
    int_ids = [int(i) for i in hc_ids if str(i) in id_to_candidate]
    if not int_ids:
        return

    query = """
    query GetBookDetails($ids: [Int!]!) {
        books(where: {id: {_in: $ids}}) {
            id
            editions(limit: 1, order_by: {users_count: desc}) {
                publisher {
                    name
                }
            }
            book_series {
                series {
                    name
                }
                position
            }
        }
    }
    """
    try:
        resp = await client.post(
            HARDCOVER_URL,
            json={"query": query, "variables": {"ids": int_ids}},
            headers=headers,
        )
        resp.raise_for_status()
        data = resp.json()
        for book in data.get("data", {}).get("books", []):
            book_id = str(book["id"])
            if book_id not in id_to_candidate:
                continue
            candidate = id_to_candidate[book_id]

            # Publisher
            editions = book.get("editions", [])
            if editions and editions[0].get("publisher"):
                pub_name = editions[0]["publisher"].get("name")
                if pub_name:
                    candidate.publisher = pub_name

            # Series
            book_series = book.get("book_series", [])
            if book_series:
                first = book_series[0]
                series_obj = first.get("series")
                if series_obj and series_obj.get("name"):
                    candidate.series = series_obj["name"]
                position = first.get("position")
                if position is not None:
                    try:
                        candidate.series_index = float(position)
                    except (ValueError, TypeError):
                        pass
    except Exception:
        logger.warning("Failed to fetch Hardcover details", exc_info=True)


def _build_series_query(series: str, series_index: float, author: str | None) -> str:
    clean = _clean_series_name(series)
    vol = int(series_index) if series_index == int(series_index) else series_index
    base = f"{clean} Vol {vol}"
    first_author = _first_author_token(author)
    if first_author:
        return f"intitle:{base} inauthor:{first_author}"
    return f"intitle:{base}"


# ── Query construction ────────────────────────────────────────────────────────

def _build_query(
    title: str,
    author: str | None,
    isbn: str | None,
    series: str | None,
    series_index: float | None,
    override: str | None,
) -> str:
    if override:
        return override

    # ISBN search is most precise — use it alone, no title/author noise
    if isbn:
        return f"isbn:{isbn}"

    first_author = _first_author_token(author)

    # Series-aware query: only when the title is a variant of the series name.
    # e.g. "The Lord of the Rings" (title == series) or
    # "The Chronicles of Narnia, Vol. 1" (title starts with series).
    # NOT for books like "The Lion, the Witch and the Wardrobe" (series: "Narnia")
    # — those have unique per-book titles that are better search terms.
    if series and series_index is not None and _title_is_series_variant(title, series):
        clean = _clean_series_name(series)
        vol = int(series_index) if series_index == int(series_index) else series_index
        base = f"{clean} Vol {vol}"
        if first_author:
            return f"intitle:{base} inauthor:{first_author}"
        return f"intitle:{base}"

    # Detect "vNNN" volume pattern in title (common in filenames like "My Series v092 (2019) (Digital)")
    # and build a volume-aware query instead of sending the raw noisy title
    vol_match = re.search(r'\bv(\d{2,4})\b', title, re.IGNORECASE)
    if vol_match:
        vol_num = int(vol_match.group(1))
        clean_title = _clean_title(title)
        base = f"{clean_title} Vol {vol_num}"
        if first_author:
            return f"intitle:{base} inauthor:{first_author}"
        return f"intitle:{base}"

    # Unique per-book title: clean it and search normally
    clean_title = _clean_title(title)
    if first_author:
        return f"intitle:{_trunc(clean_title)} inauthor:{first_author}"
    return f"intitle:{_trunc(clean_title)}"


def _title_is_series_variant(title: str, series: str) -> bool:
    """Return True if the title is essentially the series name (possibly with volume info).

    True:  "The Lord of the Rings" vs series "The Lord of the Rings"
    True:  "The Chronicles of Narnia, Vol. 1" vs series "The Chronicles of Narnia"
    False: "The Lion, the Witch and the Wardrobe" vs series "Narnia"
    """
    t = title.lower().strip()
    s = series.lower().strip()
    if not s:
        return False
    # Exact match or title starts with the series name
    prefix = s[:min(len(s), 20)]
    return t == s or t.startswith(prefix)


def _clean_series_name(series: str) -> str:
    """Strip subtitle suffixes like ' -Starting Life in Another World-' or ' - Subtitle'."""
    s = series.strip()
    s = re.sub(r'\s+[-\u2013]\s+.+$', '', s).strip()
    s = re.sub(r'\s+-[^-].*$', '', s).strip()
    return s.rstrip(',-: ')


def _clean_title(title: str) -> str:
    """Strip common epub title noise before using as a search query."""
    s = title
    # Strip LitRPG/Gamelit genre suffixes e.g. "Title A LitRPGGamelit Adventure"
    s = re.sub(r'\s+[Aa]\s+(LitRPG|Gamelit|GameLit|LitRpg|Lit RPG).*$', '', s)
    # Strip subtitle patterns after " - " (e.g. "Title - Full Subtitle Here")
    s = re.sub(r'\s+[-\u2013]\s+\w+(\s+\w+){2,}$', '', s)
    # Strip volume markers already captured as series_index
    s = re.sub(r',?\s+[Vv]ol(?:ume)?\.?\s*\d+.*$', '', s)
    # Strip short "v001" style volume markers (common in manga filenames)
    s = re.sub(r'\s+[Vv]\d{2,4}\b', '', s)
    # Strip all parenthesized groups (year, release group, quality tags, etc.)
    s = re.sub(r'\s*\([^)]*\)', '', s)
    return s.strip()


def _first_author_token(author: str | None) -> str | None:
    """Return just the first meaningful token from an author string.

    Avoids inauthor noise from multi-author strings like 'Jane Austen and Seth Grahame-Smith'
    or 'Brian Herbert, Kevin J. Anderson'.
    """
    if not author:
        return None
    # Split on common separators
    parts = re.split(r'\s+and\s+|,\s*|;\s*|&\s*', author, maxsplit=1)
    first = parts[0].strip()
    # If it's a full name, use the last token (surname) — better for inauthor:
    tokens = first.split()
    if len(tokens) >= 2:
        return _trunc(tokens[-1], 20)
    return _trunc(first, 20) if first else None


def _trunc(s: str, max_len: int = 60) -> str:
    return s[:max_len]


# ── Google Books ──────────────────────────────────────────────────────────────

async def _google_books(
    client: httpx.AsyncClient,
    query: str,
    isbn: str | None = None,
) -> list[MetadataCandidate]:
    params: dict[str, str | int] = {
        "q": query,
        "maxResults": _MAX_RESULTS,
        "printType": "books",
    }
    if settings.google_books_key:
        params["key"] = settings.google_books_key
    resp = await client.get(GOOGLE_BOOKS_URL, params=params)
    if resp.status_code in (400, 403, 429):
        # 400 is how Google reports an exhausted key quota ("Quota Exceeded");
        # 429 is the anonymous shared-pool limit; 403 shows up for "usage
        # limits" bursts on the anonymous pool. Log loudly when a key is
        # configured so the operator knows it's *their* project quota that's
        # drained, not a code bug — then let the retry/status machinery run.
        if settings.google_books_key:
            logger.warning(
                "Google Books quota exhausted for the configured API key "
                "(HTTP %s)", resp.status_code,
            )
        else:
            logger.warning("Google Books rate limited (HTTP %s) — no API key "
                           "configured; set TOME_GOOGLE_BOOKS_KEY", resp.status_code)
        raise RateLimited("google_books")
    resp.raise_for_status()
    data = resp.json()

    candidates = []
    for item in data.get("items", []):
        info = item.get("volumeInfo", {})
        c = _parse_google(item["id"], info)
        if _is_useful(c):
            candidates.append(c)

    return candidates


def _parse_google(volume_id: str, info: dict) -> MetadataCandidate:
    images = info.get("imageLinks", {})
    cover = (
        images.get("extraLarge")
        or images.get("large")
        or images.get("medium")
        or images.get("thumbnail")
    )
    if cover:
        cover = cover.replace("http://", "https://").replace("&edge=curl", "")
        cover = re.sub(r"zoom=\d", "zoom=3", cover)

    isbns = info.get("industryIdentifiers", [])
    isbn13 = next((x["identifier"] for x in isbns if x["type"] == "ISBN_13"), None)
    isbn10 = next((x["identifier"] for x in isbns if x["type"] == "ISBN_10"), None)

    year = None
    raw_date = info.get("publishedDate", "")
    if raw_date:
        m = re.search(r'\d{4}', raw_date)
        if m:
            year = int(m.group())

    authors = info.get("authors", [])

    return MetadataCandidate(
        source="google_books",
        source_id=volume_id,
        title=info.get("title", ""),
        author=", ".join(authors) if authors else None,
        description=_clean_html(info.get("description", "")),
        cover_url=cover,
        publisher=info.get("publisher"),
        year=year,
        page_count=info.get("pageCount") or None,
        isbn=isbn13 or isbn10,
        language=info.get("language"),
        tags=[c for c in info.get("categories", []) if c],
    )


# ── Open Library ─────────────────────────────────────────────────────────────

async def _open_library(
    client: httpx.AsyncClient,
    query: str,
    isbn: str | None = None,
) -> list[MetadataCandidate]:
    params: dict = {
        "limit": _MAX_RESULTS,
        "fields": "key,title,author_name,first_publish_year,isbn,publisher,cover_i,subject,number_of_pages_median,language",
    }
    if isbn:
        params["isbn"] = isbn
    else:
        # OL search doesn't support intitle:/inauthor: — strip those operators and use plain text
        plain = re.sub(r'\b(intitle|inauthor|isbn):', '', query).strip()
        params["q"] = plain

    # Failures propagate to _call_with_retry for classification/retry.
    resp = await client.get(OPEN_LIBRARY_SEARCH, params=params)
    if resp.status_code == 429:
        raise RateLimited("open_library")
    resp.raise_for_status()
    data = resp.json()

    docs = data.get("docs", [])
    candidates = [_parse_ol(doc) for doc in docs]
    candidates = [c for c in candidates if _is_useful(c)]

    # Fetch descriptions from work endpoints in parallel
    if candidates:
        keys = [c.source_id for c in candidates if c.source_id]
        descs = await asyncio.gather(
            *[_fetch_ol_description(client, k) for k in keys],
            return_exceptions=True,
        )
        for c, desc in zip(candidates, descs):
            if isinstance(desc, str) and desc:
                c.description = desc

    return candidates


def _parse_ol(doc: dict) -> MetadataCandidate:
    cover_id = doc.get("cover_i")
    cover_url = OPEN_LIBRARY_COVER.format(cover_id=cover_id) if cover_id else None

    authors = doc.get("author_name", [])
    isbns = doc.get("isbn", [])
    isbn13 = next((i for i in isbns if len(i) == 13), None)
    isbn10 = next((i for i in isbns if len(i) == 10), None)

    langs = doc.get("language", [])

    return MetadataCandidate(
        source="open_library",
        source_id=doc.get("key", ""),
        title=doc.get("title", ""),
        author=", ".join(authors[:2]) if authors else None,
        description=None,  # populated separately from work endpoint
        cover_url=cover_url,
        publisher=(doc.get("publisher") or [None])[0],
        year=doc.get("first_publish_year"),
        page_count=doc.get("number_of_pages_median"),
        isbn=isbn13 or isbn10,
        language=langs[0] if langs else None,
        tags=[s for s in (doc.get("subject") or [])[:8] if s],
    )


async def _fetch_ol_description(client: httpx.AsyncClient, work_key: str) -> str | None:
    """Fetch description from an OL work endpoint e.g. /works/OL123W.json"""
    if not work_key or not work_key.startswith("/works/"):
        return None
    try:
        resp = await client.get(f"https://openlibrary.org{work_key}.json", timeout=5)
        resp.raise_for_status()
        data = resp.json()
        desc = data.get("description")
        if isinstance(desc, dict):
            return desc.get("value", "").strip() or None
        if isinstance(desc, str):
            return desc.strip() or None
    except Exception:
        pass
    return None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_useful(c: MetadataCandidate) -> bool:
    if not c.title or len(c.title) < 2:
        return False
    return bool(c.author or c.isbn or c.description or c.publisher)


def _clean_html(text: str) -> str:
    if not text:
        return ""
    return re.sub(r'<[^>]+>', '', text).strip()
