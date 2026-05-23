# Tome docs — article outline

Working draft. One section per docs page, in the order they appear in
`src/components/docs-nav.ts`. Per article:

- **Goal** — what a reader walks away knowing
- **Sections** — h2/h3 structure
- **Components** — which custom components to lean on (`Callout`, `Steps`, `Tabs`, `Mermaid`, `Kbd`, `SectionHero`, `DocsMeta`)
- **Screenshots** — shots to capture via `frontend/scripts/screenshots.mjs --showcase`. Each shot is themed (light/dark/amber). Note any `clip` (crop) or `after` (interaction) needs.
- **Open questions** — things only Benedict can answer

The current `koreader.astro` page is the reference for tone, density, and component usage. Match that.

---

## Getting started

### `/docs` — Welcome
- **Goal:** what Tome is, who it's for, link out to install + first-run. Replaces a typical "intro" page that nobody reads.
- **Sections:** What Tome does · Who it's for (self-hosters, KOReader users, manga readers) · 5-second feature list · "Pick your path" cards → Installation / First-run / KOReader / Stats
- **Components:** `SectionHero` eyebrow "Getting started" · 4 cards for path-picker
- **Screenshots:** hero shot of dashboard (already captured: `dashboard.png` in showcase set)
- **Open questions:** is there a 1-line elevator pitch we've agreed on? The landing page has one — reuse verbatim or rewrite?

### `/docs/installation`
- **Goal:** get a server running in <5 min
- **Sections:** Docker (one-liner) · docker-compose (recommended, file inline) · Volumes & paths · Reverse proxy notes (Caddy + Nginx snippets) · Updating
- **Components:** `Tabs` for docker vs compose vs bare-metal · `Steps` for compose flow · `Callout warning` about TOME_SECRET_KEY
- **Screenshots:** none required; this is mostly code blocks. Maybe a single shot of the setup page that appears on first boot.
- **Open questions:** are we promoting docker-compose as the canonical path? (I think yes.)

### `/docs/first-run`
- **Goal:** from "container is up" → "first book is in the library"
- **Sections:** First admin user (setup page) · Pointing at your library folder · First scan · Adding a second library · Inviting users (link to users-and-roles)
- **Components:** `Steps` for the linear flow · `Callout tip` about library folder layout (Series/ vs Author/)
- **Screenshots (themed):**
  - `firstrun-setup.png` — setup page with admin form
  - `firstrun-scan-progress.png` — dashboard mid-scan with progress indicator
  - `firstrun-first-book.png` — dashboard after first scan completes
- **Open questions:** does Tome have any kind of guided onboarding inside the app I should highlight, or is "click around" the official onboarding?

---

## Features

### `/docs/reader` — Built-in reader
- **Goal:** show the EPUB + CBZ readers exist, are good, and explain the controls
- **Sections:**
  - EPUB reader: themes (light/sepia/dark), font size + family, TOC drawer, keyboard nav, position persistence (CFI)
  - CBZ reader: page vs webtoon mode, RTL toggle, fit-width/height, single/spread, theme
  - Sync with KOReader (1-liner, link to /docs/koreader)
  - Keyboard shortcuts (table)
- **Components:** `Tabs` for EPUB vs CBZ controls · `Kbd` for shortcut chips · `Callout info` about CFI persistence
- **Screenshots (themed):**
  - `reader-epub-page.png` — Frankenstein chapter 1, mid-page (need turn-page interaction via `after`)
  - `reader-epub-toc.png` — TOC drawer open
  - `reader-epub-settings.png` — font/theme settings panel open
  - `reader-cbz-page.png` — Berserk vol 1 single-page mode
  - `reader-cbz-spread.png` — same, spread mode
  - `reader-cbz-webtoon.png` — webtoon-mode crop
- **Open questions:** any features in the reader you consider beta / don't want to advertise yet?

