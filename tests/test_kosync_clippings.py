"""Clippings synced by a CrossInk reader (extended KOSync API).

As in test_kosync_stats.py, the reader cannot run in CI, so these tests hold
Tome to the firmware's own handling of each reply. The helpers below are
re-implemented from CrossInk ``src/network/ClippingsUpload.cpp`` and
``src/network/StatsUploadClient.cpp`` (v2.0.0-rc; the development branch only
differs in buffering the download on the SD card and in the type of the
``put`` count argument, which the clippings client passes as ``true``/1):

- ``_put_delivered``: StatsUploadClient::put with ``book`` set (rc lines
  100-114): 200, a reply shorter than its 256-byte buffer, ``until`` an
  unsigned integer and ``accepted`` exactly 1. A 404/405 also marks the whole
  server as having no extended API (``recordMissingApi``, line 24).
- ``_read_page``: ClippingsUpload::download (rc lines 158-212): the checks a
  ``?cursor=N&limit=1&format=reader`` page must pass before the reader applies
  it.
- ``Reader``: the reader's sync (ClippingsUpload::sync, upload then
  download, lines 241-251) and its merge (ClippingStore::mergeFromSync,
  ClippingStore.cpp lines 708-746), so a test can run whole syncs between
  two readers and Tome.
"""
import json

import pytest

from backend.core.security import create_access_token, hash_password
from backend.models.book import BOOK_NO_FILE, Book
from backend.models.kosync import KOSyncClipping, KOSyncDocumentMap, KOSyncUser
from backend.models.tome_sync import ApiKey, Annotation
from backend.models.user import User

BASE = "/api/v1/api/v1"
DOC = "0123456789abcdef0123456789abcdef"
DOC2 = "fedcba9876543210fedcba9876543210"
CLIPPING_TEXT_MAX = 4096                       # ClippingStore.h:18
DOWNLOAD_CAPACITY = CLIPPING_TEXT_MAX * 6 + 2048  # ClippingsUpload.cpp:133
PUT_REPLY_BUFFER = 256                         # StatsUploadClient.cpp:30
MAX_SAFE = 9007199254740991                    # ClippingsUpload.cpp:175


# ── the firmware's side ───────────────────────────────────────────────────────

def _is_uint(value, bits=64):
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value < 2**bits


def _put_delivered(response) -> bool:
    """StatsUploadClient::put(endpoint, payload, book=true): delivered?"""
    if response.status_code != 200:
        return False
    if len(response.content) >= PUT_REPLY_BUFFER:   # the callback refuses more
        return False
    reply = response.json()
    return _is_uint(reply.get("until")) and _is_uint(reply.get("accepted"), 32) and reply["accepted"] == 1


class Invalid(Exception):
    pass


def _read_page(response, document, cursor):
    """ClippingsUpload::download for one page. Returns (next, more, item|None)
    or raises Invalid where the firmware returns InvalidResponse."""
    if response.status_code in (404, 405):
        raise AssertionError("Unsupported: the reader would treat Tome as a plain KOSync server")
    if response.status_code != 200:
        raise Invalid(f"HTTP {response.status_code}")
    if len(response.content) >= DOWNLOAD_CAPACITY:
        raise Invalid("reply larger than the download buffer")
    reply = json.loads(response.content)
    if (reply.get("sync_version") is None and reply.get("cursor") is None
            and _is_uint(reply.get("until")) and reply.get("document") == document
            and isinstance(reply.get("more"), bool) and isinstance(reply.get("items"), list)):
        raise AssertionError("Unsupported: legacy (version 1) reply")
    if (reply.get("sync_version") != 2 or not isinstance(reply.get("document"), str)
            or reply["document"] != document or not _is_uint(reply.get("cursor"))
            or not isinstance(reply.get("more"), bool) or not isinstance(reply.get("items"), list)):
        raise Invalid("envelope")
    nxt, more, items = reply["cursor"], reply["more"], reply["items"]
    if (len(items) > 1 or (not items and (more or nxt != cursor))
            or (len(items) == 1 and (nxt <= cursor or nxt > MAX_SAFE))):
        raise Invalid("cursor")
    if not items:
        return nxt, more, None
    item = items[0]
    if (not isinstance(item.get("id"), str) or not _is_uint(item.get("revision"))
            or item["revision"] != nxt or not _is_uint(item.get("deleted"), 32) or item["deleted"] > 1):
        raise Invalid("item head")
    cid = item["id"]
    if not cid or len(cid.encode()) > 64 or "\x00" in cid or any(not 0x21 <= ord(c) <= 0x7e for c in cid):
        raise Invalid("id")
    if item["deleted"] == 0:
        for field in ("spine", "start_page", "end_page", "pages", "start_word", "end_word", "words"):
            if not _is_uint(item.get(field), 16):
                raise Invalid(field)
        if (not _is_uint(item.get("created_at"), 32) or not isinstance(item.get("text"), str)
                or not isinstance(item.get("chapter"), str)
                or (item.get("para") is not None and not _is_uint(item["para"], 16))):
            raise Invalid("fields")
        text = item["text"]
        if not text or len(text.encode()) > CLIPPING_TEXT_MAX or "\x00" in text:
            raise Invalid("text")
    return nxt, more, item


def _clip(cid, text="So we beat on, boats against the current", created_at=1752300000, **over):
    """A clipping as the reader uploads it (ClippingsUpload.cpp:89-104):
    no note, no colour, no source offsets."""
    item = {
        "id": cid, "spine": 7, "start_page": 12, "end_page": 13, "pages": 40,
        "start_word": 5, "end_word": 22, "words": 30, "para": 96,
        "chapter": "Chapter 8", "text": text, "created_at": created_at,
        "layout_signature": 123456789,
    }
    item.update(over)
    return item


