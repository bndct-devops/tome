# Importing a Library into Tome

`scripts/import_library.py` is a one-time bulk import tool for migrating an
existing ebook collection into Tome. It is designed for large libraries where
automated metadata fetching would be slow and unreliable — it parses metadata
**from filenames** instead, which is far more consistent for collections that
follow a naming convention like:

```
Title - Author (Year).epub
Series Title Volume 3 - Author (Year).epub
```

---

## Prerequisites

- Tome must have been started **at least once** so the database is initialised.
- Run the script from the **root of the Tome project directory**.
- The virtual environment must be active:

```bash
source .venv/bin/activate
```

---

## Quick start

**Always preview first with `--dry-run`:**

```bash
python scripts/import_library.py --source /path/to/your/books --dry-run
```

This prints a table of every file that would be imported with the parsed
title, author, series, and volume number. No files are moved and nothing is
written to the database.

Once you are happy with the preview, run the import:

```bash
# Copy files (originals are preserved — recommended for first import):
python scripts/import_library.py --source /path/to/your/books --copy

# Move files (originals are removed after successful import):
python scripts/import_library.py --source /path/to/your/books
```

---

## All options

| Flag | Default | Description |
|------|---------|-------------|
| `--source DIR` | *(required)* | Root directory of the library to import from. Subdirectories are walked recursively. |
| `--dry-run` / `-n` | off | Preview mode. Parses filenames and prints a table. No files are touched and the DB is not modified. |
| `--copy` / `-c` | off | Copy files instead of moving them. Originals are preserved. Without this flag files are **moved**. |
| `--no-cover` | off | Skip cover extraction. Faster, but books will show no cover until you run a metadata refresh in the UI. |
| `--library-dir DIR` | `./library` | Override Tome's library directory. Falls back to `TOME_LIBRARY_DIR` env var. |
| `--db-path FILE` | `./data/tome.db` | Override the SQLite DB path. Falls back to `TOME_DATA_DIR` env var. |
| `--covers-dir DIR` | `./data/covers` | Override the covers directory. |
| `--batch-size N` | `50` | Commit to the DB every N files. Reduce for very large libraries if you hit memory limits. |

---

## Filename format

The parser expects filenames in this format:

```
Title - Author (Year).ext
```

Examples that parse correctly:

| Filename | Title | Author | Series | Vol |
|----------|-------|--------|--------|-----|
| `The Chronicles of Narnia Volume 1 - C.S. Lewis (1950).epub` | The Chronicles of Narnia | C.S. Lewis | The Chronicles of Narnia | 1 |
| `The Lord of the Rings, Vol. 02 - J.R.R. Tolkien (1954).epub` | The Lord of the Rings | J.R.R. Tolkien | The Lord of the Rings | 2 |
| `A Tale of Two Cities Volume 01 - Charles Dickens (1859).epub` | A Tale of Two Cities | Charles Dickens | A Tale of Two Cities | 1 |
| `The Odyssey Book 5 Calypso - Homer (1900).epub` | The Odyssey | Homer | The Odyssey | 5 |
| `On the Origin of Species (1859).epub` | On the Origin of Species (1859) | — | — | — |

**Author name normalisation:** `Last, First` is automatically flipped to
`First Last`. For example `Tolkien, J.R.R.` → `J.R.R. Tolkien`.

**Series detection** recognises these patterns in the title:
- `Series Volume N`
- `Series, Vol. N` / `Series Vol. N`
- `Series Book N`
- `NN. Title` (numbered prefix style)

---

## Running in Docker

If Tome is running in a Docker container, run the script **inside the container**:

```bash
# Open a shell in the Tome container
docker exec -it tome bash

# Run the import (use the path as mounted inside the container)
cd /app
python scripts/import_library.py \
  --source /path/to/source/ebooks \
  --dry-run
```

Or, if your ebook directory is already mounted as the library volume
(`/books`), just point Tome's scanner at it via **Admin > Scanner** in the UI
instead -- no script needed.

---

## After import

1. Open Tome at `http://localhost:5173`
2. Go to **Admin → Scanner** and run a scan to verify everything was picked up
3. Use the dashboard's **bulk select** to assign book types (Light Novel, Manga, etc.) to groups of books
4. Use **bulk fetch metadata** to fill in missing descriptions and covers
5. For author name inconsistencies that slipped through, use **bulk metadata edit** to normalise them

---

## Troubleshooting

**`ERROR: database not found`**
Tome must be started at least once before importing so it can create the DB and tables.

**Books imported but no covers showing**
Run without `--no-cover`, or use **Admin → Scanner** → scan again after import.
Alternatively, select all books in the dashboard and use **Bulk Fetch Metadata**.

**Same book imported twice**
The script deduplicates by SHA-256 content hash. If the same file appears
twice under different names, the second copy is skipped automatically.

**Author names still wrong after import**
Use the bulk metadata editor in the dashboard: select affected books → Edit → set Author.
