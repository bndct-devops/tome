# Changelog

All notable changes to Tome are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). This project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- **Create highlights in the web reader — they appear on your KOReader.** Select
  text while reading on the web, pick a colour, and optionally attach a note;
  the highlight paints immediately and syncs to your devices. Since the web
  reader and KOReader address text differently, a new highlight carries a
  provisional identity until your device next syncs the book: the plugin locates
  the passage natively and "adopts" it as a first-class KOReader highlight
  (build 25 / 1.7.0). You can also **edit notes and delete** any highlight from
  the tap-card — including ones made on the device — and the change wins on
  every device at its next sync. A passage the device can't locate stays
  web-only rather than landing on the wrong words.
- **Your KOReader highlights now show up inside the web reader.** Open a book on
  the web and the passages you highlighted on your device are painted right in
  the text, in their KOReader colours; tap one to see the full passage, its note
  and when you made it. Under the hood each highlight is re-anchored by its own
  text (device anchors don't translate to the web reader's engine), so a
  highlight whose passage can't be located — say it spans a page-break element —
  simply doesn't paint rather than pointing at the wrong words. Highlights load
  lazily per chapter, so big collections don't slow the reader down.
- **Follow a series — get told when a new volume is out.** A new opt-in release
  detector (set `TOME_RELEASE_DETECTION=true`; needs your Hardcover token) lets
  members follow a series: Tome polls Hardcover on a schedule (daily by default,
  `TOME_RELEASE_CHECK_INTERVAL`) and when the tracker's latest volume number
  grows past what it saw at follow time, you get a bell notification — and an
  email when SMTP is configured. Following starts silent: it primes at the
  series' current latest volume, so following a 27-volume series doesn't fire
  27 alerts. Follows live alongside your wishlist (`/api/wishlist/follow`,
  `/api/wishlist/follows`), and admins can trigger a check on demand.
- **Focus mode on the Home page.** A new minimalist Home view that surfaces the one
  book you're most likely to pick up next — your most-recently-synced in-progress
  title — as a large cover with the upcoming volumes of its series fanned behind it
  in a rotating "coverflow" display. Alongside it: the series, your progress and
  where you last synced from, a one-click **Resume reading**, and a quiet
  **Currently reading** strip to switch the spotlight to any other in-progress book.
  Clicking the cover or series name jumps to the series. Toggle between **Focus** and
  the full **Dashboard** from the Home header; your choice is remembered.
- **Your Reading DNA.** The Home dashboard shows a reading-personality card — an
  archetype (e.g. "Night-Owl Epic Specialist") distilled from a few traits like
  when you read, how long your sessions run, and how widely you range across the
  library. On Reading Stats it's also available as a **Reading DNA** tile — add it
  from **Add tile** under Habits; the Home card's "Full breakdown" link points there.
- **A richer reading log on every book.** A book's Reading Stats now show more of
  its story: a **progress line** traced over the activity bars so you can see how
  far you got on each reading day, a **momentum** indicator comparing the last week
  to the one before, a **"Where you read"** breakdown splitting your time across the
  web reader, KOReader and your devices, and a **Finished** date once you mark a
  book read. You can also **log a session by hand** — handy for paper reading or a
  device that wasn't synced — and **export** a book's reading log to CSV or JSON.
  Small "i" hints explain the progress and reading-intensity charts in plain language.

### Fixed
- **Metadata fetching got a deep overhaul.** The same book returned by two
  sources used to eat two of the five result slots — each copy *partial*
  (Hardcover knows series but not language; Google the reverse). Duplicates are
  now merged across sources into one complete candidate, results are ranked by
  the same relevance score everywhere (the right volume of a series now beats
  the wrong volume from a "better" source), and every path — the fetch dialog,
  bulk review, bulk apply, Bindery, auto-import — agrees on what the best match
  is. Rate limits are handled honestly: sources are retried once with a pause,
  and when one is still throttled the dialog says so instead of pretending the
  book doesn't exist. Three more sharp edges filed down: auto-import no longer
  renames a book on a low-confidence match (it fills blanks instead), applying
  fetched metadata no longer wipes your hand-added tags, and bulk fetching
  paces itself instead of firing a hundred parallel searches that tripped the
  very rate limits above. A cross-check against realistic library shapes (manga
  volumes, light novels with series-only titles, wrong-edition ISBNs, foreign
  editions) caught four more: Hardcover's **series and publisher were never
  actually filled in production** (an id-type mismatch made that lookup a
  silent no-op since it shipped — fixed), foreign-language editions no longer
  merge into (and poison) the English candidate, light novels no longer match
  the manga adaptation of the same series (search is edition-aware now), and a
  stored ISBN that points at the wrong edition — damage the old auto-apply
  itself caused — no longer monopolises the results. Edition awareness also
  understands custom book types now ("Webtoons", "graphic_novel", …), not just
  the seeded four.