class Reader:
    """One CrossInk reader's clippings for one document, and its sync."""

    def __init__(self, client, acc, document=DOC):
        self.client, self.acc, self.document = client, acc, document
        self.clips = {}        # id -> (item, made_here)
        self.deletions = []    # ids deleted here, not yet delivered
        self.cursor = 0
        self.requests = []

    def make(self, cid, **kw):
        self.clips[cid] = (_clip(cid, **kw), True)

    def delete(self, cid):
        self.clips.pop(cid)
        self.deletions.append(cid)

    def _put(self, item):
        r = self.client.put(f"{BASE}/clippings/{self.document}", headers=_headers(self.acc),
                            json={"items": [item]})
        self.requests.append(("PUT", item["id"], r.status_code))
        return _put_delivered(r)

    def sync(self):
        # upload: deletions first, then the clippings made here
        for cid in list(self.deletions):
            if not self._put({"id": cid, "deleted": 1}):
                return False
        self.deletions.clear()                  # acknowledgeDeletions
        for item, made_here in list(self.clips.values()):
            if made_here and not self._put(item):
                return False
        # download: one item per page until more is false
        while True:
            url = f"{BASE}/clippings/{self.document}?cursor={self.cursor}&limit=1&format=reader"
            r = self.client.get(url, headers=_headers(self.acc))
            self.requests.append(("GET", self.cursor, r.status_code))
            nxt, more, item = _read_page(r, self.document, self.cursor)
            if item is not None and item["id"] not in self.deletions:
                if item["deleted"]:
                    self.clips.pop(item["id"], None)
                elif item["id"] not in self.clips:
                    self.clips[item["id"]] = (item, False)   # imported: never uploaded again
            self.cursor = nxt
            if not more:
                return True

    def texts(self):
        return sorted(item["text"] for item, _ in self.clips.values())


# ── Tome-side helpers ─────────────────────────────────────────────────────────

def _headers(acc):
    return {"x-auth-user": acc.username, "x-auth-key": acc.userkey}


def _jwt(user):
    return {"Authorization": f"Bearer {create_access_token(subject=user.id)}"}


def _member(db, name):
    user = User(username=name, email=f"{name}@example.com", hashed_password=hash_password("pass"),
                is_active=True, role="member", must_change_password=False)
    db.add(user)
    db.flush()
    return user


def _account(db, user, username=None):
    row = KOSyncUser(username=username or user.username, userkey="k" * 32, user_id=user.id)
    db.add(row)
    db.flush()
    return row


def _link(db, user, book, document=DOC):
    db.add(KOSyncDocumentMap(tome_user_id=user.id, document=document, book_id=book.id))
    db.flush()


def _put(client, acc, *items, document=DOC):
    return client.put(f"{BASE}/clippings/{document}", headers=_headers(acc), json={"items": list(items)})


def _get(client, acc, document=DOC, **params):
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return client.get(f"{BASE}/clippings/{document}?{query}", headers=_headers(acc))


def _rows(db, **filters):
    return db.query(KOSyncClipping).filter_by(**filters).order_by(KOSyncClipping.revision).all()


def _highlights(client, user, **params):
    query = "&".join(f"{k}={v}" for k, v in {"include_clippings": "true", **params}.items())
    r = client.get(f"/api/annotations?{query}", headers=_jwt(user))
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture()
def acc(db, admin_user):
    """The admin's sync account, under the Tome username (the manual link
    endpoint finds the account by that name)."""
    user, _ = admin_user
    return _account(db, user)


# ── the firmware's sync, end to end ───────────────────────────────────────────

def test_two_readers_sync_clippings_and_deletions_through_tome(db, client, acc):
    a, b = Reader(client, acc), Reader(client, acc)
    a.make("a" * 32, text="first")
    a.make("b" * 32, text="second")
    a.make("c" * 32, text="third")
    assert a.sync()
    assert [m for m, *_ in a.requests].count("PUT") == 3
    assert a.texts() == ["first", "second", "third"]

    assert b.sync()                          # b restores all three
    assert b.texts() == ["first", "second", "third"]
    assert all(not here for _, here in b.clips.values())
    gets = [r for r in b.requests if r[0] == "GET"]
    # one item per page; the third says more is false, so no empty page
    assert gets == [("GET", 0, 200), ("GET", gets[1][1], 200), ("GET", gets[2][1], 200)]
    assert 0 < gets[1][1] < gets[2][1]

    a.delete("b" * 32)
    assert a.sync()
    assert b.sync()
    assert b.texts() == ["first", "third"]
    # b uploads nothing it only restored (ClippingsUpload.cpp:86)
    assert not [r for r in b.requests if r[0] == "PUT"]
    row = _rows(db, clip_id="b" * 32)[0]
    assert row.deleted and row.text is None


def test_a_reader_that_syncs_again_finds_nothing_new(db, client, acc):
    a = Reader(client, acc)
    a.make("a" * 32)
    assert a.sync()
    revision = _rows(db)[0].revision
    a.requests.clear()
    assert a.sync()                          # re-sends its clipping, as the firmware does
    assert _rows(db)[0].revision == revision
    assert [r for r in a.requests if r[0] == "GET"] == [("GET", revision, 200)]


