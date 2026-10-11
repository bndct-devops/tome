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


def _build_chapter_map(
    doc_names: list[str],
    counts: list[int],
    toc_entries: list[tuple[str, str]],
) -> list[dict]:
    """Shared fraction math: ordered spine document names + their word counts +
    TOC entries as (title, href) → chapter boundaries as fraction-of-book.

    A chapter whose TOC entry points into spine item k starts at
    words(items 0..k-1) / total. Fragment anchors are ignored — top-level TOC
    entries almost always sit at a file boundary, and per-file granularity is
    what the time-per-chapter stat needs. Returns [] when there's no usable
    structure (no TOC, unresolvable hrefs, fewer than two distinct chapters).
    """
    from urllib.parse import unquote

    total = sum(counts)
    if total <= 0:
        return []
    cum = [0]
    for c in counts:
        cum.append(cum[-1] + c)
    href_to_idx = {name: i for i, name in enumerate(doc_names)}

    def resolve(href: str) -> Optional[int]:
        href = unquote(href.split("#", 1)[0])
        if href in href_to_idx:
            return href_to_idx[href]
        # Tolerate OPF-dir path differences by unique basename.
        base = href.rsplit("/", 1)[-1]
        hits = [i for h, i in href_to_idx.items() if h.rsplit("/", 1)[-1] == base]
        return hits[0] if len(hits) == 1 else None

    chapters: list[dict] = []
    for title, href in toc_entries:
        idx = resolve(href)
        if idx is None:
            continue
        chapters.append({
            "title": (title or "").strip() or f"Chapter {len(chapters) + 1}",
            "start_fraction": cum[idx] / total,
        })

    chapters.sort(key=lambda c: c["start_fraction"])
    out: list[dict] = []
    for c in chapters:
        # Two TOC entries resolving to the same start (front-matter stubs,
        # in-file subheadings) collapse to the first.
        if out and c["start_fraction"] <= out[-1]["start_fraction"] + 1e-9:
            continue
        out.append(c)
    if len(out) < 2:
        return []
    # Front matter (nav doc, cover page, copyright) sits before the first TOC
    # entry; fold it into chapter one so the map tiles the whole book.
    out[0]["start_fraction"] = 0.0
    for i, c in enumerate(out):
        c["idx"] = i
        c["end_fraction"] = out[i + 1]["start_fraction"] if i + 1 < len(out) else 1.0
    return out


def _flatten_ebooklib_toc(raw) -> list[tuple[str, str]]:
    """Normalize ebooklib's book.toc into (title, href) top-level entries.

    Real-world shapes seen: a proper list of Link/(Section, children) nodes; a
    BARE Link (single-entry nav); a single (Section, children) tuple; and a
    lone wrapping Section whose children are the actual chapters — unwrap that
    one level, or every such book collapses to "one chapter" and is dropped.
    """
    def is_node_tuple(n) -> bool:
        return isinstance(n, tuple) and len(n) >= 2 and isinstance(n[1], (list, tuple))

    if raw is None:
        nodes = []
    elif isinstance(raw, list):
        nodes = raw
    elif is_node_tuple(raw):
        nodes = [raw]
    elif isinstance(raw, tuple):
        nodes = list(raw)
    else:
        nodes = [raw]   # bare Link

    # Unwrap a single all-enclosing Section (or a single href-less entry with
    # children): its children are the real chapter list.
    while len(nodes) == 1 and is_node_tuple(nodes[0]):
        nodes = list(nodes[0][1])

    entries: list[tuple[str, str]] = []
    for node in nodes:
        # Entries are Link or (Section, [children]); a Section may carry its
        # own href, otherwise its first child anchors it. Only the top level
        # counts — nested subsections are noise for a per-chapter time split.
        head = node[0] if isinstance(node, tuple) else node
        href = getattr(head, "href", None)
        if not href and is_node_tuple(node) and node[1]:
            first = node[1][0]
            first = first[0] if isinstance(first, tuple) else first
            href = getattr(first, "href", None)
        if href:
            entries.append((getattr(head, "title", None) or "", href))
    return entries


def _ebooklib_spine_docs(book) -> tuple[list[str], list[str]]:
    """Spine documents of an already-open ebooklib book, in reading order, as
    (doc names, decoded HTML). These are exactly the inputs the chapter map
    counts; the reading companion's chapter text reuses them so its chapter
    boundaries line up with the stored fractions. An unreadable item yields
    "" (zero words)."""
    import ebooklib

    id_to_item = {item.get_id(): item for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT)}
    spine_ids = [sid for sid, _linear in book.spine if sid in id_to_item]
    doc_names: list[str] = []
    htmls: list[str] = []
    for sid in spine_ids:
        item = id_to_item[sid]
        doc_names.append(item.get_name())
        try:
            htmls.append(item.get_content().decode("utf-8", "ignore"))
        except Exception:  # noqa: BLE001 — unreadable spine item contributes no words
            htmls.append("")
    return doc_names, htmls


