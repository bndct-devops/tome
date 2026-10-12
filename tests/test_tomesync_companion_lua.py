"""The plugin's offline reading companion (run 2b), executed under LuaJIT.

The "Reading companion" section of the generated impl is lifted verbatim and
run against hand-made snapshots with minimal KOReader stubs: the device-side
spoiler trim on a re-read (aliases, identity, facts, timeline, status, the
current recap), a finished book never trimmed, "Who is this?" matching of
quoted and possessive selections, the position margin, and the snapshot
store refusing a short write."""

from __future__ import annotations

import re
import shutil
import subprocess
import textwrap

import pytest

from backend.api.tome_sync import _main_impl_lua

LUAJIT = shutil.which("luajit")

pytestmark = pytest.mark.skipif(LUAJIT is None, reason="luajit not installed")


def _section() -> str:
    lua = _main_impl_lua("http://localhost:8080", "tk_test", "tester")
    start = lua.index("-- ── Reading companion")
    end = lua.index("function TomeSync:_menuItems")
    jval = re.search(r"local function jval\(v\).*?\nend\n", lua, re.S)
    assert jval, "jval not found in the impl"
    return jval.group(0), lua[start:end]


PRELUDE = textwrap.dedent(r"""
    rapidjson = { null = setmetatable({}, { __tostring = function() return "null" end }) }
    function rapidjson.encode(t)
        local s = t[1]
        return '["' .. s:gsub('[%c"\\]', function(c) return string.format("\\u%04x", c:byte()) end) .. '"]'
    end
    logger = { info = function() end, warn = function() end, dbg = function() end }
    G_reader_settings = { nilOrTrue = function() return true end }
    package.preload["docsettings"] = function()
        return { getSidecarDir = function(_, p) return (p:gsub("%.epub$", ".sdr")) end }
    end
    lfs = { attributes = function(path, what)
        if path:match("%.sdr$") then return "directory" end
        local f = io.open(path, "rb")
        if f then f:close(); return "file" end
        return nil
    end }
    function mkdirp() end
    function readWhole(path)
        local f = io.open(path, "rb"); if not f then return nil end
        local d = f:read("*a"); f:close(); return d
    end
    WRITE_SHORT = false
    function writeWhole(path, data)
        local f = io.open(path, "wb"); if not f then return false end
        if WRITE_SHORT then data = data:sub(1, #data - 7) end
        f:write(data); f:close(); return true
    end
    TomeSync = {}
""")


