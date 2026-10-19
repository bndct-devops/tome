# v2.7.1

Fixes a KOReader crash when downloading a single book that has no book type.

## Fixed

**Downloading a single book without a book type no longer crashes
KOReader.** Tapping one such book in the TomeSync series browser, or
downloading from an author or search list, crashed the reader. On a Kindle
it also lost unsaved settings, such as a device that had just been signed
in. Tome now sends a type for every book: the series' type for a volume of
a series, "book" otherwise. An untyped volume downloads into the same
folder as the rest of its series. Thanks to @kastro5 for the report and a
precise diagnosis. (#259)

## Upgrade

```
docker pull ghcr.io/bndct-devops/tome:latest && docker compose up -d
```

No database or configuration changes. Updating Tome is enough: plugins
already on your devices stop crashing as soon as the server is updated.
TomeSync build 51 (1.19.1) adds the same safeguard on the device and
arrives through the usual self-update (Settings > Check for updates, or
auto-check on launch); it is not required.

---

**Full Changelog**: https://github.com/bndct-devops/tome/compare/v2.7.0...v2.7.1