def _extract_epub_chapters(book) -> list[dict]:
    """TOC → chapter boundaries, from an already-open ebooklib book."""
    doc_names, htmls = _ebooklib_spine_docs(book)
    if not doc_names:
        return []
    counts = [count_words_text(_html_to_text(h)) for h in htmls]
    return _build_chapter_map(doc_names, counts, _flatten_ebooklib_toc(book.toc))


def _zip_chapter_inputs(path: Path) -> Optional[tuple[list[str], list[str], list[tuple[str, str]]]]:
    """Read the chapter-map inputs straight from the EPUB zip: spine document
    names (OPF-relative hrefs), their decoded HTML ("" when unreadable), and
    the TOC entries as (title, href), from the NCX (navPoints) or, failing
    that, the EPUB3 nav document's toc list. None when the package cannot be
    read at all."""
    import posixpath
    import xml.etree.ElementTree as ET

    from backend.services.epub_package import read_package

    NS = {
        "ncx": "http://www.daisy.org/z3986/2005/ncx/",
        "xhtml": "http://www.w3.org/1999/xhtml",
    }
    with zipfile.ZipFile(path) as zf:
        pkg = read_package(zf)
        if pkg is None:
            return None
        opf_dir = pkg.opf_dir

        def from_opf_dir(href: str) -> str:
            return posixpath.normpath(posixpath.join(opf_dir, href)) if opf_dir else href

        doc_names: list[str] = []
        htmls: list[str] = []
        for idref in pkg.spine:
            item = pkg.manifest.get(idref)
            href, media = (item.href, item.media_type) if item else ("", "")
            if not href or "xml" not in media and "html" not in media:
                continue
            member = from_opf_dir(href)
            doc_names.append(href)   # TOC hrefs are OPF-relative, like these
            try:
                htmls.append(zf.read(member).decode("utf-8", "ignore"))
            except Exception:  # noqa: BLE001
                htmls.append("")

        # TOC source 1: the NCX (spine@toc, or any manifest ncx item)
        toc_entries: list[tuple[str, str]] = []
        ncx_id = pkg.spine_toc
        ncx_href = None
        if ncx_id and ncx_id in pkg.manifest:
            ncx_href = pkg.manifest[ncx_id].href
        else:
            for item in pkg.manifest.values():
                if item.media_type == "application/x-dtbncx+xml":
                    ncx_href = item.href
                    break
        if ncx_href:
            try:
                ncx = ET.fromstring(zf.read(from_opf_dir(ncx_href)))
                for np in ncx.findall("./ncx:navMap/ncx:navPoint", NS):
                    label = np.find("./ncx:navLabel/ncx:text", NS)
                    content = np.find("./ncx:content", NS)
                    if content is not None and content.get("src"):
                        toc_entries.append((
                            (label.text or "") if label is not None else "",
                            content.get("src", ""),
                        ))
            except Exception:  # noqa: BLE001 — fall through to the nav
                toc_entries = []

        # TOC source 2: the EPUB3 nav document
        if not toc_entries:
            nav_href = None
            for item in pkg.manifest.values():
                if "nav" in (item.properties or "").split():
                    nav_href = item.href
                    break
            if nav_href:
                try:
                    nav = ET.fromstring(zf.read(from_opf_dir(nav_href)))
                    nav_dir = posixpath.dirname(nav_href)
                    for a in nav.findall(".//xhtml:nav//xhtml:li/xhtml:a", NS):
                        href = a.get("href")
                        if href:
                            # nav hrefs are nav-relative; rebase to OPF-relative
                            rebased = posixpath.normpath(posixpath.join(nav_dir, href)) if nav_dir else href
                            toc_entries.append(("".join(a.itertext()), rebased))
                except Exception:  # noqa: BLE001
                    toc_entries = []

    return doc_names, htmls, toc_entries


def _chapters_from_zip(path: Path) -> list[dict]:
    """Chapter extraction straight from the EPUB zip, for the files ebooklib
    mangles or refuses outright (observed live: an EPUB3 nav without an <ol>
    crashes read_epub; another file's empty nav shadows a perfectly good NCX).

    Parses container.xml → OPF → spine order + word counts per document, then
    takes TOC entries from the NCX (navPoints) or, failing that, the EPUB3 nav
    document's toc list. Same fraction math as the ebooklib path.
    """
    try:
        inputs = _zip_chapter_inputs(path)
        if inputs is None:
            return []
        doc_names, htmls, toc_entries = inputs
        counts = [count_words_text(_html_to_text(h)) for h in htmls]
        return _build_chapter_map(doc_names, counts, toc_entries)
    except Exception as e:  # noqa: BLE001 — no chapters is always a valid outcome
        logger.info("zip-level chapter extraction failed for %s: %s", path, e)
        return []


