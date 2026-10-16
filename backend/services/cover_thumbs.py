"""Small WebP copies of book covers for grids and lists.

A library card is about 200 px wide, but the stored cover can be a 2 MB
download from a metadata source. ``thumbnail`` returns a resized WebP copy
from ``data/cover_thumbs/``, made on first request. The file name carries the
source's mtime and size, so a replaced cover (which keeps its file name)
gets a fresh thumbnail and the stale ones of that book are removed.
"""
import logging
import os
from pathlib import Path

from PIL import Image

logger = logging.getLogger(__name__)

# Widths the frontend asks for: list thumbnails and grid cards (2x density).
WIDTHS = (160, 400)
QUALITY = 80


def snap_width(w: int) -> int | None:
    """The smallest allowed width that is at least ``w``, or None (use the original)."""
    return next((x for x in WIDTHS if x >= w), None)


def thumbnail(source: Path, book_id: int, width: int, thumbs_dir: Path) -> Path:
    """Path of a WebP copy of ``source`` at most ``width`` px wide.

    Falls back to ``source`` itself when it is already that narrow or cannot
    be decoded."""
    st = source.stat()
    target = thumbs_dir / f"{book_id}_{width}_{st.st_mtime_ns}_{st.st_size}.webp"
    if target.exists():
        return target
    try:
        with Image.open(source) as img:
            if img.width <= width:
                return source
            img = img.convert("RGB")
            img = img.resize((width, round(img.height * width / img.width)), Image.LANCZOS)
            thumbs_dir.mkdir(parents=True, exist_ok=True)
            tmp = target.with_suffix(f".{os.getpid()}.tmp")
            img.save(tmp, "WEBP", quality=QUALITY, method=4)
            os.replace(tmp, target)
    except Exception as exc:  # noqa: BLE001 - a bad image still has its original
        logger.warning("cover thumbnail failed for book %s: %s", book_id, exc)
        return source
    for old in thumbs_dir.glob(f"{book_id}_{width}_*.webp"):
        if old != target:
            old.unlink(missing_ok=True)
    return target
