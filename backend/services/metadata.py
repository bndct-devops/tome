"""
Metadata extraction from ebook files.
Supports: epub, pdf, cbz/cbr, mobi (title/author only for mobi).
Covers are saved as JPEG into the covers directory.
"""
import hashlib
import io
import logging
import re
import zipfile
from pathlib import Path
from typing import Optional

from PIL import Image

logger = logging.getLogger(__name__)

SUPPORTED_FORMATS = {".epub", ".pdf", ".cbz", ".cbr", ".mobi"}

# Word tokenizer that also works for scripts without spaces: each CJK
# ideograph / kana / hangul syllable counts as one "word", while runs of
# Latin/Cyrillic/Greek letters (with internal apostrophes/hyphens) count as one
# each. Whitespace-splitting alone would massively undercount a zh/ja/ko book.
_WORD_RE = re.compile(
    r"[㐀-䶿一-鿿豈-﫿"      # CJK ideographs
    r"぀-ゟ゠-ヿ"                      # hiragana + katakana
    r"가-힣]"                                 # hangul syllables
    r"|[0-9A-Za-zÀ-ɏͰ-ϿЀ-ӿ]"
    r"+(?:['’\-][0-9A-Za-zÀ-ɏ]+)*"
)


def _html_to_text(html: str) -> str:
    """Strip <script>/<style> blocks and all tags, leaving plain text."""
    html = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    return re.sub(r"(?s)<[^>]+>", " ", html)


def count_words_text(text: str) -> int:
    return len(_WORD_RE.findall(text))


def _count_words_in_epub_book(book) -> Optional[int]:
    """Sum word counts across every XHTML document in an already-open EPUB.
    Returns None if the book has no readable document items."""
    import ebooklib

    total = 0
    found = False
    for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
        try:
            raw = item.get_content()
        except Exception:  # noqa: BLE001 — skip an unreadable spine item
            continue
        found = True
        total += count_words_text(_html_to_text(raw.decode("utf-8", "ignore")))
    return total if found else None


def _count_words_from_zip(path: Path) -> Optional[int]:
    """Fallback word count straight from the EPUB zip.

    ebooklib's reader is strict about spec-compliant packaging — a manifest
    entry pointing at a missing file, or an EPUB3 nav with no <ol>, makes it
    raise before it ever reaches the body text. The actual chapters are still
    perfectly readable, so when ebooklib bails we read every XHTML/HTML member
    directly and count those. Returns None only if the archive has no readable
    markup at all."""
    total = 0
    found = False
    try:
        with zipfile.ZipFile(path) as zf:
            for name in zf.namelist():
                if not name.lower().endswith((".xhtml", ".html", ".htm")):
                    continue
                try:
                    raw = zf.read(name)
                except Exception:  # noqa: BLE001 — skip an unreadable member
                    continue
                found = True
                total += count_words_text(_html_to_text(raw.decode("utf-8", "ignore")))
    except Exception as e:  # noqa: BLE001 — not a usable zip
        logger.warning("word count zip-fallback error for %s: %s", path, e)
        return None
    return total if found else None


def count_words_epub(path: Path) -> Optional[int]:
    """Open an EPUB from disk and count its words. Used by the backfill job;
    ingest reuses the already-open book via _count_words_in_epub_book.

    Falls back to reading the zip directly when ebooklib refuses to parse a
    technically-malformed (but readable) EPUB — see _count_words_from_zip."""
    try:
        from ebooklib import epub

        book = epub.read_epub(str(path), options={"ignore_ncx": True})
        wc = _count_words_in_epub_book(book)
        if wc is not None:
            return wc
    except Exception as e:  # noqa: BLE001
        logger.info("ebooklib could not parse %s (%s); trying zip fallback", path, e)
    return _count_words_from_zip(path)


def _opf_meta_by_name(book, name: str) -> Optional[str]:
    """Read an OPF2 <meta name="..." content="..."/> value.

    ebooklib stores these under the OPF "meta" key as (None, attrs) tuples — NOT
    under a key matching the name — so get_metadata("OPF", "calibre:series") never
    matches. Calibre embeds series this way, so we have to scan the meta list.
    """
    for _value, attrs in book.get_metadata("OPF", "meta"):
        if attrs.get("name") == name:
            return attrs.get("content")
    return None


