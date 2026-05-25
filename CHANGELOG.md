# Changelog

All notable changes to Tome are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). This project uses
[Semantic Versioning](https://semver.org/).

## [1.0.0] — 2026-05-25 — "Codex"

First stable release. Schema, API, and plugin protocol are now stable —
breaking changes get a major-version bump.

### Security
- Closed exploitable path-traversal in the upload/ingest endpoints (file
  basename is stripped before being joined to the temp directory, with a
  defense-in-depth resolved-path assertion).
- Global libraries (owner_id IS NULL) are now admin-only for mutations; any
  authenticated user including guests could previously delete every global
  library on a default install.
- `POST /api/libraries` now requires at least the `member` role.
- OPDS download and both comic-page streaming endpoints now apply
  `user_can_see_book` — closes IDORs where any user with auth could
  download books outside their visibility scope by guessing IDs.
- TomeSync `ApiKey` is now stored as `sha256(key)` rather than plaintext.
  Existing KOReader plugin installs keep working; database leak no longer
  yields a fleet of usable credentials.
- KOSync userkey now compared with `hmac.compare_digest` (timing-safe).
- `get_comic_page` now uses the same JWT signing key resolver as the rest
  of the app; was silently broken when `TOME_SECRET_KEY` was unset and the
  auto-generated `data/secret.key` was in use.
- `bindery.reject_book` now resolves cover deletions under `covers_dir`
  rather than the server's CWD.

### Added
- Per-user backup endpoint and Settings → Backup UI. Downloads a JSON
  snapshot of reading status, sessions, sync positions, shelves, and
  client preferences.
- Persistent KOReader sync-status badge in Dashboard and Stats headers
  (dot-only on mobile, full label on desktop).
- Unified reading-streak calculation: Dashboard and Stats now agree, with
  a 4-hour rollover so late-night reading sessions count toward the
  previous day.
- Download metadata embedding: EPUB downloads get OPF `dc:*` +
  `calibre:series`; CBZ downloads get `ComicInfo.xml` + prepended cover.
  Cached at `data/baked/`; auto-invalidates on metadata update.
- API tokens: user-level `tome_*` bearer tokens accepted on every `/api/*`
  endpoint. Created/revoked in Settings; admins can view all users' tokens.
- Admin duplicate detection: 4 strategies (content hash, ISBN,
  author+series+index, fuzzy title+author >85%). Merge or dismiss.
- Library health tool: lists misplaced files, one-click reorganise.
- Keyboard shortcuts modal (`?` to open).
- Scribe: Claude Code skill for batch ingest, metadata audits, series
  annotation. Alpha — command surface may change.
- Home tab with landing-page summary endpoints.
- Arcs and SeriesMeta with admin CRUD; volumes group by arc on series page.

### Fixed
- Infinite scroll re-attaches after switching dashboard tabs.
- SQLite connection-pool exhaustion under load (switched to NullPool).
- Comic reader view settings persist; final page now reports 100%.
- Session endpoint now updates reading status as safety net — catches up
  when position PUTs fail but queued sessions flush later.
- Progress scale normalised to 0–1 everywhere (was 0–100 from web reader).
- Web reader no longer overwrites KOSync progress with 0 on initial load.
- Fixed crash when opening KOReader-synced books (XPointer vs epubcfi).
- Naive datetime timestamps now include Z suffix for correct timezone display.

## [0.2.0] — 2026-04-17

### TomeSync Series Download (Plugin v4)
- Browse series from KOReader's wrench menu — lists all series in your
  library with book counts.
- Download full series or rest-of-series from within a book.
- Downloads organised by book type: `<download_dir>/<book_type>/<series_name>/`.
- Format preference: epub → kepub.epub → cbz → pdf → mobi → azw3.
- Skips books already on the device (matched by book ID).
- Plugin self-registers in KOReader's wrench menu.

### Roles & Permissions
- Replaced 14 granular permission flags with 3 roles: Admin, Member, Guest.
- Per-user book visibility: members see their own + assigned library books;
  guests see public only.

### Bindery Auto-Import
- Automatic ingestion from incoming directory on a configurable interval
  (`TOME_AUTO_IMPORT`, `TOME_AUTO_IMPORT_INTERVAL`).
- Unreviewed book queue with accept/reject workflow.

### Stats
- New Insights tab: completion estimates, year in review, period
  comparison, reading-speed trends.
- Per-book time breakdown, monthly comparison, genre over time.
- Fixed completion estimates to use actual progress gained.

### Themes
- Overhauled theme system: 3 built-in themes (light, dark, amber) plus
  fully custom themes via 10-value hex palette stored in localStorage.

### Web Reader
- Bidirectional position sync: web reader progress syncs to KOReader and back.
- Fixed crash when opening KOReader-synced books.

### UI
- Shift-click range selection across all list views.
- Mobile PWA improvements: safe areas, touch feedback, smoother animations.
- Fixed comic reader stuck spinner.
- Renamed Saved Filters to Shelves.

### Build
- `.dockerignore` for faster Docker builds.

## [0.1.0] — 2026-04-04

First public release.

- Library management: scan folders, upload files, organise into libraries.
- Built-in reader: EPUB (CFI position tracking), manga/comics (CBZ/CBR
  with two-page spread, RTL, webtoon scroll), PDF.
- Metadata: auto-extraction from files; fetching from Hardcover, Google
  Books, and OpenLibrary with side-by-side diff UI.
- KOReader integration: TomeSync plugin for reading position and session
  sync (works offline), OPDS feed, OPDS PINs.
- Reading stats: session tracking, streaks, time-of-day patterns, heatmap.
- Bindery: inbox for incoming books with metadata preview and batch
  accept/reject.
- Series browsing with per-book progress and "continue reading".
- Multi-user: JWT auth, granular permissions, Quick Connect (6-char code
  sign-in), admin impersonation.
- 9 themes: light, dark, Catppuccin (4 flavours), Nord, Neon, 8-bit.
- Mobile-responsive PWA.
- Single Docker image (FastAPI + React + SQLite).

[1.0.0]: https://github.com/bndct-devops/tome/releases/tag/v1.0.0
[0.2.0]: https://github.com/bndct-devops/tome/releases/tag/v0.2.0
[0.1.0]: https://github.com/bndct-devops/tome/releases/tag/v0.1.0
