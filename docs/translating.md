# Translating Tome

Tome's web UI is translatable. English is the source language and lives inline
in the components; every other language is a community-maintained catalog under
`frontend/src/locales/<code>/messages.po`. Anything not yet translated falls
back to English, so a partial translation is fine to ship.

Out of scope for now: the KOReader plugin (KOReader's own gettext catalog is
project-wide, third-party plugins don't get their own), the website, OPDS feed
text, and server-generated messages (API errors, notification bodies).

## Improving an existing language

1. Open `frontend/src/locales/<code>/messages.po` in any text editor or a PO
   editor such as [Poedit](https://poedit.net/).
2. Fill in `msgstr` for entries that are empty (untranslated) or marked
   `#, fuzzy` (source changed since it was translated). Leave `msgid` alone.
3. Open a pull request. Nothing else is needed; catalogs are compiled at build
   time.

Rules of thumb:

- Keep placeholders exactly as they are: `{0}`, `{count}`, `{name}`. Reorder
  them freely to suit your grammar, never rename or drop them.
- Plurals use ICU syntax (`{count, plural, one {# book} other {# books}}`).
  Keep the `#`; add or remove plural categories as your language needs.
- Don't translate product names (Tome, KOReader, OPDS, Hardcover, Bindery),
  keyboard keys, or anything that is clearly an identifier.
- Match the tone of the English: plain, short, no exclamation marks.

## Adding a new language

1. Pick the BCP-47 code (`de`, `pt-BR`, `zh-TW`). Use the same casing as the
   examples: lowercase language, uppercase region.
2. Add it to `locales` in `frontend/lingui.config.ts` and to `LOCALES` in
   `frontend/src/lib/i18n.ts` (the label is the language's own name, e.g.
   `Deutsch`, shown untranslated in the picker).
3. Run `npm run i18n:extract` in `frontend/`. This creates
   `src/locales/<code>/messages.po` with every source string and empty
   `msgstr`s.
4. Translate, then open a pull request. Please mention in the PR whether you
   are happy to be pinged for future updates to that language.

## For developers: keeping strings translatable

- JSX text: `<Trans>Add to library</Trans>` (from `@lingui/react/macro`).
- Anything outside JSX (toasts, attributes, `confirm()`): `const { t } = useLingui()` then `` t`Saved` ``.
- Interpolate with template variables, never concatenate: `` t`${n} books selected` ``.
- Plurals: `<Plural value={n} one="# book" other="# books" />`.
- Label maps outside components: `` msg`Unread` `` descriptors, rendered with `i18n._()`.
- After adding or changing strings run `npm run i18n:extract` and commit the
  updated `src/locales/*/messages.po`. CI fails on stale catalogs.
- `npm run lint` warns on user-facing strings that bypass Lingui
  (`lingui/no-unlocalized-strings`).
