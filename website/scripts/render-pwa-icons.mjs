// Renders the PWA install icons. iOS home screen (apple-touch-icon) gets the
// ink (black) tile so it pops against light wallpapers; desktop PWA install
// and Android (manifest icons) get the paper tile so the outline mark stays
// visible on dark browser sidebars. Both use the same outline mark — matches
// the homepage `Logo.astro` and `favicon.svg`.
//
// Source SVGs: scripts/icon-ink.svg + scripts/icon-paper.svg
// Re-run after editing either SVG.
import { chromium } from 'playwright'
import { writeFileSync, readFileSync } from 'node:fs'
import { resolve, join } from 'node:path'

const outDir = resolve(process.cwd(), '../frontend/public')

const targets = [
  // iOS home-screen icon — ink tile
  { name: 'apple-touch-icon.png', size: 192, svg: 'scripts/icon-ink.svg' },
  // Manifest icons (Android home screen, desktop PWA install, Arc sidebar) —
  // paper tile
  { name: 'pwa-192x192.png',        size: 192, svg: 'scripts/icon-paper.svg' },
  { name: 'pwa-512x512.png',        size: 512, svg: 'scripts/icon-paper.svg' },
  { name: 'pwa-512x512-maskable.png', size: 512, svg: 'scripts/icon-paper.svg' },
]

const browser = await chromium.launch()
for (const { name, size, svg: svgPath } of targets) {
  const svg = readFileSync(resolve(process.cwd(), svgPath), 'utf-8')
  const ctx = await browser.newContext({ viewport: { width: size, height: size }, deviceScaleFactor: 1 })
  const page = await ctx.newPage()
  await page.setContent(`<!doctype html><html><head><style>
    html,body { margin:0; padding:0; width:${size}px; height:${size}px; background:transparent }
    svg { display:block; width:${size}px; height:${size}px }
  </style></head><body>${svg}</body></html>`)
  await page.waitForTimeout(150)
  const png = await page.screenshot({ omitBackground: true, clip: { x: 0, y: 0, width: size, height: size } })
  writeFileSync(join(outDir, name), png)
  console.log(`OK ${name} ${size}x${size} ← ${svgPath}`)
  await ctx.close()
}
await browser.close()
