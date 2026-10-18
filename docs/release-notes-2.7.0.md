# v2.7.0 "Catalogue"

A catalogue is the library's record of what it holds and where each copy
sits. This release gives Tome one: it now knows which books are on each of
your e-readers, finds whole series when you search, and keeps track of every
browser and phone signed in to your account. It also stays signed in until
you sign out, and opens a lot faster.

## Highlights

**See which books are on your e-reader.** The KOReader plugin now tells Tome
which of its books are on the device, and books show an "On Kindle" pill
(named after the device) on cards and the book page. The filters get a row
per device with "On Kindle" and "Not on Kindle", which can be saved as a
shelf and also work in shelves browsed on the device, and series pages show
how many volumes each device holds. The list is replaced on every sync, so
books deleted on the device drop out on their own. Settings > KOReader lists
your devices with a way to forget one. Plugin build 50.

<img src="https://raw.githubusercontent.com/bndct-devops/tome/main/docs/screenshots/device-pills.png" alt="Library grid with On Kindle pills under the Berserk volumes">

**Stay signed in.** Signing in in a browser used to last 7 days. Now a
browser stays signed in until you sign out there, like the Tome app. Every
browser and phone is listed under Settings > Connected devices ("This
browser" marks the one you are using), where you can sign any of them out,
such as a lost laptop. A browser that is not used for 90 days signs itself
out (`TOME_WEB_SESSION_IDLE_DAYS`), and changing your password signs out
everything else.

**Search finds series.** When a search matches a series name, the series
appear as cards in a row above the matching books, with author, volume count
and your progress. A half-typed search is enough: each word only has to start
a word of the name.

**Faster everywhere.** Grids load small cached WebP covers instead of
full-size images, pages open with their data already requested while the app
loads, going back to the library keeps your scroll position, the app's main
file is a fifth of its old size, and server responses are compressed. Large
libraries also get new database indexes.

**KOReader keeps reading when the server is down.** The plugin no longer
holds the screen for every request when Tome does not answer: it backs off
after the first miss and opening a book no longer waits on the network.
There is also an optional fallback server URL, for example your LAN address
for when the main URL only works from outside. Plugin build 49.

## Also in this release

- Settings has a jump list: a sticky list of sections on desktop, a row of
  chips on phones.
- Google Books "503" bursts get one retry instead of failing the fetch, and
  the failed-fetch log line no longer contains the request URL with your API
  key (#247).
- "Connect a phone" works when Tome is opened at localhost (#253).
- The Filters button no longer shows a "0" badge during a search, and Home
  no longer jumps down after loading.

## Security

- A password set by an admin for another user was written to the audit log
  in plain text. New entries only record that the password changed. If you
  have set passwords this way, consider clearing old "users.updated" entries
  from the audit log.
- The reader's file links (EPUB, PDF, comics) now refuse a signed-out
  device and a disabled account.

## KOReader plugin

Ships build **50 (1.19.0)**: the device list behind the "On Kindle" pills
(build 50) and the no-freeze back-off with the fallback URL (build 49).
Update in-app via TomeSync > Settings > "Check for updates", or let the
auto-check on launch pick it up. No breaking changes; older builds keep
syncing, they just do not report their books or use the fallback URL.

## Upgrade

```
docker pull ghcr.io/bndct-devops/tome:latest && docker compose up -d
```

No configuration changes needed. New database indexes and one new column are
added on the first start. Browsers stay on their old 7-day sign-in until it
runs out (or until you sign out and in again), then stay signed in. Set
`TOME_WEB_SESSION_IDLE_DAYS=0` if browsers should never sign out on their
own. KOReader: the "On Kindle" pills need TomeSync build 50 - your plugin
self-updates (Settings > Check for updates, or enable auto-check on launch).

---

## Full changelog

### Added
- **Searching for a series shows the series.** When a search matches a
  series name ("good guys" finds The Good Guys), the series appear as cards
  in a row above the matching books, with author, volume count and reading
  progress, and open the series page on click. Each word of the search only
  has to start a word of the name, so a half-typed search already finds it.
- **Fallback server URL for the KOReader plugin.** TomeSync > Settings has
  an optional second address for the same server, tried when the main one
  does not answer: for example the LAN address at home when the main URL
  only works from outside (a router without hairpin NAT, a VPN address).
  Whichever address answered last is tried first, so after a network change
  the plugin switches once and sticks with it. (#249)
- **See which books are on your e-reader.** The KOReader plugin now tells
  Tome which of its books are on the device, and books show an "On Kindle"
  pill (named after the device) on cards and the book page. The filters get
  a row per device with "On Kindle" and "Not on Kindle", which can be saved
  as a shelf and also work in shelves browsed on the device; series pages
  show how many volumes each device holds and mark them on the covers.
  The list is sent on launch, wake, WiFi up, Sync now and after downloads,
  and replaces the previous one, so books deleted on the device drop out on
  the next sync. A device that has not synced in a few days is shown muted
  with its age, and Settings > KOReader lists your devices with a way to
  forget one. Only your own devices are ever shown. Plugin build 50.

### Fixed
- **Home no longer jumps down after it has loaded.** For admins who had not
  answered the telemetry question yet, its card arrived a moment after the
  rest of the page and pushed everything down. Home now waits for it and
  draws it together with the other sections. (#254)
- **"Connect a phone" works when Tome is open at localhost.** The QR code
  carried the address the page was opened at, so from `localhost` it told
  the phone to connect to itself and pairing could never succeed. Opened at
  localhost, 127.x or ::1, the window now says so and asks for the address
  the phone can reach this computer at (for example
  `http://192.168.1.20:8080`), builds the QR from that and remembers it in
  this browser. Other addresses work as before. (#253)
- **The Filters button no longer shows a "0" badge during a search.** A
  search on its own counted as "filters active" but has no filter chip, so
  the badge read 0; it now only appears when a filter is actually set.
- **The KOReader plugin no longer freezes the reader when the server is
  down.** When the device was online but Tome did not answer, every sync
  request held the screen for its 5 second timeout, and it took three
  misses before the plugin backed off, so startup and opening a book each
  stalled. It now backs off after the first miss, waits 1, 2, 4, 8 and then
  15 minutes between tries, and goes back to normal as soon as a request
  gets through, WiFi reconnects or you use a TomeSync action such as Sync
  now. Opening a book no longer waits on the network at all. Reading
  offline was never affected. Plugin build 49. (#249)
- **Google Books 503s no longer fail the fetch outright.** Google answers in
  bursts of "503 Service Unavailable", and Tome gave up on the first one, so
  the search had to be clicked again by hand. A 5xx from any metadata source
  now gets the same single retry after a short pause that a rate limit
  already got, and only then reports the source as errored. The warning
  logged on a failed fetch also no longer includes the request URL, which
  for Google Books carried the configured API key. (#247)

### Changed
- **The web app stays signed in until you sign out.** Signing in in a
  browser used to last 7 days (`TOME_JWT_EXPIRE_MINUTES`), after which you
  had to sign in again. A browser now stays signed in like the Tome app
  does, until you sign out there. Each browser is listed under Settings >
  Connected devices with its browser and system (for example Safari on
  macOS, marked "This browser" on the one you are using), where you can
  sign out any of them, such as a lost laptop. A browser that is not used for
  90 days is signed out on its own (`TOME_WEB_SESSION_IDLE_DAYS`, 0 = never),
  so a forgotten or copied sign-in does not live forever; using Tome even
  once in that time keeps it. The Tome app is not affected. Changing your
  password signs out every other browser and app; an admin setting a user's
  password signs that user out everywhere. Browsers signed in before this
  update keep their old sign-in until it runs out, then stay signed in from
  their next sign-in.
- **Settings has a jump list.** A sticky list of the page's sections sits on
  the left at desktop width, and a row of chips under the header on phones.
  It follows your scroll position and jumps to a section on click, so a
  setting is one click away instead of a scroll through the whole page.
- **Covers load much faster.** Grids and lists now get small WebP copies of
  the covers (made once and kept in `data/cover_thumbs/`) instead of the
  full-size image, which for covers fetched from a metadata source could be
  over 2 MB. The browser keeps them, so coming back to the library shows the
  covers right away without asking the server again, and a cover the
  browser already has appears at once instead of fading in. Changing a
  cover still shows the new one everywhere, also when you upload a second
  cover over the first. Covers further down the library are loaded in the
  background while you look at the top (and the first ones while you are on
  Home, so switching to All Books shows them at once), and the next page of books is
  fetched well before you reach the end, so scrolling down no longer shows
  covers popping in, and Safari clearing its cache no longer drops them.
  (#254)
- **Tome opens with the page already filled in.** Tome remembers what each
  page asked the server for when it opened, and on the next visit asks for
  it right away, while the app itself is still loading, instead of one step
  after another. The loading logo stays up until the page has its data (at
  most a second longer) and then fades out over the finished page, so a
  reload goes straight from the logo to the page instead of through a
  spinner and grey placeholders. (#254)
- **Going back to the library keeps your place.** Coming back from a book
  (or Stats, Settings, anywhere) shows the library as you left it, scrolled
  to where you were, also past the first 60 books, and refreshes it quietly
  instead of blanking it. Home works the same way: no grey placeholders on
  the way back. The library tab no longer loads Home's sections it does not
  show. (#255)
- **Tome loads less up front.** The app's main file is a fifth of its old
  size (515 KB instead of 2.4 MB). Stats, Settings, Admin, Bindery and the
  other pages load as their own pieces, fetched in the background once Tome
  is open, and the full icon set for libraries and shelves loads only when
  an icon outside the common ones is shown or the icon picker opens. (#255)
- **Smaller downloads from the server.** The server now compresses its
  responses (the book list goes from 55 KB to 6 KB), never book files or
  images. The app's files are cached by the browser for good, since their
  names change with every release, and the page itself is always checked,
  so an update never leaves a browser on the old version. (#255)
- **Faster library pages on big libraries.** New database indexes for the
  book list, series, reading status and session queries (added on startup),
  and the series overview reads all series in one query instead of one per
  series. (#255)

### Security
- **A password set by an admin is no longer written to the audit log.** The
  "user updated" entry recorded the request as sent, so a password an admin
  set for a user was stored there in plain text. It now records only that
  the password was changed. Admins who have set passwords this way may want
  to clear old entries from the audit log.
- **The reader's file links now honour a signed-out device.** The links the
  web reader uses to open EPUB, PDF and comic files checked the token's
  signature but not whether that device had been revoked, and the EPUB link
  did not check for a disabled account.
