# Changelog

All notable changes to Tome are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). This project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [1.4.0] — 2026-06-10

### Added
- **Send to KOReader (beta).** Queue a book from the web straight to your
  e-reader — no email, no Amazon Send-to-Kindle. It's the KOReader-native
  counterpart to email send-to-device: the original EPUB/CBZ arrives in your
  library folders (by series, or under the author for standalones) instead of
  being converted and dropped into a stock reader. The book detail page and the
  dashboard's bulk bar gain a split **Send to KOReader** button (the caret still
  offers "Send via email…"); the TomeSync plugin grows an **Inbox (N)** badge you
  tap to pull queued books (build 16 / 1.2.3). Delivery is a pull, not a push, so
  books arrive the next time KOReader checks in. Per-user — every connected
  KOReader shares one inbox. Off by default; enable with
  `TOME_SEND_TO_KOREADER=true`. See the
  [KOReader docs](https://tome.bndct.sh/docs/koreader#send-to-koreader).
- **Download a single book from KOReader.** The TomeSync plugin's series browser
  now drills into a per-volume list when you tap a series — pick one title to
  download on its own, or use the "Download all" row for the whole series as
  before. Standalone books with no series are now reachable too, through a
  "No Series" entry in the browser, so they no longer had to be fetched via OPDS
  or the web; each is filed under its author folder, matching Tome's library
  layout. Bumps the plugin to build 15 (1.2.2).

### Fixed
- **Relative timestamps no longer drift by your UTC offset.** The dashboard's
  Reading Log (and the notification bell and API-token "last used" times)
  showed sessions recorded minutes ago as "2h ago" for anyone not living on
  UTC: those endpoints emitted timestamps without an explicit timezone, so the
  browser parsed the UTC values as local time. All of them now carry the `Z`
  suffix the rest of the API already used.
- **The TomeSync plugin no longer breaks layout profiles that auto-execute on
  book open.** The "TomeSync: Server at X% (device: Y%)" message shown when
  another device had read ahead was a modal window, and KOReader delivers
  profile actions only to the topmost non-modal window — so a profile applying
  your layout (font size, margins, columns) on book open was silently swallowed
  exactly on those opens, leaving the book with default or stale layout
  settings. The message is now a passive toast that lets profile actions
  through. Also fixes two more issues in the same path: a position saved by the
  web reader no longer throws KOReader to page 1 (the plugin now recognises it
  isn't a KOReader-native position and jumps by percentage instead), and the
  book-open sync no longer runs twice per open. Bumps the plugin to build 17
  (1.2.4).
- **Series progress no longer shows as complete the moment you start the last
  book** (#36). The dashboard's "Series Progress" bar measured progress by the
  index of the book you were currently reading, so beginning book 2 of a 2-book
  series filled the bar to 100% before you'd finished it. It now reflects the
  number of volumes you've actually read, so the series only reads as complete
  once the last book is marked read.
- **The Bindery is now reachable from the mobile sidebar.** Admins
  could open the Bindery from the desktop sidebar but the link was missing from
  the mobile navigation drawer, so it was unreachable on a phone or the installed
  PWA. The admin-only Bindery entry (with its pending-count badge) now appears in
  the mobile drawer too.

## [1.3.2] — 2026-06-06

### Fixed
- **Reader font, size and theme no longer reset at every chapter** (#33). In the
  EPUB reader, changing the font, font size or background theme worked on the
  page you were on but was silently reverted the moment you turned into a new
  chapter — snapping back to whatever settings were saved when you first opened
  the book. The chapter-load handler was re-applying a stale snapshot of the
  reader settings captured at open time; it now always applies your current
  choices, so adjustments persist across chapters for the rest of the session.

## [1.3.1] — 2026-06-06

### Fixed
- **Shared libraries are now actually shared** (#31). Marking a library *public*
  had no effect: the library list only ever returned libraries you owned (plus
  the built-in global ones), so a library created by one user was invisible to
  everyone else regardless of its public/private flag. Public libraries are now
  visible to all users, and their books show up for members too (previously only
  guests saw public-library books). Private libraries can be shared with
  individual people: the library editor gained a **Share with users** picker, and
  library owners — not just admins — can grant and revoke access to their own
  libraries. The rename/delete/add-to-library controls now appear only on
  libraries you can actually manage (your own, or any library if you're an admin),
  so you no longer see edit buttons that error out on libraries owned by someone
  else.

## [1.3.0] — 2026-06-05 — "Diary"

### Added
- **Single sign-on (OIDC).** Tome can now authenticate against an external
  OpenID Connect identity provider (Pocket ID, Authelia, Authentik, Keycloak,
  Zitadel, Google, …). When enabled, a configurable "Sign in with SSO" button
  appears on the login page; signing in provisions a Tome account and maps the
  provider's groups to Tome roles (admin / member / guest). Existing accounts can
  attach SSO via **Settings → Single Sign-On → Link SSO**, so you keep your
  library and reading history while gaining passkey login. Local username/password
  login always stays available, and at least one local admin remains a break-glass
  login regardless of identity-provider state. Off by
  default — set `TOME_OIDC_ENABLED=true` plus issuer/client credentials to turn
  it on. New env vars: `TOME_OIDC_ENABLED`, `TOME_OIDC_ISSUER`,
  `TOME_OIDC_CLIENT_ID`, `TOME_OIDC_CLIENT_SECRET`, `TOME_OIDC_REDIRECT_URL`,
  `TOME_OIDC_ADMIN_GROUP`, `TOME_OIDC_MEMBER_GROUP`, `TOME_OIDC_GUEST_GROUP`,
  `TOME_OIDC_DEFAULT_ROLE`, `TOME_OIDC_AUTO_CREATE`, `TOME_OIDC_ALLOWED_GROUP`,
  `TOME_OIDC_ROLE_SYNC`, `TOME_OIDC_BUTTON_LABEL` (see the SSO docs).
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
- Books sent with **Send to Device** now sync their reading position back from
  KOReader. They were emailed as a bare `Title.ext`, which the TomeSync resolver
  couldn't reliably match to a library book (it failed with "Book not resolved").
  Sent files are now named the same way KOReader names its OPDS downloads —
  `Author - Vol. X — Title.ext` — so they resolve through the path that already
  works for OPDS. No change for books already on a device; re-send to pick up the
  new name (#25).
- You can no longer lock yourself out by removing the last admin. Demoting,
  deactivating, or deleting a user is now refused with "Cannot remove the last
  admin" when they are the only remaining active admin — previously a single-user
  instance that changed its own role to member (or guest) had no way back through
  the UI and needed a manual database edit to recover.
- The PWA service worker no longer swallows full-page navigations to server
  routes. Its SPA navigation fallback was serving the cached app shell for *any*
  navigation, including `/api/*` and `/opds/*` — so on an installed/cached client
  the SSO handshake silently failed (the redirect to `/api/auth/oidc/login` and
  the provider's return to the callback both got the app shell, dropping you back
  to the dashboard instead of completing sign-in). The navigation fallback now
  excludes `/api` and `/opds`.
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
- Login page could crash to a blank screen if you opened it while already signed
  in (e.g. returning to a tab with a live session) — a stale-session edge that
  tripped a React hooks error. The redirect now runs after the page's hooks.
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

### Security
- Updated `react-router` to 7.17.0, clearing four advisories (an RCE in vendored
  turbo-stream plus three DoS / open-redirect issues). None were exploitable in
  Tome's static SPA, but the alerts are now resolved. (#27)

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
