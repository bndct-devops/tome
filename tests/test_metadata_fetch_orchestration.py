"""fetch_candidates orchestration: cross-source merge, ranking, retry, status.

These behaviours — the ones most implicated in metadata pains — were entirely
unpinned before: the same book from two sources ate two result slots, 429s
read as "no results", and only the bulk path ranked anything.
"""
import httpx
import pytest
import respx

from backend.core.config import settings
from backend.services import metadata_fetch as mf
from backend.services.metadata_rank import ScoreContext, merge_candidates, rank_candidates, score_candidate

pytestmark = pytest.mark.anyio

GB = "https://www.googleapis.com/books/v1/volumes"
OL = "https://openlibrary.org/search.json"
HC = mf.HARDCOVER_URL


def _hc_payload(title, isbn=None, series=None):
    doc = {
        "id": 42, "title": title, "description": "A hardcover description.",
        "release_year": 2021, "pages": 300, "image": {"url": "https://img/hc.jpg"},
        "genres": ["Fantasy"], "isbns": [isbn] if isbn else [],
        "contributions": [{"author": {"name": "Eric Ugland"}}],
    }
    return {"data": {"search": {"ids": ["42"], "results": {"hits": [{"document": doc}]}}}}


def _gb_payload(title, isbn=None, language="en"):
    ident = [{"type": "ISBN_13", "identifier": isbn}] if isbn else []
    return {"items": [{
        "id": "gb1",
        "volumeInfo": {
            "title": title, "authors": ["Eric Ugland"], "language": language,
            "publisher": "Ugland House", "publishedDate": "2021-05-01",
            "industryIdentifiers": ident, "categories": ["Fiction"],
            "imageLinks": {"thumbnail": "http://img/gb.jpg"},
        },
    }]}


def _empty_gb():
    return {"items": []}


def _empty_ol():
    return {"docs": []}


@pytest.fixture(autouse=True)
def _token(monkeypatch):
    monkeypatch.setattr(settings, "hardcover_token", "hc_test")
    monkeypatch.setattr(settings, "google_books_key", None)
    mf._source_cache.clear()   # module-level TTL cache persists across tests
    # Details second-call for hardcover returns nothing extra.
    yield


def _mock_hc_details(router):
    # _fetch_hardcover_details posts to the same URL; return empty books list.
    return router


@respx.mock
async def test_cross_source_merge_fills_missing_fields():
    """Hardcover (series, no language) + Google (language, same ISBN) → ONE
    candidate carrying both."""
    hc_calls = respx.post(HC).mock(side_effect=[
        httpx.Response(200, json=_hc_payload("The Good Guys Vol 4", isbn="9781234567890")),
        httpx.Response(200, json={"data": {"books": [{"id": 42, "editions": [], "book_series": [
            {"series": {"name": "The Good Guys"}, "position": 4}]}]}}),
    ])
    respx.get(GB).mock(return_value=httpx.Response(200, json=_gb_payload("The Good Guys Vol 4", isbn="9781234567890")))
    respx.get(OL).mock(return_value=httpx.Response(200, json=_empty_ol()))

    result = await mf.fetch_candidates("The Good Guys Vol 4", author="Eric Ugland")
    assert hc_calls.called
    assert len(result.candidates) == 1          # merged, not two slots
    c = result.candidates[0]
    assert c.source == "hardcover"              # base = higher-priority source
    assert c.series == "The Good Guys"          # from Hardcover details
    assert c.language == "en"                   # filled from Google
    assert c.publisher                          # from either source
    assert set(t.lower() for t in c.tags) >= {"fantasy", "fiction"}
    assert result.sources == {"hardcover": "ok", "google_books": "ok", "open_library": "empty"}


@respx.mock
async def test_rate_limit_is_reported_not_swallowed(monkeypatch):
    """429 twice → status rate_limited; other sources still deliver."""
    async def _no_sleep(_): return None
    monkeypatch.setattr(mf.asyncio, "sleep", _no_sleep)

    respx.post(HC).mock(return_value=httpx.Response(429))
    respx.get(GB).mock(return_value=httpx.Response(200, json=_gb_payload("Some Book")))
    respx.get(OL).mock(return_value=httpx.Response(200, json=_empty_ol()))

    result = await mf.fetch_candidates("Some Book")
    assert result.sources["hardcover"] == "rate_limited"
    assert result.sources["google_books"] == "ok"
    assert len(result.candidates) == 1


