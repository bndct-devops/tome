# Docs pipeline — showcase stack + screenshots

Companion to `docs-outline.md`. Read this first if you're a fresh session
about to write any docs page or capture screenshots.

---

## Showcase stack (separate Tome instance with fake data)

The showcase is a fully isolated second Tome instance — separate DB,
separate library folder, separate ports — built from scripts. Used so screenshots
look like a real, populated library (sessions, streaks, currently-reading,
series) without leaking your actual reading history.

| | Showcase | Normal dev |
|---|---|---|
| Frontend | http://localhost:**5174** | http://localhost:5173 |
| Backend  | http://localhost:**8090** | http://localhost:8080 |
| Data dir | `data/showcase/` | `data/` |
| Library  | `library/showcase/` | `library/` |
| Bindery  | `bindery/showcase/` | `bindery/` |
| Login    | `benedict / showcase` | (your normal account) |

Both can run **at the same time** — different ports, different volumes.

### Start the showcase

```bash
./scripts/run-showcase.sh
```

That script:
1. If `data/showcase/tome.db` doesn't exist → runs `scripts/seed_showcase.py` first
2. Boots backend on `:8090` with `TOME_DATA_DIR=./data/showcase` etc.
3. Boots Vite frontend on `:5174` with `VITE_API_TARGET=http://localhost:8090` so its proxy points at the showcase backend
4. Prints login creds, traps Ctrl-C to stop both

### Re-seed when something changes

```bash
source .venv/bin/activate
python scripts/seed_showcase.py
```

The seed is **destructive** — it nukes `data/showcase/` and rebuilds from
scratch. After re-seeding, **book IDs will shift** (Frankenstein might go from
id 62 → 37). The screenshot script handles this via `resolveBookIds()` (looks
up by title at startup), but any **hardcoded URL in a doc page** referring to
`/books/42` will break. Use stable identifiers (series names in URLs, titles
for resolution) wherever possible.

### What's in the showcase

(Inspect `scripts/seed_showcase.py` for the full list.)

- **~41 real-ish books with covers** (covers pulled from prod / Hardcover /
  Open Library / Wikimedia and stored in `docs/seed/covers/`)
- **6 series**: Berserk (10 vols, with arcs Black Swordsman + Golden Age),
  Vinland Saga (3), Frieren (3), One Piece (10), Good Guys (3 real + 13 placeholder),
  Bad Guys (3 real + 8 placeholder)
- **4 standalone fiction** + 4 western comics + **Frankenstein** (Project
  Gutenberg, the only real readable EPUB in the set — used for reader
  screenshots)
- **~466 reading sessions** spanning ~47 days with a 47-day streak, generated
  with weekend bias + late-night skew + one binge weekend
- **2 currently reading**: Dungeon Mauling (67.4%) + Project Hail Mary (42%)
- Stalled books exist but have **no** status row (avoids polluting
  "currently reading")

### Gotchas

- `progress_pct` is stored as a **0–1 fraction**, not 0–100. `0.674` not
  `67.4`. (BookDetailPage multiplies by 100 for display.) Don't reintroduce
  the percent-vs-fraction bug.
- If you need to tweak showcase data and the change is small, **hit the API
  directly with the admin token** rather than editing + re-running the seed
  script. Saves time.
- Frankenstein's reader auto-tracks progress. The screenshot script's `cleanup`
  resets it to `unread` after each reader screenshot — don't remove that or
  the showcase will drift.

---

## Screenshot pipeline

Lives at `frontend/scripts/screenshots.mjs`. Driven by Playwright (headless
Chromium). Output → `docs/screenshots/` (for the README) **and** can be copied
into `website/public/shots/{theme}/` for use on the marketing/docs site.

### Run it

```bash
# Against the showcase stack (preferred — themed, populated, deterministic):
cd frontend
npm run screenshots:showcase                                       # light theme
TOME_SCREENSHOT_THEME=dark   npm run screenshots:showcase          # dark theme
TOME_SCREENSHOT_THEME=amber  npm run screenshots:showcase          # amber theme
```

