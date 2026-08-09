"""Hardcover sync — crypto, ISBN helpers, matcher guard, reconciler, endpoints."""
import httpx
import pytest
import respx

from backend.models.notification import Notification
from backend.models.user_book_status import UserBookStatus
from backend.services import hardcover_sync as hs
from backend.services.hardcover_sync import HARDCOVER_URL


@pytest.fixture(autouse=True)
def _no_throttle(monkeypatch):
    """Tests must not sleep 1.1s per mocked request."""
    monkeypatch.setattr(hs, "MIN_REQUEST_SPACING", 0.0)


# ── crypto ────────────────────────────────────────────────────────────────────

def test_crypto_roundtrip_and_tamper():
    from backend.core.crypto import decrypt_secret, encrypt_secret
    ct = encrypt_secret("hc_secret_token")
    assert ct != "hc_secret_token"
    assert decrypt_secret(ct) == "hc_secret_token"
    assert decrypt_secret(None) is None
    assert decrypt_secret("") is None
    assert decrypt_secret("garbage-not-fernet") is None


# ── ISBN helpers ──────────────────────────────────────────────────────────────

def test_normalize_isbn():
    assert hs.normalize_isbn("978-0-306-40615-7") == "9780306406157"
    assert hs.normalize_isbn("0-306-40615-2") == "0306406152"
    assert hs.normalize_isbn("155404295x") == "155404295X"
    assert hs.normalize_isbn("not an isbn") is None
    assert hs.normalize_isbn(None) is None
    assert hs.normalize_isbn("12345") is None


def test_isbn10_to_13_known_pair():
    assert hs.isbn10_to_13("0306406152") == "9780306406157"


def test_isbn_variants_includes_13_for_a_10():
    assert hs.isbn_variants("0-306-40615-2") == ["0306406152", "9780306406157"]
    assert hs.isbn_variants("9780306406157") == ["9780306406157"]
    assert hs.isbn_variants(None) == []


# ── reconciler diff ───────────────────────────────────────────────────────────

def _row(**kw) -> UserBookStatus:
    defaults = dict(user_id=1, book_id=1, status="unread")
    defaults.update(kw)
    return UserBookStatus(**defaults)


def test_needs_sync_rating_change():
    assert hs.needs_sync(_row(rating=4.5))
    assert not hs.needs_sync(_row(rating=4.5, hardcover_synced_rating=4.5))
    # legacy int snapshot vs float rating: 4 == 4.0 → no push
    assert not hs.needs_sync(_row(rating=4, hardcover_synced_rating=4.0))


def test_needs_sync_never_propagates_rating_clear():
    assert not hs.needs_sync(_row(rating=None, hardcover_synced_rating=4.0))


def test_needs_sync_status_and_progress():
    assert hs.needs_sync(_row(status="reading", progress_pct=0.2))
    assert hs.needs_sync(_row(status="read", hardcover_synced_status="reading",
                              progress_pct=1.0, hardcover_synced_pct=1.0))
    assert not hs.needs_sync(_row(status="reading", hardcover_synced_status="reading",
                                  progress_pct=0.5, hardcover_synced_pct=0.495))
    assert hs.needs_sync(_row(status="reading", hardcover_synced_status="reading",
                              progress_pct=0.52, hardcover_synced_pct=0.50))
    # unread/shelved never push
    assert not hs.needs_sync(_row(status="shelved", progress_pct=0.4))


def test_needs_sync_progress_is_forward_only():
    # A regression (stale device rewind, re-opened book) is never mirrored to
    # the public profile.
    assert not hs.needs_sync(_row(status="reading", hardcover_synced_status="reading",
                                  progress_pct=0.62, hardcover_synced_pct=0.90))


def test_progress_pages_math():
    assert hs._progress_pages(_row(status="reading", progress_pct=0.5), 384) == 192
    assert hs._progress_pages(_row(status="read", progress_pct=0.4), 384) == 384
    assert hs._progress_pages(_row(status="reading", progress_pct=0.5), None) is None
    assert hs._progress_pages(_row(status="reading", progress_pct=0.5), 0) is None
    assert hs._progress_pages(_row(status="reading", progress_pct=1.5), 100) == 100


# ── matcher ───────────────────────────────────────────────────────────────────

def _search_payload(hits):
    return {"data": {"search": {"results": {"hits": hits}}}}


@respx.mock
async def test_match_book_isbn_hit(make_book):
    book = make_book(title="Dune", isbn="9780441013593")
    respx.post(HARDCOVER_URL).mock(return_value=httpx.Response(200, json={
        "data": {"editions": [{"id": 555, "pages": 412, "book_id": 77,
                               "book": {"id": 77, "title": "Dune", "pages": 412}}]}
    }))
    async with httpx.AsyncClient() as client:
        assert await hs.match_book(client, "tok", book) is True
    assert book.hardcover_book_id == 77
    assert book.hardcover_edition_id == 555
    assert book.hardcover_pages == 412
    assert book.hardcover_match_method == "isbn13"


@respx.mock
async def test_match_book_search_guard_refuses_dissimilar(make_book):
    book = make_book(title="A Very Specific Novel", author="Jane Author", isbn=None)
    respx.post(HARDCOVER_URL).mock(return_value=httpx.Response(200, json=_search_payload([
        {"document": {"id": 1, "title": "Completely Different Book",
                      "author_names": ["Jane Author"]}},
        {"document": {"id": 2, "title": "A Very Specific Novel",
                      "author_names": ["Somebody Else Entirely"]}},
    ])))
    async with httpx.AsyncClient() as client:
        assert await hs.match_book(client, "tok", book) is False
    assert book.hardcover_match_method == "none"
    assert book.hardcover_book_id is None
    assert book.hardcover_matched_at is not None


@respx.mock
async def test_match_book_search_accepts_close_match(make_book):
    book = make_book(title="Project Hail Mary", author="Andy Weir", isbn=None)

    def responder(request):
        body = request.read().decode()
        if "SearchBook" in body:
            return httpx.Response(200, json=_search_payload([
                {"document": {"id": 90, "title": "Project Hail Mary",
                              "author_names": ["Andy Weir"]}},
            ]))
        return httpx.Response(200, json={"data": {"books": [
            {"id": 90, "pages": 476, "editions": [{"id": 901, "pages": 476}]}
        ]}})

    respx.post(HARDCOVER_URL).mock(side_effect=responder)
    async with httpx.AsyncClient() as client:
        assert await hs.match_book(client, "tok", book) is True
    assert book.hardcover_book_id == 90
    assert book.hardcover_edition_id == 901
    assert book.hardcover_pages == 476
    assert book.hardcover_match_method == "search"