### `/docs/series` — Series & arcs
- **Goal:** explain how Tome groups volumes, what Arcs add, and how to manage them as admin
- **Sections:** How series are auto-detected (filename + folder rules) · The series page UI (volumes grid, progress bar, status badge) · Arcs (admin CRUD, used to subdivide long series) · SeriesMeta (status: ongoing/completed/hiatus) · No-series books (the "No Series" virtual bucket)
- **Components:** `Mermaid` diagram: filename → series detection → grouping · `Callout tip` about sanitizing series names · `Steps` for "splitting a series into arcs"
- **Screenshots (themed):**
  - `series-overview.png` — series sidebar + grid view (One Piece or Berserk)
  - `series-detail.png` — Berserk series page with arcs (Black Swordsman / Golden Age)
  - `series-status-badge.png` — close crop of ongoing/completed badge
  - `series-arcs-admin.png` — admin Arc CRUD modal
- **Open questions:** is Arcs intentionally power-user / hidden? Should the docs surface it prominently or keep it tucked under series?

### `/docs/stats` — Reading stats **(the deep dive)**
- **Goal:** explain every chart, what it measures, how it's computed. This is the page Benedict cares most about.
- **Sections:**
  - **Overview tab:** totals (books read/in-progress/unread), current streak (with 4h rollover explained), recent sessions list
  - **Habits tab:** hour×DOW heatmap (what each cell means, color scale), session timeline, reading pace (pages/hr), completion estimates (algorithm: rolling avg over last 30 sessions?), period & monthly comparison
  - **Library tab:** year-in-review, series completion ladder, author affinity, completion by type, per-book time table, library growth curve
  - **Where the data comes from:** `ReadingSession` records from web reader + TomeSync plugin, deduplicated by overlap rules
  - **The 4-hour rollover rule:** why a session at 2 AM counts as "yesterday"
- **Components:** `SectionHero` eyebrow "Features" · `Callout info` for the rollover rule · `Mermaid` diagram of data flow (KOReader + web reader → ReadingSession → aggregates) · table for per-chart "what it measures / how it's computed"
- **Screenshots (themed, mostly cropped to one chart each):**
  - `stats-overview-tab.png` — full overview tab
  - `stats-streak-card.png` — close crop of the streak card
  - `stats-habits-heatmap.png` — close crop of the hour×DOW heatmap
  - `stats-habits-pace.png` — crop of reading-pace card
  - `stats-habits-comparison.png` — period/monthly comparison
  - `stats-library-year.png` — year in review
  - `stats-library-series.png` — series completion ladder
  - `stats-library-author.png` — author affinity
  - `stats-library-growth.png` — library growth curve
- **Open questions:** **per chart, half a sentence on the intended interpretation** (e.g. "heatmap = find your best reading windows" vs "pretty viz"). This is the one I can't infer from code alone. Also: is there any chart you'd quietly retire if you could?

### `/docs/bindery` — Bindery (auto-import)
- **Goal:** how the watched-folder ingestion works and how to review/accept/reject
- **Sections:** What Bindery is · Manual mode (default — drop files, review queue, accept) · Auto mode (`TOME_AUTO_IMPORT=true`, schedule) · The review UI (unreviewed badge, accept/edit/reject flow) · Filename conventions Tome understands · Chapter files vs volume files (manga)
- **Components:** `Steps` for manual flow · `Callout warning` about enabling auto-import on a noisy folder · `Tabs` for manual vs auto config
- **Screenshots (themed):**
  - `bindery-queue.png` — review queue with a few unreviewed books
  - `bindery-detail.png` — single-book review modal with detected metadata
  - `bindery-settings.png` — auto-import toggle + interval in settings
- **Open questions:** is the unreviewed-books queue genuinely useful or do most users skip review and just trust auto-import?

---

## Integrations

### `/docs/koreader` — KOReader plugin (TomeSync)
- **Status:** **already written** in `src/pages/docs/koreader.astro`. Use as the tone/density reference. Possibly add 1-2 new screenshots (plugin UI in KOReader itself, settings page in Tome where you download the plugin).