def test_a_reupload_is_idempotent(db, client, acc):
    item = _clip("x1")
    first = _put(client, acc, item)
    assert _put_delivered(first)
    before = [(r.revision, r.updated_at) for r in _rows(db)]
    again = _put(client, acc, item)
    assert _put_delivered(again)
    assert [(r.revision, r.updated_at) for r in _rows(db)] == before
    assert len(_rows(db)) == 1


def test_note_and_colour_change_and_survive_the_readers_uploads(db, client, acc):
    assert _put_delivered(_put(client, acc, _clip("x1")))
    rev = _rows(db)[0].revision
    # another client of the API sets a note and colour
    assert _put_delivered(_put(client, acc, _clip("x1", note="Gatsby's last line", color="yellow")))
    row = _rows(db)[0]
    assert (row.note, row.color) == ("Gatsby's last line", "yellow")
    assert row.revision > rev
    # the reader's upload carries neither (ClippingsUpload.cpp:104): both stay
    assert _put_delivered(_put(client, acc, _clip("x1")))
    db.expire_all()
    assert (_rows(db)[0].note, _rows(db)[0].color) == ("Gatsby's last line", "yellow")
    # a sent null clears
    assert _put_delivered(_put(client, acc, _clip("x1", note=None)))
    db.expire_all()
    assert _rows(db)[0].note is None and _rows(db)[0].color == "yellow"


def test_format_reader_leaves_note_and_colour_out(client, acc):
    _put(client, acc, _clip("x1", note="n", color="red"))
    full = _get(client, acc, cursor=0, limit=5).json()["items"][0]
    reader = _get(client, acc, cursor=0, limit=5, format="reader").json()["items"][0]
    assert (full["note"], full["color"]) == ("n", "red")
    assert "note" not in reader and "color" not in reader
    assert {k: v for k, v in full.items() if k not in ("note", "color")} == reader


def test_a_page_is_what_the_reader_restores(client, acc):
    sent = _clip("x1", para=None, start_offset=10, end_offset=40)
    sent.pop("para")
    _put(client, acc, sent)
    nxt, more, item = _read_page(_get(client, acc, cursor=0, limit=1, format="reader"), DOC, 0)
    assert not more and item["revision"] == nxt
    for key in ("id", "spine", "start_page", "end_page", "pages", "start_word", "end_word",
                "words", "chapter", "text", "created_at", "layout_signature", "start_offset", "end_offset"):
        assert item[key] == sent[key], key
    assert item["para"] is None and item["deleted"] == 0


def test_the_document_comes_back_exactly_as_requested(client, acc):
    upper = DOC.upper()
    _put(client, acc, _clip("x1"), document=upper)
    r = _get(client, acc, document=upper, cursor=0, limit=1, format="reader")
    assert r.json()["document"] == upper
    _read_page(r, upper, 0)
    # stored under one key whatever the case
    assert _read_page(_get(client, acc, cursor=0, limit=1), DOC, 0)[2]["id"] == "x1"


def test_an_unknown_document_is_an_empty_page_not_a_404(client, acc):
    """The reader pulls clippings for every EPUB on the card
    (StatsUploadActivity.cpp:977-996), most of which Tome has never heard of."""
    r = _get(client, acc, document=DOC2, cursor=0, limit=1, format="reader")
    assert _read_page(r, DOC2, 0) == (0, False, None)
    r = _get(client, acc, document=DOC2, cursor=17, limit=1, format="reader")
    assert _read_page(r, DOC2, 17) == (17, False, None)


# ── deletions ────────────────────────────────────────────────────────────────

def test_a_deleted_id_stays_deleted(db, client, acc):
    _put(client, acc, _clip("x1", note="kept?"))
    deleted = _put(client, acc, {"id": "x1", "deleted": 1})
    assert _put_delivered(deleted)
    rev = _rows(db)[0].revision
    # a late upload of the same id (another reader that had not synced yet)
    late = _put(client, acc, _clip("x1"))
    assert _put_delivered(late)              # acknowledged, so it is not retried forever
    row = _rows(db)[0]
    assert row.deleted and row.text is None and row.note is None
    assert row.revision == rev
    # deleting again changes nothing either
    assert _put_delivered(_put(client, acc, {"id": "x1", "deleted": True}))
    assert _rows(db)[0].revision == rev
    nxt, more, item = _read_page(_get(client, acc, cursor=0, limit=1, format="reader"), DOC, 0)
    assert item["deleted"] == 1 and item["id"] == "x1"


def test_deleting_an_id_tome_never_saw_leaves_a_tombstone(db, client, acc):
    assert _put_delivered(_put(client, acc, {"id": "never-seen", "deleted": 1}))
    assert _put_delivered(_put(client, acc, _clip("never-seen")))
    assert [r.deleted for r in _rows(db)] == [True]


# ── limits and malformed input ───────────────────────────────────────────────

def test_batch_and_text_limits(db, client, acc):
    assert _put(client, acc, *[_clip(f"id{i}", text=f"t{i}") for i in range(50)]).json()["accepted"] == 50
    assert _put(client, acc, *[_clip(f"x{i}") for i in range(51)]).status_code == 400
    assert _put(client, acc, _clip("big", text="é" * 2048)).status_code == 200   # 4096 bytes
    assert _put(client, acc, _clip("big2", text="é" * 2048 + "a")).status_code == 400
    assert _put(client, acc, _clip("ch", chapter="c" * 100)).status_code == 200
    assert _rows(db, clip_id="ch")[0].chapter == "c" * 64
    assert _put(client, acc, _clip("nt", note="n" * 4097)).status_code == 200
    assert _rows(db, clip_id="nt")[0].note is None
    assert _put(client, acc, _clip("id-" + "x" * 61)).status_code == 200          # 64 chars
    assert _put(client, acc, _clip("id-" + "x" * 62)).status_code == 400


