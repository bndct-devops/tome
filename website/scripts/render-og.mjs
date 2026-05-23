// Renders public/og.png from the hidden /og Astro page at exact OG dims.
// Usage: node scripts/render-og.mjs   (requires the dev server on :4321)
import { chromium } from 'playwright'
import { existsSync, mkdirSync } from 'node:fs'
import { resolve, dirname } from 'node:path'

const url = process.argv[2] || 'http://localhost:4321/og'
const out = resolve(process.cwd(), 'public/og.png')

mkdirSync(dirname(out), { recursive: true })

const browser = await chromium.launch()
const ctx = await browser.newContext({
  viewport: { width: 1200, height: 630 },
  deviceScaleFactor: 2,
})
const page = await ctx.newPage()
await page.goto(url, { waitUntil: 'networkidle' })
// Wait for the web font to render so the wordmark/h1 aren't substituted
await page.evaluate(() => document.fonts.ready)
// Astro 5 dev toolbar inserts a custom element at the bottom of the page —
// nuke it before screenshotting.
await page.evaluate(() => {
  document.querySelectorAll('astro-dev-toolbar, astro-dev-overlay').forEach(el => el.remove())
})
await page.waitForTimeout(300)
await page.screenshot({ path: out, type: 'png', clip: { x: 0, y: 0, width: 1200, height: 630 } })
await browser.close()
console.log(`og.png → ${out}`)
if (!existsSync(out)) {
  process.exit(1)
}
