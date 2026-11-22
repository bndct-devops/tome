"""Tome's plugin for CrossPoint readers.

There is no reader in CI, so ``Reader`` does what the firmware does with the
plugin Tome hands out (CrossInk ``PluginCatalogActivity.cpp`` for linking and
the list, ``PluginEvents.cpp`` for sessions): paste the config, the token and
the event's values into the URL, header and body templates as plain text, and
send. A session request that is not answered 2xx stays at the head of the
reader's queue and holds every later session back.
"""
import io
import json
import time
import zipfile

import pytest

from backend.core.security import create_access_token, hash_password
from backend.models.audit_log import AuditLog
from backend.models.ko_stats import KoHash
from backend.models.kosync import KOSyncSessionReceipt, KOSyncUser
from backend.models.tome_sync import ApiKey, ReadingSession
from backend.models.user import User
from backend.models.user_book_status import UserBookStatus
from backend.services import crosspoint_link as link
from backend.services import crosspoint_plugin

STATS = "/api/v1/api/v1"
DEVICE = "A1B2C3D4E5F6"
DOC = "0123456789abcdef0123456789abcdef"
NOBODY = {"Authorization": "Bearer not-signed-in"}


@pytest.fixture(autouse=True)
def _fresh_links():
    link.reset()
    yield
    link.reset()


def _unpack(data):
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return {name: json.loads(zf.read(name)) for name in zf.namelist()}


class Reader:
    """The firmware's side: the plugin folder on its card, and how it fills
    and sends the requests the folder declares."""

    def __init__(self, client, files):
        self.client = client
        self.device = files["tome/device.json"]
        self.config = files["tome/config.json"]
        self.token = ""
        self.device_code = ""

    def _fill(self, template, extra=None):
        template = template.replace("{token}", self.token)
        for key, value in self.config.items():
            template = template.replace("{cfg." + key + "}", value)
        for key, value in (extra or {}).items():
            template = template.replace("{" + key + "}", value)
        return template

    def _send(self, request, extra=None, default_method="POST"):
        url = self._fill(request["url"], extra)
        assert url.startswith("http://testserver/"), url
        headers = {k: self._fill(v, extra) for k, v in request.get("headers", {}).items()}
        # The test client signs every request in as the admin; a reader sends
        # only what its plugin declares.
        headers.setdefault("Authorization", "")
        return self.client.request(
            request.get("method", default_method), url[len("http://testserver"):],
            headers=headers, content=self._fill(request.get("body", ""), extra) or None,
        )

    def ask_for_code(self):
        r = self._send(self.device["auth"]["request"])
        assert r.status_code == 200, r.text
        self.device_code = r.json()["device_code"]
        return r.json()

    def poll(self):
        r = self._send(self.device["auth"]["poll"], {"device_code": self.device_code})
        token = r.json().get(self.device["auth"].get("token_path", "access_token"))
        if token:
            self.token = token      # the firmware writes it to token.json
        return r

    def deliver(self, event):
        return self._send(self.device["events"]["reader.session"]["request"],
                          {"event." + k: v for k, v in event.items()})

    def open_plugin(self):
        return self._send(self.device["browse"], default_method="GET")


@pytest.fixture()
def files():
    return _unpack(crosspoint_plugin.build_zip("http://testserver/"))


@pytest.fixture()
def reader(client, files):
    """A reader linked to the admin user, the way a user does it."""
    r = Reader(client, files)
    code = r.ask_for_code()["user_code"]
    assert client.post("/api/v1/crosspoint/device/approve", json={"code": code}).status_code == 200
    assert r.poll().status_code == 200 and r.token
    return r


def _event(event_id="3fa9c21b-7", start=None, seconds=600, begin=1000, end=1400, document=DOC):
    start = int(time.time()) - 3600 if start is None else start
    return {
        "id": event_id, "ts": str(start + seconds + 5), "book": '/Books/A "quoted" name.epub',
        "document": document, "start_time": str(start), "end_time": str(start + seconds),
        "duration_seconds": str(seconds), "start_progress_bp": str(begin),
        "end_progress_bp": str(end), "progress_scale": "10000",
    }


def _link_book(db, make_book, title="Read On The Reader"):
    book = make_book(title=title)
    db.add(KoHash(book_id=book.id, ko_partial_md5=DOC, kind="baked"))
    db.flush()
    return book


