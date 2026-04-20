#!/usr/bin/env python3
"""
Scribe extract — walk a directory and emit JSON metadata for each ebook.

Usage:
    python3 extract.py <directory>

Output: JSON array to stdout.  One object per supported file:
{
  "path":           "/abs/path/to/file.epub",
  "format":         "epub",
  "size_bytes":     1234567,
  "content_hash":   "<sha256hex>",
  "embedded":       {"title": "...", "author": "...", ...},
  "filename_hints": {"title": "...", "author": "...", ...}
}

Supported formats: .epub, .pdf, .cbz, .cbr, .mobi
PDFs require PyMuPDF (fitz).  If not available, PDFs are still listed but
embedded metadata will be empty (title falls back to filename stem).

This script is stdlib-first.  Heavy deps (ebooklib, fitz) are optional and
loaded lazily.  It runs fine from the Tome repo's venv where those deps are
already installed.
"""

import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path
from typing import Optional

SUPPORTED_FORMATS = {".epub", ".pdf", ".cbz", ".cbr", ".mobi"}


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Filename hint parser
# ---------------------------------------------------------------------------

def parse_filename_hints(path: Path) -> dict:
    """
    Parse title / author / series / series_index / year from a filename.

    Supported patterns (all optional):
      Title - Author (Year).ext
      Series Name Vol.3 - Title - Author (Year).ext
      Series Name 003 - Title.ext
    """
    stem = path.stem
    hints: dict = {}

    # Year: (YYYY)
    year_m = re.search(r'\((\d{4})\)', stem)
    if year_m:
        yr = int(year_m.group(1))
        if 1800 <= yr <= 2100:
            hints["year"] = yr
        stem = stem[:year_m.start()].strip()

    # Volume/chapter number at end or after series name
    vol_m = re.search(
        r'^(.*?)(?:[,:\s]+)(?:Vol(?:ume)?\.?\s*(\d+(?:\.\d+)?)|v(\d+(?:\.\d+)?)|#(\d+(?:\.\d+)?))\s*$',
        stem, re.IGNORECASE
    )
    if vol_m:
        idx_str = vol_m.group(2) or vol_m.group(3) or vol_m.group(4)
        if idx_str:
            try:
                hints["series_index"] = float(idx_str)
                hints["series"] = vol_m.group(1).strip().rstrip(",-: ")
                stem = hints["series"]
            except ValueError:
                pass

    # " - " separates parts; first part = title, last part = author
    parts = [p.strip() for p in stem.split(" - ")]
    if parts:
        hints.setdefault("title", parts[0])
    if len(parts) >= 2:
        hints["author"] = parts[-1]

    return {k: v for k, v in hints.items() if v is not None}


# ---------------------------------------------------------------------------
# EPUB extraction (uses ebooklib if available, falls back to raw ZIP/OPF)
# ---------------------------------------------------------------------------

def _extract_epub_stdlib(path: Path) -> dict:
    """Fallback EPUB extraction without ebooklib — reads OPF directly."""
    import xml.etree.ElementTree as ET
    meta: dict = {}
    DC = "http://purl.org/dc/elements/1.1/"
    OPF_NS = "http://www.idpf.org/2007/opf"

    try:
        with zipfile.ZipFile(path, "r") as zf:
            # Find container.xml → OPF path
            try:
                container_xml = zf.read("META-INF/container.xml")
                container_root = ET.fromstring(container_xml)
                opf_path = None
                for el in container_root.iter():
                    if el.tag.endswith("rootfile"):
                        opf_path = el.get("full-path")
                        break
            except Exception:
                opf_path = None

            if not opf_path:
                # Guess
                for name in zf.namelist():
                    if name.endswith(".opf"):
                        opf_path = name
                        break

            if not opf_path:
                return meta

            opf_bytes = zf.read(opf_path)
            root = ET.fromstring(opf_bytes)

            def _dc(tag):
                results = root.findall(f".//{{{DC}}}{tag}")
                return results[0].text.strip() if results and results[0].text else None

            title = _dc("title")
            if title:
                meta["title"] = title

            creator = _dc("creator")
            if creator:
                meta["author"] = creator

            publisher = _dc("publisher")
            if publisher:
                meta["publisher"] = publisher

            language = _dc("language")
            if language:
                meta["language"] = language[:8]

            description = _dc("description")
            if description:
                meta["description"] = re.sub(r"<[^>]+>", "", description).strip()

            identifier = _dc("identifier")
            if identifier:
                clean = identifier.replace("-", "")
                if re.match(r"^\d{9,13}$", clean):
                    meta["isbn"] = identifier

            date = _dc("date")
            if date:
                m = re.match(r"(\d{4})", date)
                if m:
                    meta["year"] = int(m.group(1))

            # Calibre series metadata
            for meta_el in root.findall(f".//{{{OPF_NS}}}meta"):
                name = meta_el.get("name", "")
                content = meta_el.get("content", "")
                if name == "calibre:series" and content:
                    meta["series"] = content
                elif name == "calibre:series_index" and content:
                    try:
                        meta["series_index"] = float(content)
                    except ValueError:
                        pass

    except Exception as e:
        pass  # Return whatever we got

    return meta