### `/docs/opds` — OPDS feed
- **Goal:** explain what OPDS is, who'd use it, how to point a client at Tome
- **Sections:** What OPDS is (1 paragraph) · Setting up an OPDS PIN per user · Pointing KOReader's OPDS at Tome · Pointing Moon+ Reader / KyBook at Tome · Auth: Basic Auth + PIN, not JWT · What's exposed (libraries, series, books)
- **Components:** `Steps` per-client · `Callout warning` about exposing OPDS publicly without HTTPS · `Tabs` for KOReader / Moon+ / KyBook setup
- **Screenshots (themed):**
  - `opds-settings.png` — settings page with PIN generator
  - `opds-koreader-add.png` — KOReader's OPDS add-catalog screen (this is *not* a Tome screenshot — would need a KOReader screenshot, harder to capture deterministically)
- **Open questions:** do you actually have non-KOReader OPDS users? If not, demote those tabs to a single "other clients" footnote.

### `/docs/scribe` — Scribe (CLI)
- **Goal:** the LLM-powered metadata + ingest CLI. This is a power-user feature.
- **Sections:** What Scribe is (Claude Code skill, runs as `/scribe`) · Install (the symlink shell script) · API token + multi-profile config · The four modes: `/scribe <path>` (ingest), `/scribe update <query>`, `/scribe audit [scope]`, `/scribe series <name>` · How auto-apply vs review thresholds work · Web fallback for missing descriptions
- **Components:** `Tabs` for the 4 modes · `Steps` for install · `Callout info` about token scopes · code blocks for example invocations
- **Screenshots:** mostly terminal output — not a great fit for the Playwright pipeline. Consider hand-captured iTerm screenshots saved into `public/shots/scribe/`. Or `<pre>` blocks of representative output.
- **Open questions:** is Scribe stable enough to document publicly, or still alpha? If alpha, prepend a `Callout warning`.

### `/docs/api-tokens` — API tokens
- **Goal:** explain user-level tokens, how to create/revoke, the `tome_<prefix>` format, the universal scope
- **Sections:** What they're for (Scribe, custom scripts, anything that wants to skip the JWT dance) · Creating one (Settings → API Tokens) · The format (`tome_<prefix>` + body, sha256-hashed at rest) · Universal scope — every `/api/*` accepts either JWT or token · Revoking · Admin view of all users' tokens
- **Components:** `Steps` for create flow · `Callout danger` "store the secret now, it's shown once" · code block for `Authorization: Bearer tome_…`
- **Screenshots (themed):**
  - `api-tokens-list.png` — Settings → API Tokens page
  - `api-tokens-new.png` — create-token modal with secret shown
  - `api-tokens-admin.png` — admin all-users view
- **Open questions:** none — this is a straightforward reference doc

---

## Reference

### `/docs/configuration` — Configuration & env vars
- **Status:** **partial stub exists.** Needs filling out.
- **Goal:** the complete env-var reference
- **Sections:** Required (`TOME_SECRET_KEY`) · Paths (`TOME_DATA_DIR`, `TOME_LIBRARY_DIR`, `TOME_INCOMING_DIR`) · Optional integrations (`TOME_AUTO_IMPORT`, `TOME_AUTO_IMPORT_INTERVAL`) · Port (`TOME_PORT`) · Production deployment checklist (proper secret, durable volumes, reverse proxy, HTTPS)
- **Components:** table per group (var · default · purpose) · `Callout warning` for the secret-key regen consequences (invalidates tokens)
- **Screenshots:** none — pure reference
- **Open questions:** any env vars I'm missing? Cross-check `backend/core/config.py`

### `/docs/users-and-roles` — Users, roles, libraries
- **Goal:** how multi-user, roles, libraries, and visibility interact. The mental model.
- **Sections:** The three roles (admin / member / guest — table of what each can do) · Per-user book visibility rules (admin sees all, member sees admin + own + assigned, guest sees admin + public) · Libraries (global vs per-user, public vs private, assigning users) · Quick Connect (6-char code, 5-min TTL) · Force-password-change · Impersonation (admin → user)
- **Components:** **a big role × permission matrix table** · `Mermaid` diagram of visibility flow · `Callout tip` about Quick Connect for tablets/phones · `Steps` for "set up a family share" scenario
- **Screenshots (themed):**
  - `users-list.png` — admin user management page
  - `users-create.png` — create-user modal with role dropdown
  - `users-quick-connect.png` — Quick Connect code on a new device
  - `libraries-edit.png` — library settings with user assignment
