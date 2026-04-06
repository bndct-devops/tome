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

Tome ships with three built-in themes:

- Light
- Dark
- Amber

Switch themes in **Settings > Appearance**. The theme is stored per-browser, so different devices can use different themes.

### Custom Themes

You can create a fully custom theme by pasting 10 comma-separated hex color values in **Settings > Appearance > Custom Theme**. The 10 values map to the theme's color palette in order. Custom themes are stored per-browser alongside your theme preference.

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

---

## Shelves

Shelves (formerly called Saved Filters) let you save any combination of active dashboard filters — search text, book type, library, series, tags, sort order — as a named entry in the sidebar. Click a shelf to instantly restore that view.

Shelves are per-user and private. Each shelf can have a custom icon chosen from the icon picker.

---

## Reading Stats

The Stats page has two tabs:

### Overview

Session history, reading streaks, total pages/time, and time-of-day activity heatmap powered by KOReader session data via TomeSync.

### Insights

Deeper analysis of your reading patterns:

- **Completion estimates** — projected finish dates for books currently in progress, based on your recent reading pace
- **Year in review** — summary of books read, pages turned, and time spent in a given year
- **Period comparison** — compare your reading activity across two time periods (e.g. this month vs last month)
- **Reading speed trend** — how your pages-per-hour has changed over time
