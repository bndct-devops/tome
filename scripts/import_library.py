#!/usr/bin/env python3
"""
tome-import — bulk library import script for Tome.

Walks a source directory tree of ebook files, parses metadata primarily
from filenames (more reliable than EPUB headers for large messy libraries),
then copies files into Tome's library directory and inserts them into the DB.

See --help for usage, or docs/import.md for a full walkthrough.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

# ── Filename parsing ──────────────────────────────────────────────────────────

# Matches:  Title - Author (Year).ext
#           Title - Author.ext          (year optional)
# Uses greedy title so it splits on the LAST " - " — handles titles with
# hyphens like "Gulliver -Starting Life in Another World-, Vol. 24 - Author"
FILENAME_RE = re.compile(
    r'^(?P<title>.+)\s+-\s+(?P<author>.+?)(?:\s*\((?P<year>\d{4})\))?\s*$'
)

# Volume/series patterns — applied to the title portion AFTER stripping author
# Order matters: more-specific patterns first
SERIES_PATTERNS = [
    # "Chronicles of Narnia Volume 1"  /  "Tale of Two Cities Volume 01 SubTitle"
    re.compile(r'^(?P<series>.+?)\s+Volume\s+(?P<idx>\d+(?:\.\d+)?)(?:\s+(?P<subtitle>.+))?$', re.IGNORECASE),
    # "That Time I Got Reincarnated, Vol. 06"
    re.compile(r'^(?P<series>.+?),?\s+Vol\.\s*(?P<idx>\d+(?:\.\d+)?)(?:\s+(?P<subtitle>.+))?$', re.IGNORECASE),
    # "Lord of the Rings Vol. 2"
    re.compile(r'^(?P<series>.+?)\s+Vol\.?\s*(?P<idx>\d+(?:\.\d+)?)(?:\s+(?P<subtitle>.+))?$', re.IGNORECASE),
    # "The Odyssey Book 5"  /  "Book 5 Calypso"
    re.compile(r'^(?P<series>.+?)\s+Book\s+(?P<idx>\d+(?:\.\d+)?)(?:\s+(?P<subtitle>.+))?$', re.IGNORECASE),
    # "01. Title"  (numbered prefix style)
    re.compile(r'^(?P<idx>\d+)\.\s+(?P<series>.+)$'),
]

# Author normalisation: "Last, First" → "First Last"
LAST_FIRST_RE = re.compile(r'^(?P<last>[^,]+),\s*(?P<first>.+)$')

# Parenthetical series hint at end of title: "(The Odyssey Book 7)" or "(Series, Book 3)"
PAREN_SERIES_RE = re.compile(
    r'^(?P<base>.+?)\s*\((?P<series>.+?),?\s+Book\s+(?P<idx>\d+(?:\.\d+)?)\)\s*$',
    re.IGNORECASE,
)


def normalise_author(raw: str) -> str:
    """Flip 'Last, First' to 'First Last'. Leaves other formats untouched."""
    raw = raw.strip()
    # Skip flip if it looks like multiple authors ("A, B and C" or "A and B")
    if " and " in raw.lower():
        return raw
    m = LAST_FIRST_RE.match(raw)
    if m:
        return f"{m.group('first').strip()} {m.group('last').strip()}"
    return raw


def parse_filename(stem: str) -> dict:
    """
    Parse a filename stem into title, author, year, and optionally
    series + series_index.

    Returns a dict with whatever fields could be extracted.
    """
    result: dict = {}

    m = FILENAME_RE.match(stem)
    if m:
        raw_title = m.group("title").strip()
        raw_author = m.group("author").strip()
        year_str = m.group("year")

        # Strip non-4-digit parenthetical suffixes from author (e.g. "(101)")
        # These appear when a library tool embeds non-year numbers in the filename.
        if not year_str:
            raw_author = re.sub(r'\s*\(\d+\)\s*$', '', raw_author).strip()

        result["author"] = normalise_author(raw_author)
        if year_str:
            result["year"] = int(year_str)

        # Check for parenthetical series hint first: "Title (Series Book N)"
        paren_match = PAREN_SERIES_RE.match(raw_title)
        if paren_match:
            result["title"] = paren_match.group("base").strip()
            result["series"] = paren_match.group("series").strip()
            try:
                result["series_index"] = float(paren_match.group("idx"))
            except ValueError:
                pass
            return result

        # Try to extract series + index from the title portion
        series_match = None
        for pattern in SERIES_PATTERNS:
            series_match = pattern.match(raw_title)
            if series_match:
                break

        if series_match:
            groups = series_match.groupdict()
            series_name = groups.get("series", "").strip()
            idx_str = groups.get("idx", "")
            subtitle = (groups.get("subtitle") or "").strip() or None
            if series_name:
                result["series"] = series_name
            if idx_str:
                try:
                    result["series_index"] = float(idx_str)
                except ValueError:
                    pass
            if subtitle:
                result["subtitle"] = subtitle
            # Title = series name (without volume info)
            result["title"] = series_name if series_name else raw_title
        else:
            # Check for "Title: Subtitle" split on non-series books
            colon_match = re.match(r'^(?P<title>[^:]+):\s+(?P<subtitle>.+)$', raw_title)
            if colon_match:
                result["title"] = colon_match.group("title").strip()
                result["subtitle"] = colon_match.group("subtitle").strip()
            else:
                result["title"] = raw_title
    else:
        # Couldn't parse Author — use whole stem as title
        result["title"] = stem

    return result


# ── File utilities ────────────────────────────────────────────────────────────

SUPPORTED_EXTENSIONS = {".epub", ".pdf", ".cbz", ".cbr", ".mobi"}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def collect_files(source_dir: Path) -> list[Path]:
    files = []
    for ext in SUPPORTED_EXTENSIONS:
        files.extend(source_dir.rglob(f"*{ext}"))
    return sorted(files)


def safe_dest(library_dir: Path, author: str, filename: str, series: str | None = None) -> Path:
    """
    Build a destination path:
      - Books with a series  → library_dir / Series / filename
      - Books without series → library_dir / Author / filename

    Light novels and manga almost always have a series, so they group under
    the series name. Western standalone books group under the author name.
    """
    if series:
        folder = re.sub(r'[<>:"/\\|?*]', "_", series).strip()
    else:
        folder = re.sub(r'[<>:"/\\|?*]', "_", author or "Unknown").strip()
    dest = library_dir / folder / filename
    # Resolve conflicts
    if dest.exists():
        stem = dest.stem
        suffix = dest.suffix
        i = 1
        while dest.exists():
            dest = dest.parent / f"{stem} ({i}){suffix}"
            i += 1
    return dest


# ── Database helpers ─────────────────────────────────────────────────────────

def get_db(db_path: Path):
    """Return a SQLAlchemy session without importing Tome's full app stack."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    Session = sessionmaker(bind=engine)
    return Session()


