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

## OPDS PINs

OPDS PINs are short app-specific passwords for authenticating OPDS clients (KOReader, Panels, Chunky, etc.). Typing a full password on an e-ink keyboard is painful -- a 6-character PIN is much easier.

To set one up:

1. Go to **Settings > KOReader > OPDS PINs** and generate a new PIN.
2. In your OPDS client, enter your Tome username and the PIN as the password.
3. The OPDS feed URL is `http://<your-server>:8080/opds`.

Each PIN is independent -- you can have one per device and revoke any of them without affecting your main password or other devices. Your regular password continues to work alongside any PINs you've created.

---

## Themes

Tome ships with 9 themes:

- Light
- Dark
- Catppuccin Latte, Frappe, Macchiato, Mocha
- Nord
- Neon
- 8-bit

Switch themes in **Settings > Appearance**. The theme is stored per-browser, so different devices can use different themes.

---

## Series Browsing

The sidebar shows all series in your library. Click a series to open an inline detail panel with:

- Volume grid with cover thumbnails
- Per-volume progress bars
- Continue reading button (jumps to the next unfinished volume)
- Mark all as read

---

## Bulk Operations

Multi-select books on the dashboard to:

- Assign to a library
- Edit metadata (shared fields across selection)
- Fetch metadata from external sources
- Download as a ZIP archive

---

## Metadata Fetching

Search Google Books, OpenLibrary, and Hardcover for metadata. Results are shown in a side-by-side diff UI so you can see exactly what will change before applying. Hardcover results are prioritized when a `TOME_HARDCOVER_TOKEN` is configured.

---

## Cover Picker

Click any book's cover to open the cover picker. Search Google Books and OpenLibrary for alternative covers, or upload one from your device.

---

## Authentication and Permissions

- JWT-based auth with first-run setup wizard
- Admin user management with granular permissions per user
- Force password change on first login
- Admin impersonation (act as another user for debugging)

### Permission List

| Permission | Default | Description |
|---|---|---|
| Upload | off | Upload new books |
| Download | on | Download book files |
| Edit metadata | off | Modify book metadata |
| Delete books | off | Remove books from library |
| Manage libraries | off | Create/edit/delete libraries |
| Manage tags | off | Edit book tags |
| Manage series | off | Edit series assignments |
| Manage users | off | Admin: manage other users |
| Approve bindery | off | Review incoming books |
| View stats | on | Access reading statistics |
| Use OPDS | on | Access OPDS feed |
| Use KOSync | on | Sync reading positions |
| Share | off | Share books/lists |
| Bulk operations | off | Multi-select actions |
