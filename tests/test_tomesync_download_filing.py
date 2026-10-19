"""Where the plugin files a download, run under luajit (issue #259).

KOReader's JSON decoder turns null into a userdata value, which Lua treats as
true. A book with `"book_type": null` crashed KOReader on a single-book or
author/search-list download: the per-book null reached the batch fallback and
was concatenated into the folder path. The real `_downloadListBooks`,
`_downloadSeriesBooks` and `renderDownloadPath` are cut out of the generated
impl and run with fakes for KOReader's UI, settings and file system, so these
tests drive the shipped Lua.
"""
import json
import shutil
import subprocess

import pytest

from backend.api.tome_sync import _main_impl_lua


def _cut(lua: str, header: str) -> str:
    start = lua.index(header)
    return lua[start:lua.index("\nend\n", start) + 5]


def _functions() -> str:
    lua = _main_impl_lua("http://main.example:8080", "tk_test", "tester")
    return "".join(_cut(lua, h) for h in (
        "local function jstr(",
        "local function mkdirp(",
        "local function renderDownloadPath(",
        "function TomeSync:_downloadSeriesBooks(",
        "function TomeSync:_downloadListBooks(",
    ))


# `NULL` stands in for rapidjson.null: a userdata, so truthy like the real one.
# Downloads "succeed" and record their destination.
_HARNESS = r"""
NULL = newproxy()
local settings = { home_dir = "/books" }
G_reader_settings = { readSetting = function(_, k) return settings[k] end }
UIManager = { show = function() end, close = function() end, forceRePaint = function() end }
InfoMessage = { new = function(_, t) return t end }
ConfirmBox = InfoMessage
lfs = { mkdir = function() end, attributes = function() return nil end }
util = { getSafeFilename = function(s) return s end }
logger = { warn = function() end, dbg = function() end, info = function() end }
local function pickBestFile(files) return files[1] end
local function fmtMB() return "1 MB" end
local dests = {}
local function downloadFile(_, _, dest) dests[#dests + 1] = dest; return true end
TomeSync = {}
function TomeSync:_saveState() end
local self = setmetatable({ book_map = {} }, { __index = TomeSync })
local function book(id, title, book_type, extra)
    local b = { id = id, title = title, author = "Quill Marrow", series_index = id,
                book_type = book_type, files = { { id = id, format = "epub", file_size = 1 } } }
    for k, v in pairs(extra or {}) do b[k] = v end
    return b
end
"""

_TAIL = r"""
local ok, err = pcall(function() SCENARIO() end)
local parts = {}
for i, d in ipairs(dests) do parts[i] = string.format("%q", d) end
print(ok and ("[" .. table.concat(parts, ",") .. "]") or ("ERROR " .. tostring(err)))
"""


def _run(scenario: str) -> list[str]:
    luajit = shutil.which("luajit")
    if luajit is None:
        pytest.skip("luajit not installed")
    src = (_HARNESS + _functions() + "function SCENARIO()\n" + scenario + "\nend\n" + _TAIL)
    proc = subprocess.run([luajit, "-"], input=src, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout.strip()
    assert not out.startswith("ERROR"), out
    return json.loads(out)


def test_single_book_with_null_type_downloads():
    """The crash in #259: one book tapped in a series, its type null."""
    assert _run(r"""
        local b = book(1, "Orphan", NULL)
        self:_downloadListBooks({ series_name = "Ink Tide", book_type = "manga", books = { b } }, b)
    """) == ["/books/manga/Ink Tide/Vol. 1 — Orphan.epub"]


def test_single_book_with_null_type_and_no_batch_type_falls_back_to_book():
    assert _run(r"""
        local b = book(1, "Orphan", NULL)
        self:_downloadListBooks({ series_name = "__unserialized__", book_type = NULL, books = { b } }, b)
    """) == ["/books/book/Quill Marrow/Vol. 1 — Orphan.epub"]


def test_author_list_with_null_type_downloads():
    """The mixed list (author / search) passes the per-book value as the batch type."""
    assert _run(r"""
        local a = book(1, "Orphan", NULL)
        local b = book(2, "Typed", "light_novel", { series = "Ink Tide" })
        self:_downloadListBooks({ books = { a, b } })
    """) == [
        "/books/book/Quill Marrow/Vol. 1 — Orphan.epub",
        "/books/light_novel/Ink Tide/Vol. 2 — Typed.epub",
    ]


def test_whole_series_with_null_type_uses_series_type():
    assert _run(r"""
        local a, b = book(1, "One", "manga"), book(2, "Two", NULL)
        self:_downloadListBooks({ series_name = "Ink Tide", book_type = "manga", books = { a, b } })
    """) == [
        "/books/manga/Ink Tide/Vol. 1 — One.epub",
        "/books/manga/Ink Tide/Vol. 2 — Two.epub",
    ]


def test_template_with_null_type_renders_book():
    assert _run(r"""
        settings.tomesync_download_template = "{book_type}/{series}/{title}"
        local b = book(1, "Orphan", NULL, { series = "Ink Tide" })
        self:_downloadListBooks({ books = { b } })
    """) == ["/books/book/Ink Tide/Orphan.epub"]


def test_own_type_still_wins_over_batch_type():
    """Issue #88 stays fixed: a typed book is filed under its own type."""
    assert _run(r"""
        local b = book(1, "Standalone", "royalroad")
        self:_downloadListBooks({ series_name = "__unserialized__", book_type = "light_novel", books = { b } })
    """) == ["/books/royalroad/Quill Marrow/Vol. 1 — Standalone.epub"]
