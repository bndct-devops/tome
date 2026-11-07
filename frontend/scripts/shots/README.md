# Website screenshot scenes

Each `*.mjs` file here exports `scenes`, an array of:

```js
{
  name: 'home',                 // -> website/public/shots/<theme>/home.png; must match a <ThemedShot name=…>
  route: '/',                   // path on the showcase frontend
  viewport: 'desktop' | 'phone' | { width, height },   // default desktop 1600x1000; phone is 400x850
  settle: 1200,                 // ms to wait after networkidle (optional)
  before: async ({ page, api }) => {},  // runs before navigation: route mocks, data via `api('/books/…')`
  setup: async ({ page }) => {},        // runs on the page: open a dialog, hover, scroll `main`, press keys
  clip: async ({ page }) => page.locator('…'),  // crop to an element (48px padding desktop, 24px phone) or a box
  fullPage: true, maxHeight: 2400,              // or grow the viewport to the page's scroll height
}
```

Rules: showcase data only; never invent titles; a scene must work in all three themes
without changes; prefer real state over route mocks, and when a mock is unavoidable say
so in a comment. The shell scrolls inside `<main>`, not the window.
