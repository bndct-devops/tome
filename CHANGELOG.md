# Changelog

All notable changes to Tome are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). This project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- **Per-series reading stats** on the series detail page. A collapsible "Reading
  Stats" card now appears between the series header and the volume grid for any
  series you have at least one session on: total time read across all volumes, a
  per-volume bar chart (unread volumes show as faint empty bars so gaps are
  visible), completion count and percentage, session count, pages turned, average
  time per volume, an estimated time remaining (based on finished-volume average),
  longest volume, and first/last read dates. Admins additionally see a
  library-wide aggregate line — total time, sessions, and distinct reader count
  across all users. Served by the new `GET /api/series/{name}/reading-stats`
  endpoint backed by the extended `backend/services/reading_stats.py`. The
  `StatTile` component is now shared from `frontend/src/components/stats/StatTile.tsx`.
- **Per-book reading stats** on the book detail page. A collapsible "Reading
  Stats" card now appears below the reading-status buttons for any book you
  have at least one session on: total time read, session count, pages turned,
  average session length, reading pace (pages/min), first and last read dates,
  an estimated time remaining (shown while the book is in "reading" status),
  and a compact daily activity bar chart. Admins additionally see a
  library-wide aggregate — total time, sessions, and distinct reader count
  across all users — in a small sub-section at the bottom of the card.
  Served by the new `GET /api/books/{book_id}/reading-stats` endpoint backed by
  the reusable `backend/services/reading_stats.py` aggregation helper.
