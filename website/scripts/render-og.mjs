// Renders public/og.png from the hidden /og Astro page at exact OG dims.
// Usage: node scripts/render-og.mjs   (requires the dev server on :4321)
import { chromium } from 'playwright'
import { existsSync, mkdirSync } from 'node:fs'
import { resolve, dirname } from 'node:path'

const url = process.argv[2] || 'http://localhost:4321/og'

// Render two variants from the same source page:
//   - 1200×630 → public/og.png             (Open Graph standard, used in meta tags)
//   - 1280×640 → public/og-github.png      (GitHub social-preview recommended size)
const targets = [
  { width: 1200, height: 630, out: resolve(process.cwd(), 'public/og.png') },
  { width: 1280, height: 640, out: resolve(process.cwd(), 'public/og-github.png') },
]

const browser = await chromium.launch()
for (const { width, height, out } of targets) {
  mkdirSync(dirname(out), { recursive: true })
  const ctx = await browser.newContext({
    viewport: { width, height },
    deviceScaleFactor: 2,
  })
  const page = await ctx.newPage()
  await page.goto(url, { waitUntil: 'networkidle' })
  await page.evaluate(() => document.fonts.ready)
  await page.evaluate(() => {
    document.querySelectorAll('astro-dev-toolbar, astro-dev-overlay').forEach(el => el.remove())
  })
  await page.waitForTimeout(300)
  await page.screenshot({ path: out, type: 'png', clip: { x: 0, y: 0, width, height } })
  await ctx.close()
  console.log(`${width}×${height} → ${out}`)
  if (!existsSync(out)) process.exit(1)
}
await browser.close()