def _user(db, name, role="member"):
    user = User(username=name, email=f"{name}@example.com", hashed_password=hash_password("pass"),
                is_active=True, role=role, must_change_password=False)
    db.add(user)
    db.flush()
    return user, {"Authorization": f"Bearer {create_access_token(user.id)}"}


# ── the files ─────────────────────────────────────────────────────────────────

def test_the_zip_is_one_folder_the_firmware_can_load(files):
    assert sorted(files) == ["tome/config.json", "tome/device.json", "tome/manifest.json"]
    manifest, device = files["tome/manifest.json"], files["tome/device.json"]
    assert manifest["name"] == "tome"                       # the folder's name
    assert manifest["version"] == device["version"]
    assert set(device["events"]) == {"reader.session"}
    assert device["config"] == {"file": "config.json"}
    # Without a list the firmware gives a plugin no screen, and so no sign-in.
    assert device["browse"]["url"]
    assert device["auth"]["type"] == "device_code"
    assert len(json.dumps(files["tome/config.json"])) < 2048      # firmware limits
    assert len(json.dumps(device)) < 8192


def test_the_zip_holds_the_server_address_and_nothing_about_the_account(client, db, admin_user):
    user, _ = admin_user
    db.add(KOSyncUser(username=user.username, userkey="k" * 32, user_id=user.id))
    db.flush()
    r = client.get("/api/v1/crosspoint/plugin.zip?server_url=https://tome.example.org/")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    files = _unpack(r.content)
    assert files["tome/config.json"] == {"server": "https://tome.example.org"}
    text = json.dumps(files)
    assert user.username not in text and "k" * 32 not in text
    assert client.get("/api/v1/crosspoint/plugin.zip", headers=NOBODY).status_code == 401


def test_the_session_body_stays_json_whatever_the_file_is_called(files):
    """Values are pasted in as text. The file name can hold a quote, so it is
    not in the body at all."""
    body = files["tome/device.json"]["events"]["reader.session"]["request"]["body"]
    assert "{event.book}" not in body
    for key, value in _event().items():
        body = body.replace("{event." + key + "}", value)
    assert json.loads(body)["document"] == DOC


# ── linking a reader ──────────────────────────────────────────────────────────

def test_the_reader_is_given_what_the_firmware_shows(client, files):
    offer = Reader(client, files).ask_for_code()
    assert set(offer) >= {"device_code", "user_code", "verification_uri", "interval", "expires_in"}
    assert len(offer["user_code"]) == 6 and offer["user_code"].isalnum()
    assert offer["verification_uri"] == f"http://testserver/link-reader?code={offer['user_code']}"
    assert len(offer["device_code"]) >= 40 and offer["user_code"] not in offer["device_code"]
    assert offer["interval"] >= 3 and offer["expires_in"] >= 60     # the firmware's own floors


def test_linking_hands_the_reader_a_device_key_once(client, db, admin_user, files):
    user, _ = admin_user
    r = Reader(client, files)
    code = r.ask_for_code()["user_code"]
    waiting = r.poll()
    assert waiting.status_code == 400 and waiting.json() == {"error": "authorization_pending"}
    assert db.query(ApiKey).count() == 0

    assert client.get(f"/api/v1/crosspoint/device/{code.lower()}").json() == {"code": code}
    assert client.post("/api/v1/crosspoint/device/approve", json={"code": f" {code[:3]}-{code[3:]} ".lower()}).json() == {"linked": True}

    done = r.poll()
    assert done.status_code == 200 and done.json()["access_token"] == r.token
    key = db.query(ApiKey).one()
    assert key.user_id == user.id and key.label.startswith("CrossPoint reader")
    assert key.key_hash == ApiKey.hash_reader_key(r.token) and r.token not in (key.key_hash, key.key_prefix)
    # A Tome from before reader keys looks keys up by the plain hash and would
    # let any key it finds onto the KOReader plugin routes: it must not find this one.
    assert db.query(ApiKey).filter(ApiKey.key_hash == ApiKey.hash_key(r.token)).count() == 0
    assert db.query(AuditLog).filter(AuditLog.action == "plugin_key.created").count() == 1
    assert db.query(AuditLog).filter(AuditLog.action == "crosspoint.reader_link_approved").count() == 1

    again = r.poll()
    assert again.status_code == 400 and again.json() == {"error": "expired_token"}
    assert db.query(ApiKey).count() == 1