def test_page_size_follows_the_reference_server(client, acc):
    _put(client, acc, *[_clip(f"id{i:03}", text=f"t{i}") for i in range(50)])
    _put(client, acc, *[_clip(f"j{i:03}", text=f"u{i}") for i in range(50)])
    _put(client, acc, *[_clip(f"k{i:03}", text=f"v{i}") for i in range(5)])
    assert len(_get(client, acc, cursor=0).json()["items"]) == 50          # default
    page = _get(client, acc, cursor=0, limit=500).json()
    assert len(page["items"]) == 100 and page["more"] is True              # capped
    assert len(_get(client, acc, cursor=0, limit=0).json()["items"]) == 50
    assert len(_get(client, acc, cursor=0, limit="abc").json()["items"]) == 50
    assert len(_get(client, acc, cursor=page["cursor"], limit=100).json()["items"]) == 5


def test_the_largest_clipping_fits_the_readers_download_buffer(client, acc):
    """Worst case JSON escaping (ClippingsUpload.cpp:131-133): every byte a
    control character, written as \\u00XX."""
    text = "\x01" * CLIPPING_TEXT_MAX
    assert _put(client, acc, _clip("big", text=text, chapter="\x02" * 64)).status_code == 200
    r = _get(client, acc, cursor=0, limit=1, format="reader")
    assert _read_page(r, DOC, 0)[2]["text"] == text


def test_a_put_reply_fits_the_readers_reply_buffer(client, acc):
    r = _put(client, acc, _clip("x1"))
    assert len(r.content) < PUT_REPLY_BUFFER and _put_delivered(r)


MALFORMED = [
    {},
    {"items": None},
    {"items": "x"},
    {"items": []},
    {"items": [None]},
    {"items": [[]]},
    {"items": [{"spine": 1, "text": "t"}]},                       # no id
    {"items": [_clip("")]},
    {"items": [_clip("has space")]},
    {"items": [_clip("ünï")]},
    {"items": [_clip(5)]},
    {"items": [{k: v for k, v in _clip("x").items() if k != "spine"}]},
    {"items": [_clip("x", spine=-1)]},
    {"items": [_clip("x", spine=70000)]},                         # the reader reads it as uint16
    {"items": [_clip("x", spine="7")]},
    {"items": [_clip("x", spine=True)]},
    {"items": [_clip("x", spine=1.5)]},
    {"items": [_clip("x", start_page=None)]},
    {"items": [_clip("x", para=65536)]},
    {"items": [_clip("x", created_at=2**32)]},
    {"items": [_clip("x", layout_signature=-1)]},
    {"items": [_clip("x", text="")]},
    {"items": [_clip("x", text=None)]},
    {"items": [_clip("x", text=["a"])]},
    {"items": [_clip("x", text="a\x00b")]},                        # the reader refuses it on the way back
    {"items": [_clip("x", start_offset=5)]},
    {"items": [_clip("x", start_offset=5, end_offset=5)]},
    {"items": [_clip("x", start_offset=5, end_offset=None)]},
    {"items": [_clip("ok"), _clip("x", spine=-1)]},               # one bad item refuses the batch
]


@pytest.mark.parametrize("body", MALFORMED)
def test_malformed_uploads_are_refused_with_400_and_write_nothing(db, client, acc, body):
    r = client.put(f"{BASE}/clippings/{DOC}", headers=_headers(acc), json=body)
    assert r.status_code == 400, r.text
    assert not _put_delivered(r)
    assert _rows(db) == []


@pytest.mark.parametrize("raw", [
    b"{not json",
    b"[]",
    b'"items"',
    b'{"items": [{"id": "x", "spine": 1, "text": "\\ud800"}]}',            # lone surrogate
    b'{"items": [{"id": "x", "spine": 1, "text": "t", "note": "\\udfff"}]}',
    b'{"items": [{"id": "x", "spine": 1, "text": "t", "chapter": "\\ud800"}]}',
])
def test_unparseable_uploads_are_client_errors_not_server_errors(db, client, acc, raw):
    r = client.put(f"{BASE}/clippings/{DOC}", headers={**_headers(acc), "Content-Type": "application/json"},
                   content=raw)
    assert r.status_code in (400, 422), r.text
    assert _rows(db) == []


@pytest.mark.parametrize("path", [
    "/clippings/not-a-hash",
    "/clippings/" + "g" * 32,
    "/clippings/" + "a" * 31,
])
def test_a_malformed_document_is_400_on_both_routes(client, acc, path):
    assert client.put(f"{BASE}{path}", headers=_headers(acc), json={"items": [_clip("x")]}).status_code == 400
    assert client.get(f"{BASE}{path}?cursor=0", headers=_headers(acc)).status_code == 400


@pytest.mark.parametrize("cursor", ["abc", "-1", "1.5", str(2**53), "%20"])
def test_a_malformed_cursor_is_400(client, acc, cursor):
    assert _get(client, acc, cursor=cursor, limit=1).status_code == 400


def test_auth_and_unlinked_accounts(db, client, admin_user):
    """No credentials: 401. A sync account with no Tome user: 404 on both
    routes, like every extended route, because the clippings would have no
    owner and the reader forgets a deletion once it is acknowledged."""
    user, _ = admin_user
    unlinked = KOSyncUser(username="nobody", userkey="k" * 32, user_id=None)
    db.add(unlinked)
    db.flush()
    assert client.put(f"{BASE}/clippings/{DOC}", json={"items": [_clip("x")]}).status_code == 401
    assert _put(client, unlinked, {"id": "x", "deleted": 1}).status_code == 404
    assert _get(client, unlinked, cursor=0, limit=1).status_code == 404
    assert _rows(db) == []