@respx.mock
async def test_match_book_volume_aware_search(make_book):
    """LN volumes share a bare series title in Tome; the matcher must pick the
    per-volume Hardcover record, never the unmarked vol-1/series record or the
    manga variant (shapes taken from the live catalogue)."""
    book = make_book(title="Black Summoner", author="Doufu Mayoi",
                     series="Black Summoner", series_index=2, isbn=None)
    seen_queries = []

    def responder(request):
        import json
        body = json.loads(request.read())
        if "SearchBook" in body["query"]:
            seen_queries.append(body["variables"]["q"])
            return httpx.Response(200, json=_search_payload([
                {"document": {"id": 2213196, "title": "Black Summoner",
                              "author_names": ["Doufu Mayoi"]}},           # unmarked vol-1 record
                {"document": {"id": 1661771, "title": "Black Summoner (Manga) Vol 2",
                              "author_names": ["Gin Ammo"]}},              # manga variant
                {"document": {"id": 2624495, "title": "Black Summoner, Vol. 2",
                              "author_names": []}},                        # the right one
            ]))
        return httpx.Response(200, json={"data": {"books": [
            {"id": 2624495, "pages": 230, "editions": [{"id": 999, "pages": 230}]}
        ]}})

    respx.post(HARDCOVER_URL).mock(side_effect=responder)
    async with httpx.AsyncClient() as client:
        assert await hs.match_book(client, "tok", book) is True
    assert book.hardcover_book_id == 2624495
    assert book.hardcover_pages == 230
    # The query itself must carry the volume, not the bare shared title.
    assert "Vol. 2" in seen_queries[0]


@respx.mock
async def test_match_book_splits_multi_author_strings(make_book):
    """Tome's 'A and B' author string must match individual author_names."""
    book = make_book(title="Cloud FinOps", author="J.R. Storment and Mike Fuller", isbn=None)

    def responder(request):
        body = request.read().decode()
        if "SearchBook" in body:
            return httpx.Response(200, json=_search_payload([
                {"document": {"id": 77, "title": "Cloud FinOps",
                              "author_names": ["J.R. Storment", "Mike Fuller"]}},
            ]))
        return httpx.Response(200, json={"data": {"books": [
            {"id": 77, "pages": 457, "editions": [{"id": 771, "pages": 457}]}
        ]}})

    respx.post(HARDCOVER_URL).mock(side_effect=responder)
    async with httpx.AsyncClient() as client:
        assert await hs.match_book(client, "tok", book) is True
    assert book.hardcover_book_id == 77
    assert book.hardcover_match_method == "search"


@respx.mock
async def test_match_book_standalone_rejects_numbered_volume(make_book):
    book = make_book(title="Dune", author="Frank Herbert", isbn=None)
    respx.post(HARDCOVER_URL).mock(return_value=httpx.Response(200, json=_search_payload([
        {"document": {"id": 5, "title": "Dune Vol. 3", "author_names": ["Frank Herbert"]}},
    ])))
    async with httpx.AsyncClient() as client:
        assert await hs.match_book(client, "tok", book) is False
    assert book.hardcover_match_method == "none"


@respx.mock
async def test_match_book_prefers_popular_record_over_stub(make_book):
    """Vol-1 case observed live: the '… Vol. 1' query surfaces only a
    user-created stub (users_count 1, no author); the real record has the bare
    series title and only appears for the bare query. Both query forms must be
    searched and the community record preferred."""
    book = make_book(title="Black Summoner", author="Doufu Mayoi",
                     series="Black Summoner", series_index=1, isbn=None)
    import json as _json
    queries = []

    def responder(request):
        body = _json.loads(request.read())
        if "SearchBook" in body["query"]:
            q = body["variables"]["q"]
            queries.append(q)
            if "Vol. 1" in q:
                return httpx.Response(200, json=_search_payload([
                    {"document": {"id": 2624493, "title": "Black Summoner -, Vol. 1",
                                  "author_names": [], "users_count": 1}},
                ]))
            return httpx.Response(200, json=_search_payload([
                {"document": {"id": 2213196, "title": "Black Summoner",
                              "author_names": ["Doufu Mayoi"], "users_count": 58}},
            ]))
        return httpx.Response(200, json={"data": {"books": [
            {"id": 2213196, "pages": 230, "editions": [{"id": 555, "pages": 230}]}
        ]}})

    respx.post(HARDCOVER_URL).mock(side_effect=responder)
    async with httpx.AsyncClient() as client:
        assert await hs.match_book(client, "tok", book) is True
    assert book.hardcover_book_id == 2213196   # the real record wins
    assert len(queries) == 2                    # both query forms were searched


@respx.mock
async def test_match_book_isbn_hit_sanity_checked(make_book):
    """A stored ISBN resolving to the WRONG volume (legacy wrong-edition ISBNs,
    or a bad catalogue mapping — observed live: vol 2's ISBN → 'Volume 10')
    must fall through to the guarded search instead of matching."""
    book = make_book(title="Black Summoner", author="Doufu Mayoi",
                     series="Black Summoner", series_index=2, isbn="9781718375666")

    def responder(request):
        body = request.read().decode()
        if "EditionByIsbn" in body:
            return httpx.Response(200, json={"data": {"editions": [
                {"id": 32716494, "title": None, "pages": 246, "book_id": 2440311,
                 "book": {"id": 2440311, "title": "Black Summoner: Volume 10", "pages": 246}}
            ]}})
        if "SearchBook" in body:
            return httpx.Response(200, json=_search_payload([
                {"document": {"id": 2624495, "title": "Black Summoner, Vol. 2",
                              "author_names": []}},
            ]))
        return httpx.Response(200, json={"data": {"books": [
            {"id": 2624495, "pages": 230, "editions": [{"id": 999, "pages": 230}]}
        ]}})

    respx.post(HARDCOVER_URL).mock(side_effect=responder)
    async with httpx.AsyncClient() as client:
        assert await hs.match_book(client, "tok", book) is True
    assert book.hardcover_book_id == 2624495
    assert book.hardcover_match_method == "search"


@respx.mock
async def test_match_book_isbn_wrong_language_edition_repicks(make_book):
    """Observed live (Eric Ugland / The Good Guys): the stored Podium ISBN
    resolves to the GERMAN-translation edition of the correct book. The book
    match must stick, but the pinned edition must be re-picked to the one
    matching the Tome book's language — else the user's Hardcover profile
    shows the German cover and title."""
    book = make_book(title="Heir Today, Pawn Tomorrow", author="Eric Ugland",
                     series="The Good Guys", series_index=2,
                     isbn="9781649712400", language="en")

    def responder(request):
        body = request.read().decode()
        if "EditionByIsbn" in body:
            return httpx.Response(200, json={"data": {"editions": [
                {"id": 30978966, "title": "Heute Erbe, morgen Schachfigur",
                 "pages": 322, "book_id": 637923, "reading_format_id": 1,
                 "language": {"code2": "de"},
                 "book": {"id": 637923, "title": "Heir Today, Pawn Tomorrow",
                          "pages": 254, "slug": "heir-today-pawn-tomorrow"}}
            ]}})
        return httpx.Response(200, json={"data": {"books": [
            {"id": 637923, "pages": 254, "slug": "heir-today-pawn-tomorrow",
             "editions": [
                 {"id": 30615792, "pages": 254, "users_count": 28,
                  "reading_format_id": 1, "language": {"code2": "en"}},
                 {"id": 30978966, "pages": 322, "users_count": 0,
                  "reading_format_id": 1, "language": {"code2": "de"}},
                 {"id": 31793774, "pages": None, "users_count": 0,
                  "reading_format_id": 2, "language": None},
             ]}
        ]}})

    respx.post(HARDCOVER_URL).mock(side_effect=responder)
    async with httpx.AsyncClient() as client:
        assert await hs.match_book(client, "tok", book) is True
    assert book.hardcover_book_id == 637923
    assert book.hardcover_match_method == "isbn13"
    assert book.hardcover_edition_id == 30615792     # English, not the ISBN's German
    assert book.hardcover_pages == 254


