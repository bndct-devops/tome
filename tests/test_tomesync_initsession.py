"""Regression tests for the plugin's book-open sync (`_initSession`).

Background (reported on Reddit against v1.3.2): the "TomeSync: Server at X%
(device: Y%)" message was an ``InfoMessage`` — a modal window. KOReader's
Profiles plugin auto-executes "on book opening" profiles right after
ReaderReady via ``UIManager:sendEvent``, which delivers events only to the
topmost *non-toast* window. The modal swallowed every profile action (font
size, margins, columns) exactly on opens where another device had read ahead,
so users' layout profiles silently failed to apply. Two more bugs lived in the
same function: a web-reader position (foliate ``epubcfi(...)``) fed to
``onGotoXPointer`` lands on page 1, and ``_initSession`` could run twice per
open.

These are tripwires on the generated Lua: they can't execute KOReader, but
they pin the load-bearing properties of the fix so a refactor can't silently
revert them.
"""
import re

from backend.api.tome_sync import _main_impl_lua


def _impl() -> str:
    return _main_impl_lua("http://localhost:8080", "tk_test", "tester")


def _init_session_body(lua: str) -> str:
    match = re.search(r"function TomeSync:_initSession\(\).*?\nend\n", lua, re.S)
    assert match, "_initSession not found in generated impl"
    return match.group(0)


def _pull_body(lua: str) -> str:
    # Build 49 (issue #249): the position pull moved out of _initSession into
    # _pullPositionAtOpen, which _initSession schedules.
    match = re.search(r"function TomeSync:_pullPositionAtOpen\(book_id\).*?\nend\n", lua, re.S)
    assert match, "_pullPositionAtOpen not found in generated impl"
    return match.group(0)


def test_open_sync_message_is_a_toast_not_a_modal():
    # Must be a Notification (toast=true: never blocks UIManager:sendEvent
    # propagation), NOT an InfoMessage, or Profiles auto-exec actions are
    # swallowed while it is on screen.
    body = _pull_body(_impl())
    assert "Notification:new" in body
    assert "InfoMessage:new" not in body


def test_notification_widget_is_required():
    assert 'require("ui/widget/notification")' in _impl()


def test_goto_guards_against_non_crengine_xpointers():
    # The web reader stores a foliate epubcfi in TomeSyncPosition.progress;
    # crengine xpointers always start with "/". Anything else must fall back
    # to a percentage jump instead of onGotoXPointer (which lands on page 1).
    # The jump lives in the shared _gotoServerPosition helper (build 32: both
    # the silent pull and the prompt callback route through it).
    lua = _impl()
    start = lua.find("function TomeSync:_gotoServerPosition")
    assert start != -1, "missing _gotoServerPosition helper"
    body = lua[start:lua.find("\nfunction ", start + 1)]
    guard = body.find('pos.progress:sub(1, 1) == "/"')
    goto_xp = body.find("onGotoXPointer")
    goto_pct = body.find("onGotoPercent")
    assert guard != -1, "missing xpointer-shape guard"
    assert goto_xp != -1 and guard < goto_xp, "goto must be behind the guard"
    assert goto_pct != -1, "missing percentage fallback for web positions"
    # And _initSession must actually route through the helper.
    assert "self:_gotoServerPosition(pos, server_pct)" in _pull_body(lua)


def test_session_init_is_deduped_per_open():
    lua = _impl()
    body = _init_session_body(lua)
    assert "last_session_init" in body, "missing duplicate-init guard"
    # The guard must be declared before onCloseDocument references it,
    # or that reference resolves to a nil global at runtime.
    decl = lua.find("local last_session_init")
    close_doc = lua.find("function TomeSync:onCloseDocument")
    assert decl != -1 and close_doc != -1 and decl < close_doc
    # And onCloseDocument must clear it so an immediate reopen still inits.
    close_body = re.search(
        r"function TomeSync:onCloseDocument\(\).*?\nend\n", lua, re.S
    ).group(0)
    assert "last_session_init.book_id = nil" in close_body


def test_book_open_never_waits_on_the_network():
    # Issue #249: every request runs on KOReader's UI thread, so a request on
    # the open path held the book closed for the full connect timeout when the
    # server did not answer. The open path sets local state only; the resolve
    # and the position pull are scheduled.
    lua = _impl()
    init = _init_session_body(lua)
    assert "apiRequest" not in init
    assert "UIManager:scheduleIn" in init and "_pullPositionAtOpen" in init
    ready = re.search(r"function TomeSync:onReaderReady\(\).*?\nend\n", lua, re.S).group(0)
    assert "apiRequest" not in ready
    # The resolve for an unmapped book happens inside the scheduled callback.
    sched = ready.find("UIManager:scheduleIn")
    assert sched != -1 and sched < ready.find("self:_tryResolve()")
    # The pull re-checks the book after the request: the reader may have
    # closed it or opened another while the request ran.
    pull = _pull_body(lua)
    assert pull.count("self.book_id ~= book_id") == 2


def test_page_update_does_not_resolve_before_reader_ready():
    # KOReader sends the first PageUpdate before ReaderReady; without the
    # gate every open (mapped books included) fired a resolve request on the
    # UI thread before the cached mapping was even read.
    lua = _impl()
    page = re.search(r"function TomeSync:onPageUpdate\(pageno\).*?\nend\n", lua, re.S).group(0)
    gate = page.find("if not self._reader_ready then return end")
    assert gate != -1 and gate < page.find("self:_tryResolve()")
    ready = re.search(r"function TomeSync:onReaderReady\(\).*?\nend\n", lua, re.S).group(0)
    assert ready.find("self._reader_ready = true") < ready.find("self.book_map[doc.file]")
