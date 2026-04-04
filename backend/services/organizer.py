"""
Library path organizer.
Determines where a book file should live inside the library directory.

Rules (in priority order):
  1. Has series  → library/{Series Name}/{Title} v{index:04.1f}.{ext}
  2. Has author  → library/{Author}/{Title} ({Year}).{ext}
  3. Fallback    → library/Unknown/{Title}.{ext}

All path components are sanitized to be filesystem-safe.
"""
import re
from pathlib import Path


_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_SPACES = re.compile(r'\s+')
_TRAIL  = re.compile(r'[.\s]+$')


def sanitize_name(s: str) -> str:
    """Remove characters that are illegal on common filesystems."""
    s = _UNSAFE.sub('', s)
    s = _SPACES.sub(' ', s).strip()
    s = _TRAIL.sub('', s)
    return s or 'Unknown'


_VOL_IN_TITLE = re.compile(r',?\s*\b(?:vol\.?|volume|v)\s*\d+(?:\.\d+)?', re.IGNORECASE)


def _vol_str(index: float | None) -> str:
    """Format a volume index: 1.0 → 'v01', 10.5 → 'v10.5'"""
    if index is None:
        return ''
    if index == int(index):
        return f'v{int(index):02d}'
    return f'v{index:.1f}'


def _strip_vol_from_title(title: str) -> str:
    """Remove any existing volume indicator from a title string."""
    return _VOL_IN_TITLE.sub('', title).strip().rstrip(',- ').strip()


def get_library_path(meta: dict, original_filename: str) -> Path:
    """
    Return a *relative* path (no leading slash) for a book inside library_dir.
    The caller is responsible for resolving it against library_dir and ensuring
    no collisions before moving the file.
    """
    suffix = Path(original_filename).suffix.lower() or '.epub'
    title  = sanitize_name(meta.get('title') or Path(original_filename).stem)
    series = meta.get('series')
    author = meta.get('author')
    year   = meta.get('year')
    idx    = meta.get('series_index')

    if series:
        folder     = sanitize_name(series)
        clean_title = _strip_vol_from_title(title)
        vol        = _vol_str(idx)
        filename   = f'{clean_title} {vol}{suffix}' if vol else f'{clean_title}{suffix}'
        return Path(folder) / filename

    if author:
        folder   = sanitize_name(author)
        yr_part  = f' ({year})' if year else ''
        filename = f'{title}{yr_part}{suffix}'
        return Path(folder) / filename

    return Path('Unknown') / (title + suffix)


def resolve_unique_path(base_dir: Path, rel_path: Path) -> Path:
    """
    Resolve rel_path inside base_dir, appending (2), (3) … to the stem
    if the destination already exists.
    """
    dest = base_dir / rel_path
    if not dest.exists():
        return dest

    stem   = dest.stem
    suffix = dest.suffix
    parent = dest.parent
    n = 2
    while True:
        candidate = parent / f'{stem} ({n}){suffix}'
        if not candidate.exists():
            return candidate
        n += 1