def hash_already_in_db(db, content_hash: str) -> bool:
    from backend.models.book import Book, BookFile
    return (
        db.query(BookFile).filter(BookFile.content_hash == content_hash).first() is not None
        or db.query(Book).filter(Book.content_hash == content_hash).first() is not None
    )


def insert_book(db, meta: dict, file_path: Path, content_hash: str, fmt: str) -> int:
    """Insert Book + BookFile row. Returns the new book ID."""
    from backend.models.book import Book, BookFile
    book = Book(
        title=meta.get("title", file_path.stem),
        subtitle=meta.get("subtitle"),
        author=meta.get("author"),
        series=meta.get("series"),
        series_index=meta.get("series_index"),
        year=meta.get("year"),
        cover_path=meta.get("cover_path"),
        content_hash=content_hash,
        status="active",
    )
    db.add(book)
    db.flush()
    db.add(BookFile(
        book_id=book.id,
        file_path=str(file_path.resolve()),
        format=fmt,
        file_size=file_path.stat().st_size,
        content_hash=content_hash,
    ))
    return book.id


def extract_cover(file_path: Path, covers_dir: Path, content_hash: str) -> str | None:
    """Extract cover from epub/pdf and save it. Returns relative filename or None."""
    try:
        from backend.services.metadata import extract_metadata
        meta = extract_metadata(file_path, covers_dir)
        return meta.get("cover_path")
    except Exception:
        return None


# ── Result tracking ───────────────────────────────────────────────────────────

