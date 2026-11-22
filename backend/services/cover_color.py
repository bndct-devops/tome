"""One muted colour per cover, shown as the tile's placeholder while it loads.

``cover_color`` reads a cover file and returns a ``#rrggbb`` string: the
largest bucket of a small quantised palette, with saturation capped and
lightness kept in a middle band so the tile reads on light and dark themes.
``refresh_cover_color`` sets it on a book whenever its cover is set or
replaced; ``backfill_cover_colors`` fills existing books in the background.
"""
import colorsys
import logging
import time
from pathlib import Path

from PIL import Image

logger = logging.getLogger(__name__)

SAMPLE = 32
PALETTE = 5
MIN_LIGHTNESS = 0.22
MAX_LIGHTNESS = 0.72
MAX_SATURATION = 0.55
BATCH = 50


def cover_color(path: Path) -> str | None:
    """The cover's representative colour as ``#rrggbb``, or None when the
    file is missing or cannot be decoded."""
    try:
        with Image.open(path) as img:
            img.draft("RGB", (SAMPLE * 2, SAMPLE * 2))
            if img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info):
                rgba = img.convert("RGBA")
                small = Image.new("RGB", rgba.size, (255, 255, 255))
                small.paste(rgba, mask=rgba.getchannel("A"))
            else:
                small = img.convert("RGB")
            small = small.resize((SAMPLE, SAMPLE), Image.BILINEAR)
        quant = small.quantize(colors=PALETTE, method=Image.Quantize.MEDIANCUT)
        counts = quant.getcolors() or []
        if not counts:
            return None
        _, index = max(counts)
        pal = quant.getpalette() or []
        r, g, b = pal[index * 3:index * 3 + 3]
    except Exception:  # noqa: BLE001 - a broken cover just has no colour
        return None
    h, lightness, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
    lightness = min(max(lightness, MIN_LIGHTNESS), MAX_LIGHTNESS)
    s = min(s, MAX_SATURATION)
    r2, g2, b2 = colorsys.hls_to_rgb(h, lightness, s)
    return "#{:02x}{:02x}{:02x}".format(round(r2 * 255), round(g2 * 255), round(b2 * 255))


def refresh_cover_color(book, covers_dir: Path | None = None) -> None:
    """Set ``book.cover_color`` from its current cover file (None without one)."""
    if covers_dir is None:
        from backend.core.config import settings
        covers_dir = settings.covers_dir
    book.cover_color = cover_color(covers_dir / book.cover_path) if book.cover_path else None


def backfill_cover_colors(engine=None, covers_dir: Path | None = None, pause: float = 0.02) -> int:
    """Fill ``cover_color`` for books that have a cover and no colour yet.

    Runs in a daemon thread at startup. Works in batches by id, reading with
    one short query and writing with one short committed UPDATE per batch, so
    other writers wait at most a moment; raw SQL keeps ``updated_at`` as it
    is (it versions cover URLs and baked downloads). A row whose file is gone
    or unreadable stays NULL and is skipped. Safe to stop at any point: the
    next run picks up the rows still NULL. Returns the number filled."""
    from sqlalchemy import text

    if engine is None:
        from backend.core.database import engine
    if covers_dir is None:
        from backend.core.config import settings
        covers_dir = settings.covers_dir
    if not covers_dir.is_dir():
        return 0
    filled = 0
    last_id = 0
    try:
        while True:
            with engine.connect() as conn:
                rows = conn.execute(text(
                    "SELECT id, cover_path FROM books WHERE cover_path IS NOT NULL "
                    "AND cover_color IS NULL AND id > :last ORDER BY id LIMIT :n"
                ), {"last": last_id, "n": BATCH}).fetchall()
            if not rows:
                break
            last_id = rows[-1][0]
            updates = []
            for book_id, rel in rows:
                path = covers_dir / rel
                if not path.is_file():
                    continue
                colour = cover_color(path)
                if colour:
                    updates.append({"c": colour, "i": book_id})
            if updates:
                with engine.begin() as conn:
                    conn.execute(text(
                        "UPDATE books SET cover_color = :c WHERE id = :i AND cover_color IS NULL"
                    ), updates)
                filled += len(updates)
            if pause:
                time.sleep(pause)
    except Exception:  # noqa: BLE001 - a background fill must never take the server down
        logger.exception("cover colour backfill stopped after %d books", filled)
        return filled
    if filled:
        logger.info("cover colour backfill: coloured %d covers", filled)
    return filled