The showcase stack must be running first (`./scripts/run-showcase.sh` in
another terminal).

### Filter to specific shots

```bash
TOME_SCREENSHOT_ONLY=stats,book-detail npm run screenshots:showcase
```

### Capture all 3 themes at once

```bash
for theme in light dark amber; do
  TOME_SCREENSHOT_THEME=$theme npm run screenshots:showcase
done
```

Pair with a copy step into `website/public/shots/$theme/` (the site reads from
there via the `<ThemedShot>` component which auto-swaps based on active
theme).

### The shot definition format

Each entry in the `SHOTS` array is one screenshot:

```js
{
  name: 'stats-habits-heatmap',         // → docs/screenshots/stats-habits-heatmap.png
  path: '/stats',                       // route to navigate to (string OR function)
  viewport: DESKTOP,                    // or mobile: true (iPhone 13 profile)
  waitFor: 'h2, [class*="streak"]',     // CSS selector to await before screenshot
  settle: 1200,                         // extra ms to wait after waitFor
  clip: { x: 0, y: 720, width: 1600, height: 600 },  // crop to a region
  prefs: { tome_sidebar: 'closed' },    // localStorage prefs to set before page load
  theme: 'dark',                        // hardcode theme (overrides env)
  syncReaderTheme: true,                // mirror app theme into reader_theme pref
  after: async (page) => { /* click tab, hover, scroll, etc. */ },
  cleanup: async (token, api) => { /* reset state after this shot */ },
}
```

**Dynamic paths** (when the URL depends on a runtime-resolved book ID):

```js
path: () => `/reader/${bookIds.frankenstein ?? 1}`,
```

`bookIds` is populated by `resolveBookIds()` at script startup — looks up
books by title against the API. To add a new resolvable book, add it to the
`wanted` map in `resolveBookIds()`.

### Capturing specific stats charts (the typical docs workflow)

The stats page is a long scroll with many charts. To capture one chart in
isolation, use `clip` to crop:

1. Open the stats page in the showcase (`http://localhost:5174/stats`)
2. Open devtools, find the chart's bounding box
3. Add a shot with `clip: { x, y, width, height }` (coordinates are in CSS
   pixels at the viewport scale)
4. For tabs other than Overview, use `after` to click the tab first:
   ```js
   after: async (page) => {
     await page.locator('button:has-text("Habits")').click()
     await page.waitForTimeout(400)
   }
   ```

### Adding interactions

`after` runs after navigation + load. Use it to:
- Click a tab (`page.locator('button:has-text("Habits")').click()`)
- Open a modal (`page.locator('[aria-label="Open settings"]').click()`)
- Scroll to an element (`page.locator('#some-chart').scrollIntoViewIfNeeded()`)
- Hover for tooltip (`page.locator('[data-cell]').first().hover()`)
- Turn pages in the reader (see the `mobile-reader` shot — uses
  `page.touchscreen.tap()`)

### Setting localStorage state

Pass `prefs: { key: value }` and the script sets those keys via
`addInitScript` before page load. Useful for:
- Closing the sidebar: `tome_sidebar: 'closed'`
- Reader theme: `reader_theme: 'sepia'`
- Reader font: `reader_font_size: '18'`
- Any other UI state stored in localStorage

### Auth

The script logs in as `benedict / showcase` (or `TOME_SCREENSHOT_USER` /
`TOME_SCREENSHOT_PASS`) and stores the JWT in `localStorage.tome_token` via
`addInitScript`. To skip login (e.g., reuse a token across runs), pass
`TOME_SCREENSHOT_TOKEN=<jwt-or-api-token>`.

### Output

Currently writes to `docs/screenshots/{name}.png` (paths resolved relative to
the repo root, not cwd). For the marketing site we'll either:
1. Run the script 3× with different `TOME_SCREENSHOT_THEME`, then copy/move
   into `website/public/shots/{theme}/`, OR