# ── the legacy since mode ────────────────────────────────────────────────────

def test_since_mode_still_answers(client, acc):
    _put(client, acc, _clip("x1"))
    page = _get(client, acc, since=0).json()
    assert page["sync_version"] == 2 and [i["id"] for i in page["items"]] == ["x1"]
    assert "note" in page["items"][0]
    assert _get(client, acc, since=page["until"] + 10).json()["items"] == []


# ── users and documents ──────────────────────────────────────────────────────

def test_two_users_with_the_same_clipping_id_never_meet(db, client, acc, admin_user):
    other = _member(db, "reader2")
    acc2 = _account(db, other)
    assert _put_delivered(_put(client, acc, _clip("same", text="mine")))
    assert _put_delivered(_put(client, acc2, _clip("same", text="theirs")))
    assert _put_delivered(_put(client, acc2, {"id": "same", "deleted": 1}))
    mine = _get(client, acc, cursor=0, limit=10).json()["items"]
    theirs = _get(client, acc2, cursor=0, limit=10).json()["items"]
    assert [(i["text"], i["deleted"]) for i in mine] == [("mine", 0)]
    assert [(i["id"], i["deleted"]) for i in theirs] == [("same", 1)]


def test_revisions_only_grow(db, client, acc):
    _put(client, acc, _clip("a"), _clip("b"), document=DOC)
    _put(client, acc, _clip("c"), document=DOC2)
    _put(client, acc, {"id": "a", "deleted": 1}, document=DOC)
    revisions = [r.revision for r in _rows(db)]
    assert revisions == sorted(set(revisions)) and len(revisions) == 3
    page = _get(client, acc, cursor=0, limit=10).json()
    assert [i["id"] for i in page["items"]] == ["b", "a"]   # a moved past b when deleted


# ── showing clippings in Tome ────────────────────────────────────────────────

def test_an_unlinked_document_shows_once_it_is_linked_and_follows_a_relink(
        db, client, acc, admin_user, make_book):
    user, _ = admin_user
    first, second = make_book(title="Gatsby"), make_book(title="Tender Is the Night")
    _put(client, acc, _clip("x1", text="boats against the current"))
    assert _highlights(client, user)["total"] == 0

    # the manual link works for a document that only has clippings
    r = client.post(f"/api/books/{first.id}/link-kosync", json={"document": DOC})
    assert r.status_code == 200, r.text
    data = _highlights(client, user)
    assert data["total"] == 1 and data["items"][0]["book_id"] == first.id

    r = client.post(f"/api/books/{second.id}/link-kosync", json={"document": DOC})
    assert r.status_code == 200, r.text
    data = _highlights(client, user)
    assert [i["book_title"] for i in data["items"]] == ["Tender Is the Night"]
    per_book = client.get(f"/api/books/{first.id}/annotations?include_clippings=true").json()
    assert per_book == []


def test_a_progress_push_links_the_document_and_the_clippings_follow(db, client, acc, admin_user, make_book):
    """A file-less book made from the reader's metadata on a progress push
    (backend/services/kosync_books.py) carries its owner's clippings."""
    user, _ = admin_user
    _put(client, acc, _clip("x1", text="Stat rosa pristina nomine"))
    r = client.put("/api/v1/syncs/progress", headers=_headers(acc), json={
        "document": DOC, "percentage": 0.4, "progress": "/body/x[1]", "device": "CrossInk",
        "device_id": "A1B2C3D4E5F6",
        "metadata": {"filename": "rosa.epub", "title": "Il nome della rosa", "authors": "Umberto Eco"},
    })
    assert r.status_code == 200, r.text
    book = db.query(Book).filter(Book.status == BOOK_NO_FILE).one()
    data = _highlights(client, user)
    assert [(i["book_id"], i["highlighted_text"]) for i in data["items"]] == [(book.id, "Stat rosa pristina nomine")]
    per_book = client.get(f"/api/books/{book.id}/annotations?include_clippings=true").json()
    assert [i["highlighted_text"] for i in per_book] == ["Stat rosa pristina nomine"]


def test_file_less_books_of_someone_else_stay_hidden(db, client, acc, admin_user):
    """A file-less book is private to the user who read it. A member whose
    document somehow points at the admin's file-less book sees nothing."""
    user, _ = admin_user
    book = Book(title="Admin's private reading", status=BOOK_NO_FILE, added_by=user.id)
    db.add(book)
    db.flush()
    member = _member(db, "reader2")
    macc = _account(db, member)
    _link(db, member, book)
    _put(client, macc, _clip("m1", text="not for anyone else"))
    assert _highlights(client, member)["total"] == 0
    # the admin's own list never holds another user's clippings
    _link(db, user, book)
    assert _highlights(client, user)["total"] == 0
    _put(client, acc, _clip("a1", text="the admin's own"))
    assert [i["highlighted_text"] for i in _highlights(client, user)["items"]] == ["the admin's own"]


