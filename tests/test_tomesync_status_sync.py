"""TomeSync status sync (Tome -> KOReader book status), issue #296.

Server half: ``POST /tome-sync/statuses`` answers only for (book, hash) pairs
Tome has on record and the user can see, with the user's own status.
Plugin half: the decision rule runs under luajit as a truth table, plus
contract tests over the generated Lua.
"""
import shutil
import subprocess

import pytest

from backend.api.tome_sync import METADATA_SYNC_MAX_ITEMS, _main_impl_lua
from backend.core.security import hash_password
from backend.models.library import Library
from backend.models.tome_sync import ApiKey
from backend.models.user import User
from backend.models.user_book_status import UserBookStatus
from backend.services.ko_hash import record_ko_hash


def _api_key_for(db, user_id: int) -> dict:
    plaintext = ApiKey.generate()
    db.add(ApiKey(user_id=user_id, key_hash=ApiKey.hash_key(plaintext),
                  key_prefix=plaintext[:11], label="test"))
    db.flush()
    return {"Authorization": f"Bearer {plaintext}"}


def _member(db, name="member") -> User:
    u = User(username=name, email=f"{name}@x.test", hashed_password=hash_password("pw"),
             is_active=True, is_admin=False, role="member", must_change_password=False)
    db.add(u)
    db.flush()
    return u


def _post(client, hdr, items):
    return client.post("/api/tome-sync/statuses", headers=hdr, json={"items": items})


def _set(db, user_id, book_id, status):
    db.add(UserBookStatus(user_id=user_id, book_id=book_id, status=status))


# ── endpoint ─────────────────────────────────────────────────────────────────

def test_statuses_are_the_users_own_and_default_to_unread(client, db, make_book, admin_user):
    user, _ = admin_user
    hdr = _api_key_for(db, user.id)
    other = _member(db, "someone")
    read, reading, queued, none = (make_book(title=t) for t in ("R", "G", "Q", "N"))
    for i, b in enumerate((read, reading, queued, none)):
        record_ko_hash(db, b.id, str(i) * 32)
    _set(db, user.id, read.id, "read")
    _set(db, user.id, reading.id, "reading")
    _set(db, user.id, queued.id, "want_to_read")
    _set(db, other.id, none.id, "read")          # another user's status never leaks
    db.commit()

    r = _post(client, hdr, [{"book_id": b.id, "ko_md5": str(i) * 32}
                            for i, b in enumerate((read, reading, queued, none))])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["rejected"] == []
    assert [(s["book_id"], s["ko_md5"], s["status"]) for s in body["statuses"]] == [
        (read.id, "0" * 32, "read"),
        (reading.id, "1" * 32, "reading"),
        (queued.id, "2" * 32, "unread"),         # the device has no queue
        (none.id, "3" * 32, "unread"),
    ]


def test_answers_only_for_hash_tome_has_on_record(client, db, make_book, admin_user):
    user, _ = admin_user
    hdr = _api_key_for(db, user.id)
    good, other = make_book(title="Good"), make_book(title="Other")
    record_ko_hash(db, good.id, "a" * 32)
    record_ko_hash(db, other.id, "b" * 32)
    _set(db, user.id, good.id, "read")
    _set(db, user.id, other.id, "read")
    db.commit()

    body = _post(client, hdr, [
        {"book_id": good.id, "ko_md5": "a" * 32},
        {"book_id": good.id, "ko_md5": "b" * 32},      # other book's hash
        {"book_id": other.id, "ko_md5": "c" * 32},     # unknown hash
        {"book_id": 999999, "ko_md5": "a" * 32},       # unknown book
    ]).json()
    assert body["statuses"] == [{"book_id": good.id, "ko_md5": "a" * 32, "status": "read"}]
    assert body["rejected"] == [
        {"book_id": good.id, "ko_md5": "b" * 32},
        {"book_id": other.id, "ko_md5": "c" * 32},
        {"book_id": 999999, "ko_md5": "a" * 32},
    ]


def test_invisible_and_inactive_books_are_rejected(client, db, make_book, admin_user):
    admin, _ = admin_user
    member = _member(db)
    hdr = _api_key_for(db, member.id)
    private = Library(name="Private", is_public=False, owner_id=admin.id)
    db.add(private)
    db.flush()
    hidden = make_book(title="Hidden")
    hidden.libraries.append(private)
    gone = make_book(title="Gone")
    gone.status = "deleted"
    for i, b in enumerate((hidden, gone)):
        record_ko_hash(db, b.id, str(i) * 32)
        _set(db, member.id, b.id, "read")
    db.commit()

    body = _post(client, hdr, [{"book_id": hidden.id, "ko_md5": "0" * 32},
                               {"book_id": gone.id, "ko_md5": "1" * 32}]).json()
    assert body["statuses"] == []
    assert len(body["rejected"]) == 2


def test_requires_api_key_empty_table_and_batch_cap(client, db, make_book, admin_user):
    user, _ = admin_user
    assert client.post("/api/tome-sync/statuses", json={"items": []}).status_code in (401, 422)
    hdr = _api_key_for(db, user.id)
    db.commit()
    # KOReader's rapidjson sends an empty Lua table as {}.
    r = client.post("/api/tome-sync/statuses", headers=hdr, json={"items": {}})
    assert r.status_code == 200 and r.json() == {"statuses": [], "rejected": []}
    many = [{"book_id": 10_000 + i, "ko_md5": "f" * 32} for i in range(METADATA_SYNC_MAX_ITEMS + 20)]
    assert len(_post(client, hdr, many).json()["rejected"]) == METADATA_SYNC_MAX_ITEMS


# ── plugin ───────────────────────────────────────────────────────────────────

def _impl() -> str:
    return _main_impl_lua("http://tome.test", "tome_testkey", "tester")