SCENARIOS = textwrap.dedent(r"""
    local null = rapidjson.null
    local B = 7
    local function at(ci) return { book_id = B, series_index = 2, chapter_idx = ci } end
    local function chapters(n)
        local out = {}
        for i = 0, n - 1 do
            out[#out + 1] = { idx = i, title = "Ch " .. (i + 1),
                              start_fraction = i / n, end_fraction = (i + 1) / n }
        end
        return out
    end
    local function snapshot(as_of)
        return {
            rev = "r1",
            as_of = { chapter_idx = as_of, pct = 0.8 },
            book = { id = B, title = "Invented", series = "Strand", series_index = 2, series_key = "strand" },
            chapters = chapters(8),
            entries = {
                { name = "Mara Vell", kind = "person", identity = "A ferry pilot.",
                  aliases = { "The Pilot", "Vell the Dead", "Old Mara" },
                  alias_places = { { name = "The Pilot", book_id = B, series_index = 2, chapter_idx = 1 },
                                   { name = "Vell the Dead", book_id = B, series_index = 2, chapter_idx = 5 },
                                   { name = "Old Mara", book_id = 3, series_index = 1, chapter_idx = 9 } },
                  identity_at = at(0), status = "dead",
                  facts = { status = { value = "dead", book_id = B, series_index = 2, chapter_idx = 5 },
                            location = { value = "the sea", book_id = B, series_index = 2, chapter_idx = 5 },
                            appearance = { value = "grey coat", book_id = B, series_index = 2, chapter_idx = 2 } },
                  first_seen = at(0), last_seen = at(5),
                  timeline = { { book_id = B, series_index = 2, chapter_idx = 0, line = "Walks to the harbour." },
                               { book_id = B, series_index = 2, chapter_idx = 2, line = "Takes the ferry out." },
                               { book_id = B, series_index = 2, chapter_idx = 5, line = "Drowns." } },
                  line = "Drowns." },
                { name = "Tobin Ash", kind = "person", identity = "The bell ringer's secret son.",
                  aliases = null, alias_places = null, identity_at = at(4), status = null, facts = {},
                  first_seen = at(1), last_seen = at(1),
                  timeline = { { book_id = B, series_index = 2, chapter_idx = 1, line = "Rings the bell." } },
                  line = "Rings the bell." },
                { name = "Ada Quill", kind = "person", identity = "A clerk.", aliases = {},
                  alias_places = {}, identity_at = at(2), status = null, facts = {},
                  first_seen = at(2), last_seen = at(2),
                  timeline = { { book_id = B, series_index = 2, chapter_idx = 2, line = "Files a report." } },
                  line = "Files a report." },
                { name = "Kell Ward", kind = "person", identity = "Arrives late.", aliases = {},
                  alias_places = {}, identity_at = at(5), status = null, facts = {},
                  first_seen = at(5), last_seen = at(5),
                  timeline = { { book_id = B, series_index = 2, chapter_idx = 5, line = "Arrives late." } },
                  line = "Arrives late." },
            },
            recaps = { volumes = {}, current = { text = "So far.", chapter_idx = 6, created_at = "x" } },
        }
    end
    local function names(entries)
        local out = {}
        for _, e in ipairs(entries) do out[#out + 1] = e.name end
        return table.concat(out, ",")
    end
    local function find(entries, name)
        for _, e in ipairs(entries) do if e.name == name then return e end end
    end
    local function keys(t)
        local out = {}
        for k in pairs(t or {}) do out[#out + 1] = k end
        table.sort(out)
        return table.concat(out, ",")
    end

    -- Re-read: the snapshot is as of chapter 6, the reader is back in chapter 3.
    local snap = snapshot(6)
    local v = Companion.view(snap, 3.5 / 8)
    assert(v.limit == 3, "limit " .. tostring(v.limit))
    assert(names(v.entries) == "Mara Vell,Tobin Ash,Ada Quill", names(v.entries))
    local m = find(v.entries, "Mara Vell")
    assert(table.concat(m.aliases, "|") == "The Pilot|Old Mara", table.concat(m.aliases, "|"))
    assert(m.identity == "A ferry pilot.")
    assert(keys(m.facts) == "appearance", keys(m.facts))
    assert(m.status == nil, "status past the page shown")
    assert(#m.timeline == 2 and m.line == "Takes the ferry out.", tostring(m.line))
    local text = Companion.entryText(m)
    assert(not text:find("Drowns") and not text:find("dead") and not text:find("sea")
           and not text:find("Vell the Dead"), text)
    local t = find(v.entries, "Tobin Ash")
    assert(t.identity == nil, "identity written past the page shown")
    assert(Companion.rowText(t) == "Tobin Ash · Rings the bell.", Companion.rowText(t))
    assert(not Companion.entryText(t):find("secret"), Companion.entryText(t))
    assert(v.current == nil, "recap past the page shown")
    -- The trimmed view drives search and "Who is this?".
    assert(#Companion.lookup(v.entries, "Vell the Dead") == 0, "hidden alias matched")
    assert(#Companion.search(v.entries, "vell the dead") == 0, "hidden alias searched")
    assert(#Companion.search(v.entries, "secret") == 0, "hidden identity searched")
    local hits = Companion.search(v.entries, "pilot")
    assert(hits[1] and hits[1].name == "Mara Vell", "alias search")

    -- Reading at the snapshot's position: nothing trimmed.
    v = Companion.view(snap, 6.5 / 8)
    assert(v.limit == nil and #v.entries == 4 and v.current ~= nil)
    m = find(v.entries, "Mara Vell")
    assert(#m.aliases == 3 and m.status == "dead" and #m.timeline == 3)
    assert(type(m.aliases[1]) == "string")

    -- A finished book (whole-book window, as_of = chapter count): never a
    -- limit, wherever the page sits.
    local done = snapshot(8)
    done.recaps.current.chapter_idx = 8
    v = Companion.view(done, 0.1)
    assert(v.limit == nil, "finished book limited at " .. tostring(v.limit))
    assert(#v.entries == 4 and v.current ~= nil)
    assert(#find(v.entries, "Mara Vell").aliases == 3)

    -- Margin: a page fraction just short of chapter 3 (by less than 3%) keeps
    -- chapter 2, the one the reader is in, in view; further back it goes.
    assert(Companion.currentChapter(snap, 3 / 8 - 0.02) == 2)
    v = Companion.view(snap, 3 / 8 - 0.02)
    assert(v.limit == 3 and find(v.entries, "Ada Quill"), "margin: " .. tostring(v.limit))
    v = Companion.view(snap, 2 / 8 + 0.05)
    assert(v.limit == 2 and not find(v.entries, "Ada Quill"), "no margin: " .. tostring(v.limit))

    -- "Who is this?": curly quotes, guillemets, dashes, possessives.
    v = Companion.view(snap, 6.5 / 8)
    for _, sel in ipairs({ "\u{201C}Mara\u{2019}s\u{201D}", "Mara's,", "\u{00AB}The Pilot\u{00BB}",
                           "\u{2014}Mara Vell\u{2026}", "\u{2018}mara vell\u{2019}", "  MARA VELL. " }) do
        local h = Companion.lookup(v.entries, sel)
        assert(#h == 1 and h[1].name == "Mara Vell", "lookup " .. sel .. " -> " .. names(h))
    end
    local h = Companion.lookup(v.entries, "\u{201C}Tobin\u{2019}s\u{201D}")
    assert(#h == 1 and h[1].name == "Tobin Ash", "lookup Tobin -> " .. names(h))
    assert(#Companion.lookup(v.entries, "\u{201C}\u{201D}") == 0)

    -- store: a verified write lands; a short one never replaces it.
    local doc = DOC_PATH
    local body = '{"rev":"r1","entries":[]}'
    local stored, err = Companion.store(doc, B, body, { rev = "r1", book = { series_key = "strand" } })
    assert(stored and stored.rev == "r1", tostring(err))
    local file = doc:gsub("%.epub$", ".sdr") .. "/tome_companion.json"
    local good = readWhole(file)
    assert(good and good:find('"rev":"r1"', 1, true), tostring(good))
    WRITE_SHORT = true
    stored, err = Companion.store(doc, B, '{"rev":"r2","entries":[]}', { rev = "r2", book = {} })
    WRITE_SHORT = false
    assert(stored == nil and tostring(err):find("short write"), tostring(err))
    assert(readWhole(file) == good, "short write replaced the snapshot")
    assert(readWhole(file .. ".tmp") == nil, "tmp file left behind")

    print("COMPANION-OK")
""")


def test_companion_section_under_luajit(tmp_path):
    jval, section = _section()
    (tmp_path / "Invented.sdr").mkdir()
    doc = tmp_path / "Invented.epub"
    script = tmp_path / "companion.lua"
    script.write_text("\n".join([
        PRELUDE,
        jval,
        f"DOC_PATH = {str(doc)!r}",
        section,
        SCENARIOS,
    ]))
    proc = subprocess.run([LUAJIT, str(script)], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "COMPANION-OK" in proc.stdout
