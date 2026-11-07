// Home docs page shots (website/src/pages/docs/home.astro): the hero with Also
// reading and Up next, the four-number strip, and the Quick look.

// Union of several element boxes, so one crop can hold two sibling sections.
async function unionBox(page, selectors) {
  return page.evaluate((sels) => {
    const boxes = sels.map(s => document.querySelector(s)?.getBoundingClientRect()).filter(Boolean)
    const x = Math.min(...boxes.map(b => b.left)), y = Math.min(...boxes.map(b => b.top))
    const r = Math.max(...boxes.map(b => b.right)), btm = Math.max(...boxes.map(b => b.bottom))
    return { x, y, width: r - x, height: btm - y }
  }, selectors)
}

// The page body: the hero is the section holding the title, the row after it
// holds Also reading and Up next.
const HERO = 'main section:has(h2.text-\\[32px\\])'
const ROW = 'main section:has(h2.text-\\[32px\\]) + section'

export const scenes = [
  {
    name: 'home-hero',
    route: '/',
    settle: 1800,
    pad: 28, // 48 reaches into the page title above
    clip: async ({ page }) => {
      await page.locator(HERO).waitFor()
      const box = await unionBox(page, [HERO, ROW])
      // the hero cover tilts and blooms past its box; the page's own width is the frame
      const main = await page.locator(HERO).evaluate(el => el.parentElement.getBoundingClientRect().toJSON())
      return { x: main.x, y: box.y, width: main.width, height: box.height }
    },
  },
  {
    name: 'home-numbers',
    route: '/',
    settle: 1800,
    pad: 20, // the strip has its own hairlines; more would show the rows around it
    clip: async ({ page }) => page.locator('main section.grid:has-text("This month")').first(),
  },
  {
    name: 'home-quick-look',
    route: '/',
    settle: 1800,
    setup: async ({ page }) => {
      const cover = page.locator('main section:has-text("Recently added") button[aria-label^="Quick look"]').first()
      await cover.scrollIntoViewIfNeeded()
      await cover.click()
      await page.waitForTimeout(900)
    },
    clip: async ({ page }) => page.locator('[role="dialog"]').first(),
  },
]