- **Open questions:** which "scenarios" are most common? I'm picturing: (a) solo user, (b) family share with kids = guests, (c) friend group with shared admin. Are there others worth a recipe?

### `/docs/troubleshooting`
- **Goal:** searchable list of "X is broken, here's why and how to fix it"
- **Sections (each ~3-5 lines, Q&A style):**
  - Books aren't appearing after upload → scan progress, file format, permissions
  - Covers missing → metadata fetch failed, manual cover picker
  - KOReader "test connection" fails → URL, API key, redownload plugin
  - Position not syncing → which side last updated wins, conflict resolution
  - "Series view shows wrong volumes" → filename parsing rules, manual series edit
  - Forgot admin password → `python -m backend.cli reset-password` (verify command)
  - Reverse proxy 502/504 → uvicorn worker count, timeout
  - Stats page slow → SQLite + many sessions, no fix yet (or is there?)
  - Permissions errors on /library mount → docker user/group mapping
- **Components:** `Callout` per common-mistake item · `Kbd` for any keyboard-shortcut tips
- **Screenshots:** none — text reference
- **Open questions:** what are the top 5 support questions you'd predict from people who haven't read docs? Those become the first 5 entries.

### `/docs/changelog`
- **Goal:** human-readable changelog, latest-first. Linked from in-app "What's new" if we ever build that.
- **Sections:** v1.0 (codename "Codex"?) · v0.x backstory paragraph (or omit pre-1.0 entirely)
- **Components:** none custom; just `<h2>` per version + bulleted lists. `Callout` for breaking changes.
- **Open questions:** start at 1.0 (clean slate) or backfill 0.x? Going with **start fresh at 1.0** matches the "Codenames at v1.0" memory.

---

## Cross-cutting work (do once, before/after writing pages)

1. **Screenshot plan finalised.** Aggregate every screenshot listed above into one batch for `frontend/scripts/screenshots.mjs`. Group by page where the showcase setup needs identical preconditions to avoid repeated state setup. Run all in light/dark/amber.
2. **Mermaid diagram set:** 3 needed — series detection (in `/docs/series`), stats data flow (in `/docs/stats`), visibility rules (in `/docs/users-and-roles`). Style them with `style X fill:var(--accent)` for theme consistency.
3. **Voice/tone:** confirm with Benedict — match the landing-page voice (punchy, opinionated) vs neutral manual style. Current `/docs/koreader` leans **punchy** ("Other tools document KOSync setup. Tome ships a plugin pre-configured.") — recommend keeping that.
4. **Search index:** `pagefind` runs at build, no per-page work needed beyond writing real content.
5. **DocsMeta `lastUpdated`:** confirm where this date comes from. Right now it's hardcoded — wire to file mtime or git log -1?

---

## Suggested writing order

1. `/docs/installation` + `/docs/first-run` — the on-ramp, validate the writing voice before going deep
2. `/docs/configuration` — pure reference, fast to write
3. `/docs/users-and-roles` — the big mental-model doc, do while you still remember the role rules
4. `/docs/stats` — the deep dive Benedict cares about. Needs the per-chart bullet list from him first.
5. `/docs/series` + `/docs/reader` + `/docs/bindery` — feature pages
6. `/docs/opds` + `/docs/scribe` + `/docs/api-tokens` — integrations
7. `/docs/troubleshooting` + `/docs/changelog` — last, after the rest exists to link into
8. `/docs` (Welcome) — write absolutely last so it can link confidently into the finished set

---

## What I still need from Benedict (priority order)

1. **Stats:** half a sentence per chart on the intended interpretation
2. **Tone confirmation:** punchy/opinionated (like koreader.astro) — yes/no
3. **Troubleshooting:** top 5 questions you'd predict from confused users
4. **Scribe status:** stable enough to document publicly, or mark alpha?
5. **Users-and-roles "recipes":** which family/friend setups deserve a worked example?
6. **Welcome page elevator pitch:** reuse landing-page hero verbatim or rewrite?

---

_Last updated: 2026-05-22_
