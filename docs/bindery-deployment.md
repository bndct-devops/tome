# Bindery Deployment Guide

How to deploy the Bindery feature to production.

---

## Overview

The Bindery is an inbox for incoming books. Files land in a watched directory, Tome detects them, and an admin reviews pre-filled metadata before accepting books into the library.

Both Tome and any external tools (e.g. download automation) mount the **same host directory** for the bindery. External tools write, Tome reads and triages.

---

## 1. Create the bindery directory on the host

```bash
mkdir -p /path/to/bindery/chapters
```

The `chapters/` subfolder is for chapter-level content (e.g. individual manga chapters). Volumes go directly into `bindery/{series}/`.

---

## 2. Deploy Tome

### Rebuild the image

```bash
cd /path/to/tome
docker build -t tome:latest .
```

### Update the container config

Add the bindery volume mount. Full volumes should be:

```yaml
volumes:
  - /path/to/appdata:/data              # DB + covers
  - /path/to/ebooks:/books:ro           # library (read-only)
  - /path/to/bindery:/bindery           # bindery inbox
```

Environment variables are unchanged:

```
TOME_SECRET_KEY=<your-secret>
TOME_HARDCOVER_TOKEN=<your-token>   # optional but recommended
```

### Run the database migration

After starting the new container:

```bash
docker exec -it tome alembic upgrade head
```

This renames the `can_approve_bookdrop` column to `can_approve_bindery` in the `user_permissions` table. Existing permission values are preserved.

If Alembic fails because tables already exist (common when the DB was created by `create_all()` rather than migrations), run the rename directly:

```bash
sqlite3 /path/to/appdata/tome.db "ALTER TABLE user_permissions RENAME COLUMN can_approve_bookdrop TO can_approve_bindery;"
```

### Verify

- Open Tome in the browser
- Sidebar should show "Bindery" between Stats and Libraries
- Navigate to `/bindery` -- should show an empty inbox
- Drop a test file into the bindery directory and refresh -- it should appear

---

## 3. File flow after deployment

```
External tool downloads a file
  -> staging directory
  -> /bindery/chapters/Series Name/file.cbz   (chapters)
  -> /bindery/Series Name/file.cbz            (volumes)

Manual drop
  -> copy file directly to /bindery/

Admin opens Tome -> Bindery
  -> sees pending files with auto-fetched metadata suggestions
  -> reviews, edits if needed, accepts
  -> file moves to /books/{Author or Series}/filename.ext
  -> Book record created in Tome's database
```

---

## 4. Rollback

If something goes wrong:

- **Tome**: The only DB change is the column rename. To revert: `docker exec -it tome alembic downgrade -1` or rename the column back manually.
- **Files in bindery**: Nothing is lost. Files sit in the bindery directory until explicitly accepted or rejected through the UI.

---

## Troubleshooting

**Bindery page shows no files but files are on disk:**
- Check the file extensions are supported: `.epub`, `.pdf`, `.cbz`, `.cbr`, `.mobi`
- Check permissions: the container user needs read access to `/bindery`

**Migration fails:**
- If Alembic complains about existing tables, use the direct SQLite command shown above.

**Permission denied on accept:**
- User needs admin role or `can_approve_bindery` permission (set in Admin > Users)
