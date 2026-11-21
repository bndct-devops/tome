# Features

Detailed descriptions of Tome's features. For a quick overview, see the [README](../README.md).

---

## Quick Connect

Quick Connect lets you sign in on a new device without typing your password -- useful on mobile, shared computers, or anywhere with an awkward keyboard.

1. On the login page, tap **Quick Connect** to get a 6-character code.
2. On a device where you're already logged in, go to **Settings > Security** and enter the code.
3. The new device is signed in immediately.

Codes expire after a few minutes and can only be used once.

---

## API Tokens

User-level API tokens let scripts and external tools (including [Scribe](scribe.md)) authenticate against Tome without embedding a username and password.

To create one:

1. Go to **Settings → API Tokens** and click **Create Token**.
2. Give it a descriptive name (e.g. `scribe-laptop`, `nightly-backup`).
3. Copy the token — the full value is shown **once**. It cannot be retrieved later.

Tokens look like `tome_<32 random chars>` and can be revoked at any time. Every `/api/*` endpoint accepts either a JWT or a `tome_*` bearer token, and a token inherits the role and visibility of the user that created it — so tokens are as powerful as the user behind them.

Admins can view all users' tokens via the same page for auditing.

---

## Scribe

Scribe is a Claude Code Skill shipped with Tome (`skills/scribe/`) that turns [Claude Code](https://claude.ai/code) into a conversational ingest and metadata-curation tool. It talks to your Tome instance over HTTP using an API token, and supports three modes:

- **Ingest** — batch-upload a folder of new books, dedupe by content hash, fetch metadata, and apply high-confidence picks automatically.
- **Update** — refresh metadata on books already in Tome via free-text queries like `"update descriptions for the Classics library"`.
- **Audit** — scan for weak metadata (missing descriptions, years, covers) and series title drift, then propose bulk fixes.

Install with `./skills/scribe/install.sh`. Full setup and usage in [docs/scribe.md](scribe.md).

---

## OPDS PINs

OPDS PINs are short app-specific passwords for authenticating OPDS clients (KOReader, Panels, Chunky, etc.). Typing a full password on an e-ink keyboard is painful -- a 10-character PIN of lowercase letters and digits is much easier.

To set one up:

1. Go to **Settings > KOReader > OPDS PINs** and generate a new PIN.
2. In your OPDS client, enter your Tome username and the PIN as the password.
3. The OPDS feed URL is `http://<your-server>:8080/opds`.

Each PIN is independent -- you can have one per device and revoke any of them without affecting your main password or other devices. Your regular password continues to work alongside any PINs you've created.

---

## Themes

Tome ships with three built-in themes:

- Light
- Dark
- Amber

Switch themes in **Settings > Appearance**. The theme is stored per-browser, so different devices can use different themes.

### Interface Language

The web UI is translatable. Choose a language in **Settings > Language and region**; on first visit Tome follows the browser language when a catalog exists for it. Translations are community-maintained `.po` files and anything untranslated falls back to English -- see [translating.md](translating.md) to improve or add one. The KOReader plugin, OPDS and the website stay English.

### Custom Themes

You can create a fully custom theme by pasting 10 comma-separated hex color values in **Settings > Appearance > Custom Theme**. The 10 values map to the theme's color palette in order. Custom themes are stored per-browser alongside your theme preference.

---

## Series Browsing

The sidebar shows all series in your library. Click a series to open an inline detail panel with:

- Volume grid with cover thumbnails
- Per-volume progress bars
- Continue reading button (jumps to the next unfinished volume)
- Mark all as read
- Publication status badge (ongoing, finished, hiatus, unknown) shown in the series list and detail header
- Story arcs — admins can split the volume grid into named sections (e.g. Berserk's Golden Age, Conviction, Fantasia) via the Manage button. Each arc has a name, volume range, and optional description; volumes outside any arc fall into an Unassigned section. Arcs can be filled in bulk from Claude's knowledge with `/scribe series <name>`.

---

## Ratings & Reviews

Rate the books you read on a 1–5 star scale, with an optional written review. Ratings are **per-user and private to you** — everyone keeps their own.

- **Per book** — the book detail page has a star rating and a review field. Both auto-save; the review collapses to a tidy quote (with an edit affordance) once written, rather than sitting as an always-open box.
- **On book cards** — your stars show across the library grid. You can **sort by "My Rating"** and **filter** the grid to `Rated`, `3+`, `4+`, or `5` stars.
- **Per series** — rate a whole series from its page. A series rating is **inherited** by every volume you haven't rated individually (your own volume rating always wins). A series' shown rating is your explicit rating if you set one, otherwise the average of your volume ratings — surfaced on series cards too. (The "No Series" group can't be rated.)

Stars use a theme-aware "rating gold" that fits each theme's palette. Ratings also sync both ways with KOReader's native Book status screen — see the [KOReader plugin](koreader-plugin.md) docs.

---

## Bulk Operations

Multi-select books on the dashboard to:

- Assign to a library
- Edit metadata (shared fields across selection)
- Fetch metadata from external sources
- Download as a ZIP archive
- Delete (removes the books and their files from disk — members can only delete their own uploads)

---

## Metadata Fetching

Search Google Books, OpenLibrary, and Hardcover for metadata. Results are shown in a side-by-side diff UI so you can see exactly what will change before applying. Hardcover results are prioritized when a `TOME_HARDCOVER_TOKEN` is configured.

By default Google Books is queried anonymously against a shared quota, which can hit `429`/`400` "Quota Exceeded" errors — most noticeable for non-English (e.g. traditional Chinese) catalogues that lean on Google as the fallback. Set `TOME_GOOGLE_BOOKS_KEY` to a free [Google Books API key](https://developers.google.com/books/docs/v1/using) and requests are charged against your own Cloud project quota instead. It's a plain public-search API key — no OAuth and no access to any user's private data.

---

## Cover Picker

Click any book's cover to open the cover picker. Search Google Books and OpenLibrary for alternative covers, or upload one from your device.

---

## Authentication and Roles

- JWT-based auth with first-run setup wizard
- Role-based user management (Admin / Member / Guest)
- Force password change on first login
- Admin impersonation (act as another user for debugging)

### Roles

| Role | What they can do |
|---|---|
| **Admin** | Everything — full access to all books, settings, users, bindery, and admin tools |
| **Member** | Upload books, download, edit/delete their own books, manage libraries, use OPDS/KOSync, view stats, bulk operations |
| **Guest** | Browse, download, and read books; access the OPDS feed |

### Per-User Book Visibility

- **Admins** see all books in the library
- **Members** see books added by admins, their own uploads, and books in libraries they have been assigned to. The dashboard shows a "My Books / Shared Library" filter to switch between the two views
- **Guests** see books added by admins and books in public libraries

Admins have an additional uploader dropdown filter on the dashboard to view books by a specific user.

### Content Restrictions

Admins can narrow an individual account further under **Admin > Users**:

- **Hidden tags** -- a comma-separated list. Books carrying any of these tags (matched case-insensitively) are invisible to that user everywhere: browsing, search, filters, OPDS and the KOReader plugin, including the user's own uploads. Untagged books are not affected, so tag first, then hide.
- **Downloads per day** -- a cap on files fetched per UTC day, enforced on every download path (single file, bulk ZIP, OPDS, TomeSync). Blank means unlimited, `0` disables downloads.

Admin accounts are never restricted.

---

## Filtering

The dashboard filter bar narrows the grid by **Series**, **Author**, **Tag**, **Format**, and **Language**, plus reading status, rating, and (for admins) uploader. Filters combine, show as removable chips, and live in the URL — so a filtered view is shareable and can be saved as a Shelf.

### On a device

With TomeSync plugin build 50 or newer, each KOReader device reports which Tome books are on it, and the filters get one row per device: **On Kindle** / **Not on Kindle** (the row is named after the device, see TomeSync > Settings > Device name). The filter is always about that one device of yours, never "any device". Combined with the Unread status, "Not on Kindle" is the "what do I load next" list. A shelf saved with a device filter also works in the plugin's shelf browser, so its books can be downloaded from the device itself. Books on a device show an "On ..." pill on cards and the book page, and a series page shows how many volumes each device holds. See [KOReader plugin: Books on the device](koreader-plugin.md#books-on-the-device).

### Language

The **Language** dropdown appears whenever your catalogue holds more than one language. There is no language *detection* — Tome never inspects the book's text. It reads the language each file *declares* in its embedded metadata (an EPUB's `<dc:language>`, a CBZ's `LanguageISO`, etc.), captured at scan/upload time. A book whose file declares nothing has no language and simply doesn't appear in the dropdown.

Because that declared value is inconsistent across files — `en`, `eng`, `en-US`, and `English` all mean the same thing — Tome folds it to a single canonical entry before showing it. Matching is a static lookup (`backend/services/languages.py`), tried in order:

1. **Whole-string match** against a table of ISO-639-2 three-letter codes and English names (`eng` / `english` → `en`, `ger` / `deu` / `german` → `de`, …).
2. **Base token** of a region/script-tagged value against the known two-letter codes (`en-US` → `en`, `zh-Hant` → `zh`).
3. **Base token** against the alias table, as a fallback (`eng-US` → `en`).

If nothing matches, the lowercased base token is kept as-is, so an unrecognized language still groups consistently — it just shows as an upper-cased code (e.g. `TL`) rather than a friendly name until it's added to the table. Note this is deliberate, not lossy by accident: `pt-BR` and `pt-PT` both collapse to "Portuguese", which is what you want in a filter dropdown.

---

## Shelves

Shelves (formerly called Saved Filters) let you save any combination of active dashboard filters — search text, book type, library, series, author, tags, format, language, sort order — as a named entry in the sidebar. Click a shelf to instantly restore that view. This is also the simplest way to get a "library per language": filter by a language and save it as a Shelf — it self-populates as matching books arrive, with nothing to add by hand.

Shelves are per-user and private. Each shelf can have a custom icon chosen from the icon picker.

---

## Reading Stats

The Stats page has seven fixed pages, all powered by reading sessions from the web reader and from KOReader via TomeSync. The time-based charts can also be **backfilled from KOReader's own per-page reading history** (from before TomeSync) by enabling reading-history import in the plugin — see the [KOReader plugin](koreader-plugin.md#reading-history-import) docs. The backfill adds reading time and pages only; it never changes your read/unread status.

The range pills (7d, 30d, 90d, 1y, All, or a custom range) apply to every page that depends on a period. Each page keeps its layout while it loads, and the image button saves the page as a PNG.

### Overview

Reading time in the range with the change against the period before, sessions, pages, books finished and your streak; reading time per day (per week for longer ranges); one row of up to four reading goals; the books you are reading now with an estimate of the days left; the books you finished; your top books by time; when you read; a 365-day grid; and all-time totals and records.

### Goals

Your reading goals, each as a card: how far you are against the target, how far ahead of or behind pace, and what it takes. Goals count calendar periods (this week, this month, this year, or up to the date of a finish-by goal) with the same numbers as the rest of Stats, so this page ignores the range pills. Home shows one row of up to four of the same cards, the goals that need attention first.

You can set a goal without opening Stats: Home has **Set a reading goal** under its numbers (or **New goal** under the goal cards), and a series page or a book page has **Finish by…** among its actions, which asks only for the day and shows what it takes a day. With a finish-by goal already set for that title the button shows its day and opens the goal to change it.

Click a card for the goal in detail: the period so far day by day, past periods against the target that applied then, and your best and current runs. Periods before you set the goal are shown but not counted as met or missed.

**New goal** offers eight kinds: books a year or a month, minutes or pages a day or a week, **reading days a week** (a day counts as it does for your streak) and **finish by a date** (a book or a series, with the minutes a day it takes at your own pace). Count goals can be limited to one book type. The dialog suggests three targets from your last 90 days (12 weeks for weekly goals), each with how often you reached it; without enough history it offers fixed targets. A deleted goal can be restored with **Undo** for 30 minutes.

Weeks start on Monday unless you choose Sunday under **Settings > Language and region > Week starts on**. Goals follow it, and so do the weekly streak, the Calendar and the Stats charts by weekday.

### Habits

When and how you read:

- **When you read** — hour × weekday grid, with weekday and hour totals
- **Last 14 days** — one row per day, each sitting at its start time
- **Sessions** — average and longest sitting, sittings per reading day, and session lengths
- **Pace** — pages per minute per reading day with a 7-day mean, and pages per minute per format
- **Reading speed** — words per minute, with your fastest and slowest books
- **The last 12 months** — reading hours and books finished per month
- **Reading character** — your reading personality and the traits behind it

### Library

What is on your shelves and what you have made of it:

- **Your library** — books owned, read, in progress and wanted
- **Series** — how far you are through each series, and the next volume
- **By type** — owned, read, finish rate and reading time per book type
- **Languages** and **Authors** — reading time per language, your most-read authors
- **Backlog** — how long your unstarted books would take at your own pace (hours from word count × your reading speed, days from your recent minutes per day), scoped to Want to Read, all unread, a library, or a shelf. The same estimate shows as "Est. read time" on the book page and "left in this series" on the series page
- **Book length**, **Published**, **Time to finish** and **Added to the library**
- **All books by reading time** — time, sessions and pages for every book, with its sessions one click away

### Taste

Your ratings: the distribution, highest and lowest rated, rated series, reading time against rating, and ratings over time.

### Timeline

Your reading life as a Gantt chart: one lane per series, one bar per book, a tick for every day you read.

### Sessions

Every reading session, grouped by day, with trim and delete for runaway sessions.

### Boards

The rearrangeable boards from earlier versions are still there. If you customised a board, a **Boards** tab shows it. **Settings › Appearance › Stats › Show Boards** shows the tab always (with the default boards) or never.
