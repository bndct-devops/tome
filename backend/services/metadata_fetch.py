"""
External metadata fetching.
Sources: Hardcover (primary), Google Books, Open Library — all queried in parallel.
Returns a list of MetadataCandidate objects for the user to review.
"""
import asyncio
import logging
import re
from dataclasses import dataclass, field

import httpx

from backend.core.config import settings

logger = logging.getLogger(__name__)

GOOGLE_BOOKS_URL = "https://www.googleapis.com/books/v1/volumes"
OPEN_LIBRARY_SEARCH = "https://openlibrary.org/search.json"
OPEN_LIBRARY_WORK = "https://openlibrary.org{key}.json"
OPEN_LIBRARY_COVER = "https://covers.openlibrary.org/b/id/{cover_id}-L.jpg"
HARDCOVER_URL = "https://api.hardcover.app/v1/graphql"

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


async def fetch_candidates(
    title: str,
    author: str | None = None,
    isbn: str | None = None,
    series: str | None = None,
    series_index: float | None = None,
    query_override: str | None = None,
) -> FetchResult:
    """Return up to _MAX_RESULTS candidates, querying Hardcover, Google Books, and OpenLibrary in parallel.

    Hardcover results come first — they have significantly better coverage for
    light novels, manga, web novels, and self-published LitRPG.
    """
    # When the user has typed a manual query, ignore the stored ISBN entirely —
    # treat it like Plex "Fix Match": search only by what was typed.
    effective_isbn = None if query_override else isbn
    query = _build_query(title, author, effective_isbn, series, series_index, query_override)
    hc_query = _build_hardcover_query(title, author, effective_isbn, series, series_index, query_override)

    async with httpx.AsyncClient(timeout=10) as client:
        hc_results, gb_results, ol_results = await asyncio.gather(
            _hardcover(client, title, author, effective_isbn, series, series_index, query_override),
            _google_books(client, query, effective_isbn),
            _open_library(client, query, effective_isbn),
            return_exceptions=True,
        )

        # Fallback for Google/OL: if both returned nothing and we used a title-based query,
        # retry with a series-aware query (helps when the per-book title is obscure).
        if (
            not query_override and not isbn
            and series and series_index is not None
            and not _title_is_series_variant(title, series)
            and not _any_results(gb_results, ol_results)
        ):
            fallback_query = _build_series_query(series, series_index, author)
            gb_results, ol_results = await asyncio.gather(
                _google_books(client, fallback_query, None),
                _open_library(client, fallback_query, None),
                return_exceptions=True,
            )

    candidates: list[MetadataCandidate] = []
    seen: set[str] = set()  # deduplicate by ISBN

    # Hardcover first (best quality), then Google, then OL
    for result_set in (hc_results, gb_results, ol_results):
        if isinstance(result_set, Exception):
            logger.warning("Metadata fetch failed: %s", result_set)
            continue
        for c in result_set:
            key = c.isbn or f"{c.source}:{c.source_id}"
            if key not in seen:
                seen.add(key)
                candidates.append(c)

    return FetchResult(candidates=candidates[:_MAX_RESULTS], query_used=hc_query)


# ── Hardcover ─────────────────────────────────────────────────────────────────

def _build_hardcover_query(
    title: str,
    author: str | None,
    isbn: str | None,
    series: str | None,
    series_index: float | None,
    query_override: str | None = None,
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
) -> list[MetadataCandidate]:
    """Fetch candidates from Hardcover.app GraphQL API."""
    token = settings.hardcover_token
    if not token:
        return []

    query_str = _build_hardcover_query(title, author, isbn, series, series_index, query_override)

    graphql_query = """
    query SearchBook($q: String!, $perPage: Int!) {
        search(query: $q, query_type: "Book", per_page: $perPage) {
            ids
            results
        }
    }
    """

    headers = {"authorization": token}
    try:
        resp = await client.post(
            HARDCOVER_URL,
            json={"query": graphql_query, "variables": {"q": query_str, "perPage": _MAX_RESULTS}},
            headers=headers,
        )
        if resp.status_code == 429:
            logger.warning("Hardcover rate limited")
            return []
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning("Hardcover request failed: %s", exc)
        return []

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
    """Fetch publisher and series info from Hardcover for each candidate, in-place."""
    id_to_candidate = {c.source_id: c for c in candidates}
    int_ids = [int(i) for i in hc_ids if i in id_to_candidate]
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


def _any_results(*result_sets: object) -> bool:
    for rs in result_sets:
        if isinstance(rs, list) and rs:
            return True
    return False


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
    try:
        resp = await client.get(
            GOOGLE_BOOKS_URL,
            params={"q": query, "maxResults": _MAX_RESULTS, "printType": "books"},
        )
        if resp.status_code == 429:
            logger.warning("Google Books rate limited")
            return []
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning("Google Books request failed: %s", exc)
        return []

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

    try:
        resp = await client.get(OPEN_LIBRARY_SEARCH, params=params)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning("Open Library request failed: %s", exc)
        return []

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