@respx.mock
async def test_match_book_isbn_audio_edition_repicks(make_book):
    """An ISBN naming the audiobook edition must not pin it (no pages, audio
    cover on the profile) — re-pick a text edition of the same book."""
    book = make_book(title="Dune", isbn="9780441013593", language="en")

    def responder(request):
        body = request.read().decode()
        if "EditionByIsbn" in body:
            return httpx.Response(200, json={"data": {"editions": [
                {"id": 1, "title": "Dune", "pages": None, "book_id": 77,
                 "reading_format_id": 2, "language": {"code2": "en"},
                 "book": {"id": 77, "title": "Dune", "pages": 412, "slug": "dune"}}
            ]}})
        return httpx.Response(200, json={"data": {"books": [
            {"id": 77, "pages": 412, "slug": "dune", "editions": [
                {"id": 2, "pages": 412, "users_count": 900,
                 "reading_format_id": 1, "language": {"code2": "en"}},
                {"id": 1, "pages": None, "users_count": 1200,
                 "reading_format_id": 2, "language": {"code2": "en"}},
            ]}
        ]}})

    respx.post(HARDCOVER_URL).mock(side_effect=responder)
    async with httpx.AsyncClient() as client:
        assert await hs.match_book(client, "tok", book) is True
    assert book.hardcover_edition_id == 2            # text edition beats popular audio
    assert book.hardcover_pages == 412


@respx.mock
async def test_match_book_isbn_repick_fetch_failure_keeps_isbn_edition(make_book):
    """If the edition re-pick query fails, degrade to the old behaviour (pin
    the ISBN's own edition) rather than losing the match."""
    book = make_book(title="Dune", isbn="9780441013593", language="en")

    def responder(request):
        body = request.read().decode()
        if "EditionByIsbn" in body:
            return httpx.Response(200, json={"data": {"editions": [
                {"id": 5, "title": "Der Wüstenplanet", "pages": 800, "book_id": 77,
                 "reading_format_id": 1, "language": {"code2": "de"},
                 "book": {"id": 77, "title": "Dune", "pages": 412, "slug": "dune"}}
            ]}})
        return httpx.Response(200, json={"errors": [{"message": "boom"}]})

    respx.post(HARDCOVER_URL).mock(side_effect=responder)
    async with httpx.AsyncClient() as client:
        assert await hs.match_book(client, "tok", book) is True
    assert book.hardcover_book_id == 77
    assert book.hardcover_edition_id == 5
    assert book.hardcover_pages == 800


@respx.mock
async def test_match_book_search_picks_language_matching_edition(make_book):
    """The search path must not blindly take the most-used edition — a German
    Tome book pins the German edition even when the English one is bigger."""
    book = make_book(title="Der Wüstenplanet", author="Frank Herbert",
                     isbn=None, language="de")

    def responder(request):
        body = request.read().decode()
        if "SearchBook" in body:
            return httpx.Response(200, json=_search_payload([
                {"document": {"id": 77, "title": "Der Wüstenplanet",
                              "author_names": ["Frank Herbert"]}},
            ]))
        return httpx.Response(200, json={"data": {"books": [
            {"id": 77, "pages": 412, "slug": "dune", "editions": [
                {"id": 2, "pages": 412, "users_count": 900,
                 "reading_format_id": 1, "language": {"code2": "en"}},
                {"id": 9, "pages": 800, "users_count": 3,
                 "reading_format_id": 1, "language": {"code2": "de"}},
            ]}
        ]}})

    respx.post(HARDCOVER_URL).mock(side_effect=responder)
    async with httpx.AsyncClient() as client:
        assert await hs.match_book(client, "tok", book) is True
    assert book.hardcover_edition_id == 9
    assert book.hardcover_pages == 800


def test_edition_picker_helpers():
    assert hs._lang2("en") == "en"
    assert hs._lang2("en-US") == "en"
    assert hs._lang2("English") == "en"
    assert hs._lang2("Deutsch") == "de"
    assert hs._lang2(None) is None
    assert hs._lang2("  ") is None

    de = {"id": 1, "reading_format_id": 1, "language": {"code2": "de"}, "users_count": 50, "pages": 300}
    en = {"id": 2, "reading_format_id": 1, "language": {"code2": "en"}, "users_count": 5, "pages": 250}
    unk = {"id": 3, "reading_format_id": 1, "language": None, "users_count": 80, "pages": 260}
    audio = {"id": 4, "reading_format_id": 2, "language": {"code2": "en"}, "users_count": 999, "pages": None}
    # language match wins over popularity and over unknown
    assert hs._pick_edition([de, en, unk, audio], "en")["id"] == 2
    # unknown want-language: unknown/known tie on language, popularity decides
    assert hs._pick_edition([de, unk], None)["id"] == 3
    # language outranks format: if the only right-language edition is the
    # audiobook, it still beats a wrong-language text edition
    assert hs._pick_edition([de, audio], "en")["id"] == 4
    # within one language, text always beats audio regardless of popularity
    assert hs._pick_edition([en, audio], "en")["id"] == 2
    assert hs._pick_edition([], "en") is None

    assert hs._edition_acceptable(en, "en")
    assert hs._edition_acceptable(unk, "en")      # unknown language: permissive
    assert hs._edition_acceptable(en, None)
    assert not hs._edition_acceptable(de, "en")
    assert not hs._edition_acceptable(audio, "en")


def test_isbn_hit_plausible(make_book):
    vol2 = make_book(title="Black Summoner", series="Black Summoner", series_index=2)
    assert not hs._isbn_hit_plausible(vol2, "Black Summoner: Volume 10")
    assert hs._isbn_hit_plausible(vol2, "Black Summoner: Volume 2")
    assert hs._isbn_hit_plausible(vol2, "Black Summoner")   # unnumbered title: permissive
    assert not hs._isbn_hit_plausible(vol2, "Black Summoner (Manga) Vol 2")
    standalone = make_book(title="Dune")
    assert not hs._isbn_hit_plausible(standalone, "Dune Vol. 3")
    assert not hs._isbn_hit_plausible(standalone, "A Totally Unrelated Cookbook")
    assert hs._isbn_hit_plausible(standalone, "Dune")
    assert hs._isbn_hit_plausible(standalone, "")           # nothing to check


def test_vol_and_author_helpers():
    assert hs._vol_in_title("Black Summoner, Vol. 2") == 2
    assert hs._vol_in_title("Overlord Volume 14") == 14
    assert hs._vol_in_title("Berserk v03") == 3
    assert hs._vol_in_title("Dune") is None
    assert hs._split_authors("J.R. Storment and Mike Fuller") == ["J.R. Storment", "Mike Fuller"]
    assert hs._split_authors("A, B & C") == ["A", "B", "C"]
    assert hs._split_authors(None) == []
    assert hs._is_manga_title("Black Summoner (Manga) Vol 2")
    assert not hs._is_manga_title("Black Summoner, Vol. 2")


# ── push flow ─────────────────────────────────────────────────────────────────