@dataclass
class ImportResult:
    added: int = 0
    skipped_duplicate: int = 0
    skipped_unsupported: int = 0
    errors: int = 0
    error_details: list[str] = field(default_factory=list)
    rows: list[dict] = field(default_factory=list)  # for --dry-run preview


# ── Core import logic ─────────────────────────────────────────────────────────

def process_file(
    src: Path,
    library_dir: Path,
    covers_dir: Path | None,
    db,
    result: ImportResult,
    *,
    dry_run: bool,
    copy: bool,
    no_cover: bool,
) -> None:
    ext = src.suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        result.skipped_unsupported += 1
        return

    fmt = ext.lstrip(".")
    meta = parse_filename(src.stem)

    # Compute hash to detect duplicates
    try:
        content_hash = sha256_file(src)
    except OSError as e:
        result.errors += 1
        result.error_details.append(f"{src.name}: cannot read — {e}")
        return

    if dry_run:
        result.rows.append({
            "file": src.name,
            "title": meta.get("title", src.stem),
            "subtitle": meta.get("subtitle", ""),
            "author": meta.get("author", "—"),
            "series": meta.get("series", ""),
            "idx": meta.get("series_index", ""),
            "year": meta.get("year", ""),
            "format": fmt,
        })
        result.added += 1
        return

    # Duplicate check
    if hash_already_in_db(db, content_hash):
        result.skipped_duplicate += 1
        return

    # Determine destination
    author = meta.get("author", "Unknown")
    dest = safe_dest(library_dir, author, src.name, series=meta.get("series"))
    dest.parent.mkdir(parents=True, exist_ok=True)

    try:
        if copy:
            shutil.copy2(str(src), str(dest))
        else:
            shutil.move(str(src), str(dest))
    except OSError as e:
        result.errors += 1
        result.error_details.append(f"{src.name}: file operation failed — {e}")
        return

    # Extract cover from the now-moved/copied file
    if not no_cover and covers_dir:
        cover_path = extract_cover(dest, covers_dir, content_hash)
        if cover_path:
            meta["cover_path"] = cover_path

    try:
        insert_book(db, meta, dest, content_hash, fmt)
        result.added += 1
    except Exception as e:
        result.errors += 1
        result.error_details.append(f"{src.name}: DB insert failed — {e}")


# ── Output helpers ────────────────────────────────────────────────────────────

def col(text: str, width: int) -> str:
    text = str(text)
    return text[:width].ljust(width)


def print_table(rows: list[dict]) -> None:
    if not rows:
        return
    W = {"file": 40, "title": 35, "author": 25, "series": 25, "idx": 5, "year": 6, "format": 6}
    header = (
        col("File", W["file"]) + col("Title", W["title"]) +
        col("Author", W["author"]) + col("Series", W["series"]) +
        col("Idx", W["idx"]) + col("Year", W["year"]) + col("Fmt", W["format"])
    )
    sep = "─" * len(header)
    print(sep)
    print(header)
    print(sep)
    for r in rows:
        print(
            col(r["file"], W["file"]) + col(r["title"], W["title"]) +
            col(r["author"], W["author"]) + col(r["series"], W["series"]) +
            col(r.get("idx", ""), W["idx"]) + col(r.get("year", ""), W["year"]) +
            col(r["format"], W["format"])
        )
    print(sep)