@respx.mock
async def test_rate_limit_retry_succeeds(monkeypatch):
    """First call 429, retry 200 → candidates arrive, status ok."""
    async def _no_sleep(_): return None
    monkeypatch.setattr(mf.asyncio, "sleep", _no_sleep)

    respx.post(HC).mock(side_effect=[
        httpx.Response(429),
        httpx.Response(200, json=_hc_payload("Retry Book")),
        httpx.Response(200, json={"data": {"books": []}}),
    ])
    respx.get(GB).mock(return_value=httpx.Response(200, json=_empty_gb()))
    respx.get(OL).mock(return_value=httpx.Response(200, json=_empty_ol()))

    result = await mf.fetch_candidates("Retry Book")
    assert result.sources["hardcover"] == "ok"
    assert result.candidates and result.candidates[0].title == "Retry Book"


@respx.mock
async def test_hardcover_disabled_without_token(monkeypatch):
    monkeypatch.setattr(settings, "hardcover_token", None)
    respx.get(GB).mock(return_value=httpx.Response(200, json=_empty_gb()))
    respx.get(OL).mock(return_value=httpx.Response(200, json=_empty_ol()))

    result = await mf.fetch_candidates("Anything")
    assert result.sources["hardcover"] == "disabled"
    assert result.candidates == []


def test_merge_candidates_fuzzy_without_isbn():
    """Same title+author from two sources, neither with ISBN → merged."""
    a = mf.MetadataCandidate(source="google_books", source_id="g", title="Dungeon Crawler Carl",
                             author="Matt Dinniman", language="en")
    b = mf.MetadataCandidate(source="open_library", source_id="o", title="Dungeon Crawler Carl!",
                             author="Matt Dinniman", description="LitRPG.", tags=["LitRPG"])
    # note: punctuation differences are normalized away by the fuzzy key
    merged = merge_candidates([a, b])
    assert len(merged) == 1
    assert merged[0].source == "google_books"
    assert merged[0].description == "LitRPG."      # filled from OL
    assert merged[0].language == "en"


def test_rank_right_volume_beats_source_priority():
    """A Google hit for the RIGHT volume outranks a Hardcover hit for the wrong
    one — the old candidates[0] behaviour always surfaced Hardcover."""
    wrong = mf.MetadataCandidate(source="hardcover", source_id="h", title="My Series Vol 2",
                                 author="A. Author", description="d", cover_url="c")
    right = mf.MetadataCandidate(source="google_books", source_id="g", title="My Series Vol 4",
                                 author="A. Author", description="d", cover_url="c")
    ctx = ScoreContext(title="My Series v004", author="A. Author")
    ranked = rank_candidates([wrong, right], ctx)
    assert ranked[0] is right


def test_classify_media_hint_covers_custom_slugs():
    """Edition awareness must work for user-created BookTypes, not just the
    seeded four — slugs are normalized and substring-classified."""
    from backend.services.metadata_rank import classify_media_hint

    assert classify_media_hint("light_novel") == "light_novel"
    assert classify_media_hint("LightNovels") == "light_novel"
    assert classify_media_hint("ln") == "light_novel"
    assert classify_media_hint("book") == "prose"
    assert classify_media_hint("novel") == "prose"
    for art in ("manga", "comic", "webtoon", "Manhwa", "manhua", "graphic_novel"):
        assert classify_media_hint(art) == "art", art
    assert classify_media_hint(None) is None
    assert classify_media_hint("magazine") is None


def test_media_hint_penalty_applies_to_custom_slug():
    """A custom 'webtoon' type must penalise light-novel editions same as manga."""
    ln = mf.MetadataCandidate(source="hardcover", source_id="1",
                              title="Solo Leveling, Vol. 1 (light novel)")
    ctx_art = ScoreContext(title="Solo Leveling, Vol. 1", media_hint="webtoon")
    ctx_none = ScoreContext(title="Solo Leveling, Vol. 1")
    assert score_candidate(ln, ctx_art) == score_candidate(ln, ctx_none) - 5


AL = mf.ANILIST_URL


def _al_payload():
    return {"data": {"Page": {"media": [{
        "id": 30002,
        "title": {"english": "Berserk", "romaji": "Berserk"},
        "description": "His name is Guts.<br><br>(Source: Dark Horse)",
        "coverImage": {"extraLarge": "https://img/al.jpg"},
        "startDate": {"year": 1989},
        "genres": ["Action", "Fantasy"],
        "countryOfOrigin": "JP",
        "staff": {"edges": [
            {"role": "Story & Art", "node": {"name": {"full": "Kentarou Miura"}}},
        ]},
    }]}}}