2. Add a `--out` flag to the script so it writes directly into the website's
   public dir per theme.

Option 2 is cleaner — worth a small refactor if we're going to do this a lot.

### When a shot looks wrong

Common causes:
- **Network not idle yet** → bump `settle`
- **Element not rendered** → add `waitFor: '<selector>'`
- **Empty/stale data** → re-seed the showcase
- **Book ID hardcoded somewhere** → use the `bookIds` resolver instead
- **Theme not applied** → check that `addInitScript` ran before navigation
  (it should — but verify with `console.log` in `after`)

---

## Putting it all together — typical workflow

For each docs page that needs screenshots:

1. **Pick the shots** from `docs-outline.md`
2. **Boot the showcase** in terminal A: `./scripts/run-showcase.sh`
3. **Add shot definitions** to `SHOTS` in `frontend/scripts/screenshots.mjs`
4. **Iterate**: `TOME_SCREENSHOT_ONLY=new-shot-name npm run screenshots:showcase`
   in terminal B until the shot looks right
5. **Re-run for all themes**:
   ```bash
   for theme in light dark amber; do
     TOME_SCREENSHOT_THEME=$theme TOME_SCREENSHOT_ONLY=new-shot-name \
       npm run screenshots:showcase
   done
   ```
6. **Move into website**: `cp docs/screenshots/new-shot-name.png
   website/public/shots/$theme/` (or wire it once via the option 2 refactor
   above)
7. **Reference in the docs page** via `<ThemedShot>`:
   ```astro
   <ThemedShot name="new-shot-name" alt="Description of the shot" />
   ```
   `ThemedShot` auto-swaps the source when the user toggles theme.

---

## File locations cheat-sheet

```
scripts/
├── seed_showcase.py          # Builds data/showcase/ from scratch
├── run-showcase.sh           # Starts showcase backend + frontend
└── fetch_seed_covers.py      # One-shot: pulls cover images into docs/seed/covers/

docs/
├── seed/
│   ├── covers/               # 40 cover JPGs (committed)
│   └── library/
│       └── frankenstein.epub # Project Gutenberg, committed
└── screenshots/              # Default output for screenshots.mjs

frontend/
├── scripts/screenshots.mjs   # The Playwright runner
├── vite.config.ts            # Env-driven proxy (respects VITE_API_TARGET, VITE_PORT)
└── package.json              # npm scripts: screenshots, screenshots:showcase

website/
├── docs-outline.md           # ← Read first for what to write
├── docs-pipeline.md          # ← This file
├── public/shots/{theme}/     # Where the site loads themed screenshots from
└── src/
    ├── components/
    │   ├── docs-nav.ts       # The docs sidebar / prev-next source of truth
    │   ├── ThemedShot.tsx    # Auto-swaps shot based on active theme
    │   ├── Callout.astro     # info / tip / warning / danger / note
    │   ├── Steps.astro
    │   ├── Tabs.tsx          # Takes { label, code: string }[]
    │   ├── Mermaid.astro     # Lazy-loads from CDN, re-renders on theme change
    │   ├── Kbd.astro
    │   ├── SectionHero.astro
    │   └── DocsMeta.astro
    ├── layouts/DocsLayout.astro
    └── pages/docs/*.astro    # Where the actual articles live
```

---

## Things a fresh session should NOT do

- Don't manually edit `data/showcase/tome.db` for one-off fixes — go through
  the API with the admin token, or rebuild via `seed_showcase.py`.
- Don't push to `origin` without explicit ask. Local commits only.
- Don't hardcode book IDs in shot paths — always go through `bookIds`.
- Don't break the showcase by re-seeding while the screenshot script is
  running.
- Don't add `progress_pct: 67.4` style values — it's a 0–1 fraction.

---

_Last updated: 2026-05-22_
