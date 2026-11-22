# v3.2.0 "Chapbook"

A chapbook is a small booklet made to fit in a pocket. This release is
small too, and it is about a pocket-size reader: Tome now takes reading
from Xteink readers running CrossInk or CrossPoint. All of it is marked
experimental.

## Highlights

**Reading stats from CrossInk.** An Xteink reader on CrossInk 2.0 or newer
sends its reading to Tome over the KOReader Sync account it already uses
for progress. Sessions arrive with their reading time and pages, a book the
reader marks as finished is marked read in Tome, and the reading from
before the first sync shows on the Calendar and in the all-time totals.
Set it up under Settings > Xteink readers.

**A plugin for CrossPoint 2.0 and CrossInk.** Copy it to the reader's SD
card, open it and scan the code it shows. From then on the reader sends
each reading session by itself when it goes to sleep: reading time and
progress, no pages. This part has only run in CrossInk's desktop simulator
so far, not on a real reader. If you try it, please open an issue and say
how it went, also if it just worked.

**Books that are only on the reader.** A file you copied to the reader
yourself gets a book in Tome without a file, so its reading has a place.
It shows in your Library with a "No file" badge. Put the same file on a
KOReader device with the TomeSync plugin and both devices keep one
position.

## Also

- Clippings saved on a CrossInk reader show on the Highlights page.
- Settings > Import reading history takes a CrossPoint Sync account
  export, with a preview before anything is written.
- A new docs page, "Xteink readers", compares the two ways and says which
  to use: https://tome.bndct.sh/docs/xteink
- The progress line on a book's page is no longer empty or cut short for
  books read on a Kindle with page-stats.

## Thanks

To @gabrieledettorre, who asked for this and tested the CrossInk support
on an Xteink X3 over two weeks.

## Upgrade

```
docker pull ghcr.io/bndct-devops/tome:latest && docker compose up -d
```

Nothing has to be configured. On first start Tome adds a few tables and
columns; nothing existing is changed.

KOReader: no new plugin build. TomeSync stays at build 55.

---

## Full changelog

### Added
- **Xteink readers: CrossInk's built-in sync (experimental).** An Xteink
  reader on CrossInk 2.0 or newer sends its reading to Tome over the
  KOReader Sync account it already uses for progress: sessions with their
  reading time, pages per session from the reader's page counter, finished
  books, and the reading from before its first sync (shown on the Calendar,
  in streaks and as "before sync" totals on Stats). Set it up under
  Settings > Xteink readers; the new docs page "Xteink readers" has the
  details and says which of the two ways to use. Thanks to
  @gabrieledettorre, who asked for this and tested every part of the
  CrossInk support below on an Xteink X3 over two weeks: the sync, books
  without a file, reading one book on two devices, and the import.
- **Xteink readers: a plugin for CrossPoint 2.0 and CrossInk
  (experimental).** With the plugin on its SD card, a reader sends each
  reading session to Tome by itself when it goes to sleep: reading time and
  progress, no pages. Download it under Settings > Xteink readers, open it
  on the reader and scan the code it shows; the reader gets a key of its
  own that you can revoke under Settings > Access. On CrossInk it can run
  next to the built-in sync, and a sitting that arrives both ways counts
  once. Tested in CrossInk's desktop simulator, not yet on a device.
- **Books your reader knows and your library does not (experimental).** A
  file you copied to the reader yourself gets a book in Tome without a
  file, from the title and author the reader sends (on CrossInk: Send
  Document Metadata), so its reading has a place. It shows in your Library
  with a "No file" badge and is yours alone. The same file on a KOReader
  device with the TomeSync plugin is the same book, and each device picks
  up where the other stopped (tested by @gabrieledettorre on a Kindle and
  the Xteink).
- **Clippings from CrossInk readers (experimental).** Passages saved on the
  reader show on the Highlights page and on the book, read-only.
- **Import of a CrossPoint Sync account export (experimental).** Settings >
  Import reading history takes the export and brings over sessions, read
  status, progress, clippings and page counts, with a preview before
  anything is written.

### Changed
- Pages per minute on a book's page is now worked out only from sittings
  that counted page turns. Reading time from a source that reports none
  (the web reader, a CrossInk reader) no longer makes the pace look slower.
- Linking a KOReader sync document to a book now requires a book you can
  see; a book in someone else's private library answers as not found.
- The account data export includes the stats a reader uploaded.
- The Link KOReader sync picker on a book's page shows the title the reader
  sent for a document, when it sent one, next to device, progress and date.
- A KOSync push that names its book no longer claims the oldest pending
  OPDS download as a guess. That guess stays only for apps that send no
  metadata.

### Fixed
- **The progress line on books with KOReader page-stats.** A sitting the
  plugin recorded reaches Tome twice, as a live session with its position and
  as page-stats without one. Where both covered the same minutes the line
  dropped the session along with its time, so a book read on a Kindle got a
  line from the few sittings the page-stats had missed (ending at 63% on a
  finished book) or no line at all. A superseded sitting's position now counts,
  so the line fills in for books already read, and the device's synced
  positions (Position history) feed it too. A restored position stops an
  earlier bad jump from pinning the line.