def count_pages_fixed_layout(path: Path) -> Optional[int]:
    """Intrinsic page count for fixed-layout formats (PDF/CBZ/CBR), without
    doing any of the cover/metadata work extract_metadata does. None for
    reflowable or unreadable files."""
    fmt = get_format(path)
    try:
        if fmt == "pdf":
            import fitz
            with fitz.open(str(path)) as doc:
                return len(doc) or None
        if fmt == "cbz":
            with zipfile.ZipFile(path) as zf:
                n = sum(1 for name in zf.namelist()
                        if name.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))
                        and not name.startswith("__MACOSX"))
                return n or None
        if fmt == "cbr":
            import rarfile
            with rarfile.RarFile(str(path)) as rf:
                n = sum(1 for name in rf.namelist()
                        if name.lower().endswith((".jpg", ".jpeg", ".png", ".webp")))
                return n or None
    except Exception as e:  # noqa: BLE001
        logger.warning("page count error for %s: %s", path, e)
    return None


def epub_chapter_documents(path: Path) -> tuple[list[str], list[dict]]:
    """The chapter map of an EPUB together with the spine documents (decoded
    HTML, reading order) it was computed from: ``(htmls, chapters)``.

    ebooklib first (fast, and consistent with the rest of this module); when
    it yields nothing — or refuses the file entirely — the zip-level parser
    takes over. That second path is what rescues EPUB2 books whose TOC lives
    only in the NCX, files whose empty nav shadows a good NCX, and the
    malformed-nav EPUBs read_epub crashes on. ``chapters`` is [] (and
    ``htmls`` whatever was readable) when neither path finds a usable TOC."""
    htmls: list[str] = []
    try:
        from ebooklib import epub

        book = epub.read_epub(str(path), options={"ignore_ncx": True})
        doc_names, htmls = _ebooklib_spine_docs(book)
        chapters: list[dict] = []
        if doc_names:
            counts = [count_words_text(_html_to_text(h)) for h in htmls]
            chapters = _build_chapter_map(doc_names, counts, _flatten_ebooklib_toc(book.toc))
        if chapters:
            return htmls, chapters
    except Exception as e:  # noqa: BLE001 — the zip path gets its turn
        logger.info("ebooklib chapter extraction failed for %s: %s", path, e)
    try:
        inputs = _zip_chapter_inputs(path)
    except Exception as e:  # noqa: BLE001 — no chapters is always a valid outcome
        logger.info("zip-level chapter extraction failed for %s: %s", path, e)
        inputs = None
    if inputs is None:
        return htmls, []
    doc_names, zip_htmls, toc_entries = inputs
    counts = [count_words_text(_html_to_text(h)) for h in zip_htmls]
    return zip_htmls, _build_chapter_map(doc_names, counts, toc_entries)


def extract_chapters_epub(path: Path) -> list[dict]:
    """Open an EPUB from disk and extract its chapter map. Used by the backfill
    job and as the ingest fallback. See ``epub_chapter_documents``."""
    return epub_chapter_documents(path)[1]


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

        # Chapter map (TOC → fraction boundaries) — same open book. Private
        # key: creation sites persist it as BookChapter rows; API previews
        # strip underscore keys.
        chapters = _extract_epub_chapters(book)
        if not chapters:
            # The already-open book gave nothing — let the zip-level parser
            # have a go (NCX-only EPUB2, empty/malformed nav — see
            # extract_chapters_epub).
            chapters = _chapters_from_zip(path)
        # Always set for EPUBs (possibly []) — the empty list records "tried,
        # no usable TOC" downstream so the backfill stops re-queuing the book.
        meta["_chapters"] = chapters

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

        # Intrinsic page count — PDFs are fixed-layout, so this is a real
        # property of the book (unlike reflowable EPUB pagination).
        if len(doc) > 0:
            meta["page_count"] = len(doc)

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
                # Intrinsic page count: one image = one page. Counted images
                # are ground truth; ComicInfo's PageCount is only a fallback.
                meta["page_count"] = len(images)
            elif meta.get("_page_count"):
                meta["page_count"] = meta["_page_count"]
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
                meta["page_count"] = len(images)
            elif meta.get("_page_count"):
                meta["page_count"] = meta["_page_count"]
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