def test_highlights_list_marks_clippings_and_mixes_them_in(db, client, acc, admin_user, make_book):
    user, _ = admin_user
    book = make_book(title="Gatsby")
    _link(db, user, book)
    db.add(Annotation(user_id=user.id, book_id=book.id, anchor="/body/p[1]", highlighted_text="koreader one",
                      koreader_datetime="2000-01-01 10:00:00"))
    db.flush()
    _put(client, acc, _clip("x1", text="crossink one", created_at=1752300000, note="mine", color="blue"))
    _put(client, acc, _clip("x2", text="gone"))
    _put(client, acc, {"id": "x2", "deleted": 1})

    plain = client.get("/api/annotations", headers=_jwt(user)).json()
    assert [i["highlighted_text"] for i in plain["items"]] == ["koreader one"]  # opt-in only

    data = _highlights(client, user)
    assert data["total"] == 2 and data["books"] == 1
    clip = data["items"][0]                     # 2025 is newer than 2000
    assert clip["id"] == f"clipping:{_rows(db, clip_id='x1')[0].id}"
    assert clip["source"] == "crossink" and clip["read_only"] is True
    assert (clip["highlighted_text"], clip["note"], clip["color"], clip["chapter"]) == \
        ("crossink one", "mine", "blue", "Chapter 8")
    assert clip["datetime"].startswith("2025-07-1")
    assert clip["book_title"] == "Gatsby" and clip["synced_at"].endswith("Z")
    assert isinstance(data["items"][1]["id"], int)

    assert _highlights(client, user, q="CROSSINK")["total"] == 1
    assert _highlights(client, user, only_notes=1)["total"] == 1
    assert _highlights(client, user, book_id=book.id + 1)["total"] == 0
    assert _highlights(client, user, limit=1, offset=1)["items"][0]["highlighted_text"] == "koreader one"


# 2025-07-12 22:30:00 UTC: 2025-07-13 00:30 in Vienna (CEST, UTC+2).
LATE_EVENING_UTC = 1752359400
VIENNA = "Europe/Vienna"


def _fix_now(monkeypatch, iso_utc):
    """Pin both clocks the highlights views read: the server's local time for
    KOReader highlights and the UTC now that clippings are judged by."""
    from datetime import datetime, timezone

    from backend.api import annotations as annotations_api
    from backend.services import kosync_clippings

    now = datetime.fromisoformat(iso_utc).replace(tzinfo=timezone.utc)
    monkeypatch.setattr(kosync_clippings, "_utcnow", lambda: now)
    monkeypatch.setattr(annotations_api, "func_now_str", lambda: now.strftime("%Y-%m-%d %H:%M:%S"))


def test_clipping_times_are_shown_in_the_viewers_zone(db, client, acc, admin_user, make_book, monkeypatch):
    user, _ = admin_user
    book = make_book(title="Gatsby")
    _link(db, user, book)
    _put(client, acc, _clip("x1", text="after midnight in Vienna", created_at=LATE_EVENING_UTC))
    # one year later, 13 July, mid-morning in Vienna
    _fix_now(monkeypatch, "2026-07-13T08:00:00")

    vienna = _highlights(client, user, tz=VIENNA, tz_offset=-120)
    assert vienna["items"][0]["datetime"] == "2025-07-13 00:30:00"
    assert _highlights(client, user, tz=VIENNA, on_this_day=1)["total"] == 1
    # the offset alone (what the browser sends without a zone name) agrees
    assert _highlights(client, user, tz_offset=-120)["items"][0]["datetime"] == "2025-07-13 00:30:00"
    # New York sees 12 July 18:30, which is not "this day" there on 13 July
    ny = _highlights(client, user, tz="America/New_York")
    assert ny["items"][0]["datetime"] == "2025-07-12 18:30:00"
    assert _highlights(client, user, tz="America/New_York", on_this_day=1)["total"] == 0
    # no zone sent: UTC
    assert _highlights(client, user)["items"][0]["datetime"] == "2025-07-12 22:30:00"
    # an unknown zone name falls back to the offset
    assert _highlights(client, user, tz="Mars/Olympus", tz_offset=-120)["items"][0]["datetime"] == \
        "2025-07-13 00:30:00"

    per_book = client.get(f"/api/books/{book.id}/annotations?include_clippings=1&tz={VIENNA}").json()
    assert per_book[0]["datetime"] == "2025-07-13 00:30:00"

    spot = client.get(f"/api/annotations/spotlight?include_clippings=1&tz={VIENNA}", headers=_jwt(user)).json()
    assert spot["on_this_day"] is True and spot["highlight"]["datetime"] == "2025-07-13 00:30:00"
    spot_ny = client.get("/api/annotations/spotlight?include_clippings=1&tz=America/New_York",
                         headers=_jwt(user)).json()
    assert spot_ny["on_this_day"] is False and spot_ny["highlight"]["source"] == "crossink"
    again = client.get(f"/api/annotations/spotlight?include_clippings=1&tz={VIENNA}"
                       f"&exclude={spot['highlight']['id']}", headers=_jwt(user)).json()
    assert again["highlight"]["id"] == spot["highlight"]["id"]   # the only one
    assert client.get("/api/annotations/spotlight", headers=_jwt(user)).json()["highlight"] is None


def test_clipping_order_among_koreader_highlights_uses_the_viewers_zone(db, client, acc, admin_user, make_book):
    """A KOReader highlight carries the device's wall-clock time. One made at
    00:15 Vienna time is older than the clipping made at 00:30 Vienna time
    (22:30 UTC the evening before), which shows only in Vienna time."""
    user, _ = admin_user
    book = make_book()
    _link(db, user, book)
    db.add(Annotation(user_id=user.id, book_id=book.id, anchor="/body/p[1]", highlighted_text="koreader",
                      koreader_datetime="2025-07-13 00:15:00"))
    db.flush()
    _put(client, acc, _clip("x1", text="crossink", created_at=LATE_EVENING_UTC))
    order = [i["highlighted_text"] for i in _highlights(client, user, tz=VIENNA)["items"]]
    assert order == ["crossink", "koreader"]   # newest first
    order_utc = [i["highlighted_text"] for i in _highlights(client, user)["items"]]
    assert order_utc == ["koreader", "crossink"]