def test_only_the_reader_that_asked_collects_the_key(client, db, files):
    mine, other = Reader(client, files), Reader(client, files)
    code = mine.ask_for_code()["user_code"]
    other.ask_for_code()
    client.post("/api/v1/crosspoint/device/approve", json={"code": code})
    assert other.poll().json() == {"error": "authorization_pending"}
    stranger = Reader(client, files)
    stranger.device_code = "guessed-" + "x" * 40
    assert stranger.poll().json() == {"error": "expired_token"}
    assert db.query(ApiKey).count() == 0
    assert mine.poll().status_code == 200


def test_a_code_cannot_be_confirmed_twice_or_by_someone_signed_out(client, db, files):
    r = Reader(client, files)
    code = r.ask_for_code()["user_code"]
    assert client.post("/api/v1/crosspoint/device/approve", json={"code": code}, headers=NOBODY).status_code == 401
    assert client.get(f"/api/v1/crosspoint/device/{code}", headers=NOBODY).status_code == 401
    assert client.post("/api/v1/crosspoint/device/approve", json={"code": code}).status_code == 200
    _, other = _user(db, "second")
    assert client.post("/api/v1/crosspoint/device/approve", json={"code": code}, headers=other).status_code == 404
    assert client.get(f"/api/v1/crosspoint/device/{code}", headers=other).status_code == 404


def test_the_key_belongs_to_whoever_confirmed(client, db, files):
    member, headers = _user(db, "second")
    r = Reader(client, files)
    code = r.ask_for_code()["user_code"]
    assert client.post("/api/v1/crosspoint/device/approve", json={"code": code}, headers=headers).status_code == 200
    r.poll()
    assert db.query(ApiKey).one().user_id == member.id
    assert r.deliver(_event()).json()["stored"] is True
    assert db.query(ReadingSession).one().user_id == member.id


def test_a_guest_cannot_link_a_reader(client, db, files):
    _, guest = _user(db, "visitor", role="guest")
    r = Reader(client, files)
    code = r.ask_for_code()["user_code"]
    assert client.post("/api/v1/crosspoint/device/approve", json={"code": code}, headers=guest).status_code == 403
    assert client.get(f"/api/v1/crosspoint/device/{code}", headers=guest).status_code == 403
    assert r.poll().json() == {"error": "authorization_pending"}


def test_an_expired_code_links_nothing(client, db, files):
    device_code, user_code = link.start(now=time.time() - link.PENDING_TTL - 1)
    assert client.post("/api/v1/crosspoint/device/approve", json={"code": user_code}).status_code == 404
    r = Reader(client, files)
    r.device_code = device_code
    assert r.poll().json() == {"error": "expired_token"}
    assert db.query(ApiKey).count() == 0


def test_wrong_codes_run_out(client, files):
    r = Reader(client, files)
    code = r.ask_for_code()["user_code"]
    for _ in range(link.MAX_WRONG_CODES):
        assert client.post("/api/v1/crosspoint/device/approve", json={"code": "ZZZZZZ"}).status_code == 404
    assert client.post("/api/v1/crosspoint/device/approve", json={"code": code}).status_code == 429
    assert client.get(f"/api/v1/crosspoint/device/{code}").status_code == 429
    assert r.poll().json() == {"error": "authorization_pending"}


def test_a_flood_of_requests_cannot_switch_linking_off(client, files, monkeypatch):
    """Asking for a code needs no sign-in. When the list is full the oldest
    unconfirmed link makes room; a confirmed one is never dropped."""
    monkeypatch.setattr(link, "MAX_PENDING", 3)
    confirmed, oldest = Reader(client, files), Reader(client, files)
    code = confirmed.ask_for_code()["user_code"]
    assert client.post("/api/v1/crosspoint/device/approve", json={"code": code}).status_code == 200
    oldest.ask_for_code()
    Reader(client, files).ask_for_code()
    newest = Reader(client, files)
    late = newest.ask_for_code()["user_code"]          # the list was full
    assert oldest.poll().json() == {"error": "expired_token"}
    assert client.post("/api/v1/crosspoint/device/approve", json={"code": late}).status_code == 200
    assert newest.poll().status_code == 200 and confirmed.poll().status_code == 200