- **AniList as a manga metadata source.** The existing sources are prose-first
  and often know nothing about a manga volume beyond its title. Books with a
  sequential-art type (manga, comics, webtoons, custom equivalents) now also
  query AniList (no API key needed): it appears as its own candidate with the
  series blurb, genres, staff and cover, and its series-level description,
  author and genres back-fill volume-level hits from the other sources that
  are missing them.
- **Repeat searches are served from a short-lived cache.** Identical source
  queries within 15 minutes (re-opening the fetch dialog, bulk-fetching a
  series whose volumes share a series-level lookup) no longer re-hit the
  external APIs — less rate-limiting, faster results. Concurrent duplicates
  (a bulk fetch firing the same lookup for every volume at once) share a
  single outbound request instead of racing past each other.
- **No more flicker of the Follow UI on instances without release detection.**
  The Follow button, Following section and Upcoming-releases card rendered
  optimistically and hid after the server said the feature is off — a visible
  flash on every page view. They now stay hidden until the check resolves, and
  a disabled verdict is remembered for the session.
- **Deleting a highlight from the web now sticks, even for a highlight you just
  made.** The deletion marker was stamped with the server's clock, but compared
  against the device's local wall-clock — so with a UTC server and a device in a
  later timezone, deleting a recent highlight produced a marker that looked *older*
  than the highlight itself, and the device quietly kept (and could re-upload) it.
  The marker is now stamped no earlier than the highlight's own timestamp, so the
  deleted copy always loses the tie while a deliberate re-highlight still wins.
  Also, the "N from M books" counter on the Highlights page no longer under-counts
  books after deleting when more highlights are still unloaded.
- **Hand-logged and web-reader sessions no longer vanish on device-synced books.**
  On a book with imported KOReader history, the "history wins" rule replaced *all*
  other reading records — so logging 30 minutes of paper reading appeared to do
  nothing, and web-reader time was invisible. The rule is now per source: the
  imported history replaces only the device's own live sessions (the same reading,
  recorded twice), while web-reader and manual sessions add on top — everywhere,
  from the book page's reading log down to the dashboard totals and streak days.
  A pleasant side effect: **"Where you read"** now actually shows the web/device
  split for mixed readers, and the progress line can draw through web-reading days
  on a device-synced book. Manual logging also got sturdier: a failed log now
  shows an error instead of silently resetting, absurd inputs (negative pages, a
  duration over 24h) are rejected instead of crashing, and a timezone-annotated
  start time is converted to UTC instead of having its offset ignored. The
  Activity chart also earned a real time axis: days without reading now appear
  as gaps instead of active days being stretched edge-to-edge (two adjacent
  bars could silently be a month apart), the "Where you read" bar separates its
  segments with a hairline seam, and the admin "All readers" line stays hidden
  when the only reader it would describe is you.
- **The "Finished" date is now the date you finished.** It used to be the last
  time anything touched the status row — rating a book in March that you finished
  in January showed "Finished: Mar", and even a device sync could nudge it. The
  finish date is now recorded explicitly at the moment a book becomes "read"
  (existing read books keep their best-known date), survives later ratings,
  reviews and position syncs, and clears if you un-finish the book. Also fixed on
  the way: a book synced straight from unread to 100% in one sitting now lands on
  "read" immediately (it used to sit at "reading" until the next sync), the
  admin-only "All readers" line counts consistent units (reading days) instead of
  mixing sessions with days, a book whose progress was known but had only one
  progress point showed that progress nowhere, and the chart "i" hints now close
  on outside tap on iPhones.