- Highlight & note sync for KOReader, **bidirectional across devices**. Highlights
  and notes you make on one e-reader sync through Tome to your other KOReader
  devices (pulled when you open a book; pushed on suspend, on close, via **Sync
  now**, or the **TomeSync: Sync highlights** gesture), and show up in a
  **Highlights & Notes** section on the book's detail page — highlighted text,
  note, and chapter. The highlight's position is its identity (same passage on two
  devices = one highlight); edits resolve last-write-wins and deletes propagate via
  tombstones (a removed highlight won't reappear). Device-to-device needs the
  Tome-served copy of the book on both. Rendering highlights inside the web reader
  is a separate, later step. Plugin build 12. (TomeSync)
- `TOME_GOOGLE_BOOKS_KEY`: optionally supply your own Google Books API key.
  Without it, Google Books is queried anonymously against a shared global quota
  that is exhausted almost immediately, making the fallback silently return zero
  results — felt most acutely for non-English (e.g. traditional Chinese)
  catalogues that depend on Google for coverage. With a key set, requests are
  charged against your own Cloud project quota instead. Public-volume search
  only — no OAuth, no access to user data. A configured key that hits its quota
  now logs a clear warning instead of failing silently. (#10)

### Changed
- Book detail page layout: genres moved into the left sidebar (below the
  cover), book metadata consolidated into a collapsible "Details" grid below
  the description, and the description itself is now truncated with a "Show
  more" toggle. The reading-stats card and Highlights & Notes section sit in
  the same right column, giving the page a cleaner two-column structure.
- The home "Pick up where you left off" panel is now a compact cover strip
  instead of a full-width grid.

### Fixed
- Finishing a book on KOReader is now permanent. Previously, opening a finished
  book again (even briefly) could push a lower position percentage and silently
  un-finish it — dropping the status back to "reading" and erasing the 100%
  mark. Completion is now sticky: once a book reaches "read", any later position
  update from the device leaves the status and progress untouched. The device's
  resume position (used to reopen the book at the right place) still updates as
  normal, so returning to a finished book still opens at the last page. The same
  fix applies to reading sessions flushed from the plugin's offline queue.
  Finishing always normalizes progress to exactly 100%.
- Web reading progress now syncs to KOReader at the correct scale. Progress
  fractions are 0–1 throughout the stack, but the web reader was mistakenly
  dividing an already-fractional value by 100 before writing to the sync
  position table — so marking a book finished on the web synced as ~1% to
  KOReader, and mid-read positions appeared near the start of the book.
  Web and device positions now match.
- TomeSync (KOReader) sync silently failing on HTTPS deployments behind a
  reverse proxy (also released as the 1.2.1 hotfix). The plugin baked its server
  URL from the scheme the app server saw, which is `http` when TLS is terminated
  upstream — and if the proxy then redirected HTTP→HTTPS, KOReader could not
  follow the 307 on POST/PUT, so every reading session and position update failed
  (sessions piled up as "pending" and nothing reached the library). The server
  now honours `X-Forwarded-Proto` when baking the plugin's `SERVER_URL`, and a
  new optional `TOME_PUBLIC_URL` setting pins the canonical public origin
  explicitly. The plugin build was bumped so existing installs re-bake the
  corrected URL on **TomeSync → Check for updates**. Plain HTTP, LAN, and
  localhost deployments are unaffected.
- OPDS feeds are now served with the standard default Atom namespace
  (`<feed xmlns="http://www.w3.org/2005/Atom">`) instead of prefixed
  `ns0:` elements, so strict OPDS clients such as KOReader parse them and the
  catalog is no longer empty. The feed builder shared a process-global XML
  namespace map with the download metadata embedder; whichever module was
  imported last claimed the default prefix and silently broke the other.
  Namespace prefixes are now re-asserted, under a lock, at serialization time so
  they no longer depend on import order. The Content-Type was already
  `application/atom+xml`. (#15)
- Adding or editing a book type in admin settings no longer fails with a 422
  error. The create/edit form never sends a `slug` (it is derived from the
  label), but the API required one, so every save was rejected before it
  reached the handler. The slug is now optional and auto-derived from the label
  on create. (#12)
- Dismissing the home "Pick up where you left off" panel now persists across
  refreshes (it previously reappeared every reload). It resurfaces only if a
  different set of books surfaces.
- Series metadata embedded by Calibre (`calibre:series`) and EPUB3 collections
  (`belongs-to-collection`) is now read correctly on import. It was previously
  dropped — ingest fell back to parsing the title, which mis-grouped or failed
  to group same-series books whose titles lacked a "Vol. N".
- Genre/category tags embedded in EPUBs (`dc:subject` — what Calibre stores as
  "tags") are now imported as book tags. They were previously read only from CBZ
  `ComicInfo.xml`, so EPUB tags were silently dropped and shelf filters showed
  none. Tags also now round-trip back out on download — embedded as `dc:subject`
  in EPUBs and `<Genre>` in CBZ `ComicInfo.xml`. Applies to newly imported books;
  existing books are not retroactively re-tagged.

## [1.2.0] — 2026-06-02 — "Press"

### Added
- TomeSync self-update: the KOReader plugin can now update itself from the
  server — **TomeSync → Check for updates** (manual) plus an opt-in
  **Auto-check on launch** toggle — replacing the SSH-into-every-device
  workflow. A bad update cannot brick: the plugin is split into a frozen
  stable shim (`main.lua`) and a replaceable implementation (`main_impl.lua`),
  and the shim runs an anti-brick rollback state machine — a syntax-broken
  update rolls back on the same boot, an init-crashing update rolls back on the
  next, and a corrupt download is rejected before it is ever swapped in.
  Reading progress, book mappings, and pending sessions live in KOReader
  settings, so updates never touch them.
- TomeSync gesture actions: **TomeSync: Open menu** (pops the full context-aware
  TomeSync menu) and **TomeSync: Browse series** (jumps straight to the series
  browser). Bindable from KOReader's Gesture manager, available in both the
  reader and the file manager.
- One-command installer (`install.sh`): a `curl | bash` path for newcomers and
  evals. Checks Docker is installed and running, writes `~/Tome` with a compose
  file and volumes, auto-picks a free port, pulls + starts, and waits until Tome
  answers. Re-running reuses the existing port and updates in place. ASCII-only,
  no `sudo`, never touches a real library. Docker Compose remains the primary,
  homelab-first install path; the one-liner is positioned as a "just want to try
  it?" option with a complete teardown note.

### Changed
- TomeSync plugin versioning: a hidden monotonic **build** integer (now `10`)
  drives update comparisons, with an independent human-facing **semver**
  (`1.0.1`). `GET /plugin/version` now returns `build` and `semver` alongside
  the existing `version` field (kept as `str(build)` for back-compat). New
  authenticated `GET /plugin/main-impl.lua` serves the config-baked
  implementation for self-update. The first shim+impl build must be installed
  manually once (the last SSH deploy); every update after is in-app.

### Fixed
- TomeSync series browser crashed (`attempt to concatenate field 'author'`)
  when a series' first book had no author. The server emitted JSON `null`,
  which rapidjson decodes to a truthy userdata sentinel, so the plugin's guard
  passed and then failed concatenating it. The server now omits the author when
  absent, and the plugin type-checks the field; the same hardening covers a
  null `series_index` in the series-download paths. Plugin build `10`.

## [1.1.0] — 2026-05-31 — "Vellum"

### Added
- Send to device: email books to a Kindle, Kobo, or any address straight
  from the web UI — single or bulk (max 25 per send). Per-user device list
  in Settings, admin Email tab (SMTP status, test email, all devices, send
  history). SMTP configured via `TOME_SMTP_*` env vars; 25 MB attachment
  limit; 50/user/day rate limit (`TOME_SMTP_DAILY_LIMIT`). Members and
  admins only.
- API token scopes: tokens can now be created with `"full"` (default) or
  `"readonly"` scope. Read-only tokens are blocked from non-GET requests.
  Existing tokens default to full access. Settings UI shows a scope dropdown
  on creation and a badge on read-only tokens.
- Opt-in parallel library scans via `TOME_SCAN_WORKERS` (default `1` = serial,
  in-process). Set it higher (e.g. CPU core count) to fan the CPU-bound
  extract/hash phase across worker processes for large imports; database writes
  stay single-process (SQLite single-writer). ~60–80 MB per worker.
- Wishlist: members and admins can wish for a book or a whole series. The add
  dialog has two modes — **Book** (structured search across Hardcover, Google
  Books, and OpenLibrary, plus a free-text fallback) and **Series** (Hardcover's
  series catalogue, so a series is disambiguated by author and carries its true
  volume count). Admins get a Wishlist tab; a matcher links wishes to library
  books both when a book is added (forward) and when a wish is created against
  books already present (reverse), author-aware so same-named series don't
  cross-match. Single-book wishes are fulfilled by linking a book; whole-series
  wishes are standing wants that stay open, show an "X of N" coverage strip
  (present volumes vs. the series total), notify the requester as each volume
  arrives, and close via "mark complete". In-app notifications via a new top-bar
  bell, plus email on fulfilment when SMTP is configured. The Series tab requires
  a Hardcover token (`hardcover_token`) — without it the dialog falls back to Book
  search only. Toggles: `TOME_WISHLIST_ENABLED`, `TOME_WISHLIST_MAX`.

### Changed
- Website: added raster favicons (`.ico` + `.png`) for Google search results
  and Cloudflare Web Analytics tracking snippet.
- Performance: faster library scans — removed per-book ORM lazy-loads, one
  directory walk instead of one-per-format, and throughput-oriented SQLite
  pragmas (`synchronous=NORMAL`, larger page cache, mmap). The book-list
  endpoint (`GET /api/books`) is also much faster — relationships are
  eager-loaded, eliminating an N+1 of ~3 queries per row.

### Fixed
- Full-text search now indexes books inline during scan / upload / ingest, so
  newly added books are searchable immediately — previously the index was only
  rebuilt at startup, leaving them invisible to search until the next restart.
- Cover-bearing files are no longer hashed twice during ingest.
- Bulk ZIP download now embeds Tome metadata like every other download path
  (single, OPDS, TomeSync) — previously it zipped the raw library files, so
  downloaded books carried stale/original metadata instead of Tome's. Library
  files on disk are still left untouched.

---

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

[1.2.0]: https://github.com/bndct-devops/tome/releases/tag/v1.2.0
[1.1.0]: https://github.com/bndct-devops/tome/releases/tag/v1.1.0
[1.0.0]: https://github.com/bndct-devops/tome/releases/tag/v1.0.0
[0.2.0]: https://github.com/bndct-devops/tome/releases/tag/v0.2.0
[0.1.0]: https://github.com/bndct-devops/tome/releases/tag/v0.1.0
