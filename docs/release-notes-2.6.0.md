# v2.6.0 "Pamphlet"

A pamphlet is a few folded pages, small enough to pass from one hand to the
next. This release moves reading around the same way: an e-reader with no
network hands a day's reading to your phone as a QR code, and Tome itself
can, if you say yes, hand the project one small report a month. It also
shows your reading as a calendar, counts streaks by the week, and estimates
when the book will be done.

## Highlights

**Calendar.** A new page in the sidebar shows each month of your reading as
a calendar. Every book you read sits in its day as a small chip with the time
you spent on it, marked when you finished it. The month's total, your daily
and weekly streaks and the books you finished sit above the grid. Click a day
to see what it looked like: the books, the chapters and pages, your pace
against your usual for that book, when you read, and a line summing the day
up. Arrow keys step through days, Shift steps months, T jumps back to today.
Imported KOReader history and web-reader sessions are combined the same way
as on the Stats page.

<img src="https://raw.githubusercontent.com/bndct-devops/tome/main/docs/screenshots/calendar.png" alt="Calendar: a month of reading with a day open on the right">

**Hand offline reading to your phone with a sync code.** When the e-reader
has no network but your phone does, TomeSync > Show sync code turns
everything the device could not send - sessions, positions, ratings - into a
QR code on the e-ink screen. A long offline stretch becomes several pages;
tap to turn. Scan it with the Tome app (Home or Settings > KOReader) or the
web UI (Settings > KOReader > Scan a sync code, with the camera or a photo of
the screen) and it lands in Tome at once, with an overview of which books
were touched. Scanning the same code twice, or the device flushing the same
queue later over WiFi, changes nothing: sessions carry the same dedup key as
the plugin's own sync, and a position never overwrites a newer one on the
server. Plugin build 47.

**Weekly streaks and a finish date.** Alongside the daily streak, Tome now
counts weeks in a row with at least one reading day, so reading every few
days still builds a streak. It is a Custom Stat tile, and Personal Records
lists your longest daily and weekly streaks with the dates they ran. Books
you are reading show an estimated finish date on their page, worked out from
how much of that book you have read per day over the last two weeks.