def _opf3_collection(book) -> tuple[Optional[str], Optional[str]]:
    """Read an EPUB3 series: <meta property="belongs-to-collection">Name</meta>
    plus the refining <meta property="group-position">N</meta>."""
    name = idx = coll_id = None
    for value, attrs in book.get_metadata("OPF", "meta"):
        if attrs.get("property") == "belongs-to-collection":
            name = value
            coll_id = attrs.get("id")
    if coll_id:
        for value, attrs in book.get_metadata("OPF", "meta"):
            if attrs.get("refines") in (f"#{coll_id}", coll_id) and attrs.get("property") == "group-position":
                idx = value
    return name, idx


def get_format(path: Path) -> Optional[str]:
    return path.suffix.lower().lstrip(".") if path.suffix.lower() in SUPPORTED_FORMATS else None


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def save_cover(image_data: bytes, covers_dir: Path, book_hash: str) -> Optional[str]:
    """Save cover image as JPEG, return relative filename."""
    try:
        img = Image.open(io.BytesIO(image_data))
        img = img.convert("RGB")
        # Cap at 600px wide to save space
        if img.width > 600:
            ratio = 600 / img.width
            img = img.resize((600, int(img.height * ratio)), Image.LANCZOS)
        covers_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{book_hash[:16]}.jpg"
        img.save(covers_dir / filename, "JPEG", quality=85, optimize=True)
        return filename
    except Exception as e:
        logger.warning("Failed to save cover: %s", e)
        return None


def extract_epub(path: Path, covers_dir: Path) -> dict:
    import ebooklib
    from ebooklib import epub

    meta: dict = {}
    cover_data: Optional[bytes] = None

    try:
        book = epub.read_epub(str(path), options={"ignore_ncx": True})

        def _first(items):
            return items[0] if items else None

        title = _first(book.get_metadata("DC", "title"))
        if title:
            meta["title"] = title[0]

        authors = book.get_metadata("DC", "creator")
        if authors:
            meta["author"] = ", ".join(a[0] for a in authors)

        publisher = _first(book.get_metadata("DC", "publisher"))
        if publisher:
            meta["publisher"] = publisher[0]

        language = _first(book.get_metadata("DC", "language"))
        if language:
            meta["language"] = language[0][:8]

        description = _first(book.get_metadata("DC", "description"))
        if description:
            # Strip HTML tags from description
            meta["description"] = re.sub(r"<[^>]+>", "", description[0]).strip()

        identifier = _first(book.get_metadata("DC", "identifier"))
        if identifier and identifier[0]:
            val = identifier[0]
            if re.match(r"^\d{9,13}$", val.replace("-", "")):
                meta["isbn"] = val

        date = _first(book.get_metadata("DC", "date"))
        if date and date[0]:
            m = re.match(r"(\d{4})", date[0])
            if m:
                meta["year"] = int(m.group(1))

        # Genre/category tags. Calibre and most tools store these as
        # <dc:subject> elements — one per tag.
        subjects = book.get_metadata("DC", "subject")
        if subjects:
            genres: list[str] = []
            seen: set[str] = set()
            for s in subjects:
                val = (s[0] or "").strip()
                if val and val.lower() not in seen:
                    seen.add(val.lower())
                    genres.append(val)
            if genres:
                meta["_genres"] = genres
                meta["_genre_source"] = "embedded"

        # Series from embedded metadata. Calibre writes OPF2
        # <meta name="calibre:series" .../>; EPUB3 uses belongs-to-collection.
        series = _opf_meta_by_name(book, "calibre:series")
        series_idx = _opf_meta_by_name(book, "calibre:series_index")
        if not series:
            series, series_idx = _opf3_collection(book)
        if series:
            meta["series"] = series
        if series_idx:
            try:
                meta["series_index"] = float(series_idx)
            except (ValueError, TypeError):
                pass

        # Fallback: parse series from title if not found
        # Handles: "Title: Volume 15", "Title, Vol. 1", "Title Vol.1", etc.
        if "series" not in meta and "title" in meta:
            title = meta["title"]
            vol_match = re.search(
                r'^(.*?)(?:[,:]?\s*)(?:Vol(?:ume)?\.?\s*(\d+(?:\.\d+)?))\s*$',
                title, re.IGNORECASE
            )
            if vol_match:
                series_name = vol_match.group(1).strip().rstrip(",-: ")
                if series_name:
                    meta["series"] = series_name
                    try:
                        meta["series_index"] = float(vol_match.group(2))
                    except (ValueError, TypeError):
                        pass

        # Word count — reuse the already-open book (no second parse).
        wc = _count_words_in_epub_book(book)
        if wc:
            meta["word_count"] = wc

        # Cover extraction
        cover_id = None
        for item in book.get_metadata("OPF", "cover"):
            if item[1] and "name" in item[1] and item[1]["name"] == "cover":
                cover_id = item[1].get("content")

        # Try to find cover image item
        for item in book.get_items():
            if item.get_type() == ebooklib.ITEM_COVER:
                cover_data = item.get_content()
                break
            if cover_id and item.get_id() == cover_id:
                cover_data = item.get_content()
                break

        # Fallback: first image item
        if not cover_data:
            for item in book.get_items_of_type(ebooklib.ITEM_IMAGE):
                name = item.get_name().lower()
                if any(kw in name for kw in ("cover", "front", "thumb")):
                    cover_data = item.get_content()
                    break
            if not cover_data:
                for item in book.get_items_of_type(ebooklib.ITEM_IMAGE):
                    cover_data = item.get_content()
                    break

    except Exception as e:
        logger.warning("epub extraction error for %s: %s", path, e)

    if cover_data:
        meta["_cover_data"] = cover_data

    return meta