def test_clipping_times_do_not_depend_on_the_servers_zone(db, client, acc, admin_user, make_book, monkeypatch):
    import os
    import time as _time

    user, _ = admin_user
    _link(db, user, make_book())
    _put(client, acc, _clip("x1", created_at=LATE_EVENING_UTC))
    _fix_now(monkeypatch, "2026-07-13T08:00:00")
    saved = os.environ.get("TZ")
    seen = []
    try:
        for server_zone in ("UTC", "America/Los_Angeles", "Asia/Tokyo"):
            os.environ["TZ"] = server_zone
            _time.tzset()
            data = _highlights(client, user, tz=VIENNA, on_this_day=1)
            plain = _highlights(client, user)
            seen.append((data["total"], data["items"][0]["datetime"], plain["items"][0]["datetime"]))
    finally:
        if saved is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = saved
        _time.tzset()
    assert seen == [(1, "2025-07-13 00:30:00", "2025-07-12 22:30:00")] * 3


def test_highlight_search_treats_clippings_like_annotations(db, client, acc, admin_user, make_book):
    """Annotations are searched with SQL ilike (lower(x) LIKE lower(q)):
    % and _ are wildcards and only ASCII letters fold case. Clippings match the same way."""
    user, _ = admin_user
    book = make_book(title="Gatsby")
    _link(db, user, book)
    for i, text in enumerate(["ÉCOLE ancienne", "100% sure", "a_b", "axb"]):
        db.add(Annotation(user_id=user.id, book_id=book.id, anchor=f"/body/p[{i}]", highlighted_text=text,
                          koreader_datetime=f"2024-01-0{i + 1} 10:00:00"))
        _put(client, acc, _clip(f"c{i}", text=text))
    db.flush()

    def sources(q):
        items = _highlights(client, user, q=q)["items"]
        return sorted((i["highlighted_text"], i.get("source", "koreader")) for i in items)

    for q in ("école", "ÉCOLE", "ANCIENNE", "100%", "a_b", "a%b", "zzz"):
        found = sources(q)
        # the same texts match, once as an annotation and once as a clipping
        assert sorted(t for t, s in found if s == "koreader") == sorted(t for t, s in found if s == "crossink"), q
    assert {t for t, _ in sources("a_b")} == {"a_b", "axb"}       # _ is any one character
    assert sources("école") == []                                  # É does not fold to é (ASCII only)
    assert {t for t, _ in sources("ANCIENNE")} == {"ÉCOLE ancienne"}


def _make_member_with_sync(db, name, sync_name):
    user = _member(db, name)
    return user, _account(db, user, username=sync_name)


def test_revisions_never_go_back_after_the_rows_holding_them_are_gone(db, client):
    """The reader keeps its cursor per server + sync username + document. If
    the user is deleted and the same sync username is linked to a new user,
    a new change must still come after that reader's cursor."""
    old_user, sync = _make_member_with_sync(db, "olduser", "shared-sync")
    for i in range(3):
        assert _put_delivered(_put(client, sync, _clip(f"old{i}", text=f"old {i}")))
    reader = Reader(client, sync)
    assert reader.sync()
    old_cursor = reader.cursor
    assert old_cursor == max(r.revision for r in _rows(db))

    db.delete(old_user)                      # cascades the clippings, unlinks the sync account
    db.flush()
    db.expire_all()
    assert _rows(db) == [] and db.get(KOSyncUser, sync.id).user_id is None
    new_user = _member(db, "newuser")
    db.get(KOSyncUser, sync.id).user_id = new_user.id
    db.flush()

    other = Reader(client, sync)
    other.make("n" * 32, text="written after the relink")
    assert other.sync()
    page = _get(client, sync, cursor=old_cursor, limit=1, format="reader")
    nxt, more, item = _read_page(page, DOC, old_cursor)
    assert item is not None and item["text"] == "written after the relink"
    # (the reader keeps the old clippings it holds: nothing tells it they went)
    assert reader.sync() and "written after the relink" in reader.texts()


def test_two_changes_in_one_microsecond_still_get_increasing_revisions(db, client, acc, monkeypatch):
    from backend.services import kosync_clippings

    monkeypatch.setattr(kosync_clippings.time, "time_ns", lambda: 1_700_000_000_000_000_000)
    _put(client, acc, _clip("a", text="one"), _clip("b", text="two"), _clip("c", text="three"))
    revisions = [r.revision for r in _rows(db)]
    assert revisions == [1_700_000_000_000_000, 1_700_000_000_000_001, 1_700_000_000_000_002]
    # the clock stepping back changes nothing about the order
    monkeypatch.setattr(kosync_clippings.time, "time_ns", lambda: 1_600_000_000_000_000_000)
    _put(client, acc, {"id": "a", "deleted": 1})
    assert _rows(db, clip_id="a")[0].revision == 1_700_000_000_000_003
    assert all(r <= MAX_SAFE for r in revisions)


def test_revisions_follow_the_clock(db, client, acc):
    import time as _time

    before = _time.time_ns() // 1000
    _put(client, acc, _clip("a"))
    after = _time.time_ns() // 1000
    assert before <= _rows(db)[0].revision <= after


