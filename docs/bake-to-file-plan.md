# Bake Metadata to File — Implementation Plan

> Status: **PLANNED, not started.** Spun out of the wishlist PR after discovering
> that bulk ZIP downloads weren't baking metadata (since fixed — see
> `downloads.py`). This plan is the *separate*, additive "write Tome's metadata
> into the actual library file on disk" feature. The lazy bake-on-download model
> (`services/metadata_embed.py`) **stays** — this is an opt-in power tool, not a
> replacement.

Branch (when started): `feat/bake-to-file`

---

## 1. Summary

Today Tome never mutates files in the library. Metadata is embedded lazily into a
disposable cache (`data/baked/{book_id}_{updated_at}.{ext}`) at **download** time
via `get_baked_path()`, used by every download path (single, OPDS, TomeSync, and —
as of the wishlist PR — bulk ZIP). The on-disk library file stays pristine.

This feature adds an **explicit, admin-only, confirm-gated action** to write
Tome's metadata **into the source file on disk** (per-book and bulk). Useful when
the files are consumed *outside* Tome (rsync/Syncthing to another device, a
Calibre library pointed at the same folder, direct NAS browsing) — none of which
go through Tome's download endpoints and so never see the baked cache.

**It is destructive and irreversible**, so the bulk of this plan is the safety
machinery, not the embed (which already exists in `metadata_embed._embed`).

---

## 2. Why this is a guarded power tool, not the default