def extract_pdf(path: Path, covers_dir: Path) -> dict:
    import fitz  # PyMuPDF

    meta: dict = {}
    try:
        doc = fitz.open(str(path))
        info = doc.metadata or {}

        if info.get("title"):
            meta["title"] = info["title"].strip()
        if info.get("author"):
            meta["author"] = info["author"].strip()
        if info.get("subject"):
            meta["description"] = info["subject"].strip()
        if info.get("creationDate"):
            m = re.match(r"D:(\d{4})", info["creationDate"])
            if m:
                meta["year"] = int(m.group(1))

        # Cover: render first page as image
        if len(doc) > 0:
            page = doc[0]
            mat = fitz.Matrix(1.5, 1.5)  # 1.5x zoom
            pix = page.get_pixmap(matrix=mat)
            meta["_cover_data"] = pix.tobytes("jpeg")

        doc.close()
    except Exception as e:
        logger.warning("pdf extraction error for %s: %s", path, e)

    return meta


def _parse_comic_info_xml(xml_bytes: bytes) -> dict:
    """Parse ComicInfo.xml (ComicRack standard) into metadata dict."""
    import xml.etree.ElementTree as ET
    meta: dict = {}
    try:
        root = ET.fromstring(xml_bytes)

        field_map = {
            "Title": "title",
            "Series": "series",
            "Writer": "author",
            "Publisher": "publisher",
            "Summary": "description",
            "LanguageISO": "language",
        }
        for xml_field, tome_field in field_map.items():
            el = root.find(xml_field)
            if el is not None and el.text:
                meta[tome_field] = el.text.strip()

        # Series index: prefer Number, fallback to Volume
        for field in ("Number", "Volume"):
            el = root.find(field)
            if el is not None and el.text:
                try:
                    meta["series_index"] = float(el.text.strip())
                    break
                except ValueError:
                    pass

        # Year
        el = root.find("Year")
        if el is not None and el.text:
            try:
                meta["year"] = int(el.text.strip())
            except ValueError:
                pass

        # Genre -> tags
        el = root.find("Genre")
        if el is not None and el.text:
            meta["_genres"] = [g.strip() for g in el.text.split(",") if g.strip()]
            meta["_genre_source"] = "comic_info"

        # Manga detection
        el = root.find("Manga")
        if el is not None and el.text:
            manga_val = el.text.strip()
            if manga_val in ("Yes", "YesAndRightToLeft"):
                meta["_is_manga"] = True
                if manga_val == "YesAndRightToLeft":
                    meta["_is_rtl"] = True

        # Page count
        el = root.find("PageCount")
        if el is not None and el.text:
            try:
                meta["_page_count"] = int(el.text.strip())
            except ValueError:
                pass
    except ET.ParseError:
        logger.warning("Failed to parse ComicInfo.xml")

    return meta