@respx.mock
async def test_push_row_full_flow(db, admin_user, make_book):
    user, _ = admin_user
    user.hardcover_user_id = 42
    book = make_book(title="Dune", isbn="9780441013593")
    book.hardcover_book_id = 77
    book.hardcover_edition_id = 555
    book.hardcover_pages = 400
    row = UserBookStatus(user_id=user.id, book_id=book.id, status="reading",
                         progress_pct=0.5, rating=4.5)
    db.add(row)
    db.flush()

    seen = []

    def responder(request):
        body = request.read().decode()
        seen.append(body)
        if "mutation InsertUserBook" in body:
            return httpx.Response(200, json={"data": {"insert_user_book": {"id": 1001, "error": None}}})
        if "mutation UpdateUserBook" in body:
            return httpx.Response(200, json={"data": {"update_user_book": {"id": 1001, "error": None}}})
        if "mutation InsertRead" in body:
            return httpx.Response(200, json={"data": {"insert_user_book_read": {"id": 2002, "error": None}}})
        if "query UserBook" in body:
            return httpx.Response(200, json={"data": {"user_books": []}})
        return httpx.Response(200, json={"data": {}})

    respx.post(HARDCOVER_URL).mock(side_effect=responder)
    async with httpx.AsyncClient() as client:
        await hs._push_row(client, "tok", user, row, book)

    assert row.hardcover_user_book_id == 1001
    assert row.hardcover_read_id == 2002
    assert row.hardcover_synced_rating == 4.5
    assert row.hardcover_synced_status == "reading"
    assert row.hardcover_synced_pct == 0.5
    assert row.hardcover_error is None
    # progress_pages must be page-based: round(0.5 × 400) = 200
    assert any('"progress_pages": 200' in b or '"progress_pages":200' in b for b in seen)


@respx.mock
async def test_push_row_adopts_auto_created_read(db, admin_user, make_book):
    """insert_user_book auto-creates an initial read row on Hardcover (observed
    live) — the push must adopt it, not insert a duplicate."""
    user, _ = admin_user
    user.hardcover_user_id = 42
    book = make_book(title="Dune")
    book.hardcover_book_id = 77
    book.hardcover_pages = 400
    row = UserBookStatus(user_id=user.id, book_id=book.id, status="reading", progress_pct=0.5)
    db.add(row)
    db.flush()

    def responder(request):
        body = request.read().decode()
        assert "mutation InsertRead" not in body, "must adopt the auto-created read, not insert"
        if "query UserBook" in body:
            return httpx.Response(200, json={"data": {"user_books": []}})
        if "mutation InsertUserBook" in body:
            return httpx.Response(200, json={"data": {"insert_user_book": {"id": 1001, "error": None}}})
        if "query Reads" in body:
            return httpx.Response(200, json={"data": {"user_book_reads": [{"id": 3003}]}})
        if "mutation UpdateRead" in body:
            return httpx.Response(200, json={"data": {"update_user_book_read": {"id": 3003, "error": None}}})
        return httpx.Response(200, json={"data": {}})

    respx.post(HARDCOVER_URL).mock(side_effect=responder)
    async with httpx.AsyncClient() as client:
        await hs._push_row(client, "tok", user, row, book)
    assert row.hardcover_read_id == 3003
    assert row.hardcover_synced_pct == 0.5


@respx.mock
async def test_push_row_status_only_without_pages(db, admin_user, make_book):
    user, _ = admin_user
    user.hardcover_user_id = 42
    book = make_book(title="Obscure Book")
    book.hardcover_book_id = 88
    book.hardcover_pages = None
    row = UserBookStatus(user_id=user.id, book_id=book.id, status="reading", progress_pct=0.3)
    db.add(row)
    db.flush()

    def responder(request):
        body = request.read().decode()
        assert "mutation InsertRead" not in body and "mutation UpdateRead" not in body, \
            "page-less book must not push progress reads"
        if "query UserBook" in body:
            return httpx.Response(200, json={"data": {"user_books": [
                {"id": 1001, "status_id": None, "user_book_reads": []}]}})
        if "mutation UpdateUserBook" in body:
            return httpx.Response(200, json={"data": {"update_user_book": {"id": 1001, "error": None}}})
        return httpx.Response(200, json={"data": {}})

    respx.post(HARDCOVER_URL).mock(side_effect=responder)
    async with httpx.AsyncClient() as client:
        await hs._push_row(client, "tok", user, row, book)
    assert row.hardcover_synced_status == "reading"
    assert row.hardcover_synced_pct == 0.3  # snapshotted so needs_sync stops firing
    assert not hs.needs_sync(row)


# ── auth failure → expired + one notification ────────────────────────────────

def test_mark_token_expired_notifies_once(db, admin_user):
    user, _ = admin_user
    user.hardcover_token = "enc"
    user.hardcover_token_status = "ok"
    hs.mark_token_expired(db, user)
    hs.mark_token_expired(db, user)
    assert user.hardcover_token_status == "expired"
    notes = db.query(Notification).filter_by(user_id=user.id, kind="hardcover_token_expired").all()
    assert len(notes) == 1


# ── API endpoints ─────────────────────────────────────────────────────────────

def test_link_flow_and_status(client, db, admin_user, monkeypatch):
    user, _ = admin_user

    async def fake_verify(token):
        # Hardcover needs the literal "Bearer " prefix; the endpoint normalizes
        # a bare pasted token (verified against the live API).
        assert token == "Bearer hc_tok"
        return {"id": 42, "username": "benedict"}

    monkeypatch.setattr(hs, "verify_token", fake_verify)
    started_for: list[int] = []
    monkeypatch.setattr(hs, "start_manual_sync", lambda uid: started_for.append(uid) or True)

    r = client.get("/api/hardcover/status")
    assert r.status_code == 200 and r.json() == {"linked": False}

    r = client.post("/api/hardcover/link", json={"token": "hc_tok"})
    assert r.status_code == 200
    assert r.json()["username"] == "benedict"
    # Linking kicks off the initial backfill itself.
    assert r.json()["sync_started"] is True
    assert started_for == [user.id]
    assert user.hardcover_sync_enabled is True
    assert user.hardcover_user_id == 42
    # token stored encrypted (with the normalized Bearer prefix), decrypts back
    from backend.core.crypto import decrypt_secret
    assert "hc_tok" not in (user.hardcover_token or "")
    assert decrypt_secret(user.hardcover_token) == "Bearer hc_tok"

    r = client.get("/api/hardcover/status")
    body = r.json()
    assert body["linked"] is True
    assert body["username"] == "benedict"
    assert body["token_status"] == "ok"

    r = client.put("/api/hardcover/settings", json={"sync_enabled": False})
    assert r.status_code == 200 and r.json()["sync_enabled"] is False

    r = client.delete("/api/hardcover/link")
    assert r.status_code == 204
    assert user.hardcover_token is None
    assert client.get("/api/hardcover/status").json() == {"linked": False}


def test_link_rejects_bad_token(client, monkeypatch):
    async def fake_verify(token):
        raise hs.HardcoverAuthError()

    monkeypatch.setattr(hs, "verify_token", fake_verify)
    r = client.post("/api/hardcover/link", json={"token": "bad"})
    assert r.status_code == 400


def test_sync_now_requires_link(client):
    r = client.post("/api/hardcover/sync-now")
    assert r.status_code == 400


def test_sync_now_resets_parked_rows(client, db, admin_user, make_book, monkeypatch):
    user, _ = admin_user
    from backend.core.crypto import encrypt_secret
    user.hardcover_token = encrypt_secret("t")
    user.hardcover_token_status = "ok"
    book = make_book(title="Parked")
    row = UserBookStatus(user_id=user.id, book_id=book.id, status="read",
                         hardcover_fail_count=10, hardcover_error="boom")
    db.add(row)
    db.flush()

    monkeypatch.setattr(hs, "start_manual_sync", lambda uid: True)
    r = client.post("/api/hardcover/sync-now")
    assert r.status_code == 200 and r.json()["started"] is True
    db.refresh(row)
    assert row.hardcover_fail_count == 0