**Telemetry, opt-in.** Admins are asked once, on Home, whether Tome may send
one small anonymous report a month: version, platform, bucketed counts of
users and books, which features were used in the last 30 days. Never a
title, a name or a hostname. You see the exact report before you can say
yes, "No thanks" sends nothing and is remembered, and `TOME_TELEMETRY=false`
hides the question entirely. What the report contains is at
[tome.bndct.sh/docs/telemetry](https://tome.bndct.sh/docs/telemetry), the
receiver is the public [tome-pulse](https://github.com/bndct-devops/tome-pulse)
repository, and the numbers it produces are at
[tome.bndct.sh/stats](https://tome.bndct.sh/stats).

## Also in this release

- Closing a book while offline no longer loses its reading session. The
  plugin queued a session only on sleep; closing the book with no network
  dropped it. Both paths now queue, and the queue holds 200 sessions instead
  of 50.
- Search matches Korean, Chinese and Japanese text anywhere in a word, not
  only at its start (#206). The index is rebuilt once on the first start
  after the update. Based on #207 by @ziozzang.
- The sidebar, the collapsed rail and the mobile drawer share one navigation
  list, so an entry can no longer go missing from one of them. Contributed by
  @maichler (#237).
- The Bindery badge counts files when the bindery directory sits inside a
  hidden directory. Contributed by @maichler (#236).

## KOReader plugin

Ships build **47 (1.16.1)**: the sync code, and the offline session fix
above. Update in-app via TomeSync > Settings > "Check for updates", or let
the auto-check on launch pick it up. No breaking changes; older builds keep
syncing unchanged if you don't update. The sync code needs build 47 on the
device and 2.6.0 on the server.

## Upgrade

```
docker pull ghcr.io/bndct-devops/tome:latest && docker compose up -d
```

No configuration changes. The search index is rebuilt on the first start,
which takes a moment on a large library. Admins see the telemetry card on
Home once; it goes away with either answer, and `TOME_TELEMETRY=false` keeps
it from appearing at all.

---

## Full changelog

### Added
- **Telemetry, opt-in.** Admins are asked once, on Home, whether Tome may
  send one small anonymous report a month: version, plugin build, platform,
  bucketed counts of users, books and libraries, which features were used in
  the last 30 days, and the format mix. Never a title, a name or a hostname.
  The card shows the exact report before you can say yes, "No thanks" sends
  nothing and is remembered, and `TOME_TELEMETRY=false` hides the question
  entirely. Nothing is sent without a stored, positive answer, and if a later
  version changes what the report contains, sending pauses until you have
  looked at the new shape. Settings > About shows where things stand in
  words, with the last and next report dates, and every send is in the audit
  log with a hash of what left. What the report contains and where it goes
  is documented at tome.bndct.sh/docs/telemetry; the receiver is the public
  tome-pulse repository, and its aggregates are public at tome.bndct.sh/stats.
- **Calendar.** A new page in the sidebar shows each month of your
  reading as a calendar: each book you read sits in its day as a small chip
  with the time you spent on it, marked when you finished it (on a phone, a
  bar under the day carries the time instead). The
  month's total time, your current daily and weekly streaks and the books you
  finished sit above it. Click a day for what it looked like: the books,
  chapters, pages and progress you made in each, your pace compared with your
  usual for that book, when you read, and a line summing the day up. Arrow
  keys step through days (Shift for months) and T jumps back to today. Imported
  KOReader history and web-reader reading are combined the same way as on the
  Stats page.
- **Weekly reading streaks.** Alongside the daily streak, Tome now counts
  weeks in a row with at least one reading day, so reading every few days
  still builds a streak. Add it as a Custom Stat tile ("Weekly Streak"), and
  Personal Records now lists your longest daily and weekly streaks with the
  dates they ran (so does the "Longest Streak" Custom Stat).
- **Estimated finish date.** Books you are reading show an "Est. finish" date
  on their page, worked out from how much of that book you have read per day
  over the last two weeks. Books you have not touched in two weeks show the
  time left but no date.
- **KOReader sync code: hand offline reading to your phone.** When the
  e-reader has no network but your phone does, TomeSync > "Show sync code"
  turns everything the device could not send - reading sessions, positions
  and ratings - into a QR code on the e-ink screen (several pages for a long
  offline stretch, tap to turn). The book you are reading is included:
  showing the code ends the sitting so far, like closing the lid.
  Scan it with the Tome app (Home or Settings
  > KOReader) or the web UI (Settings > KOReader > Scan a sync code, camera
  or a photo of the screen) and it lands in Tome at once, with an overview of
  which books were touched: cover, sessions and time, pages, progress before
  and after. Scanning a code twice, or the device flushing the same queue
  later over WiFi, changes nothing: sessions carry the same dedup key as the
  plugin's own sync, and a position never overwrites one that is newer on the
  server (the phone read on meanwhile). "Scanned" on the device moves a
  watermark so the next code only carries newer reading; the queues stay put
  for the WiFi sync. A device clock that is clearly wrong (ahead of the
  server, or days behind) is corrected. Plugin build 47 / 1.16.1, gesture
  "TomeSync: Show sync code", `POST /api/sync-code`.

### Changed
- The sidebar, the collapsed rail and the mobile drawer render the top-level
  navigation from one shared item list, so an entry can no longer go missing
  from one of them (the cause of #230). The drawer's Bindery badge now matches
  the sidebar's, and tapping the library or shelf you are already on closes
  the drawer. Contributed by @maichler (#237).

### Fixed
- **Closing a book while offline no longer loses its reading session.** The
  plugin queued a session only when the device went to sleep; closing the
  book with no network dropped it. Both paths now queue, and the offline
  queue holds 200 sessions instead of 50. Plugin build 47.
- Search matches Korean, Chinese and Japanese text anywhere in a word, not
  only at its start (#206). The search index now uses SQLite's trigram
  tokenizer, which indexes every overlapping run of three characters; a
  search term shorter than that, a normal whole word in these languages,
  falls back to a substring scan over the same columns. Accents are still
  folded, so "gunter" keeps finding "Günter". Existing installs rebuild the
  index on the next start. Based on #207 by @ziozzang.
- The Bindery badge showed no count when the bindery directory itself sits
  inside a hidden directory such as `~/.local/share/tome/bindery`, although
  the Bindery page listed the waiting files. Contributed by @maichler (#236).