- **A font change no longer scrambles a book's page stats.** KOReader re-paginates
  when the font or margins change, and Tome sometimes mixed page numbers from
  different paginations: a book finished at 250 pages then reopened once at a
  1571-page pagination could show **"250 of 1571 pages · 16%"** despite being
  fully read, the Completion Estimates tile could report a nearly-finished book
  as barely started, and the **Re-reads** tile counted "page 10" under two
  different paginations as a revisit of the same page. Page coverage and reading
  position are now computed in fraction-of-book space (per row, against that
  row's own page count) and expressed against the *latest* pagination. Two more
  estimate fixes ride along: pages-per-day no longer drops the first active day
  from the denominator (which doubled a two-day reader's pace and halved the
  estimate), and the confidence label now comes from the signal that actually
  drove the estimate instead of whichever source happened to have more data.
- **Late-night reading no longer splits across two days — anywhere.** Tome has
  always counted a session started at, say, 1:30 am toward the previous evening's
  reading day for **streaks** (a local day with a 4-hour rollover), but newer
  features quietly bucketed by plain calendar days instead. The consequences: the
  activity heatmap could show a gap on a day the streak counted (the long-standing
  heatmap/streak drift), a continuous evening read crossing midnight could mark a
  book as a **re-read** and double its **reading-log** day count, the per-book
  **momentum** ("last 7 days vs prior") could disagree with the dashboard, and the
  Reading DNA **rhythm** trait split night reads into two active days. Every
  day-based view — daily chart, heatmap, re-reads, completion estimates, per-book
  timelines, reading intensity, momentum, Reading DNA — now shares the streak's
  single reading-day rule. The one deliberate exception is the hour-of-weekday
  heatmap, where 1 am should still display as 1 am.
- **Focus mode rough edges, rounded off.** A book without a cover showed the
  browser's broken-image glyph in the hero, the fan and the Currently-reading
  strip — it now gets the same tidy placeholder as everywhere else. Rapidly
  switching between strip books could leave the hero stuck on the wrong book (a
  slower earlier response landing last); switches are now cancelled cleanly. With
  more than 12 books in progress the extras were silently unreachable — the strip
  now ends in a **"+N more"** chip into the filtered library. Sync times read
  naturally ("1 hour ago", and past a month the actual date instead of "412 days
  ago"), the empty state gained a **Browse library** button, Series Progress rows
  no longer reload the whole app, and a brand-new user no longer sees an empty
  bordered box beside the dashboard. The Reading DNA figures are also no longer
  computed for views that never show them (and on phones, only fetched when the
  Home dashboard actually renders). Two touch-ups from a polish pass: the small
  kicker line no longer repeats the series name that the big title right below
  it already shows, and on phones the hero stacks from the top instead of
  floating mid-screen with dead space above the covers. The Reading DNA trait
  markers also stay inside their bars at the extremes instead of half-clipping.
- **Sharper axis labels and richer tooltips on stats charts** *(shipped in v1.7.x
  polish, previously missing from this log)*: KPI tile labels no longer truncate
  mid-word, and the daily chart's tooltips include pages alongside minutes.
- **The top bar on Stats, Highlights, Wishlist and the Bindery is now the real
  one.** It was a near-copy of the dashboard's header that had already drifted:
  on phones its Upload button was an empty pill (the icon had been lost in the
  copy), uploading from those pages silently dropped the "this upload satisfies
  N wishes" notice — and on the Bindery didn't refresh the inbox — and the
  search box neither advertised nor honoured the **/** focus shortcut the
  dashboard has. There is now a single shared header component used everywhere,
  so the two can't drift apart again; the wish notice, Bindery refresh and
  **/** shortcut all work from every page.
- **Standalone books download to the correct book-type folder in KOReader.** A book
  with no series — say a RoyalRoad title — could be filed under the wrong type's
  folder (e.g. `light_novel`) when downloaded through the plugin, while books in a
  series went to the right place. The plugin filed an entire batch under a single
  type, which is fine for a real series but wrong for the "No Series" bucket, where
  standalone books of different types are mixed together. Each book now carries its
  own type and is filed accordingly, in both the built-in layout and custom download
  templates. Requires plugin build 24 (1.6.2), delivered via the usual in-app update.
- **A tidier Home header on phones.** On narrow screens the stats strip now spans
  the full width with the figures spaced apart instead of crowding together, and the
  **Focus / Dashboard** switch sits below it as a full-width toggle rather than
  floating in a half-empty row. The desktop layout is unchanged.
- **Reading progress for device-read books is no longer understated.** A book you
  were, say, 35% through could show as 11% — both in its **progress** figure and in
  the **Completion Estimates** tile (which then wildly overestimated the time left).
  The cause: progress was derived from how many distinct pages your KOReader history
  had logged time on (coverage), not how far through you actually are (position).
  Progress now uses your real reading position, falling back to the furthest page
  reached, so it matches what your reader shows — and finished books read 100%.
- **Per-book "all readers" totals now include device reading.** On a book read
  only through the KOReader plugin (imported page-stats, no live sessions), the
  admin "All readers" line showed 0m / 0 sessions / 0 readers; it now reflects that
  reading.

## [1.7.0] — 2026-06-28 — "Signature"

### Added
- **Per-page reading stats from your KOReader history.** Once you've imported your
  device's reading history, each book page gains a **Reading intensity** strip —
  where your time actually went across the book, page by page — plus an honest
  **"X of Y pages"** read (from the real page count, not a guess) and a note when
  you've re-read stretches. A book's reading stats (time, pages, dates, pace) now
  also reflect that imported history, so books you only ever read on the device
  are no longer shown as blank. On the dashboard, a new **Re-reads** tile (from the
  tile gallery) ranks the books whose pages you keep coming back to, and the
  **Completion Estimates** tile now measures progress by real pages turned — so it
  works for device reading too, not just the web reader.
- **The sidebar now follows you onto every page.** Stats, Highlights, Wishlist and
  the Bindery used to drop you onto a bare screen with only a back-arrow — to get
  from your stats to your highlights you had to bounce back through Home first.
  They now carry the same sidebar as the rest of Tome (your libraries and shelves,
  the full nav, your profile), so you can jump straight between any section and the
  whole app feels like one place instead of a handful of detached screens. The
  active section highlights itself, and on phones it's the same slide-in drawer.
- **A Highlights page — your commonplace book.** Every highlight and note you make
  in KOReader already syncs into Tome; now there's one place to read them all. The
  new **Highlights** page (in the sidebar) gathers your highlights across the whole
  library, grouped by book, each as a quote card with its chapter and note. Search
  filters across everything at once — text, notes, chapter, and book title — with
  the matches marked; collapse books to scan a big collection, or fold them all at
  once. **On this day** resurfaces the highlights you made on today's date in past
  years, and the Home tab shows a single "on this day" highlight as a quote card
  (falling back to a random one). Each card shows when it was highlighted, with the
  full detail (time, chapter, colour, when it synced) on hover. **Export** copies
  your highlights as Markdown — per book or the whole set. This is the library-wide
  view of the same data you already see per book on each book's page. KOReader still
  owns your highlights, but you can now **delete** one from the web — handy for an
  accidental highlight you'd otherwise have to reopen the book to clear. Deleting
  here removes it everywhere: Tome records the deletion and your KOReader devices
  drop it on their next sync (a hover trash button with a quick confirm, on both the
  Highlights page and each book's Highlights & Notes panel).
- **Word counts for your books.** Tome now parses each EPUB's text to record its
  word count, shown in the **Details** panel on the book page. New uploads are
  counted automatically as they're added; CJK titles (Chinese / Japanese / Korean)
  are counted per character so they aren't undercounted. PDFs and comics don't have
  a word count. This is the groundwork for upcoming reading-speed and words-read
  stats. To fill in books added before this release, admins get a **Word Counts**
  tab under Admin with a one-click background backfill (it only reads your files —
  nothing on disk is changed — and shows live progress you can stop and resume).
- **Three reading-speed & length tiles on Reading Stats.** Now that Tome counts
  the words in your books, three new tiles turn that into insight: **Words Read**
  (your lifetime word count, broken out by year once you've finished books across
  more than one), **Reading Speed** (your true words-per-minute — words divided by
  the time KOReader actually recorded — with your fastest and slowest books, side
  by side on a wide tile), and **Book Length** (how long the books you finish tend
  to be, as a distribution with your average, median and longest). They're not on
  any board by default — add the ones you want from **Add tile** under Overview /
  Habits / Library.
- **A "Taste" tab on Reading Stats.** A fourth board next to Overview / Habits /
  Library, built from your book ratings: a **rating distribution** (how you spread
  your stars), **taste by genre** (your average rating per book type), your
  **highest & lowest rated** books, a **rating-vs-time** scatter (do you linger on
  the ones you rate higher?), **best-rated series**, and a **rating trend** over
  time. Like every other tile, each is add/move/resize/removable, and these ignore
  the date-range picker since ratings are all-time. Existing customised dashboards
  get the new tab appended without touching your current boards.
- **Five new Reading Stats tiles, available from the tile gallery.**
  **Lifetime Totals** (all-time hours / pages / books / streak), **Personal
  Records** (longest session, biggest reading day, most pages in a day),
  **Library Completion** (how much of what you own you've read, overall and per
  type), a **Reading Clock** (a 24-hour radial of when you read), and **Reading by
  Language**. They're not on any board by default — add the ones you want to any
  tab from **Add tile**.
- **Your stats now include reading from before TomeSync.** KOReader keeps its own
  per-page reading log (`statistics.sqlite3`) going back to whenever you started
  reading — often long before Tome existed. TomeSync can now import that history,
  backfilling your reading-time charts, streaks, heat-map, top books and pace with
  everything you read on the device. The first sync pushes your whole history
  (chunked and resumable, so it survives the device sleeping or dropping Wi-Fi);
  later syncs send only new reading. Turn it on in **TomeSync → Auto-sync reading
  history on launch**, or run it once from **TomeSync → Sync reading history**
  (also assignable to a gesture). It imports **reading time and pages only** — it
  never changes your read/unread status; that stays yours to set. Books are matched
  to your library automatically; anything it can't confidently match is left out
  rather than guessed. Requires TomeSync plugin build 22. (KOReader plugin semver
  1.6.0.)
- **Ratings set offline now sync.** A rating or review you set on KOReader while
  offline (or any time the server can't be reached) is now remembered and pushed
  to Tome the next time the device is online — on resume, on **Sync now**, or
  when you next close a book. Previously the rating was only sent when you opened
  or closed *that* book, so rating a book and never opening it again — the normal
  case for one you've just finished — could leave the rating stranded on the
  device. It now rides a small pending queue (like reading sessions do) that
  survives reboots. Requires TomeSync plugin build 21. (KOReader plugin semver
  1.5.1.)

### Changed
- **Send to KOReader is now on by default.** Queue a book from the web straight to
  your e-reader's TomeSync inbox — no email, no Amazon. It shipped as an off-by-
  default beta; now that it's had real-hardware time it's on out of the box
  (`TOME_SEND_TO_KOREADER`, still settable to `false` to disable).

### Fixed
- **KOReader no longer syncs one book's reading progress onto another.** When the
  plugin had to match a book by filename (e.g. after a file was moved or "Re-resolve
  all books" was used), a series whose name matched its first book's title — combined
  with a flat `{series} - 02 - {title}` download-naming template — could resolve every
  volume back to volume 1, so later volumes overwrote volume 1's position. The matcher
  now reads the volume number from all the filename shapes Tome produces, treats it as
  authoritative, and refuses to resolve (rather than guess wrong) when a filename is
  genuinely ambiguous.
- **TomeSync no longer gets stuck "offline" after your Kindle wakes up.** When the
  device slept and Wi-Fi dropped, three failed sync attempts in a row used to latch
  TomeSync into a permanent back-off — it then skipped every request and never
  recovered until KOReader was fully restarted, even once the network was back.
  Back-off is now time-based: it goes quiet for a minute, then quietly retries, and
  also clears the moment Wi-Fi reconnects, so reading sessions and positions resume
  syncing on their own. (Requires updating the KOReader plugin to build 23 via
  **TomeSync → Check for updates**.)
- **The Day-streak on the Home tab now matches your stats page.** After importing
  your KOReader reading history, the Home summary kept showing a shorter streak
  than the Stats page because it only counted live TomeSync sessions and ignored
  the imported page-stat days. Both now count reconciled reading, so a single,
  consistent streak shows everywhere.
- **A Home link in the book-page breadcrumb.** The breadcrumb root let you jump to
  the library but not back to the Home tab — and on a phone the lone house icon
  confusingly went to the library. It's now a proper root: **Home** then
  **Library**, both reachable (icon-only on mobile so neither is lost).

## [1.6.0] — 2026-06-21 — "Marginalia"

### Added
- **Filter your library by language.** The dashboard filter bar now has a
  **Language** dropdown alongside Series / Author / Tag / Format. It appears
  whenever your catalogue holds more than one language. Books carry messy
  language values from their embedded metadata (`en`, `eng`, `en-US`,
  `English` all mean the same thing) — Tome folds these to a single tidy entry
  ("English") so the dropdown stays clean. Because a Shelf just saves the active
  filters, you can save a per-language Shelf (e.g. one Shelf per language) and it
  populates itself — no manually adding books.
- **Your book ratings now sync with KOReader (both ways).** KOReader has its own
  native 1–5 star rating and review on the Book status screen — TomeSync now keeps
  it in step with Tome. Rate a book on the web and the next time you open it on the
  device (if it came over via TomeSync) the stars and review are written into the
  book, and rate it on KOReader and it flows back up to Tome when you close or
  suspend. A saved per-book baseline means only the side that actually changed is
  pushed; if both changed since the last sync, the web rating wins (Tome stays the
  single source of truth). Reading status (reading / finished) is untouched — that
  already syncs separately. Requires TomeSync plugin build 20. (KOReader plugin
  semver 1.5.0.)
- **Rate and review your books — and whole series.** Each book's detail page now
  has a 1–5 star rating and an optional review (auto-saved; collapses to a tidy
  quote with an edit affordance rather than an always-open box). Ratings are
  per-user and private to you. Your stars show on book cards across the library,
  and you can **sort by "My Rating"** and **filter** the grid (`Rated`, `3+`,
  `4+`, `5`). You can also rate a **series** as a whole from its page: a series
  rating is inherited by every volume you haven't rated individually (your own
  volume rating always wins), and a series' shown rating is your explicit rating
  if set, otherwise the average of your volume ratings — surfaced on series cards
  too. Stars use a theme-aware "rating gold" that fits each theme's palette. The
  "No Series" group can't be rated.
- **"Auto-fit height" toggle for list tiles on the Reading Stats dashboard.** Tiles
  like *Currently Reading* and *Reading Goals* can now size themselves to their
  content instead of a fixed height. Open a tile's config (gear icon in edit mode)
  and tick **Auto-fit height**: the tile then grows and shrinks to fit exactly how
  many items it holds — no half-empty box when you have one book in progress, and
  no need to resize by hand when you have a dozen. It's off by default, so existing
  tiles keep their manual size (and scroll); when on, the height handle is hidden
  (only width stays adjustable) and the tile carries a small "Auto" tag in edit
  mode, where it also previews its fitted height as you arrange the board. The book
  rows in *Currently Reading* are more compact, and the tile now shows a
  "No books in progress" placeholder instead of rendering blank when empty.
- **PDF books are now readable in the web reader.** Opening a PDF previously
  landed on "No readable file found" — the book detail page offered a **Read**
  button, but the reader only knew how to render EPUB and comics. PDFs now open
  in a proper in-browser reader (continuous scroll, rendered with pdf.js) that
  matches the rest of the app: light / sepia / dark page tint, fit-to-width or
  fit-to-height, zoom, keyboard navigation, and reading-progress tracking that
  syncs your position like the EPUB and comic readers. Large PDFs stay smooth —
  only the pages near the viewport are rendered, the rest are torn down to keep
  memory in check. (Being fixed-layout, PDFs don't reflow, so there's no
  font-size/font-family control as there is for EPUB.) (#61)
- **Shelved reading status.** A fourth reading state, set apart from
  Unread / Reading / Read, for books you've set aside without finishing.
  Shelving a book pulls it off Continue Reading, Series Progress, and the
  completion stats, but keeps your exact position (progress + CFI) so you can
  resume where you left off later — the middle ground between a stalled book
  cluttering your "reading" list and marking it Unread (which clears progress).
  A new **Shelved** library filter lists them, and reading the book again on
  any device moves it back to Reading automatically.
- **Undo on reading-status changes.** Changing a book's reading status now
  shows a toast with an **Undo** button (and lingers a little longer than a
  normal toast). Undo restores the full prior state — status, progress, and
  reading position — so an accidental tap on **Unread**, which clears your
  progress, is no longer a one-way trip.

### Fixed
- **Stats headline tiles no longer clip their numbers.** The small metric tiles
  on the Reading Stats dashboard (Reading Time, Sessions, Streak, …) now keep
  their value on a single line and on a shared baseline, so a long figure like a
  multi-hundred-hour reading time no longer wraps and gets cut off at the top,
  and the smaller "Longest: …" / "x of y started" captions no longer clip at the
  bottom. The "Completion Rate" and "Books Finished" tile titles were shortened
  to "Completion" and "Finished" so their headers fit without truncating.

## [1.5.1] — 2026-06-16

### Fixed
- **Private libraries now actually hide books.** Book visibility is gated solely
  by library membership: a book placed in a private library is hidden from
  everyone except the library's owner, its assigned users, and admins —
  regardless of who uploaded it. Previously every book uploaded by an admin was
  shown to all members and guests no matter which library it was in, so an admin
  who filed books into a private library still leaked their contents. Books that
  aren't in any library remain visible to everyone (a member's own unfiled
  uploads stay private to them); to restrict a book, place it in a private
  library. The rule is now applied consistently everywhere books surface — the
  library grid, the series and filter (facet) lists, single-book and series
  pages, OPDS, and the TomeSync (KOReader) series browser, which previously
  applied no visibility filter at all and exposed the entire catalogue. (#53)
- **Custom themes now apply to the stats charts.** The reading-stats widgets
  (activity heatmaps, progress rings, bars) and the card hover-glow read
  dedicated `--chart-accent` / `--accent-soft` CSS variables that a custom
  theme never set, so they kept rendering in the built-in coral accent no
  matter which palette you picked. Custom themes now derive both from the
  palette's primary colour, like the built-in themes do. (#55)

## [1.5.0] — 2026-06-13 — "Rubric"

### Added
- **Reading goals.** Set yourself a target — books per year or month, minutes
  or pages per day or week — and watch a progress ring fill as you read. Goals
  live on the stats dashboard in a new **Reading Goals** tile — one card per
  goal, all managed in place (add, edit and delete each goal right on its
  card; preset chips like the classic 12/24/52-books year challenge get you
  started) — and compact read-only rings appear on the Home tab. A goal can
  be scoped to a single book type, so "20 books this year" and "20 manga this
  year" count separately — no padding the year challenge with
  one-sitting volumes. Progress is computed from reading you already track
  (sessions from KOReader and the web reader, finished books), year and month
  goals show whether you're ahead of or behind pace, and reaching one drops a
  notification in the bell. Goals are per-user; nothing is shared.
- **Group by series in the library view.** A new toggle in the All Books
  toolbar collapses each series into a single stacked card — first volume's
  cover, a volume-count badge, and a subtle stacked-paper look — so one long
  manga run no longer drowns out the rest of the grid. Standalone books render
  as normal cards. Clicking a stack opens the series detail view (status badge,
  arcs, Continue Reading and all); clicking the series name on an individual
  book card still filters the grid as before. Active filters apply inside the
  stacks: if a filter matches only 2 of 15 volumes the badge shows 2, and
  series with no matching volumes disappear. The toggle is off by default and
  remembered per device. (#43)
- **KOReader plugin: opt-in WiFi auto-connect.** Some devices (notably
  PocketBook) sleep WiFi so aggressively that every TomeSync action just failed
  with "offline". A new **Settings → Auto-connect WiFi when needed** toggle
  lets KOReader re-establish the connection first (honouring your KOReader
  network prompt/auto setting) and then runs the action — browsing series,
  downloads, Sync now, Test connection, update checks, and the Inbox. Off by
  default: with the toggle off the plugin behaves exactly as before. Only
  user-initiated actions reconnect; background tracking never wakes the radio.
  (build 18 / 1.3.0, #38)
- **KOReader plugin: choose where downloads go.** A new **Settings → Download
  location & naming** option controls how the plugin files series downloads,
  inbox deliveries — everything. Three choices: the default layout
  (book-type/series folders, standalones under their author), **Flat in home
  folder** (every book lands directly in the home folder as
  "Series - NN - Title", so nothing nests), or a **custom template** built
  from tokens — `{book_type}` `{series}` `{volume}` `{volume:00}` `{title}`
  `{author}`, with `{Lower(...)}`/`{Upper(...)}` case modifiers and `/`
  starting a new folder, Sonarr-style. Empty tokens drop out cleanly (one
  template serves series books and standalones), every path segment is
  sanitized so a template can never escape the library folder, and templates
  are validated when saved with a preview of the resulting filename. The
  setting is per-device and stored in KOReader. Already-downloaded books are
  remembered by ID, so changing layout doesn't re-download your library.
  (build 19 / 1.4.0)

### Changed
- **Pick your own cover size.** The library's three fixed views (large grid,
  small grid, list) become two — grid and list — with a slider next to the
  view toggle that sets the cover size anywhere between dense and poster-sized.
  Columns flow to fit, card text scales with the size, and your old
  large/small preference migrates to the matching slider position. Layout
  changes animate: covers glide into their new positions when the grid
  reflows — including when you change filters or sorting — and the grid/list
  switch fades instead of hard-cutting.
- **A new look: oxblood, paper, and a proper display face.** Tome's violet
  accent is gone — the new identity is a deep bookbinding-leather red (oxblood
  in the light themes, a dusty rosewood in the dark ones) on warm paper
  neutrals, with headings set in Bricolage Grotesque. Smaller refinements ride
  along: the book-detail reading stats collapse from a wall of bordered
  mini-tiles into one quiet panel, section headers drop the ALL-CAPS treatment
  (the stats dashboard's tile titles and table headers included, so long labels
  like "Completion Rate" no longer clip — and dashboard tiles sit flat instead
  of floating on a shadow outside of edit mode),
  cover hover-tilt is subtler, the grid no longer re-runs its entrance
  animation on every filter change, and grid cards lose the repetitive
  book-type pill (list view keeps it). The series page's reading stats get the
  same flattened one-panel treatment as the book detail page, the Settings
  section headers move to the display face, the admin audit-log and sync badges
  trade the last of the violet for the new accent, and the book-detail delete
  confirmation no longer makes the toolbar buttons shift by a border's width.
- **One green, one blue, one amber.** Success, info, and warning colors across
  the app (audit-log badges, settings notices, upload states, toasts, sync
  dots, library-health panels and more) now come from three theme-aware tokens
  tuned to the new identity — dusty and low-chroma like the rosewood accent —
  instead of ~80 hardcoded Tailwind greens, blues, and ambers that each picked
  their own shade. The stats dashboard joins in: the Reading Pace charts trade
  their hardcoded green for the chart accent, and trend indicators and the
  100%-complete series bar use the semantic tokens. Book-type and file-format
  color labels keep their palette.
- The Wishlist page no longer repeats its own title above the list: the
  in-page section header now reads "Open (N)" (pairing with "Fulfilled"), and
  on an empty wishlist it disappears entirely — the empty state moves up and
  carries the "Learn more" link inline. The Fulfilled section also starts
  expanded instead of hiding its cards behind a collapsed row.
- **A calmer Home tab.** The four boxed stat chips become one quiet
  hairline-divided panel (icons intact, and a zero-day streak no longer leads
  the page), Continue Reading is ordered by when you last read instead of when
  the book was added, and reading-progress strips on grid covers get a minimum
  width so a just-started book shows a visible nub. The sidebar's collapse
  toggle moves from its own orphaned row down to the user footer, the Shelves
  section header only appears once you have shelves, and empty-library counts
  render muted.
- **Five themes, structured.** The theme lineup is now a neutral pair
  (Light/Dark), a warm pair (Amber and the new **Ember**, a cappuccino dark),
  and a new true-black **Black** theme for OLED screens. The pickers in
  Settings, the sidebar menu, and the login screen group them accordingly.
- **KOReader plugin: clearer menu.** The ambiguous in-book "Enabled (tap to
  disable)" entry is now "Tracking: on (tap to pause)" — it pauses automatic
  session tracking and syncing for the current KOReader run (it was never a
  permanent setting, and now says so). Persistent options and diagnostics
  (auto-connect, update checks, Test connection, Re-resolve all books) moved
  into a **Settings** submenu, so the in-book menu no longer spills onto a
  second page. The gesture-opened popup menu now shows toggle states and opens
  submenus instead of silently ignoring them. (build 18 / 1.3.0)
- **Reading Stats is now a fully customisable dashboard.** The page looks the
  same on day one — the default boards replicate the old layout one-to-one —
  but everything is now a tile on a drag-and-resize grid: hit **Edit** to
  rearrange, resize, duplicate, or remove any tile (with undo), and configure
  tiles individually — chart style (bar/line/area), per-tile timeframe, a
  pick-your-own-metric stat card, and a Series Spotlight that focuses on a
  series of your choice. Boards are per-user and saved on the server, so your
  layout follows you across devices. Tabs are boards too: create new ones
  empty, duplicated from the current board, from a built-in default, or
  imported from a file — and share a board by exporting it as JSON. A camera
  button saves any board as a PNG. List tiles (Currently Reading, Recent
  Sessions, and friends) size themselves to their content in view mode, so two
  in-progress books no longer rattle around a five-row tile. The widget gallery
  has 35 entries, including
  new ones the old page never had: a paginated session log on Overview, reading
  by weekday, time-of-day split, time by format, recently finished, and a
  monthly streak calendar.

### Fixed
- The Activity strip in the book detail page's reading-stats panel rendered as
  empty space — the bars were laid out into a zero-height container after the
  panel's redesign. The per-day bars are back.
- The stats dashboard no longer slides in from the side when the page opens.
  The widget grid's first paint was laid out for a hardcoded 1280px width and
  then animated every tile over to the real container size; it now measures
  the container before mounting, so the board appears in place.
- On phones the new stats dashboard squeezed every tile into a narrow column
  with dead space beside it: the default "A lot" side-padding setting applied
  its 16% gutters even on a 390px screen, and the time-range pills overflowed
  the header, making the whole page scroll sideways. Phones now always get a
  slim fixed gutter (the padding setting still applies from tablet width up)
  and the range pills wrap onto their own header line instead of spilling off
  the edge.
- The 365-day reading heatmap bucketed days in UTC, so for anyone east of
  Greenwich an evening session could light up the wrong day (and dent a streak's
  look). It now uses local dates, matching how every other chart counts days.
- The KOReader sync status in Settings could fail to load for accounts that had
  used both the TomeSync plugin and a legacy KOSync client — the two record the
  last-sync time in different formats and the page errored trying to compare
  them. TomeSync is now treated as the primary source and the status loads
  reliably.

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