def _body(lua: str, func: str) -> str:
    start = lua.find(f"\nfunction TomeSync:{func}")
    assert start != -1, f"missing function {func}"
    return lua[start:lua.find("\nfunction ", start + 1)]


def _status_target_src(lua: str) -> str:
    start = lua.find("\nlocal function statusTarget(")
    assert start != -1
    return lua[start:lua.find("\nend\n", start) + 5]


# (remote, base, device) -> what the plugin writes; None = leaves the device alone.
RULES = [
    # First run for a file: fill in, never undo.
    ("read",    None, None,        "complete"),
    ("read",    None, "reading",   "complete"),
    ("read",    None, "complete",  None),
    ("reading", None, None,        "reading"),
    ("reading", None, "complete",  None),
    ("reading", None, "reading",   None),
    ("unread",  None, None,        None),
    ("unread",  None, "complete",  None),
    ("unread",  None, "reading",   None),
    # Tome unchanged: a mark set by hand on the device stays.
    ("unread",  "unread",  "complete", None),
    ("reading", "reading", "complete", None),
    ("read",    "read",    "reading",  None),
    # Tome changed: the device follows.
    ("read",    "reading", "reading",  "complete"),
    ("read",    "unread",  None,       "complete"),
    ("read",    "unread",  "complete", None),
    ("reading", "read",    "complete", "reading"),     # re-read started elsewhere
    ("reading", "unread",  None,       "reading"),
    ("unread",  "read",    "complete", "reading"),     # set back to unread in Tome
    ("unread",  "read",    "reading",  None),
    ("unread",  "reading", None,       None),
    # "On hold" is never touched.
    ("read",    None,      "abandoned", None),
    ("read",    "reading", "abandoned", None),
    ("unread",  "read",    "abandoned", None),
]


@pytest.mark.skipif(shutil.which("luajit") is None, reason="luajit not installed")
def test_status_rule_truth_table_under_luajit(tmp_path):
    def lit(v):
        return "nil" if v is None else f'"{v}"'

    lines = [_status_target_src(_impl()), "local bad = 0"]
    for remote, base, device, want in RULES:
        lines.append(
            f"do local got = statusTarget({lit(remote)}, {lit(base)}, {lit(device)}) "
            f"if got ~= {lit(want)} then bad = bad + 1 "
            f"print('{remote}/{base}/{device}: got ' .. tostring(got) .. ', want {want}') end end"
        )
    lines.append("os.exit(bad == 0 and 0 or 1)")
    script = tmp_path / "rule.lua"
    script.write_text("\n".join(lines))
    r = subprocess.run([shutil.which("luajit"), str(script)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.skipif(shutil.which("luajit") is None, reason="luajit not installed")
def test_plugin_compiles_under_luajit(tmp_path):
    script = tmp_path / "impl.lua"
    script.write_text(_impl())
    r = subprocess.run([shutil.which("luajit"), "-bl", str(script)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_plugin_status_sync_surface_and_default_on():
    lua = _impl()
    for fn in ("_syncStatuses", "_syncStatusesImpl", "_deviceStatus", "_writeDeviceStatus"):
        assert f"\nfunction TomeSync:{fn}" in lua, fn
    assert '"/tome-sync/statuses"' in lua
    # On unless switched off, and independent of the opt-in metadata sync.
    assert 'G_reader_settings:nilOrTrue("tomesync_status_sync")' in lua
    gate = _body(lua, "_syncStatuses")
    assert "statusSyncOn()" in gate and "tomesync_meta_sync" not in gate
    assert "tomesync_meta_sync" not in _body(lua, "_syncStatusesImpl")
    # Baseline is a data table: dedicated state file, pruned with book_map.
    assert '"tomesync_status_base"' in lua
    assert 'self.state:readSetting("tomesync_status_base")' in _body(lua, "init")
    assert "status_base" in _body(lua, "_pruneState")


def test_plugin_status_sync_triggers():
    lua = _impl()
    assert "_syncStatuses(false)" in _body(lua, "init")
    assert "_syncStatuses(false)" in _body(lua, "onResume")
    assert "_syncStatuses(false)" in _body(lua, "onNetworkConnected")
    # "Sync now" forces a run; page turns never do.
    at = lua.find('text         = "Sync now"')
    assert at != -1 and "self:_syncStatuses(true)" in lua[at:at + 900]
    assert "_syncStatuses" not in _body(lua, "onPageUpdate")
    # Automatic runs are debounced; a forced one is not.
    gate = _body(lua, "_syncStatuses")
    assert "if not force then" in gate and "status_last_auto < 120" in gate


def test_plugin_status_write_goes_through_the_sidecar_summary():
    lua = _impl()
    write = _body(lua, "_writeDeviceStatus")
    assert 'ds:readSetting("summary")' in write and "summary.status = status" in write
    assert 'ds:saveSetting("summary", summary)' in write and "ds:flush()" in write
    assert "setBookInfoCacheProperty" in write
    impl = _body(lua, "_syncStatusesImpl")
    # The open book is skipped; a failed write keeps the baseline for a retry;
    # a rejected pair forgets what Tome said and writes nothing.
    assert "path ~= open_file" in impl
    assert impl.find("b.remote = w.remote") > impl.find("elseif ok then")
    # "On hold" writes nothing and keeps the baseline, so the change stays pending.
    held = impl[impl.find('if device == "abandoned"'):impl.find("elseif ok then")]
    assert "held = true" in held and "b.remote = w.remote" not in held
    rejected = impl[impl.find("each(resp.rejected"):impl.find("local work")]
    assert "b.remote = nil" in rejected and "_writeDeviceStatus" not in rejected
    # An old server is asked once per launch, not on every trigger.
    assert "status_sync_unsupported = true" in impl
