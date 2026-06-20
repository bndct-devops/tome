"""Tripwires for the plugin's bidirectional book-rating sync.

KOReader stores a native 1–5 star rating + free-text review in each book's
sidecar (`summary.rating` / `summary.note`). Tome holds the same per-user
rating/review on UserBookStatus. The plugin reconciles them, using a saved
baseline to tell which side changed since the last sync:

    only device changed  -> push device → Tome
    only Tome changed     -> write Tome → device sidecar
    both changed (tie)    -> Tome wins (single source of truth)

These can't execute KOReader; they pin the load-bearing properties on the
generated Lua so a refactor can't silently revert them.
"""
import re
import shutil
import subprocess
import tempfile

from backend.api.tome_sync import _main_impl_lua


def _impl() -> str:
    return _main_impl_lua("http://localhost:8080", "tk_test", "tester")


def _body(lua: str, fn: str) -> str:
    match = re.search(r"function TomeSync:" + re.escape(fn) + r"\(.*?\).*?\nend\n", lua, re.S)
    assert match, f"{fn} not found in generated impl"
    return match.group(0)


def test_rating_functions_exist():
    lua = _impl()
    assert "function TomeSync:_pullRatingAtOpen" in lua
    assert "function TomeSync:_pushRating" in lua
    assert "function TomeSync:_pushRatingOnLeave" in lua


def test_uses_correct_endpoints():
    lua = _impl()
    # Must use the tome-sync rating endpoints (api-key auth the plugin's baked
    # tk_ key satisfies), NOT the web /books/{id}/rating + /status ones, which
    # authenticate via get_current_user and reject the tk_ key (401).
    assert '"GET", "/tome-sync/rating/" .. self.book_id' in lua
    assert '"PUT",\n        "/tome-sync/rating/" .. self.book_id' in lua
    assert "/status" not in lua.split("function TomeSync:_pullRatingAtOpen")[1].split("\nend\n")[0]


def test_reconcile_is_tome_wins_on_conflict():
    # remote_changed is checked first, so a both-changed tie writes Tome onto the
    # device (Tome wins). Device-only changes fall through to the push branch.
    body = _body(_impl(), "_pullRatingAtOpen")
    remote_branch = body.find("if remote_changed then")
    local_branch = body.find("elseif local_changed then")
    assert remote_branch != -1 and local_branch != -1
    assert remote_branch < local_branch, "remote (Tome-wins) branch must come first"


def test_maps_koreader_note_to_tome_review():
    # KOReader's review text lives in summary.note; Tome calls it review.
    lua = _impl()
    assert "review = summary.note" in lua
    assert "summary.note     = remote_review" in lua


def test_status_field_is_left_untouched():
    # Reading status (reading/complete/abandoned) already syncs via position;
    # the rating path must never write summary.status.
    body = _body(_impl(), "_pullRatingAtOpen")
    assert "summary.status" not in body


def test_nil_rating_clears_on_the_wire():
    # An absent Lua key is dropped from the JSON body, which would leave Tome's
    # old value in place. nil must be sent as rapidjson.null so clears propagate.
    body = _body(_impl(), "_pushRating")
    assert "rating = rating or rapidjson.null" in body
    assert "review = review or rapidjson.null" in body


def test_json_null_is_normalized_to_nil_on_read():
    # rapidjson decodes JSON null to a sentinel, not nil; without normalizing it,
    # "no rating" would never compare equal to an absent baseline value.
    lua = _impl()
    assert "local remote_rating = jval(status.rating)" in lua
    assert "local remote_review = jval(status.review)" in lua


def test_baseline_is_persisted():
    lua = _impl()
    assert 'G_reader_settings:readSetting("tomesync_rating_baseline")' in lua
    assert 'G_reader_settings:saveSetting("tomesync_rating_baseline"' in lua


def test_open_and_leave_hooks_are_wired():
    lua = _impl()
    assert "self:_pullRatingAtOpen()" in _body(lua, "onReaderReady")
    assert "self:_pushRatingOnLeave()" in _body(lua, "onCloseDocument")
    assert "self:_pushRatingOnLeave()" in _body(lua, "onSuspend")


def test_impl_compiles_under_luajit():
    # KOReader runs LuaJIT (5.1). Compile the rendered impl if a checker is on
    # PATH; skip cleanly in CI environments without one.
    checker = shutil.which("luajit") or shutil.which("luac5.1")
    if not checker:
        import pytest

        pytest.skip("no LuaJIT/luac5.1 available to syntax-check")
    with tempfile.NamedTemporaryFile("w", suffix=".lua", delete=False) as f:
        f.write(_impl())
        path = f.name
    args = [checker, "-bl", path, "/dev/null"] if "luajit" in checker else [checker, "-p", path]
    proc = subprocess.run(args, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