def _link_test_user(user):
    from backend.core.crypto import encrypt_secret
    user.hardcover_token = encrypt_secret("Bearer t")
    user.hardcover_token_status = "ok"
    user.hardcover_user_id = 42


def test_hardcover_books_page_endpoint(client, db, admin_user, make_book):
    """The /hardcover page's data source: every status-row book with its state."""
    user, _ = admin_user
    _link_test_user(user)
    matched = make_book(title="Black Summoner", series="Black Summoner", series_index=10)
    matched.hardcover_book_id = 2440311
    matched.hardcover_slug = "black-summoner-volume-10"
    matched.hardcover_match_method = "isbn13"
    unmatched = make_book(title="Obscure Zine")
    unmatched.hardcover_match_method = "none"
    from datetime import datetime as _dt
    unmatched.hardcover_matched_at = _dt.utcnow()
    pending = make_book(title="Fresh Upload")
    for i, b in enumerate((matched, unmatched, pending)):
        db.add(UserBookStatus(user_id=user.id, book_id=b.id, status="reading",
                              progress_pct=0.1 * (i + 1)))
    db.flush()

    r = client.get("/api/hardcover/books")
    assert r.status_code == 200
    by_id = {i["book_id"]: i for i in r.json()}
    assert by_id[matched.id]["state"] == "matched"
    assert by_id[matched.id]["slug"] == "black-summoner-volume-10"
    assert by_id[matched.id]["series_index"] == 10       # volume is renderable
    assert by_id[unmatched.id]["state"] == "unmatched"
    assert by_id[pending.id]["state"] == "pending"


def test_matches_visible_in_books_endpoint(client, db, admin_user, make_book):
    user, _ = admin_user
    _link_test_user(user)
    book = make_book(title="Dune", author="Frank Herbert")
    book.hardcover_book_id = 77
    book.hardcover_slug = "dune"
    book.hardcover_match_method = "isbn13"
    book.hardcover_pages = 412
    db.add(UserBookStatus(user_id=user.id, book_id=book.id, status="read"))
    db.flush()

    items = client.get("/api/hardcover/books").json()
    mine = [i for i in items if i["book_id"] == book.id]
    assert mine and mine[0]["slug"] == "dune" and mine[0]["method"] == "isbn13"


def test_rematch_retry_clears_match_and_deletes_profile_entry(client, db, admin_user, make_book, monkeypatch):
    user, _ = admin_user
    _link_test_user(user)
    book = make_book(title="Wrongly Matched")
    book.hardcover_book_id = 999
    book.hardcover_slug = "wrong-record"
    book.hardcover_match_method = "search"
    row = UserBookStatus(user_id=user.id, book_id=book.id, status="reading",
                         progress_pct=0.5, hardcover_user_book_id=1234,
                         hardcover_read_id=55, hardcover_synced_pct=0.5,
                         hardcover_synced_status="reading")
    db.add(row)
    db.flush()

    deleted: list[int] = []

    async def fake_delete(client_, token, ub_id):
        deleted.append(ub_id)
        return True

    monkeypatch.setattr(hs, "delete_user_book", fake_delete)
    r = client.post(f"/api/hardcover/books/{book.id}/rematch", json={"mode": "retry"})
    assert r.status_code == 200
    assert r.json()["removed_from_profile"] is True
    assert deleted == [1234]
    db.refresh(book); db.refresh(row)
    assert book.hardcover_book_id is None
    assert book.hardcover_match_method is None       # eligible for re-match
    assert row.hardcover_user_book_id is None
    assert row.hardcover_synced_pct is None          # will re-push after re-match


def test_rematch_exclude_stops_sync_attempts(client, db, admin_user, make_book, monkeypatch):
    user, _ = admin_user
    _link_test_user(user)
    book = make_book(title="Not On Hardcover")
    row = UserBookStatus(user_id=user.id, book_id=book.id, status="read", rating=5)
    db.add(row)
    db.flush()

    async def fake_delete(client_, token, ub_id):
        raise AssertionError("nothing to delete")

    monkeypatch.setattr(hs, "delete_user_book", fake_delete)
    r = client.post(f"/api/hardcover/books/{book.id}/rematch", json={"mode": "exclude"})
    assert r.status_code == 200
    db.refresh(book)
    assert book.hardcover_match_method == "excluded"
    # appears in the page's book list, flagged
    items = client.get("/api/hardcover/books").json()
    assert any(i["book_id"] == book.id and i["state"] == "excluded" for i in items)
    # and Sync-now must NOT clear the exclusion (only 'none' markers)
    monkeypatch.setattr(hs, "start_manual_sync", lambda uid: True)
    client.post("/api/hardcover/sync-now")
    db.refresh(book)
    assert book.hardcover_match_method == "excluded"


@respx.mock
async def test_failed_match_not_retried_next_cycle(db, admin_user, make_book):
    """Regression: the failed-match commit bumps Book.updated_at (onupdate)
    PAST the in-flight matched_at — the retry guard must still hold, or every
    cycle re-matches (and re-bills) every unmatched book forever."""
    user, _ = admin_user
    _link_test_user(user)
    book = make_book(title="Never On Hardcover", author="Nobody")
    db.add(UserBookStatus(user_id=user.id, book_id=book.id, status="read", rating=4))
    db.flush()

    route = respx.post(HARDCOVER_URL).mock(
        return_value=httpx.Response(200, json=_search_payload([])))
    async with httpx.AsyncClient() as client:
        stats1 = await hs.sync_user(db, client, user, hs._Budget(50))
        calls_after_first = route.call_count
        stats2 = await hs.sync_user(db, client, user, hs._Budget(50))
    assert stats1["skipped_unmatched"] == 1
    assert stats2["skipped_unmatched"] == 1
    assert route.call_count == calls_after_first, "second cycle must not re-match"


@respx.mock
async def test_sync_user_skips_excluded_without_requests(db, admin_user, make_book):
    user, _ = admin_user
    _link_test_user(user)
    book = make_book(title="Excluded Book")
    book.hardcover_match_method = "excluded"
    db.add(UserBookStatus(user_id=user.id, book_id=book.id, status="read", rating=4))
    db.flush()

    route = respx.post(HARDCOVER_URL).mock(
        return_value=httpx.Response(200, json={"data": {}}))
    budget = hs._Budget(10)
    async with httpx.AsyncClient() as client:
        stats = await hs.sync_user(db, client, user, budget)
    assert stats["pushed"] == 0
    assert budget.used == 0          # skip must not consume budget
    assert not route.called          # and must make zero API calls


def test_manual_match_search_endpoint(client, db, admin_user, monkeypatch):
    user, _ = admin_user
    _link_test_user(user)

    async def fake_search(token, q, limit=8):
        assert q == "black summoner"
        return [{"hardcover_book_id": 785858, "title": "Black Summoner: Volume 1",
                 "authors": ["Doufu Mayoi"], "slug": "black-summoner-volume-1",
                 "users_count": 12, "cover_url": None, "series": "Black Summoner"}]

    monkeypatch.setattr(hs, "search_candidates", fake_search)
    r = client.get("/api/hardcover/search", params={"q": "black summoner"})
    assert r.status_code == 200
    assert r.json()[0]["hardcover_book_id"] == 785858


