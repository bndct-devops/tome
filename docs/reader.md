# Built-in Reader

Tome has a built-in reader that handles EPUBs, manga/comics (CBZ/CBR), and PDFs directly in the browser. Click the "Read" button on any book detail page to open it.

---

## EPUB Reader

- Table of contents sidebar
- Three themes: light, sepia, dark
- Adjustable font size and font family (serif, sans-serif, monospace)
- Reading position saved automatically via EPUB CFI -- reopen a book and you're right where you left off
- Progress percentage tracked and visible on the dashboard

---

## Comic/Manga Reader (CBZ/CBR)

Pages are streamed individually from the server -- no need to download the entire archive before reading.

- **Page navigation** -- click/tap left or right half of the screen, use arrow keys, or swipe on mobile
- **Two-page spread** -- auto-enabled on wide screens, toggle with `S` key. Pages display side-by-side like an open book
- **RTL (right-to-left)** -- auto-enabled for manga book types. Page order and navigation direction flip so manga reads correctly. Toggle with `R` key
- **Fit modes** -- fit-to-width or fit-to-height, toggle with `W` key
- **Pinch-to-zoom** -- on mobile, pinch to zoom into panels. Double-tap to reset. Pan while zoomed
- **Webtoon/scroll mode** -- for manhwa and vertical-scroll comics. Toggle via the toolbar button. Renders all pages in a continuous vertical scroll instead of page-by-page
- **Page thumbnails** -- toggle a thumbnail strip at the bottom to jump to any page at a glance
- **Fullscreen** -- press `F` to toggle
- **Theme support** -- reader background respects your chosen theme (no white flash between pages in dark mode)
- **Progress tracking** -- current page saved automatically. Reopen and you're on the same page
- **Preloading** -- adjacent pages are preloaded in the background for instant page turns

---

## Keyboard Shortcuts (Comic Reader)

| Key | Action |
|-----|--------|
| Arrow keys | Navigate pages |
| `F` | Toggle fullscreen |
| `R` | Toggle RTL direction |
| `S` | Toggle two-page spread |
| `W` | Toggle fit-width / fit-height |
| `T` | Toggle page thumbnails |
| `Escape` | Exit reader |

---

## Supported Formats

| Format | Reader | Notes |
|--------|--------|-------|
| EPUB | Text reader | Full CFI position tracking |
| CBZ | Comic reader | Streaming page delivery |
| CBR | Comic reader | Auto-repacked to ZIP, cached |
| PDF | Browser PDF viewer | Served directly |

---

## ComicInfo.xml Support

CBZ and CBR files containing a `ComicInfo.xml` (the ComicRack/Kavita/Komga standard) get automatic metadata extraction:

- Title, series, volume/issue number, author, publisher, year, language, description
- Genre tags imported automatically
- Manga flag detected -- books with `<Manga>Yes</Manga>` are auto-assigned the Manga book type and default to RTL reading