@respx.mock
async def test_anilist_queried_for_art_types_and_fills_missing_description():
    """A manga fetch adds the AniList series candidate AND back-fills a missing
    description on the volume-level Hardcover hit from the series blurb."""
    hc = _hc_payload("Berserk, Vol. 1")
    doc = hc["data"]["search"]["results"]["hits"][0]["document"]
    doc["description"] = None
    doc["author_names"] = ["Kentaro Miura"]   # keeps the desc-less doc _is_useful
    respx.post(HC).mock(return_value=httpx.Response(200, json=hc))
    respx.get(GB).mock(return_value=httpx.Response(200, json=_empty_gb()))
    respx.get(OL).mock(return_value=httpx.Response(200, json=_empty_ol()))
    respx.post(AL).mock(return_value=httpx.Response(200, json=_al_payload()))

    result = await mf.fetch_candidates("Berserk, Vol. 1", series="Berserk",
                                       series_index=1, media_hint="manga")
    assert result.sources["anilist"] == "ok"
    al = [c for c in result.candidates if c.source == "anilist"]
    assert al and al[0].author == "Kentarou Miura"
    assert al[0].description == "His name is Guts."      # markup + credit stripped
    assert al[0].tags == ["Action", "Fantasy"]
    hc_cand = next(c for c in result.candidates if c.source == "hardcover")
    assert hc_cand.description == "His name is Guts."    # filled from the series


@respx.mock
async def test_anilist_skipped_for_prose_types():
    """Light novels and plain books never hit AniList — no wasted call, and the
    sources dict doesn't mention it."""
    respx.post(HC).mock(return_value=httpx.Response(200, json=_hc_payload("Some Novel")))
    respx.get(GB).mock(return_value=httpx.Response(200, json=_empty_gb()))
    respx.get(OL).mock(return_value=httpx.Response(200, json=_empty_ol()))
    al_route = respx.post(AL).mock(return_value=httpx.Response(200, json=_al_payload()))

    result = await mf.fetch_candidates("Some Novel", media_hint="light_novel")
    assert not al_route.called
    assert "anilist" not in result.sources


@respx.mock
async def test_repeat_fetch_served_from_cache():
    """An identical fetch within the TTL re-uses cached source results instead
    of re-hitting the external APIs."""
    hc_route = respx.post(HC).mock(return_value=httpx.Response(200, json=_hc_payload("Some Novel", isbn="9781111111111")))
    gb_route = respx.get(GB).mock(return_value=httpx.Response(200, json=_empty_gb()))
    respx.get(OL).mock(return_value=httpx.Response(200, json=_empty_ol()))

    r1 = await mf.fetch_candidates("Some Novel", author="Eric Ugland")
    hc_calls, gb_calls = hc_route.call_count, gb_route.call_count
    r2 = await mf.fetch_candidates("Some Novel", author="Eric Ugland")
    assert hc_route.call_count == hc_calls          # no new outbound calls
    assert gb_route.call_count == gb_calls
    assert [c.title for c in r2.candidates] == [c.title for c in r1.candidates]
    assert r2.sources["hardcover"] == "ok"


@respx.mock
async def test_concurrent_series_volumes_share_one_anilist_call():
    """Bulk-fetching volumes of one series concurrently must not fan the same
    series-level AniList query out N times — followers await the leader."""
    import asyncio

    def _hc_vol(req):
        return httpx.Response(200, json=_hc_payload("Berserk"))

    respx.post(HC).mock(side_effect=_hc_vol)
    respx.get(GB).mock(return_value=httpx.Response(200, json=_empty_gb()))
    respx.get(OL).mock(return_value=httpx.Response(200, json=_empty_ol()))
    al_route = respx.post(AL).mock(return_value=httpx.Response(200, json=_al_payload()))

    results = await asyncio.gather(*[
        mf.fetch_candidates(f"Berserk, Vol. {i}", series="Berserk",
                            series_index=i, media_hint="manga")
        for i in (1, 2, 3, 4)
    ])
    assert al_route.call_count == 1
    for r in results:
        assert r.sources["anilist"] == "ok"
        assert any(c.source == "anilist" for c in r.candidates)