def test_manual_match_pins_record_and_clears_old_state(client, db, admin_user, make_book, monkeypatch):
    user, _ = admin_user
    _link_test_user(user)
    book = make_book(title="Black Summoner", series="Black Summoner", series_index=1)
    book.hardcover_book_id = 2624493            # the stub it wrongly matched
    book.hardcover_slug = "stub-record"
    book.hardcover_match_method = "search"
    row = UserBookStatus(user_id=user.id, book_id=book.id, status="reading",
                         progress_pct=0.42, hardcover_user_book_id=111,
                         hardcover_synced_pct=0.42, hardcover_synced_status="reading")
    db.add(row)
    db.flush()

    deleted: list[int] = []

    async def fake_delete(client_, token, ub_id):
        deleted.append(ub_id)
        return True

    async def fake_resolve(token, b, hc_id):
        assert hc_id == 785858
        b.hardcover_book_id = hc_id
        b.hardcover_slug = "black-summoner-volume-1"
        b.hardcover_edition_id = 30789992
        b.hardcover_pages = 227
        b.hardcover_match_method = "manual"
        from datetime import datetime as _dt
        b.hardcover_matched_at = _dt.utcnow()

    monkeypatch.setattr(hs, "delete_user_book", fake_delete)
    monkeypatch.setattr(hs, "resolve_manual_match", fake_resolve)

    r = client.post(f"/api/hardcover/books/{book.id}/match", json={"hardcover_book_id": 785858})
    assert r.status_code == 200
    assert r.json()["slug"] == "black-summoner-volume-1"
    assert deleted == [111]                     # old profile entry removed
    db.refresh(book); db.refresh(row)
    assert book.hardcover_book_id == 785858
    assert book.hardcover_match_method == "manual"
    assert row.hardcover_user_book_id is None   # push state reset → re-pushes to the pick
    assert row.hardcover_synced_pct is None
    # 'manual' is not auto-cleared by Sync-now (only 'none' markers are)
    monkeypatch.setattr(hs, "start_manual_sync", lambda uid: True)
    client.post("/api/hardcover/sync-now")
    db.refresh(book)
    assert book.hardcover_match_method == "manual"


def test_sync_now_retries_unmatched_books(client, db, admin_user, make_book, monkeypatch):
    user, _ = admin_user
    from datetime import datetime
    from backend.core.crypto import encrypt_secret
    user.hardcover_token = encrypt_secret("t")
    user.hardcover_token_status = "ok"
    book = make_book(title="Missed Last Month")
    book.hardcover_match_method = "none"
    book.hardcover_matched_at = datetime.utcnow()
    db.add(UserBookStatus(user_id=user.id, book_id=book.id, status="read"))
    db.flush()

    monkeypatch.setattr(hs, "start_manual_sync", lambda uid: True)
    r = client.post("/api/hardcover/sync-now")
    assert r.status_code == 200
    db.refresh(book)
    # Explicit click clears the failed-match marker so the matcher tries again.
    assert book.hardcover_match_method is None
    assert book.hardcover_matched_at is None


# ── want_to_read: reconciler diff + push ──────────────────────────────────────

def test_needs_sync_want_to_read():
    # Newly queued → status push needed
    assert hs.needs_sync(_row(status="want_to_read"))
    # Agreed on both sides → quiet, even with leftover sample progress
    assert not hs.needs_sync(_row(status="want_to_read",
                                  hardcover_synced_status="want_to_read",
                                  progress_pct=0.15))


@respx.mock
async def test_push_row_want_to_read_no_read_row(db, admin_user, make_book):
    """Queued books get a shelf entry (status 1) but never a progress read-row,
    even when they carry leftover sample progress and the edition has pages."""
    user, _ = admin_user
    user.hardcover_user_id = 42
    book = make_book(title="Queued Push")
    book.hardcover_book_id = 77
    book.hardcover_edition_id = 555
    book.hardcover_pages = 400
    row = UserBookStatus(user_id=user.id, book_id=book.id, status="want_to_read",
                         progress_pct=0.2)
    db.add(row)
    db.flush()

    seen = []

    def responder(request):
        body = request.read().decode()
        seen.append(body)
        assert "InsertRead" not in body and "UpdateRead" not in body \
            and "query Reads" not in body, "want_to_read must never touch read rows"
        if "query UserBook" in body:
            return httpx.Response(200, json={"data": {"user_books": []}})
        if "mutation InsertUserBook" in body:
            assert '"status_id": 1' in body or '"status_id":1' in body
            return httpx.Response(200, json={"data": {"insert_user_book": {"id": 1001, "error": None}}})
        return httpx.Response(200, json={"data": {}})

    respx.post(HARDCOVER_URL).mock(side_effect=responder)
    async with httpx.AsyncClient() as client:
        await hs._push_row(client, "tok", user, row, book)

    assert row.hardcover_user_book_id == 1001
    assert row.hardcover_synced_status == "want_to_read"
    assert not hs.needs_sync(row)


# ── want_to_read: pull direction ──────────────────────────────────────────────

def _link_pull_user(user):
    from backend.core.crypto import encrypt_secret
    user.hardcover_token = encrypt_secret("Bearer hc_tok")
    user.hardcover_token_status = "ok"
    user.hardcover_sync_enabled = True
    user.hardcover_user_id = 42


def _shelf_responder(shelf_entries, user_book_lookups=None):
    """Responder serving the WTR shelf query and targeted UserBook lookups."""
    def responder(request):
        body = request.read().decode()
        if "query WantToReadShelf" in body:
            return httpx.Response(200, json={"data": {"user_books": shelf_entries}})
        if "query UserBook" in body:
            import json as _json
            bid = _json.loads(body)["variables"]["bid"]
            found = (user_book_lookups or {}).get(bid, [])
            return httpx.Response(200, json={"data": {"user_books": found}})
        return httpx.Response(200, json={"data": {}})
    return responder


@respx.mock
async def test_pull_wtr_applies_to_missing_and_unread(db, admin_user, make_book):
    user, _ = admin_user
    _link_pull_user(user)
    matched_new = make_book(title="Shelved On HC")
    matched_new.hardcover_book_id = 77
    matched_unread = make_book(title="Unread In Tome")
    matched_unread.hardcover_book_id = 88
    db.add(UserBookStatus(user_id=user.id, book_id=matched_unread.id, status="unread"))
    db.flush()

    respx.post(HARDCOVER_URL).mock(side_effect=_shelf_responder(
        [{"id": 501, "book_id": 77}, {"id": 502, "book_id": 88}]))
    async with httpx.AsyncClient() as client:
        stats = await hs.pull_want_to_read(db, client, user, hs._Budget(50))

    assert stats == {"pulled": 2, "reverted": 0, "wished": 0, "wish_dismissed": 0, "wish_reopened": 0, "adopted": 0}
    for book, ub_id in ((matched_new, 501), (matched_unread, 502)):
        row = db.query(UserBookStatus).filter_by(user_id=user.id, book_id=book.id).one()
        assert row.status == "want_to_read"
        assert row.hardcover_synced_status == "want_to_read"
        assert row.hardcover_user_book_id == ub_id
        # Echo prevention: the push reconciler must see no diff
        assert not hs.needs_sync(row)