# ── CLI ───────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="tome-import",
        description=(
            "Bulk import an existing ebook library into Tome.\n\n"
            "Parses metadata primarily from filenames (format: 'Title - Author (Year).ext'),\n"
            "normalises author names, detects series/volume, deduplicates by content hash,\n"
            "and copies/moves files into Tome's library directory.\n\n"
            "Always run with --dry-run first to preview what will be imported."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  # Preview what would be imported (no writes):
  python scripts/import_library.py --source /mnt/ebooks --dry-run

  # Import with file copy (keeps originals):
  python scripts/import_library.py --source /mnt/ebooks --copy

  # Import with file move (originals removed after import):
  python scripts/import_library.py --source /mnt/ebooks

  # Custom paths (e.g. running outside the Tome project directory):
  python scripts/import_library.py \\
    --source /mnt/ebooks \\
    --library-dir /data/tome/library \\
    --db-path /data/tome/data/tome.db \\
    --covers-dir /data/tome/data/covers

  # Skip cover extraction (faster, saves CPU):
  python scripts/import_library.py --source /mnt/ebooks --no-cover --dry-run
""",
    )
    p.add_argument(
        "--source", "-s",
        required=True,
        metavar="DIR",
        help="Root directory of the library to import from.",
    )
    p.add_argument(
        "--library-dir",
        metavar="DIR",
        default=None,
        help="Tome library directory (default: ./library, or TOME_LIBRARY_DIR env var).",
    )
    p.add_argument(
        "--db-path",
        metavar="FILE",
        default=None,
        help="Path to Tome's SQLite database (default: ./data/tome.db, or TOME_DATA_DIR env var).",
    )
    p.add_argument(
        "--covers-dir",
        metavar="DIR",
        default=None,
        help="Directory to store extracted covers (default: ./data/covers).",
    )
    p.add_argument(
        "--dry-run", "-n",
        action="store_true",
        help="Parse and preview what would be imported. No files are copied and nothing is written to the DB.",
    )
    p.add_argument(
        "--copy", "-c",
        action="store_true",
        help="Copy files instead of moving them. Originals are preserved. "
             "Without this flag, files are moved (originals removed).",
    )
    p.add_argument(
        "--no-cover",
        action="store_true",
        help="Skip cover image extraction. Faster, but books will have no covers until you run a metadata refresh in the UI.",
    )
    p.add_argument(
        "--batch-size",
        type=int,
        default=50,
        metavar="N",
        help="Commit to DB every N files (default: 50). Reduces memory use for very large libraries.",
    )
    p.add_argument(
        "--ssh-key",
        metavar="FILE",
        default=None,
        help="Path to SSH private key for remote --source (e.g. ~/.ssh/id_ed25519).",
    )
    p.add_argument(
        "--ssh-port",
        metavar="PORT",
        default=22,
        type=int,
        help="SSH port for remote --source (default: 22).",
    )
    p.add_argument(
        "--keep-temp",
        action="store_true",
        help="Keep the temporary rsync download directory after import (useful for debugging).",
    )
    return p


# ── SSH / rsync ───────────────────────────────────────────────────────────────

SSH_SOURCE_RE = re.compile(r'^(?P<userhost>[^/]+):(?P<path>/.+)$')


def is_ssh_source(source: str) -> bool:
    return bool(SSH_SOURCE_RE.match(source))


def rsync_from_ssh(source: str, ssh_key: str | None, ssh_port: int) -> Path:
    """
    rsync a remote path to a local temp directory and return the temp path.
    source format: user@host:/remote/path
    """
    import subprocess
    import tempfile

    m = SSH_SOURCE_RE.match(source)
    if not m:
        raise ValueError(f"Invalid SSH source format: {source!r}  Expected user@host:/path")

    userhost = m.group("userhost")
    remote_path = m.group("path").rstrip("/") + "/"

    tmp_dir = Path(tempfile.mkdtemp(prefix="tome-import-"))
    print(f"  temp dir    : {tmp_dir}")
    print()

    ssh_opts = ["-o", "StrictHostKeyChecking=no", "-p", str(ssh_port)]
    if ssh_key:
        ssh_opts += ["-i", str(Path(ssh_key).expanduser())]

    ssh_cmd = "ssh " + " ".join(ssh_opts)
    cmd = [
        "rsync",
        "-av", "--progress",
        "--include=*/",
        "--include=*.epub", "--include=*.pdf",
        "--include=*.cbz", "--include=*.cbr", "--include=*.mobi",
        "--exclude=*",
        "-e", ssh_cmd,
        f"{userhost}:{remote_path}",
        str(tmp_dir) + "/",
    ]

    print(f"Fetching files from {userhost}:{remote_path} via rsync…")
    print(f"Command: {' '.join(cmd)}\n")
    result = subprocess.run(cmd)
    if result.returncode not in (0, 24):  # 24 = partial transfer (vanished files, ok)
        print(f"\nERROR: rsync exited with code {result.returncode}", file=sys.stderr)
        sys.exit(1)

    return tmp_dir


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    # Resolve Tome paths — CLI args > env vars > defaults
    from backend.core.config import settings
    library_dir = Path(args.library_dir).expanduser().resolve() if args.library_dir else settings.library_dir.resolve()
    db_path = Path(args.db_path).expanduser().resolve() if args.db_path else settings.db_path.resolve()
    covers_dir = Path(args.covers_dir).expanduser().resolve() if args.covers_dir else settings.covers_dir.resolve()

    # SSH source: rsync to temp dir first
    tmp_dir: Path | None = None
    raw_source = args.source
    if is_ssh_source(raw_source):
        print(f"tome-import  (SSH mode)")
        print(f"  remote      : {raw_source}")
        print(f"  library-dir : {library_dir}")
        print(f"  db          : {db_path}")
        print(f"  mode        : {'DRY RUN (no writes)' if args.dry_run else ('copy' if args.copy else 'move')}")
        if not args.dry_run:
            print(f"  covers      : {'skipped' if args.no_cover else 'extracted'}")
        print()
        if args.dry_run:
            print("Note: --dry-run with SSH still downloads files to parse them.\n")
        tmp_dir = rsync_from_ssh(raw_source, args.ssh_key, args.ssh_port)
        source_dir = tmp_dir
    else:
        source_dir = Path(raw_source).expanduser().resolve()
        if not source_dir.exists():
            print(f"ERROR: source directory does not exist: {source_dir}", file=sys.stderr)
            sys.exit(1)
        print(f"tome-import")
        print(f"  source      : {source_dir}")
        print(f"  library-dir : {library_dir}")
        print(f"  db          : {db_path}")
        print(f"  covers      : {covers_dir}")
        print(f"  mode        : {'DRY RUN (no writes)' if args.dry_run else ('copy' if args.copy else 'move')}")
        print(f"  covers      : {'skipped' if args.no_cover else 'extracted'}")
        print()
        if not args.dry_run and not db_path.exists():
            print("WARNING: DB not found. Make sure Tome has been started at least once to initialise the DB.", file=sys.stderr)
            sys.exit(1)


    # Collect files
    print("Scanning source directory…")
    files = collect_files(source_dir)
    print(f"Found {len(files)} supported file(s).\n")
    if not files:
        print("Nothing to import.")
        sys.exit(0)

    if not args.dry_run:
        library_dir.mkdir(parents=True, exist_ok=True)
        covers_dir.mkdir(parents=True, exist_ok=True)

    db = None if args.dry_run else get_db(db_path)
    result = ImportResult()
    batch_count = 0

    for i, src in enumerate(files, 1):
        rel = src.relative_to(source_dir) if source_dir in src.parents else src.name
        if not args.dry_run:
            # Progress line — overwrite in place
            print(f"\r[{i:>{len(str(len(files)))}}/{len(files)}] {str(rel)[:72]:<72}", end="", flush=True)

        process_file(
            src, library_dir, covers_dir, db, result,
            dry_run=args.dry_run,
            copy=args.copy,
            no_cover=args.no_cover,
        )

        if db and not args.dry_run:
            batch_count += 1
            if batch_count >= args.batch_size:
                db.commit()
                batch_count = 0

    if db:
        db.commit()
        db.close()
        print()  # newline after progress line

    # Clean up SSH temp dir unless --keep-temp was passed
    if tmp_dir and not args.keep_temp:
        import shutil as _shutil
        _shutil.rmtree(tmp_dir, ignore_errors=True)

    # ── Output ──────────────────────────────────────────────────────────────
    if args.dry_run:
        print(f"DRY RUN — {result.added} file(s) would be imported:\n")
        print_table(result.rows)
    else:
        print()

    print(f"\nSummary")
    print(f"  {'Would import' if args.dry_run else 'Imported'}   : {result.added}")
    if not args.dry_run:
        print(f"  Duplicates  : {result.skipped_duplicate}  (already in DB, skipped)")
    print(f"  Unsupported : {result.skipped_unsupported}")
    print(f"  Errors      : {result.errors}")

    if result.error_details:
        print("\nErrors:")
        for msg in result.error_details:
            print(f"  ✗ {msg}")

    if args.dry_run:
        print("\nRun without --dry-run to perform the import.")
    else:
        print("\nDone. Go to Admin → Scanner in Tome to verify.")

    sys.exit(1 if result.errors and not result.added else 0)


if __name__ == "__main__":
    main()
