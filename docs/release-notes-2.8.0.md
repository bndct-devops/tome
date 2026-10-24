# v2.8.0 "Folio"

A folio is a sheet folded once, and the shelf size libraries sort books by.
This release lets each book type choose its folder and layout on disk.

## Highlights

**Folder and layout per book type.** Each type (Admin > Types) can get its
own folder under the library root, such as `Manga/` or `Comics/`, and its
own layout: series first (the default so far), author then series, author
only, or a template like `{author}/{title}[ ({year})]{ext}` with a live
preview. Nothing moves on upgrade or on save. Changed books show as
misplaced in Library Health, and Reorganize moves them when you say so. A
scan takes a matching top-level folder as a type hint. (#270, #276)

**Secrets from files.** `TOME_SECRET_KEY`, `TOME_HARDCOVER_TOKEN`,
`TOME_GOOGLE_BOOKS_KEY`, `TOME_SMTP_USER`, `TOME_SMTP_PASSWORD` and
`TOME_OIDC_CLIENT_SECRET` accept a `_FILE` variant for Docker and
Kubernetes secrets. (#267)

**French.** The web UI is available in French, contributed by llaumgui.
(#272)

## Also

- The update check runs once a day in the background against
  `upgrades.bndct.dev`, sending only the version number.
  `TOME_UPDATE_CHECK=false` still turns it off.
- Send to Device works with SMTP servers without authentication. (#271)
- Unknown `/api` and `/opds` paths return a plain 404 instead of the web
  page.
- Settings > About lists GitHub Sponsors supporters.

## Upgrade

```
docker pull ghcr.io/bndct-devops/tome:latest && docker compose up -d
```

No configuration changes. Three new book-type columns are added on first
start; no files move until you set a folder or layout and run Reorganize.
KOReader: a sign-in made with a code now survives a crash with TomeSync
build 52 - your plugin self-updates (Settings > Check for updates, or enable
auto-check on launch). (#259)

---

## Full changelog

### Added
- **Supporters on the About page.** Settings > About lists the people who
  sponsor Tome on GitHub Sponsors. The repo also shows a Sponsor button.
  Sponsoring is a thank-you; nothing in Tome is locked behind it.
- **Secrets can be read from files.** `TOME_SECRET_KEY`, `TOME_HARDCOVER_TOKEN`,
  `TOME_GOOGLE_BOOKS_KEY`, `TOME_SMTP_USER`, `TOME_SMTP_PASSWORD` and
  `TOME_OIDC_CLIENT_SECRET` each accept a `_FILE` variant naming a file that
  holds the value, e.g. `TOME_SECRET_KEY_FILE=/run/secrets/tome_secret_key`,
  for Docker and Kubernetes secrets. Setting both the variable and its `_FILE`
  variant is an error. (#267)
- **French translation.** The web UI is available in French (Settings →
  Appearance → Language). Machine-translated first pass, corrected in the
  running UI by the contributor; wording fixes are welcome as pull requests.
  Contributed by llaumgui. (#272)
- **A folder per book type.** Each book type can have a folder (Admin → Types),
  so books of that type are stored under it, e.g. `Manga/Berserk/…` or
  `Comics/Saga/…`. Empty by default, so nothing moves on upgrade. Existing
  books move only when you run Reorganize in Library Health; a library scan
  takes a matching top-level folder as a hint for the book type, so a plain
  CBZ under your own `Manga/` stays manga. (#270)
- **A layout per book type.** Each book type picks how its books are laid
  out (Admin → Types): Series first (the layout so far, still the default),
  Author, then series (`Harlan Coben/Myron Bolitar/Deal Breaker v01.epub`), or
  Author only, each with an example from your own books. Advanced takes a
  template such as `{author}/{title}[ ({year})]{ext}`, with a live preview.
  Saving never moves files: changed books show as misplaced in Library Health,
  and Dry Run All / Reorganize All moves them. (#276)

### Changed
- **The update check asks Tome's own endpoint.** Tome now checks
  `upgrades.bndct.dev` once a day for a newer version, in the background
  instead of only when an admin opens Settings. The request carries the
  version number and nothing else. `TOME_UPDATE_CHECK=false` turns it off,
  as before.

### Fixed
- **A KOReader crash no longer undoes a fresh sign-in.** The plugin kept a
  new pairing (Sign in with code), a changed server URL and a connection
  reset in memory until KOReader next closed normally, so a crash before
  that lost them and the device asked to sign in again. They are now
  written to disk right away. Plugin build 52. (#259)
- **Unknown API addresses answer "not found".** A request to an address
  under `/api` or `/opds` that does not exist used to get the web app's page
  back (or "method not allowed" for anything but a read). It now gets a
  plain 404, so the KOReader plugin and other clients can tell that a server
  does not have a feature yet instead of choking on a web page.
- **Send to Device works with SMTP servers that need no authentication.**
  `TOME_SMTP_USER` and `TOME_SMTP_PASSWORD` may both be left empty; Tome
  then skips the login step. `TOME_SMTP_FROM` is required in that case, so
  the sender address is never blank. (#271)