def test_a_retried_put_racing_the_first_into_a_new_link_is_not_a_500(db, client, acc, admin_user,
                                                                       make_book, monkeypatch):
    """The first PUT for a file Tome served links its document. A reader that
    timed out retries while the first request runs; the loser hits the unique
    constraint on the link. That has to be retried like the store itself."""
    from sqlalchemy.exc import IntegrityError

    from backend.api import kosync_stats as api_module
    from backend.models.ko_stats import KoHash

    user, _ = admin_user
    book = make_book()
    db.add(KoHash(book_id=book.id, ko_partial_md5=DOC, kind="raw"))
    db.flush()
    real = api_module._find_or_hash_link
    calls = []

    def racing(db_, user_id, document):
        calls.append(document)
        if len(calls) == 1:
            raise IntegrityError("INSERT INTO kosync_document_map", {}, Exception("UNIQUE constraint failed"))
        return real(db_, user_id, document)

    monkeypatch.setattr(api_module, "_find_or_hash_link", racing)
    # The test session holds every fixture in one outer transaction; let the
    # handler's rollback undo only what the request did, as it would in prod.
    savepoint = db.begin_nested()
    monkeypatch.setattr(db, "rollback", lambda: savepoint.rollback() if savepoint.is_active else None)
    r = _put(client, acc, _clip("x1", text="linked on first sight"))
    assert _put_delivered(r), r.text
    assert len(calls) == 2
    assert [i["highlighted_text"] for i in _highlights(client, user)["items"]] == ["linked on first sight"]


def test_tome_cannot_edit_or_delete_a_clipping(db, client, acc, admin_user, make_book):
    """With both an annotation and a clipping present: the clipping's display
    id is refused by the edit and delete routes, and the integer that equals
    the clipping's row id only ever reaches the annotation table."""
    user, _ = admin_user
    book = make_book()
    _link(db, user, book)
    _put(client, acc, _clip("x1", text="read only", note="n"))
    clip_row = _rows(db)[0]
    clip_id, clip_revision = clip_row.id, clip_row.revision
    db.add(Annotation(id=clip_id, user_id=user.id, book_id=book.id, anchor="/body/p[1]",
                      highlighted_text="koreader", note="old", koreader_datetime="2024-01-01 10:00:00"))
    db.flush()
    item = next(i for i in _highlights(client, user)["items"] if i.get("source") == "crossink")
    assert item["id"] == f"clipping:{clip_id}"

    assert client.put(f"/api/annotations/{item['id']}", json={"note": "edited"}).status_code == 422
    assert client.delete(f"/api/annotations/{item['id']}").status_code == 422

    edited = client.put(f"/api/annotations/{clip_id}", json={"note": "edited"})
    assert edited.status_code == 200 and edited.json()["highlighted_text"] == "koreader"
    db.expire_all()
    assert db.get(Annotation, clip_id).note == "edited"
    assert client.delete(f"/api/annotations/{clip_id}").status_code == 204
    db.expire_all()
    assert db.get(Annotation, clip_id) is None

    row = _rows(db)[0]
    assert (row.deleted, row.text, row.note, row.revision) == (False, "read only", "n", clip_revision)
    assert [(i["id"], i["note"]) for i in _highlights(client, user)["items"]] == [(item["id"], "n")]


def test_clippings_never_reach_the_plugin_or_the_web_reader(db, client, acc, admin_user, make_book):
    user, _ = admin_user
    book = make_book()
    _link(db, user, book)
    _put(client, acc, _clip("x1", text="crossink only"))
    key = ApiKey.generate()
    db.add(ApiKey(user_id=user.id, key_hash=ApiKey.hash_key(key), key_prefix=key[:11], label="t"))
    db.flush()
    plugin = {"Authorization": f"Bearer {key}"}

    got = client.get(f"/api/tome-sync/annotations/{book.id}", headers=plugin)
    assert got.status_code == 200 and "crossink only" not in got.text
    synced = client.post(f"/api/tome-sync/annotations/{book.id}/sync", headers=plugin,
                         json={"upserts": [], "deletes": []})
    assert synced.status_code == 200 and "crossink only" not in synced.text
    # the web reader's list (ReaderPage.tsx asks without include_clippings)
    reader = client.get(f"/api/books/{book.id}/annotations")
    assert reader.status_code == 200 and reader.json() == []
    # the book page asks for them
    page = client.get(f"/api/books/{book.id}/annotations?include_clippings=true").json()
    assert [(i["highlighted_text"], i["source"], i["anchor"], i["cfi"]) for i in page] == \
        [("crossink only", "crossink", None, None)]


def test_a_deletion_on_the_reader_removes_it_from_tome(db, client, acc, admin_user, make_book):
    user, _ = admin_user
    book = make_book()
    _link(db, user, book)
    _put(client, acc, _clip("x1"))
    assert _highlights(client, user)["total"] == 1
    _put(client, acc, {"id": "x1", "deleted": 1})
    assert _highlights(client, user)["total"] == 0
    assert client.get(f"/api/books/{book.id}/annotations?include_clippings=true").json() == []


def test_the_account_export_holds_the_clippings(client, acc):
    _put(client, acc, _clip("x1", text="exported", note="n"))
    _put(client, acc, {"id": "gone", "deleted": 1})
    export = client.get("/api/auth/me/backup").json()["kosync_legacy"]["clippings"]
    assert [(c["document"], c["id"], c["text"], c["note"], c["deleted"]) for c in export] == [
        (DOC, "x1", "exported", "n", 0), (DOC, "gone", None, None, 1),
    ]