(Captured from the design discussion so the rationale isn't lost.)

- **Read-only libraries.** The common `-v /books:ro` deployment makes in-file
  baking impossible. The lazy cache exists precisely so read-only libraries work.
  This button must detect non-writable libraries and disable itself.
- **`content_hash` churn.** Rewriting bytes invalidates `BookFile.content_hash`,
  which drives dedup (`check-hashes`), duplicate detection, and
  `duplicate_dismissals`. The hash must be recomputed in the same transaction,
  and consumers of the old hash will drift (documented trade-off).
- **No undo.** The lazy cache is forgiving because it's disposable; an in-file
  bake damages the real source if the embed is buggy. Requires atomic write +
  validation, ideally a transient backup.
- **Doesn't replace the cache.** The instant metadata is edited again, the
  on-disk file is stale, so lazy-bake must still cover the "edited since last
  write" window. This is additive, not a simplification.

---

## 3. Reuse — what already exists

- `backend/services/metadata_embed.py`
  - `_embed(book, src: Path, fmt, cover_bytes) -> Optional[bytes]` — produces the
    baked bytes for EPUB + CBZ. Returns `None` for unsupported formats (PDF).
  - `_load_cover(book)`, `_purge_stale(book_id, keep)`, `get_baked_path(...)`.
- `BookFile.content_hash`, `BookFile.file_path`, `BookFile.format`.
- `backend/services/scanner.py` hashing helper (reuse its sha256 routine — do not
  reimplement) to recompute `content_hash`.
- `book_visibility_filter` / `is_admin` from `backend/core/permissions`.

The embed is solved. This plan is: **write it safely + recompute hash + read-only
guard + UI.**

---

## 4. Data model

### `BookFile` — one additive column
```
metadata_synced_at  DateTime nullable
```
Set to `book.updated_at` at the moment of a successful in-file bake. Meaning: "the
bytes on disk already carry the metadata as of this `updated_at`."

This column does double duty:
1. **Avoids redundant work.** `get_baked_path()` gains an early check: if
   `book_file.metadata_synced_at == book.updated_at`, the on-disk file is already
   current → return the raw path directly (skip the embed + cache entirely).
2. **Auto-invalidates on edit.** Editing metadata bumps `book.updated_at`, so
   `metadata_synced_at` no longer matches → lazy-bake transparently resumes until
   the user bakes to file again.

This adds a column to an **existing** table (`book_files`), and `create_all()`
does **not** alter live tables. Follow the established runtime pattern: add a
manual guarded `ALTER TABLE book_files ADD COLUMN metadata_synced_at DATETIME`
block to `backend/main.py`'s lifespan, next to the existing `content_type` /
`is_reviewed` / `role` blocks (check `PRAGMA table_info` first, then add if
missing). That's how every column-on-existing-table change has shipped — Alembic
(`backend/alembic/`) exists but is abandoned; don't reach for it here.

---

## 5. Service — `backend/services/metadata_embed.py`

### `bake_to_file(db, book, book_file) -> BakeResult`
Per-file in-place bake. Steps, in order:
1. **Skip if unsupported:** `_embed` returns `None` for PDF → `BakeResult.skipped("format")`.
2. **Writability check:** if the file's directory is not writable
   (`os.access(dir, os.W_OK)` is False) → raise/return `BakeResult.readonly`.
3. **Produce bytes:** `cover = _load_cover(book); out = _embed(book, src, fmt, cover)`.
4. **Validate** `out` parses as a valid EPUB (zip + container.xml present) / CBZ
   (valid zip) before touching the original. A corrupt embed must never replace a
   good source. On validation failure → `BakeResult.failed`, original untouched.
5. **Atomic replace:** write to `src.with_suffix(src.suffix + ".tmp")` in the
   **same directory** (same filesystem → atomic), `fsync`, then `os.replace(tmp, src)`.
6. **Recompute hash:** sha256 of the new file → update `book_file.content_hash`
   (reuse scanner's hashing helper).
7. **Mark synced:** `book_file.metadata_synced_at = book.updated_at`.
8. **Purge the now-redundant cache** entry for this book (`_purge_stale`).
9. Return `BakeResult.baked`.

Failures must be per-file isolated (one bad file doesn't abort a bulk run) and
must never leave a half-written `.tmp` behind.

### `get_baked_path` change
At the top: `if book_file.metadata_synced_at and book.updated_at and book_file.metadata_synced_at == book.updated_at: return Path(book_file.file_path)`
(serve raw — it's already current). Everything else unchanged.

### `library_writable() -> bool`
Helper: is `settings.library_dir` writable? Used by the health/UI gate.

---

## 6. Backend API — `backend/api/books.py` (or a small `bake.py`)

Admin only (`require_role(user, "admin")`).

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/books/{book_id}/bake` | Bake all files of one book in place. Returns per-file `BakeResult`. |
| `POST` | `/books/bake` | Body `{ "book_ids": [int] }` (cap 200, mirror `/downloads`). Bulk; returns a per-book/per-file result summary. |
| `GET`  | `/library/writable` (or extend `/health` / admin server-info) | `{ "writable": bool }` so the UI can disable the button on read-only mounts. |

Audit every bake: `audit(db, action="book.metadata_baked", resource_type="book", resource_id=..., details={file_count, results})`.

Optional kill switch: `TOME_ALLOW_INFILE_BAKE: bool = True` (env) — a hard off for
operators who never want files mutated; 403 when disabled.

### Schemas
`BakeResult` (`status: baked|skipped|readonly|failed`, `file_path`, `reason?`,
`old_hash?`, `new_hash?`), `BulkBakeRequest`, `BulkBakeResponse`.

---

## 7. Frontend

- **`BookDetailPage`** — admin action "Write metadata to file" (icon: `HardDriveDownload`
  or `FileDown`). Opens a confirm dialog that states plainly: *this rewrites the
  source file(s) on disk and recomputes their content hash; it cannot be undone.*
  Disabled with a tooltip ("Library is read-only") when `GET /library/writable`
  is false, or when the book is PDF-only.
- **Bulk** — a "Write metadata to files" item in the dashboard multi-select bulk
  menu (next to bulk download), same confirm + read-only gate.
- **Result toast** — "Baked N file(s); skipped M (read-only/unsupported)."
- `frontend/src/lib/books.ts` — `bakeBook(id)`, `bulkBake(ids)`, `getLibraryWritable()`.
- Optional later: an admin "Library Health"-style "Bake entire library" with a
  progress UI — out of scope for v1 (heavy, long-running; needs a job/queue).

---

## 8. Edge cases & interactions

- **Re-edit after bake:** `updated_at` bumps → `metadata_synced_at` stale →
  `get_baked_path` resumes lazy-baking. Correct, no action needed.
- **Folder scan after bake:** path unchanged, `content_hash` updated to the new
  file's hash → scanner reconciles it as the known file, not a new/changed one.
  **Verify** the scanner keys on path+hash and won't re-ingest or flag a dupe.
- **Duplicate detection:** two formerly byte-identical raw files baked at
  different `updated_at`s will now have different hashes → no longer flagged as
  same-hash duplicates. Acceptable; document it.
- **Concurrency:** admin-triggered and infrequent; accept last-writer-wins per
  file. Guard only against a half-written tmp (atomic replace handles it).
- **PDF-only books:** button disabled / endpoint returns `skipped("format")`.
- **Missing file on disk:** `skipped("missing")`, no hash change.

---

## 9. Tests

Backend (`tests/test_bake_to_file.py`):
- Bake an EPUB → re-extract metadata shows Tome's series/title; file still a valid
  EPUB; `content_hash` updated to match new bytes; `metadata_synced_at == updated_at`.
- `get_baked_path` returns the **raw** path when `metadata_synced_at == updated_at`,
  and re-bakes (cache path) after a metadata edit bumps `updated_at`.
- Read-only dir (chmod or monkeypatch `os.access`) → `readonly`, original bytes
  untouched, hash unchanged.
- `_embed` raising / returning corrupt bytes (monkeypatch) → `failed`, original
  untouched, no leftover `.tmp`.
- PDF-only book → `skipped("format")`.
- Bulk: mixed set (EPUB + PDF + missing) → correct per-file statuses; one failure
  doesn't abort the rest.
- Non-admin → 403; audit row written on success.

Frontend: `npm run build` clean; button disabled state honors `writable=false`.

---

## 10. Docs & DoD

- `website/.../docs/` page or section: what in-file baking does, the read-only
  caveat, the hash implication. Add to docs-nav.
- `CHANGELOG.md` `[Unreleased] / Added`.
- `CLAUDE.md` "What's Built" + Known Gotchas (note the `content_hash` churn and
  `metadata_synced_at` semantics).
- The lifespan `ALTER TABLE` block for `metadata_synced_at` (matching the
  `content_type` / `is_reviewed` / `role` pattern in `backend/main.py`).

### Definition of Done
- [ ] `BookFile.metadata_synced_at` column + lifespan ALTER block
- [ ] `bake_to_file`, `library_writable`, `get_baked_path` early-return
- [ ] `POST /books/{id}/bake`, `POST /books/bake`, `GET /library/writable`; schemas; audit
- [ ] Optional `TOME_ALLOW_INFILE_BAKE` kill switch
- [ ] Frontend: per-book + bulk action, confirm dialog, read-only/PDF disabled states, lib client
- [ ] Atomic write + validation + hash recompute + cache purge
- [ ] Backend tests green; `npm run build` clean
- [ ] Docs + CHANGELOG + CLAUDE.md updated

---

## 11. Open questions

1. **Scope of v1:** per-book + bulk-on-selection only? (Recommended.) Defer
   "bake entire library" until there's a background-job story.
2. **Backup on write:** keep a transient `.bak` until success, or trust
   validate-then-atomic-replace? (Lean: validate + atomic, no `.bak`, to avoid
   doubling disk use — but revisit if anyone reports corruption.)
3. **Kill switch default:** ship `TOME_ALLOW_INFILE_BAKE=True` (on) or `False`
   (opt-in)? Leaning **on**, since the action is already admin + confirm + read-only-gated.