def extract_cbz(path: Path, covers_dir: Path) -> dict:
    """Extract metadata and cover from CBZ archive."""
    meta: dict = {}
    try:
        with zipfile.ZipFile(path, "r") as zf:
            # Check for ComicInfo.xml (case-insensitive)
            comic_info_name = None
            for name in zf.namelist():
                if name.lower() == "comicinfo.xml":
                    comic_info_name = name
                    break

            if comic_info_name:
                xml_bytes = zf.read(comic_info_name)
                meta.update(_parse_comic_info_xml(xml_bytes))

            # Extract cover from first image
            images = sorted(
                n for n in zf.namelist()
                if n.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))
                and not n.startswith("__MACOSX")
                and not n.lower().endswith("comicinfo.xml")
            )
            if images:
                meta["_cover_data"] = zf.read(images[0])
    except Exception as e:
        logger.warning("cbz extraction error for %s: %s", path, e)

    return meta


def extract_cbr(path: Path, covers_dir: Path) -> dict:
    """Extract metadata and cover from CBR (RAR) archive."""
    meta: dict = {}
    try:
        import rarfile
        with rarfile.RarFile(str(path)) as rf:
            # Check for ComicInfo.xml
            comic_info_name = None
            for name in rf.namelist():
                if name.lower() == "comicinfo.xml":
                    comic_info_name = name
                    break

            if comic_info_name:
                xml_bytes = rf.read(comic_info_name)
                meta.update(_parse_comic_info_xml(xml_bytes))

            # Extract cover from first image
            images = sorted(
                n for n in rf.namelist()
                if n.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))
            )
            if images:
                meta["_cover_data"] = rf.read(images[0])
    except Exception as e:
        logger.warning("cbr extraction error for %s: %s", path, e)

    return meta


def extract_metadata(path: Path, covers_dir: Path, content_hash: Optional[str] = None) -> dict:
    """
    Extract metadata from a book file. Returns a dict with fields matching
    the Book model. Cover is saved to disk if found.
    """
    fmt = get_format(path)
    if not fmt:
        return {}

    if fmt == "epub":
        meta = extract_epub(path, covers_dir)
    elif fmt == "pdf":
        meta = extract_pdf(path, covers_dir)
    elif fmt == "cbz":
        meta = extract_cbz(path, covers_dir)
    elif fmt == "cbr":
        meta = extract_cbr(path, covers_dir)
    else:
        meta = {}

    # Fallback title: filename without extension
    if "title" not in meta:
        meta["title"] = path.stem

    # Normalize series name: strip subtitle suffixes like " - Subtitle" or " -Subtitle-"
    if "series" in meta and meta["series"]:
        s = meta["series"].strip()
        # Strip patterns like " - Subtitle" or " -Subtitle-" or " -Subtitle" at end
        s = re.sub(r'\s+[-\u2013]\s+.+$', '', s).strip()
        s = re.sub(r'\s+-[^-].*$', '', s).strip()
        s = s.rstrip(',-: ')
        if s:
            meta["series"] = s

    # Fallback year from filename: look for (YYYY) pattern
    if "year" not in meta:
        m = re.search(r'\((\d{4})\)', path.stem)
        if m:
            yr = int(m.group(1))
            if 1800 <= yr <= 2100:
                meta["year"] = yr

    # Save cover if we got data
    cover_data = meta.pop("_cover_data", None)
    if cover_data:
        # Reuse the caller's already-computed hash for the cover filename when
        # provided — avoids a second full-file SHA-256 per book during scans.
        book_hash = content_hash or sha256_file(path)
        filename = save_cover(cover_data, covers_dir, book_hash)
        if filename:
            meta["cover_path"] = filename

    return meta
