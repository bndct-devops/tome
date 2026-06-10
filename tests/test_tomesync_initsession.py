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


def test_open_sync_message_is_a_toast_not_a_modal():
    # Must be a Notification (toast=true: never blocks UIManager:sendEvent
    # propagation), NOT an InfoMessage, or Profiles auto-exec actions are
    # swallowed while it is on screen.
    body = _init_session_body(_impl())
    assert "Notification:new" in body
    assert "InfoMessage:new" not in body


def test_notification_widget_is_required():
    assert 'require("ui/widget/notification")' in _impl()


def test_goto_guards_against_non_crengine_xpointers():
    # The web reader stores a foliate epubcfi in TomeSyncPosition.progress;
    # crengine xpointers always start with "/". Anything else must fall back
    # to a percentage jump instead of onGotoXPointer (which lands on page 1).
    body = _init_session_body(_impl())
    guard = body.find('pos.progress:sub(1, 1) == "/"')
    goto_xp = body.find("onGotoXPointer")
    goto_pct = body.find("onGotoPercent")
    assert guard != -1, "missing xpointer-shape guard"
    assert goto_xp != -1 and guard < goto_xp, "goto must be behind the guard"
    assert goto_pct != -1, "missing percentage fallback for web positions"


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
