# Tome

A self-hosted ebook library server that knows how you read -- not just what you own.

Most library servers stop at file management. Tome connects to your e-reader via a custom [KOReader](https://koreader.rocks) plugin, records every reading session with page-level granularity, syncs positions bidirectionally between device and browser, and turns all of it into stats that get sharper the more you read.

Built with FastAPI, React, and SQLite. Ships as a single Docker image.

> **Early release** -- actively developed, expect rough edges.

![Home](docs/screenshots/home.png)
*Continue reading where you left off. Track streaks, pages, and reading time at a glance.*

## Highlights

- **TomeSync** -- custom KOReader plugin records reading sessions, syncs positions bidirectionally (device to web, web to device), and works fully offline. This is what makes Tome different. [Details](docs/koreader-plugin.md)
- **Reading stats** -- session tracking, streaks, time-of-day heatmaps, reading pace, completion estimates, genre trends, monthly comparisons, and per-book breakdowns -- all powered by real session data from your e-reader
- **Metadata from 3 sources** -- fetch and compare metadata from [Hardcover](https://hardcover.app), Google Books, and OpenLibrary with a side-by-side diff UI
- **Built-in reader** -- EPUBs, manga (CBZ/CBR), and PDFs render directly in the browser. Two-page spread, RTL mode, webtoon scroll, pinch-to-zoom on mobile. [Details](docs/reader.md)
- **Bindery** -- an inbox for incoming books. Drop files in a folder, review pre-filled metadata, accept into your library. Optional auto-import on a schedule. [Details](docs/bindery-deployment.md)
- **Scribe** -- a Claude Code Skill for conversational batch ingest, metadata refresh, and series-wide audits. Uses API tokens for auth and talks to Tome over HTTP. [Details](docs/scribe.md)
- **OPDS feed** -- browse and download from KOReader, Panels, Chunky, or any OPDS client
- **Themes** -- 3 built-in (light, dark, amber) plus fully custom themes via 10-value hex palette

Plus: series browsing, bulk operations, libraries with icons, shelves (saved filters), Quick Connect (6-char code sign-in), OPDS PINs (e-ink-friendly passwords), role-based access control, per-user book visibility, user-level API tokens, audit logging, and a bulk import script. [Full feature list](docs/features.md)

![Dashboard](docs/screenshots/dashboard.png)
*Filter, sort, and browse your library. Bulk select for metadata edits, library assignment, or export.*

![Series Detail](docs/screenshots/series-detail.png)
*Drill into a series to see every volume, track progress per book, and pick up where you stopped.*

![Book Detail](docs/screenshots/book-detail.png)
*Full metadata view with cover, description, tags, and one-click reading.*

![Series](docs/screenshots/series.png)
*All your series at a glance with volume counts and descriptions.*

![Stats](docs/screenshots/stats.png)
*Reading activity, streaks, session history, and time-of-day patterns.*

### Mobile

Tome works as a PWA on mobile. Pin it to your home screen for a native app feel.

| | | | | |
|---|---|---|---|---|
| ![Home](docs/screenshots/mobile-home.png) | ![Stats](docs/screenshots/mobile-stats.png) | ![Sidebar](docs/screenshots/mobile-sidebar.png) | ![Series](docs/screenshots/mobile-series.png) | ![Reader](docs/screenshots/mobile-reader.png) |

## Quick Start

```bash
docker run -d \
  -p 8080:8080 \
  -v /path/to/data:/data \
  -v /path/to/ebooks:/books:ro \
  -v /path/to/bindery:/bindery \
  -e TOME_SECRET_KEY=changeme \
  ghcr.io/bndct-devops/tome
```

Open `http://localhost:8080` and follow the setup wizard to create your admin account.

Or use Docker Compose -- copy `docker-compose.example.yml`, edit the values, and `docker compose up -d`.

### Volumes

| Mount | Purpose |
|-------|---------|
| `/data` | SQLite database and cover cache |
| `/books` | Ebook library (read-only is fine) |
| `/bindery` | Incoming folder for new books |

### Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `TOME_SECRET_KEY` | Yes | -- | JWT signing secret |
| `TOME_DATA_DIR` | No | `/data` | DB and cover cache |
| `TOME_LIBRARY_DIR` | No | `/books` | Library root |
| `TOME_INCOMING_DIR` | No | `/bindery` | Bindery folder |
| `TOME_PORT` | No | `8080` | HTTP port |
| `TOME_HARDCOVER_TOKEN` | No | -- | [Hardcover](https://hardcover.app) API token for metadata |
| `TOME_AUTO_IMPORT` | No | `false` | Auto-import files from the bindery on a schedule |
| `TOME_AUTO_IMPORT_INTERVAL` | No | `300` | Seconds between auto-import scans |

### Supported Formats

| Format | Reader | Notes |
|--------|--------|-------|
| EPUB | Text reader | CFI position tracking |
| CBZ | Comic reader | Streaming page delivery |
| CBR | Comic reader | Auto-repacked to ZIP |
| PDF | Browser viewer | Served directly |

## Development

Requirements: Python 3.12+, Node.js 18+

```bash
./dev.sh   # starts backend :8080 + frontend :5173
```

| Layer | Technology |
|-------|------------|
| Backend | Python 3.12+ / FastAPI |
| Database | SQLite (WAL) / SQLAlchemy 2.0 |
| Frontend | React 19 / Vite / TypeScript |
| Styling | Tailwind CSS 4 |
| Auth | JWT (python-jose) |

Built with [Claude Code](https://claude.ai/code).

## Documentation

- [Reader](docs/reader.md) -- EPUB, comic/manga reader, keyboard shortcuts, ComicInfo.xml
- [KOReader Plugin](docs/koreader-plugin.md) -- TomeSync setup, sync behavior, offline support
- [Bindery](docs/bindery-deployment.md) -- setting up the incoming book inbox
- [Scribe](docs/scribe.md) -- Claude Code Skill for batch ingest, metadata refresh, and series audits
- [Import Script](docs/import.md) -- bulk importing an existing collection from filenames
- [Features](docs/features.md) -- Quick Connect, OPDS PINs, permissions, themes, API tokens, and more

## Acknowledgements

- [KOReader](https://koreader.rocks) -- the open source e-reader app that Tome's sync plugin and OPDS integration are built for
- [Hardcover](https://hardcover.app) -- book metadata and cover art API
- [foliate-js](https://github.com/johnfactotum/foliate-js) -- the EPUB rendering engine powering Tome's built-in reader

## License

AGPL-3.0 — see [LICENSE](LICENSE)