@respx.mock
async def test_pull_wtr_never_downgrades_engagement(db, admin_user, make_book):
    user, _ = admin_user
    _link_pull_user(user)
    book = make_book(title="Reading In Tome")
    book.hardcover_book_id = 77
    db.add(UserBookStatus(user_id=user.id, book_id=book.id, status="reading",
                          progress_pct=0.6))
    db.flush()

    respx.post(HARDCOVER_URL).mock(side_effect=_shelf_responder(
        [{"id": 501, "book_id": 77}]))
    async with httpx.AsyncClient() as client:
        stats = await hs.pull_want_to_read(db, client, user, hs._Budget(50))

    assert stats == {"pulled": 0, "reverted": 0, "wished": 0, "wish_dismissed": 0, "wish_reopened": 0, "adopted": 0}
    row = db.query(UserBookStatus).filter_by(user_id=user.id, book_id=book.id).one()
    assert row.status == "reading"


@respx.mock
async def test_pull_wtr_convergence_removed_vs_moved(db, admin_user, make_book):
    """Both sides had agreed want_to_read. Entry gone from HC entirely → revert
    to unread; entry merely moved to another HC status → leave both sides be."""
    user, _ = admin_user
    _link_pull_user(user)
    removed = make_book(title="Unshelved On HC")
    removed.hardcover_book_id = 77
    moved = make_book(title="Moved To Reading On HC")
    moved.hardcover_book_id = 88
    for book in (removed, moved):
        db.add(UserBookStatus(user_id=user.id, book_id=book.id, status="want_to_read",
                              hardcover_synced_status="want_to_read",
                              hardcover_user_book_id=600 + book.id))
    db.flush()

    respx.post(HARDCOVER_URL).mock(side_effect=_shelf_responder(
        [],  # WTR shelf is now empty
        user_book_lookups={77: [], 88: [{"id": 999, "status_id": 2, "user_book_reads": []}]},
    ))
    async with httpx.AsyncClient() as client:
        stats = await hs.pull_want_to_read(db, client, user, hs._Budget(50))

    assert stats == {"pulled": 0, "reverted": 1, "wished": 0, "wish_dismissed": 0, "wish_reopened": 0, "adopted": 0}
    removed_row = db.query(UserBookStatus).filter_by(user_id=user.id, book_id=removed.id).one()
    assert removed_row.status == "unread"
    assert removed_row.hardcover_synced_status is None
    moved_row = db.query(UserBookStatus).filter_by(user_id=user.id, book_id=moved.id).one()
    assert moved_row.status == "want_to_read"
    assert moved_row.hardcover_synced_status == "want_to_read"
    # Snapshot intact → nothing re-pushes over the user's HC-side change
    assert not hs.needs_sync(moved_row)


@respx.mock
async def test_pull_wtr_tome_only_row_untouched_by_convergence(db, admin_user, make_book):
    """A Tome-set want_to_read that was never pushed (no snapshot) must survive
    an empty HC shelf — convergence only reverts previously-agreed rows."""
    user, _ = admin_user
    _link_pull_user(user)
    book = make_book(title="Tome Only Queue")
    book.hardcover_book_id = 77
    db.add(UserBookStatus(user_id=user.id, book_id=book.id, status="want_to_read"))
    db.flush()

    respx.post(HARDCOVER_URL).mock(side_effect=_shelf_responder([]))
    async with httpx.AsyncClient() as client:
        stats = await hs.pull_want_to_read(db, client, user, hs._Budget(50))

    assert stats == {"pulled": 0, "reverted": 0, "wished": 0, "wish_dismissed": 0, "wish_reopened": 0, "adopted": 0}
    row = db.query(UserBookStatus).filter_by(user_id=user.id, book_id=book.id).one()
    assert row.status == "want_to_read"


@respx.mock
async def test_pull_wtr_partial_fetch_aborts(db, admin_user, make_book):
    """Budget exhaustion mid-fetch must abort the whole pull — applying a
    partial shelf would mass-revert agreed rows."""
    user, _ = admin_user
    _link_pull_user(user)
    book = make_book(title="Agreed Queued")
    book.hardcover_book_id = 77
    db.add(UserBookStatus(user_id=user.id, book_id=book.id, status="want_to_read",
                          hardcover_synced_status="want_to_read"))
    db.flush()

    respx.post(HARDCOVER_URL).mock(side_effect=_shelf_responder([]))
    async with httpx.AsyncClient() as client:
        stats = await hs.pull_want_to_read(db, client, user, hs._Budget(0))

    assert stats == {"pulled": 0, "reverted": 0}
    row = db.query(UserBookStatus).filter_by(user_id=user.id, book_id=book.id).one()
    assert row.status == "want_to_read"  # untouched


# ── want_to_read pull: unresolved entries → wishlist ──────────────────────────

def _shelf_with_books_responder(shelf_entries, books_by_ids):
    def responder(request):
        body = request.read().decode()
        if "query WantToReadShelf" in body:
            return httpx.Response(200, json={"data": {"user_books": shelf_entries}})
        if "query BooksByIds" in body:
            return httpx.Response(200, json={"data": {"books": books_by_ids}})
        if "query UserBook" in body:
            return httpx.Response(200, json={"data": {"user_books": []}})
        return httpx.Response(200, json={"data": {}})
    return responder


@respx.mock
async def test_pull_wtr_unresolved_entry_creates_wish(db, admin_user):
    """A shelf entry with no Tome book becomes a wish — and stays deduped."""
    from backend.models.wish import Wish

    user, _ = admin_user
    _link_pull_user(user)

    responder = _shelf_with_books_responder(
        [{"id": 501, "book_id": 999}],
        [{"id": 999, "title": "Not In Tome Yet", "slug": "not-in-tome",
          "image": {"url": "https://img.example/x.jpg"},
          "contributions": [{"author": {"name": "Shelf Author"}}]}],
    )
    respx.post(HARDCOVER_URL).mock(side_effect=responder)
    async with httpx.AsyncClient() as client:
        stats = await hs.pull_want_to_read(db, client, user, hs._Budget(50))
    assert stats["wished"] == 1

    wish = db.query(Wish).filter_by(user_id=user.id, source=hs.SHELF_WISH_SOURCE, source_id="999").one()
    assert wish.status == "open"
    assert wish.title == "Not In Tome Yet"
    assert wish.author == "Shelf Author"
    assert wish.cover_url == "https://img.example/x.jpg"

    # Second cycle: the existing wish blocks re-creation (no API refetch needed)
    async with httpx.AsyncClient() as client:
        stats = await hs.pull_want_to_read(db, client, user, hs._Budget(50))
    assert stats["wished"] == 0
    assert db.query(Wish).filter_by(user_id=user.id, source=hs.SHELF_WISH_SOURCE).count() == 1


@respx.mock
async def test_pull_wtr_owned_but_unmatched_creates_no_wish(db, admin_user, make_book):
    """A library book that simply lacks a catalogue match must not spawn a wish."""
    from backend.models.wish import Wish

    user, _ = admin_user
    _link_pull_user(user)
    make_book(title="Already Owned Novel", author="Owned Author")  # no hardcover_book_id

    responder = _shelf_with_books_responder(
        [{"id": 501, "book_id": 999}],
        [{"id": 999, "title": "Already Owned Novel", "slug": "owned",
          "contributions": [{"author": {"name": "Owned Author"}}]}],
    )
    respx.post(HARDCOVER_URL).mock(side_effect=responder)
    async with httpx.AsyncClient() as client:
        stats = await hs.pull_want_to_read(db, client, user, hs._Budget(50))
    assert stats["wished"] == 0
    assert db.query(Wish).filter_by(user_id=user.id).count() == 0


