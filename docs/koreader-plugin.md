# TomeSync KOReader Plugin

Syncs reading progress and sessions between KOReader and your Tome library.

---

## Setup

1. Open Tome in your browser at `http://<your-server>:<port>/settings`
2. Scroll to the **KOReader** section
3. Click **Download Plugin**
4. Extract the ZIP to `koreader/plugins/` on your device so you have `koreader/plugins/tomesync.koplugin/main.lua`
5. Restart KOReader

The plugin is pre-configured with your server URL and API key. No manual configuration needed.

**Important:** Download the plugin from the same address your e-reader can reach. If your server is at `<your-server-ip>:8080`, open the settings page at that address before downloading.

---

## Features

- **Reading sync** -- position, progress, and reading sessions sync between KOReader and Tome's web reader
- **Series browser** -- browse your library's series and download entire series to your device in one tap
- **Offline-safe** -- everything works seamlessly when your server is unreachable; sessions queue and flush later
- **Context-aware menu** -- different options when a book is open vs. from the home screen

---

## How It Works

### Book Matching

When you open a book, the plugin tries to match the file to a book in your Tome library:

1. Checks its local cache for a known mapping
2. If not cached, sends the filename to `GET /api/tome-sync/resolve?filename=<name>`
3. The server matches by file path, title, and volume number (e.g. `Vol. 1` in the filename matches `series_index = 1`)
4. Once resolved, the mapping is cached locally so future opens are instant

Books downloaded via OPDS are matched automatically. Books sideloaded from other sources will also match as long as the filename contains the book title.

If no match is found, the plugin silently skips sync for that book.

### When It Syncs

| Event | What happens |
|---|---|
| **Open a book** | Pulls latest position from server. If the server is ahead, jumps to that position. |
| **Every 50 page turns** | Pushes current position to server (heartbeat). |
| **Close the lid (suspend)** | Pushes position and records a reading session. |
| **Open the lid (resume)** | Starts a new session, pushes position, and flushes any pending offline sessions. |
| **Close a book** | Pushes final position and records a reading session. |
| **"Sync now" in menu** | Manual push of current position and flushes pending offline sessions. |

### Web Reader → KOReader Sync

Progress saved in Tome's built-in web reader is written to `TomeSyncPosition` on the server. When you next open the same book in KOReader, the plugin detects the server is ahead and jumps to the position you left off at in the browser. This makes the web reader and KOReader fully bidirectional — you can switch between them without losing your place.

### Reading Sessions

Every time you close the lid or close a book, the plugin records a session with:

- Start and end timestamps
- Duration in seconds
- Progress at start and end (percentage)
- Number of page turns
- Device name

Sessions shorter than 10 seconds are filtered out. Each session has a unique ID to prevent duplicates.

These sessions power the **Stats** page in Tome, showing reading time, streaks, and charts.

### Reading Status

The server automatically updates the book's reading status based on progress:

- **unread** to **reading** when progress > 0%
- **reading** to **read** when progress >= 99%

### Series Download

The plugin can browse and download entire series directly to your device.

**From the home screen (no book open):**

1. Open the wrench menu, find **TomeSync > Browse series**
2. A list shows all series in your Tome library with book counts and authors
3. Tap a series to download all books in it

**From inside a book:**

- **Download full series** -- downloads every book in the current book's series
- **Download rest of series** -- downloads only books after your current volume (based on series_index)

**How downloads work:**