def extract_epub(path: Path) -> dict:
    try:
        import ebooklib
        from ebooklib import epub

        meta: dict = {}
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

        series = _first(book.get_metadata("OPF", "series"))
        if series:
            meta["series"] = series[0]
        series_idx = _first(book.get_metadata("OPF", "series_index"))
        if series_idx:
            try:
                meta["series_index"] = float(series_idx[0])
            except ValueError:
                pass

        # Fallback: parse series from title
        if "series" not in meta and "title" in meta:
            vol_match = re.search(
                r'^(.*?)(?:[,:]?\s*)(?:Vol(?:ume)?\.?\s*(\d+(?:\.\d+)?))\s*$',
                meta["title"], re.IGNORECASE
            )
            if vol_match:
                series_name = vol_match.group(1).strip().rstrip(",-: ")
                if series_name:
                    meta["series"] = series_name
                    try:
                        meta["series_index"] = float(vol_match.group(2))
                    except (ValueError, TypeError):
                        pass

        return meta

    except ImportError:
        # Fall back to stdlib OPF parsing
        return _extract_epub_stdlib(path)
    except Exception:
        return _extract_epub_stdlib(path)


# ---------------------------------------------------------------------------
# PDF extraction
# ---------------------------------------------------------------------------

def extract_pdf(path: Path) -> dict:
    meta: dict = {}
    try:
        import fitz  # PyMuPDF
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
        doc.close()
    except ImportError:
        # PyMuPDF not available — title will fall back to filename
        pass
    except Exception:
        pass
    return meta


# ---------------------------------------------------------------------------
# CBZ / ComicInfo.xml extraction
# ---------------------------------------------------------------------------

def _parse_comic_info_xml(xml_bytes: bytes) -> dict:
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
        for xml_field, key in field_map.items():
            el = root.find(xml_field)
            if el is not None and el.text:
                meta[key] = el.text.strip()

        for field in ("Number", "Volume"):
            el = root.find(field)
            if el is not None and el.text:
                try:
                    meta["series_index"] = float(el.text.strip())
                    break
                except ValueError:
                    pass

        el = root.find("Year")
        if el is not None and el.text:
            try:
                meta["year"] = int(el.text.strip())
            except ValueError:
                pass
    except ET.ParseError:
        pass
    return meta


def extract_cbz(path: Path) -> dict:
    meta: dict = {}
    try:
        with zipfile.ZipFile(path, "r") as zf:
            for name in zf.namelist():
                if name.lower() == "comicinfo.xml":
                    meta.update(_parse_comic_info_xml(zf.read(name)))
                    break
    except Exception:
        pass
    return meta


# ---------------------------------------------------------------------------
# Normalisation helpers (ported from Tome's metadata.py)
# ---------------------------------------------------------------------------

def normalize_series(s: str) -> str:
    s = s.strip()
    s = re.sub(r'\s+[-\u2013]\s+.+$', '', s).strip()
    s = re.sub(r'\s+-[^-].*$', '', s).strip()
    s = s.rstrip(',-: ')
    return s


def finalize_embedded(meta: dict, path: Path) -> dict:
    """Apply fallbacks and normalisations to raw extracted metadata."""
    # Title fallback
    if not meta.get("title"):
        meta["title"] = path.stem

    # Normalize series
    if meta.get("series"):
        normalized = normalize_series(meta["series"])
        if normalized:
            meta["series"] = normalized
        else:
            del meta["series"]

    # Year fallback from filename
    if "year" not in meta:
        m = re.search(r'\((\d{4})\)', path.stem)
        if m:
            yr = int(m.group(1))
            if 1800 <= yr <= 2100:
                meta["year"] = yr

    return meta


# ---------------------------------------------------------------------------
# Main per-file extractor
# ---------------------------------------------------------------------------

def extract_file(path: Path) -> Optional[dict]:
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_FORMATS:
        return None

    fmt = suffix.lstrip(".")
    size = path.stat().st_size

    # Compute hash
    try:
        content_hash = sha256_file(path)
    except OSError as e:
        print(f"[warn] cannot hash {path}: {e}", file=sys.stderr)
        return None

    # Embedded metadata
    if fmt == "epub":
        raw = extract_epub(path)
    elif fmt == "pdf":
        raw = extract_pdf(path)
    elif fmt in ("cbz", "cbr"):
        raw = extract_cbz(path) if fmt == "cbz" else {}
    else:
        raw = {}  # mobi: no stdlib extraction; title falls back to filename

    embedded = finalize_embedded(raw, path)
    filename_hints = parse_filename_hints(path)

    # Only keep fields relevant to ingest schema
    schema_fields = {"title", "author", "series", "series_index", "isbn",
                     "publisher", "description", "language", "year"}
    embedded_out = {k: v for k, v in embedded.items() if k in schema_fields and v is not None}
    hints_out = {k: v for k, v in filename_hints.items() if k in schema_fields and v is not None}

    return {
        "path": str(path.resolve()),
        "format": fmt,
        "size_bytes": size,
        "content_hash": content_hash,
        "embedded": embedded_out,
        "filename_hints": hints_out,
    }


# ---------------------------------------------------------------------------
# Directory walker
# ---------------------------------------------------------------------------

def walk_directory(directory: Path) -> list[dict]:
    results = []
    errors = 0
    for path in sorted(directory.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in SUPPORTED_FORMATS:
            continue
        # Skip hidden files and macOS junk
        if any(part.startswith(".") or part == "__MACOSX" for part in path.parts):
            continue
        record = extract_file(path)
        if record is not None:
            results.append(record)
        else:
            errors += 1

    if errors:
        print(f"[warn] {errors} file(s) could not be processed", file=sys.stderr)
    return results


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: extract.py <directory>", file=sys.stderr)
        sys.exit(1)

    target = Path(sys.argv[1]).expanduser().resolve()
    if not target.exists():
        print(f"Error: path does not exist: {target}", file=sys.stderr)
        sys.exit(1)

    if target.is_file():
        # Single file mode
        record = extract_file(target)
        if record is None:
            print(f"Error: unsupported or unreadable file: {target}", file=sys.stderr)
            sys.exit(1)
        print(json.dumps([record], indent=2))
    else:
        records = walk_directory(target)
        print(json.dumps(records, indent=2))