@respx.mock
async def test_pull_wtr_unshelve_dismisses_hardcover_wish(db, admin_user):
    """Un-shelving on Hardcover auto-dismisses the wish it created."""
    from backend.models.wish import Wish

    user, _ = admin_user
    _link_pull_user(user)
    db.add(Wish(user_id=user.id, title="Was Shelved", source=hs.SHELF_WISH_SOURCE,
                source_id="999", status="open", kind="wish"))
    db.flush()

    respx.post(HARDCOVER_URL).mock(side_effect=_shelf_with_books_responder([], []))
    async with httpx.AsyncClient() as client:
        stats = await hs.pull_want_to_read(db, client, user, hs._Budget(50))
    assert stats["wish_dismissed"] == 1

    wish = db.query(Wish).filter_by(user_id=user.id, source_id="999").one()
    assert wish.status == "dismissed"


@respx.mock
async def test_pull_wtr_reshelve_reopens_dismissed_wish(db, admin_user):
    """Re-shelving a previously un-shelved book reopens its dismissed wish
    instead of being blocked by the dedup — the shelf is authoritative for
    the wishes it created. Fulfilled wishes stay final."""
    from backend.models.wish import Wish

    user, _ = admin_user
    _link_pull_user(user)
    db.add(Wish(user_id=user.id, title="Shelved Again", source=hs.SHELF_WISH_SOURCE,
                source_id="999", status="dismissed", kind="wish"))
    db.add(Wish(user_id=user.id, title="Already Fulfilled", source=hs.SHELF_WISH_SOURCE,
                source_id="888", status="fulfilled", kind="wish"))
    db.flush()

    respx.post(HARDCOVER_URL).mock(side_effect=_shelf_with_books_responder(
        [{"id": 501, "book_id": 999}, {"id": 502, "book_id": 888}], []))
    async with httpx.AsyncClient() as client:
        stats = await hs.pull_want_to_read(db, client, user, hs._Budget(50))

    assert stats["wish_reopened"] == 1
    assert stats["wished"] == 0  # reopened, not recreated
    assert db.query(Wish).filter_by(user_id=user.id, source_id="999").one().status == "open"
    assert db.query(Wish).filter_by(user_id=user.id, source_id="888").one().status == "fulfilled"


# ── regression: the mirror must never touch follows or user-created wishes ────

@respx.mock
async def test_pull_wtr_never_touches_follows_or_native_wishes(db, admin_user):
    """The prod incident: series follows (kind='follow', source='hardcover')
    and user-created wishes whose metadata came from Hardcover were dismissed
    by the shelf mirror on the first sync. Both must survive an empty shelf."""
    from backend.models.wish import Wish

    user, _ = admin_user
    _link_pull_user(user)
    for i in range(4):
        db.add(Wish(user_id=user.id, title=f"Followed Series {i}", kind="follow",
                    status="open", source="hardcover", source_id=f"series:{i}"))
    db.add(Wish(user_id=user.id, title="Native Series Wish", kind="wish",
                status="open", source="hardcover", source_id="12345",
                series="Native Series"))
    db.flush()

    respx.post(HARDCOVER_URL).mock(side_effect=_shelf_with_books_responder([], []))
    async with httpx.AsyncClient() as client:
        stats = await hs.pull_want_to_read(db, client, user, hs._Budget(50))

    assert stats["wish_dismissed"] == 0
    assert db.query(Wish).filter_by(user_id=user.id, status="open").count() == 5


def test_repair_reopens_bug_dismissed_rows_only(db, admin_user):
    """Startup repair: reopen rows the buggy mirror dismissed (no audit entry),
    leave admin-dismissed wishes closed, reclassify shelf-created wishes."""
    from backend.models.audit_log import AuditLog
    from backend.models.wish import Wish

    user, _ = admin_user
    bug_follow = Wish(user_id=user.id, title="Bug-Dismissed Follow", kind="follow",
                      status="dismissed", source="hardcover", source_id="series:1")
    bug_wish = Wish(user_id=user.id, title="Bug-Dismissed Wish", kind="wish",
                    status="dismissed", source="hardcover", source_id="111")
    admin_closed = Wish(user_id=user.id, title="Admin-Dismissed Wish", kind="wish",
                        status="dismissed", source="hardcover", source_id="222")
    old_shelf_wish = Wish(user_id=user.id, title="Shelf-Created", kind="wish",
                          status="open", source="hardcover", source_id="333",
                          note=hs.SHELF_WISH_NOTE)
    google_wish = Wish(user_id=user.id, title="Google Wish", kind="wish",
                       status="dismissed", source="google_books", source_id="g1")
    db.add_all([bug_follow, bug_wish, admin_closed, old_shelf_wish, google_wish])
    db.flush()
    db.add(AuditLog(user_id=user.id, username="testadmin",
                    action="wishlist.dismissed", resource_type="wish",
                    resource_id=admin_closed.id))
    db.flush()

    result = hs.repair_shelf_sync_collateral(db)
    assert result == {"reopened": 2, "reclassified": 1}

    assert db.get(Wish, bug_follow.id).status == "open"
    assert db.get(Wish, bug_wish.id).status == "open"
    assert db.get(Wish, admin_closed.id).status == "dismissed"
    assert db.get(Wish, old_shelf_wish.id).source == hs.SHELF_WISH_SOURCE
    assert db.get(Wish, google_wish.id).status == "dismissed"  # not hardcover's mess

    # Idempotent: second run is a no-op
    assert hs.repair_shelf_sync_collateral(db) == {"reopened": 0, "reclassified": 0}


@respx.mock
async def test_pull_wtr_adopts_owned_unmatched_book(db, admin_user, make_book):
    """A shelf entry whose book exists in the library but was never
    catalogue-matched adopts the match from the shelf and gets queued,
    instead of dead-ending as neither status nor wish."""
    from backend.models.wish import Wish

    user, _ = admin_user
    _link_pull_user(user)
    book = make_book(title="Owned Unmatched Novel", author="Local Author")
    assert book.hardcover_book_id is None

    def responder(request):
        body = request.read().decode()
        if "query WantToReadShelf" in body:
            return httpx.Response(200, json={"data": {"user_books": [
                {"id": 501, "book_id": 999}]}})
        if "query BooksByIds" in body:
            return httpx.Response(200, json={"data": {"books": [
                {"id": 999, "title": "Owned Unmatched Novel", "slug": "owned-unmatched",
                 "contributions": [{"author": {"name": "Local Author"}}]}]}})
        if "query BookEdition" in body:
            return httpx.Response(200, json={"data": {"books": [
                {"id": 999, "pages": 320, "slug": "owned-unmatched",
                 "editions": [{"id": 777, "pages": 320, "users_count": 5,
                               "reading_format_id": 1, "language": {"code2": "en"}}]}]}})
        return httpx.Response(200, json={"data": {}})

    respx.post(HARDCOVER_URL).mock(side_effect=responder)
    async with httpx.AsyncClient() as client:
        stats = await hs.pull_want_to_read(db, client, user, hs._Budget(50))

    assert stats["adopted"] == 1
    assert stats["wished"] == 0
    assert book.hardcover_book_id == 999
    assert book.hardcover_match_method == "shelf"
    assert book.hardcover_edition_id == 777
    row = db.query(UserBookStatus).filter_by(user_id=user.id, book_id=book.id).one()
    assert row.status == "want_to_read"
    assert row.hardcover_user_book_id == 501
    assert not hs.needs_sync(row)
    assert db.query(Wish).filter_by(user_id=user.id).count() == 0
