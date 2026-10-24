"""
Library layout per book type (#276): presets plus an optional free template.

A layout turns a book's metadata into a relative path *inside* the type
folder (BookType.folder, #270). The organizer adds the type folder in front.

Presets
-------
``series_first`` (default, the layout Tome always had)
    series      → ``Series/Title v01.ext``
    else author → ``Author/Title (Year).ext``
    else        → ``Unknown/Title.ext``
``author_series``
    series      → ``Author/Series/Title v01.ext``
    else        → ``Author/Title (Year).ext``
``author_only``
    series      → ``Author/Title v01.ext``
    else        → ``Author/Title (Year).ext``

In the author presets the author is the first author of a joined list and
falls back to ``Unknown``.

Template (Advanced)
-------------------
Fields ``{author} {series} {title} {index} {year} {ext}``. ``{a|b}`` takes the
first non-empty field, and the last item may be literal text:
``{series|author|Unknown}``. ``[...]`` is an optional group, included only when
every field inside it is non-empty: ``[ v{index}]``. Groups do not nest and may
contain ``/``. ``/`` separates folders. Every rendered component goes through
``sanitize_name``, so nothing can escape the type folder.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Union

from backend.services.organizer import (
    _SPACES,
    _TRAIL,
    _UNSAFE,
    _layout_path,
    _strip_vol_from_title,
    _vol_str,
    sanitize_name,
)

DEFAULT_PRESET = "series_first"
TEMPLATE_MAX_LEN = 255
FIELDS = ("author", "series", "title", "index", "year", "ext")


# ── Presets ──────────────────────────────────────────────────────────────────

PRESETS: dict[str, dict[str, str]] = {
    "series_first": {
        "label": "Series first",
        "description": "Series books in a folder per series, other books in a folder per author.",
        "template": "{series|author|Unknown}/{title}[ v{index}]{ext}",
    },
    "author_series": {
        "label": "Author, then series",
        "description": "A folder per author, with a folder per series inside it.",
        "template": "{author|Unknown}/[{series}/]{title}[ v{index}]{ext}",
    },
    "author_only": {
        "label": "Author only",
        "description": "A folder per author; series books keep their volume number in the file name.",
        "template": "{author|Unknown}/{title}[ v{index}]{ext}",
    },
}


def normalize_preset(raw: Optional[str]) -> str:
    """Validate a preset name. Raises ValueError for an unknown one."""
    s = (raw or "").strip() or DEFAULT_PRESET
    if s not in PRESETS:
        raise ValueError(f"Unknown layout preset: {s}. Use one of: {', '.join(PRESETS)}")
    return s


_AND_SPLIT = re.compile(r"\s*(?:;|\s&\s)\s*")


def first_author(author: Optional[str]) -> str:
    """The first author of a joined author string.

    Tome stores several authors as one string joined with ", " (EPUB creators,
    metadata fetchers). ``;`` and `` & `` always separate authors; a comma does
    only when every part looks like a full name (has a space), so a single
    "Coben, Harlan" stays whole.
    """
    s = (author or "").strip()
    if not s:
        return ""
    s = _AND_SPLIT.split(s)[0].strip()
    if "," in s:
        parts = [p.strip() for p in s.split(",") if p.strip()]
        if len(parts) > 1 and all(" " in p for p in parts):
            s = parts[0]
    return s


@dataclass
class _Fields:
    author: str
    series: str
    title: str
    index: str
    year: str
    ext: str

    def get(self, name: str) -> str:
        return getattr(self, name)


def _clean(s) -> str:
    """sanitize_name without the 'Unknown' fallback: "" stays ""."""
    if s is None:
        return ""
    s = _UNSAFE.sub("", str(s))
    s = _SPACES.sub(" ", s).strip()
    return _TRAIL.sub("", s)


def _fields(meta: dict, original_filename: str) -> _Fields:
    suffix = Path(original_filename).suffix.lower() or ".epub"
    title = sanitize_name(meta.get("title") or Path(original_filename).stem)
    series = _clean(meta.get("series"))
    if meta.get("series"):
        title = _strip_vol_from_title(title)
    vol = _vol_str(meta.get("series_index"))
    year = meta.get("year")
    return _Fields(
        author=_clean(first_author(meta.get("author"))),
        series=series,
        title=title,
        index=vol[1:] if vol else "",
        year=str(year) if year else "",
        ext=suffix,
    )


def _author_folder(f: _Fields) -> str:
    return f.author or "Unknown"


def _series_file(f: _Fields) -> str:
    return f"{f.title} v{f.index}{f.ext}" if f.index else f"{f.title}{f.ext}"


def _plain_file(f: _Fields) -> str:
    return f"{f.title} ({f.year}){f.ext}" if f.year else f"{f.title}{f.ext}"


def _preset_author_series(meta: dict, original_filename: str) -> Path:
    f = _fields(meta, original_filename)
    if f.series:
        return Path(_author_folder(f)) / f.series / _series_file(f)
    return Path(_author_folder(f)) / _plain_file(f)


def _preset_author_only(meta: dict, original_filename: str) -> Path:
    f = _fields(meta, original_filename)
    if f.series:
        return Path(_author_folder(f)) / _series_file(f)
    return Path(_author_folder(f)) / _plain_file(f)


_PRESET_FUNCS = {
    "series_first": _layout_path,  # byte-identical to the layout before #276
    "author_series": _preset_author_series,
    "author_only": _preset_author_only,
}


def render_preset(preset: str, meta: dict, original_filename: str) -> Path:
    func = _PRESET_FUNCS.get(preset) or _PRESET_FUNCS[DEFAULT_PRESET]
    return func(meta, original_filename)


# ── Template language ────────────────────────────────────────────────────────

class TemplateError(ValueError):
    pass


@dataclass
class _Lit:
    text: str
    pos: int = 0


@dataclass
class _Field:
    names: list[str]
    fallback: Optional[str] = None  # literal last item
    pos: int = 0


@dataclass
class _Sep:
    pos: int = 0


@dataclass
class _Group:
    items: list = field(default_factory=list)
    pos: int = 0


def _err(msg: str, pos: int) -> TemplateError:
    return TemplateError(f"{msg} at position {pos + 1}")


def parse_template(template: str) -> list:
    """Parse a template into tokens, or raise TemplateError naming the position."""
    t = template or ""
    if not t.strip():
        raise TemplateError("Template is empty")
    if len(t) > TEMPLATE_MAX_LEN:
        raise TemplateError(f"Template must be at most {TEMPLATE_MAX_LEN} characters")
    if t.startswith("/"):
        raise _err("Template must not start with /", 0)
    if "\\" in t:
        raise _err("Use / to separate folders, not \\", t.index("\\"))
    if any(ord(c) < 32 for c in t):
        raise TemplateError("Template contains control characters")

    tokens: list = []
    group: Optional[_Group] = None
    group_start = 0
    lit = ""
    lit_pos = 0
    i = 0

    def flush():
        nonlocal lit
        if lit:
            (group.items if group is not None else tokens).append(_Lit(lit, lit_pos))
            lit = ""

    while i < len(t):
        c = t[i]
        if c == "{":
            end = t.find("}", i + 1)
            nxt = t.find("{", i + 1)
            if end == -1 or (nxt != -1 and nxt < end):
                raise _err("Unclosed {", i)
            body = t[i + 1:end]
            parts = [p.strip() for p in body.split("|")]
            if not body.strip() or any(not p for p in parts):
                raise _err("Empty field name", i)
            if any(ch in body for ch in "[]/"):
                raise _err("Field names cannot contain [, ] or /", i)
            names: list[str] = []
            fallback = None
            for k, p in enumerate(parts):
                if p in FIELDS:
                    if fallback is not None:
                        raise _err("Literal text is only allowed as the last fallback", i)
                    names.append(p)
                elif k == len(parts) - 1 and k > 0:
                    fallback = p
                elif k > 0:
                    raise _err("Literal text is only allowed as the last fallback", i)
                else:
                    raise TemplateError(
                        f"Unknown field {{{p}}} at position {i + 1}; fields are "
                        + ", ".join("{" + f + "}" for f in FIELDS)
                    )
            flush()
            (group.items if group is not None else tokens).append(_Field(names, fallback, i))
            i = end + 1
            continue
        if c == "}":
            raise _err("Unexpected }", i)
        if c == "[":
            if group is not None:
                raise _err("Optional groups cannot be nested", i)
            flush()
            group = _Group(pos=i)
            group_start = i
            i += 1
            continue
        if c == "]":
            if group is None:
                raise _err("Unexpected ]", i)
            flush()
            if not any(isinstance(x, _Field) for x in group.items):
                raise _err("Optional group needs at least one field", group_start)
            tokens.append(group)
            group = None
            i += 1
            continue
        if c == "/":
            flush()
            (group.items if group is not None else tokens).append(_Sep(i))
            i += 1
            continue
        if not lit:
            lit_pos = i
        lit += c
        i += 1

    if group is not None:
        raise _err("Unclosed [", group_start)
    flush()

    # Folder components made only of literal text must not be empty, . or ..
    comp: list = []
    comp_pos = 0
    for tok in tokens + [_Sep(len(t))]:
        if isinstance(tok, _Sep):
            if all(isinstance(x, _Lit) for x in comp):
                text = "".join(x.text for x in comp).strip()
                if not text:
                    raise _err("Empty folder name", comp_pos)
                if text in (".", ".."):
                    raise _err("Folders named . or .. are not allowed", comp_pos)
            comp = []
            comp_pos = tok.pos + 1
        else:
            comp.append(tok)

    last = tokens[-1] if tokens else None
    if not (isinstance(last, _Field) and last.names == ["ext"] and last.fallback is None):
        raise TemplateError("Template must end with {ext}")

    # The file name (after the last top-level /) needs a real field outside
    # optional groups, or every book would be named ".epub".
    tail_start = max((k + 1 for k, x in enumerate(tokens) if isinstance(x, _Sep)), default=0)
    tail = tokens[tail_start:-1]
    if not any(isinstance(x, _Field) and x.names != ["ext"] for x in tail):
        raise TemplateError("The file name needs a field outside optional groups, e.g. {title}")
    return tokens


def validate_template(template: str) -> Optional[str]:
    """None when valid, else a readable error message."""
    try:
        parse_template(template)
        return None
    except TemplateError as e:
        return str(e)


def _field_value(tok: _Field, f: _Fields) -> str:
    for n in tok.names:
        v = f.get(n)
        if v:
            return v
    return tok.fallback or ""


def render_template(template: str, meta: dict, original_filename: str) -> Path:
    tokens = parse_template(template)
    f = _fields(meta, original_filename)
    comps: list[str] = [""]

    def emit(tok):
        if isinstance(tok, _Sep):
            comps.append("")
        elif isinstance(tok, _Lit):
            comps[-1] += tok.text
        elif isinstance(tok, _Field):
            # Field values cannot add folders: '/' is stripped by sanitize.
            comps[-1] += _field_value(tok, f).replace("/", "")

    for tok in tokens:
        if isinstance(tok, _Group):
            if all(_field_value(x, f) for x in tok.items if isinstance(x, _Field)):
                for x in tok.items:
                    emit(x)
        else:
            emit(tok)

    # Each component is sanitized on its own; an empty one becomes "Unknown".
    parts = [sanitize_name(c) for c in comps]
    return Path(*parts)


# ── Entry point ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class TypeLayout:
    """Where books of one type go: folder (#270), preset and template (#276)."""
    folder: str = ""
    preset: str = DEFAULT_PRESET
    template: str = ""


def render_layout(meta: dict, original_filename: str, preset: str = DEFAULT_PRESET, template: str = "") -> Path:
    """Relative path inside the type folder. A non-empty template wins; a
    template that no longer parses falls back to the preset rather than
    leaving a book without a destination."""
    if (template or "").strip():
        try:
            return render_template(template, meta, original_filename)
        except TemplateError:
            pass
    return render_preset(preset or DEFAULT_PRESET, meta, original_filename)


LayoutLike = Union[TypeLayout, str, None]