- Books are saved to `<download_dir>/<Series Name>/` (uses KOReader's configured download directory)
- Format preference: epub > kepub.epub > cbz > pdf > mobi > azw3
- Files that already exist on disk are skipped
- Downloaded books are automatically registered in the book map, so sync works immediately when you open them
- A summary shows downloaded/skipped/failed counts when finished

---

## Offline and Unreachable Server

The plugin is designed to work seamlessly when your server is not reachable -- whether you are on a plane, commuting without VPN access, or your server is simply down.

### What happens when you are offline

- **No WiFi at all:** Every sync request is skipped instantly. There is zero delay or freezing. You will not notice anything different while reading.
- **WiFi connected but server unreachable** (e.g. public WiFi without VPN): Requests time out after 5 seconds. After 3 consecutive failures, the plugin stops trying for the rest of the session. No further delays.
- **Reading sessions are saved locally:** If a session cannot be sent to the server (because you are offline when you close the lid), it is saved to disk and retried automatically the next time you open the lid with a working connection.

### Typical commute flow

1. You start reading on the train (no server access)
2. All sync silently skips -- reading is completely uninterrupted
3. You close the lid -- the session is saved locally on your device
4. You get home, open a book -- within the first 50 page turns, the plugin flushes all saved sessions to the server
5. Your Stats page and reading progress are up to date

Sessions also flush when you tap "Sync now" in the menu, or when WiFi reconnects and you resume reading.

### Limits

- Up to 50 sessions can be saved locally. If you go offline for an extremely long time, the oldest sessions are dropped to prevent unbounded storage use.
- Sessions saved locally survive KOReader restarts -- they are stored in KOReader's settings file.
- The backoff counter (3 failures before giving up) resets automatically on the next successful request. You can also reset it manually via **Test connection** in the plugin menu.

---

## Menu

The plugin menu is context-aware. It lives in the **wrench menu** under **TomeSync** and shows different options depending on whether a book is open.

### Always visible

| Option | Description |
|---|---|
| **Browse series** | Opens the series browser. Lists all series with book count and author. Tap to download. |
| **Test connection** | Verifies the server is reachable. Also resets the backoff counter if the server was previously unreachable. |
| **Re-resolve all books** | Wipes the local filename-to-book-ID cache. Use if a book matched incorrectly. |
| **About** | Version info. |

### Only when a book is open

| Option | Description |
|---|---|
| **Download full series** | Downloads all books in the current book's series. |
| **Download rest of series** | Downloads books after the current volume only. |
| **Sync now** | Pushes current position and flushes any pending offline sessions. |
| **Enabled / Disabled** | Toggle sync on or off. |
| **Pending sessions (N)** | Shows how many sessions are queued for sync. Tap for details. |

---

## API Keys

The plugin authenticates with an API key (`Authorization: Bearer tk_...`), not your login password. Keys are managed in Tome's settings page:

- A key is auto-created when you first download the plugin
- You can create additional keys or revoke existing ones
- Revoking a key immediately disables any plugin using it

---

## Updating the Plugin

The current plugin version is shown on the Settings page next to "TomeSync Plugin". Re-download when it changes, or when your server address changes.

### Via SSH (recommended for Kindle)

```bash
# 1. Download plugin ZIP from Tome Settings page, unzip it
# 2. Deploy in one command:
ssh root@<kindle-ip> "rm -rf /mnt/us/koreader/plugins/tomesync.koplugin" && \
scp -r tomesync.koplugin root@<kindle-ip>:/mnt/us/koreader/plugins/
# 3. Restart KOReader from its menu: Settings > Device > Restart KOReader
```

No cable or USB mode needed. Requires SSH enabled on KOReader (Settings > Network > SSH server).

### Via USB

1. Exit KOReader
2. Connect via USB cable
3. Replace `koreader/plugins/tomesync.koplugin/` with the new version
4. Eject and restart KOReader

The downloaded plugin has your server URL and API key baked in, so there is nothing to configure after extracting.

---

## Troubleshooting

**Plugin does not appear in the menu:**
- Make sure the folder structure is `koreader/plugins/tomesync.koplugin/main.lua` (not double-nested)
- Restart KOReader after installing
- Check `koreader/crash.log` for errors

**"Connection failed" on test:**
- Verify your server is running and reachable from the e-reader's network
- If you are on a VPN (e.g. Tailscale), make sure it is connected on the e-reader
- Re-download the plugin if your server address changed

**Wrong book matched:**
- Use "Clear book mappings" in the plugin menu, then re-open the book
- The resolve endpoint matches by title and volume number; ambiguous titles may match incorrectly

**Progress shows 0% on the web after syncing:**
- The plugin only syncs when the book is matched to a Tome book ID
- Check the plugin menu: if "Sync now" is greyed out, the book is not matched

**Stats page is empty:**
- Sessions are recorded when you close the lid or close a book. If you just started using the plugin, read for a while and close the lid to generate your first session.
- If you were previously using an older version of the plugin that only recorded sessions on book close, re-download the latest version. The new version records a session every time you close the lid.

**KOReader froze or became unresponsive:**
- Older versions of the plugin could freeze KOReader for up to 60 seconds per request when the server was unreachable. Re-download the latest version, which has a 5-second timeout and automatic backoff.

---

## Files

The plugin consists of two files:

| File | Purpose |
|---|---|
| `_meta.lua` | Plugin metadata (name, description) |
| `main.lua` | All plugin logic, HTTP client, and config (server URL, API key baked in at download time) |