def test_an_api_token_cannot_link_a_reader(client, db, files):
    made = client.post("/api/tokens/", json={"name": "script", "scope": "full"})
    assert made.status_code in (200, 201), made.text
    token = made.json()["token"]
    code = Reader(client, files).ask_for_code()["user_code"]
    r = client.post("/api/v1/crosspoint/device/approve", json={"code": code}, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403


def test_a_revoked_key_is_refused_and_the_reader_keeps_its_sessions(client, db, reader):
    db.query(ApiKey).delete()
    db.flush()
    assert reader.deliver(_event()).status_code == 401      # not 2xx: the event stays queued
    assert reader.open_plugin().status_code == 401          # and the reader offers to sign in again
    assert db.query(ReadingSession).count() == 0


def test_a_readers_key_opens_only_the_readers_routes(client, db, admin_user, reader, make_book):
    """What sits on a reader's SD card must not download books or read
    positions, and a KOReader plugin key is not a reader."""
    user, _ = admin_user
    book = make_book(title="Not For The Reader")
    as_reader = {"Authorization": f"Bearer {reader.token}"}
    assert db.query(ApiKey).one().kind == ApiKey.KIND_READER
    for path in (f"/api/tome-sync/position/{book.id}", "/api/tome-sync/series", "/api/books"):
        assert client.get(path, headers=as_reader).status_code == 401, path

    plugin_key = ApiKey.generate()
    db.add(ApiKey(user_id=user.id, key_hash=ApiKey.hash_key(plugin_key), label="KOReader Plugin"))
    db.flush()
    as_plugin = {"Authorization": f"Bearer {plugin_key}"}
    assert client.get(f"/api/tome-sync/position/{book.id}", headers=as_plugin).status_code != 401
    assert client.get("/api/v1/crosspoint/catalog", headers=as_plugin).status_code == 401
    assert client.post("/api/v1/crosspoint/sessions", headers=as_plugin, json=_event()).status_code == 401


def test_the_startup_migration_marks_existing_keys_as_plugin_keys():
    """A database from before the column: every key there is a KOReader
    plugin key and keeps working as one. Runs twice."""
    from sqlalchemy import create_engine, text

    from backend.main import migrate_api_key_kind

    engine = create_engine("sqlite://")
    with engine.connect() as conn:
        conn.execute(text(
            "CREATE TABLE api_keys (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, key_hash VARCHAR(64) NOT NULL, "
            "key_prefix VARCHAR(16), label VARCHAR(100) NOT NULL, created_at DATETIME NOT NULL, last_used_at DATETIME)"))
        conn.execute(text("INSERT INTO api_keys (user_id, key_hash, label, created_at) "
                          "VALUES (1, 'h', 'KOReader Plugin', '2026-11-01 10:00:00')"))
        conn.commit()
        migrate_api_key_kind(conn)
        migrate_api_key_kind(conn)
        assert conn.execute(text("SELECT kind FROM api_keys")).scalar() == "plugin"
        # An older Tome writing to the migrated table does not name the column.
        conn.execute(text("INSERT INTO api_keys (user_id, key_hash, label, created_at) "
                          "VALUES (1, 'h2', 'KOReader Plugin', '2026-11-02 10:00:00')"))
        assert [r[0] for r in conn.execute(text("SELECT kind FROM api_keys ORDER BY id"))] == ["plugin", "plugin"]


def test_a_reader_that_was_never_linked_delivers_nothing(client, db, files):
    r = Reader(client, files)
    assert r.deliver(_event()).status_code == 401
    assert db.query(ReadingSession).count() == 0


# ── a session ─────────────────────────────────────────────────────────────────

def test_a_session_becomes_a_reading_session_with_progress(db, admin_user, reader, make_book):
    user, _ = admin_user
    book = _link_book(db, make_book)
    r = reader.deliver(_event())
    assert r.status_code == 200 and r.json() == {"stored": True, "outcome": "created"}
    row = db.query(ReadingSession).one()
    assert (row.user_id, row.book_id, row.duration_seconds) == (user.id, book.id, 600)
    assert (row.progress_start, row.progress_end) == (0.1, 0.14)
    assert row.pages_turned is None and row.device == "CrossPoint reader"
    status = db.query(UserBookStatus).filter_by(user_id=user.id, book_id=book.id).one()
    assert status.status == "reading" and status.progress_pct == pytest.approx(0.14)


def test_a_repeat_adds_nothing(db, reader, make_book):
    """Delivery is at least once: the same event can arrive again."""
    _link_book(db, make_book)
    event = _event()
    assert reader.deliver(event).json()["outcome"] == "created"
    assert reader.deliver(event).json()["outcome"] == "unchanged"
    assert db.query(ReadingSession).count() == 1


def test_a_late_old_session_never_moves_the_book_back(db, admin_user, reader, make_book):
    user, _ = admin_user
    book = _link_book(db, make_book)
    now = int(time.time())
    reader.deliver(_event("new-1", start=now - 3600, begin=5000, end=6000))
    reader.deliver(_event("old-1", start=now - 90000, begin=1000, end=2000))
    status = db.query(UserBookStatus).filter_by(user_id=user.id, book_id=book.id).one()
    assert status.progress_pct == pytest.approx(0.6)
    assert db.query(ReadingSession).count() == 2


def test_a_book_tome_does_not_know_is_kept_without_a_book(db, reader):
    assert reader.deliver(_event()).json()["stored"] is True
    assert db.query(ReadingSession).one().book_id is None


@pytest.mark.parametrize("change", [
    {"duration_seconds": "\u00b2\u00b3"},                   # digits only to isdigit()
    {"start_time": "0", "end_time": "600"},                 # no clock: 1970
    {"duration_seconds": "5"},                              # too short
    {"document": "not-a-hash"},
    {"start_time": ""},                                     # the firmware had no value
    {"end_time": str(int(time.time()) + 3 * 86400)},        # days in the future
])
def test_a_session_tome_cannot_store_is_dropped_not_refused(db, reader, change):
    """Refusing it would block the reader's queue for good."""
    r = reader.deliver({**_event(), **change})
    assert r.status_code == 200 and r.json()["stored"] is False and r.json()["reason"]
    assert db.query(ReadingSession).count() == 0
    assert reader.deliver(_event("next-1")).json()["stored"] is True


# ── the same sitting from the stats sync as well (CrossInk) ───────────────────

@pytest.fixture()
def acc(db, admin_user):
    user, _ = admin_user
    account = KOSyncUser(username=user.username, userkey="k" * 32, user_id=user.id)
    db.add(account)
    db.flush()
    return account


def _stats_session(client, acc, session_id, start, seconds):
    return client.put(
        f"{STATS}/stats/sessions", headers={"x-auth-user": acc.username, "x-auth-key": acc.userkey},
        json={"device_id": DEVICE, "items": [{
            "session_id": session_id, "document": DOC, "started_at": start,
            "ended_at": start + seconds, "seconds": seconds,
        }]})


def _fragments(start):
    """How the firmware's own sync reports a 35-minute sitting with two short
    pauses: a new session after every pause (ReadingSessionJournal::accept)."""
    return [("f1" * 16, start, 600), ("f2" * 16, start + 720, 700), ("f3" * 16, start + 1500, 800)]


def _rows(db):
    return [(r.duration_seconds, r.progress_end) for r in db.query(ReadingSession).order_by(ReadingSession.started_at)]


def test_plugin_after_the_built_in_sync_adds_progress_not_time(db, client, acc, reader, make_book):
    _link_book(db, make_book)
    start = int(time.time()) - 7200
    for sid, at, seconds in _fragments(start):
        assert _stats_session(client, acc, sid, at, seconds).status_code == 200
    r = reader.deliver(_event(start=start, seconds=2100))      # the same sitting, whole
    assert r.json() == {"stored": True, "outcome": "merged"}
    assert _rows(db) == [(600, 0.14), (700, None), (800, None)]
    assert db.query(KOSyncSessionReceipt).count() == 4


def test_the_built_in_sync_after_the_plugin_ends_up_the_same(db, client, acc, reader, make_book):
    """The plugin's one session is replaced by the pieces: the first takes it
    over, the others are sessions of their own. Nothing counts twice, also
    not the pieces that begin after the plugin's (pause-free) end."""
    _link_book(db, make_book)
    start = int(time.time()) - 7200
    reader.deliver(_event(start=start, seconds=2100))
    assert _rows(db) == [(2100, 0.14)]
    for sid, at, seconds in _fragments(start):
        r = _stats_session(client, acc, sid, at, seconds)
        assert r.status_code == 200 and r.json()["accepted_session_ids"] == [sid]
    assert _rows(db) == [(600, 0.14), (700, None), (800, None)]
    # Repeats of either source change nothing.
    for sid, at, seconds in _fragments(start):
        _stats_session(client, acc, sid, at, seconds)
    reader.deliver(_event(start=start, seconds=2100))
    assert _rows(db) == [(600, 0.14), (700, None), (800, None)]


def test_the_pieces_in_one_upload_behave_like_separate_uploads(db, client, acc, reader, make_book):
    _link_book(db, make_book)
    start = int(time.time()) - 7200
    reader.deliver(_event(start=start, seconds=2100))
    r = client.put(
        f"{STATS}/stats/sessions", headers={"x-auth-user": acc.username, "x-auth-key": acc.userkey},
        json={"device_id": DEVICE, "items": [
            {"session_id": sid, "document": DOC, "started_at": at, "ended_at": at + seconds, "seconds": seconds}
            for sid, at, seconds in _fragments(start)]})
    assert r.status_code == 200
    assert _rows(db) == [(600, 0.14), (700, None), (800, None)]


def test_a_piece_that_grows_after_taking_over_grows_normally(db, client, acc, reader, make_book):
    _link_book(db, make_book)
    start = int(time.time()) - 7200
    reader.deliver(_event(start=start, seconds=900))
    _stats_session(client, acc, "a" * 32, start, 600)
    _stats_session(client, acc, "a" * 32, start, 900)
    assert _rows(db) == [(900, 0.14)]


def test_a_plugin_session_trimmed_in_tome_keeps_its_trim(db, client, acc, reader, make_book):
    _link_book(db, make_book)
    start = int(time.time()) - 7200
    reader.deliver(_event(start=start, seconds=2100))
    db.query(ReadingSession).one().duration_seconds = 300      # the user cut it down
    db.flush()
    _stats_session(client, acc, "a" * 32, start, 600)
    assert _rows(db)[0][0] == 300


def test_two_sittings_of_one_book_stay_two(db, client, acc, reader, make_book):
    _link_book(db, make_book)
    start = int(time.time()) - 7200
    _stats_session(client, acc, "a" * 32, start, 600)
    assert reader.deliver(_event(start=start + 1800, seconds=600)).json()["outcome"] == "created"
    assert db.query(ReadingSession).count() == 2


@pytest.mark.parametrize("plugin_first", [True, False])
def test_a_session_deleted_in_tome_stays_deleted(db, client, acc, reader, make_book, plugin_first):
    _link_book(db, make_book)
    start = int(time.time()) - 3600
    first = (lambda: reader.deliver(_event(start=start, seconds=590))) if plugin_first else (lambda: _stats_session(client, acc, "a" * 32, start, 620))
    second = (lambda: _stats_session(client, acc, "a" * 32, start, 620)) if plugin_first else (lambda: reader.deliver(_event(start=start, seconds=590)))
    first()
    db.delete(db.query(ReadingSession).one())
    db.flush()
    db.expire_all()
    assert second().status_code == 200
    assert db.query(ReadingSession).count() == 0


# ── what the reader and Settings show ─────────────────────────────────────────

def test_the_reader_lists_that_it_is_linked_and_what_arrived(db, admin_user, reader):
    user, _ = admin_user
    first = reader.open_plugin()
    assert first.status_code == 200
    titles = [i["title"] for i in first.json()["items"]]
    assert titles == [f"Linked to {user.username}", "No sessions received yet"]
    reader.deliver(_event(seconds=1500))
    items = reader.open_plugin().json()["items"]
    assert [i["title"] for i in items] == [f"Linked to {user.username}", "1 session received", "Last session"]
    reader.deliver(_event("second-1", start=int(time.time()) - 600, seconds=300))
    assert reader.open_plugin().json()["items"][1]["title"] == "2 sessions received"
    assert items[1]["subtitle"] == "25 min of reading"
    assert all(set(i) == {"id", "title", "subtitle"} for i in items)


def test_settings_sees_linked_readers_and_the_last_delivery(client, db, reader):
    before = client.get("/api/v1/crosspoint/status").json()
    assert len(before["readers"]) == 1 and before["sessions"] == 0 and before["last_session_at"] is None
    assert before["readers"][0]["label"].startswith("CrossPoint reader")
    reader.deliver(_event())
    after = client.get("/api/v1/crosspoint/status").json()
    assert after["sessions"] == 1 and after["last_session_at"].endswith("Z")
    assert after["readers"][0]["last_used_at"]
    _, other = _user(db, "second")
    assert client.get("/api/v1/crosspoint/status", headers=other).json()["readers"] == []
