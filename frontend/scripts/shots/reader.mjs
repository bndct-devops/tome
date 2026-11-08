// Web reader shots. Every reader route uses ?preview=1 so opening a book here never writes
// progress; the Preview chip is removed from the DOM before the shot (see the end of this file).
// writes status, progress or a reading session. The reader has its own page themes:
// light -> light, dark -> dark, amber -> sepia.
const READER_THEME = { light: 'light', dark: 'dark', amber: 'sepia' }
const readerTheme = async ({ ctx, theme }) => {
  await ctx.addInitScript(t => localStorage.setItem('reader_theme', t), READER_THEME[theme] ?? 'light')
}
const FRANKENSTEIN = '/reader/37?preview=1'
// The seeded KOReader highlight on Frankenstein (Letter 1, paragraph 3).
const HIGHLIGHT_START = 'You will rejoice to hear'

// MOCK (reader-annotations, reader-annotation-card): the seeded KOReader highlight is
// real, but the reader re-anchors KOReader highlights by text search, and this EPUB
// breaks the paragraph with a hard newline after "commencement of", which the
// search does not match across, so it never paints. The mock passes the real
// annotation through and only adds the CFI of that same passage, so it paints.
const HIGHLIGHT_CFI = 'epubcfi(/6/8!/4/2[pgepubid00003]/8,/1:1,/1:144)'
const paintSeededHighlight = async (args) => {
  await readerTheme(args)
  await args.page.route('**/api/books/37/annotations', async route => {
    const res = await route.fetch()
    const list = await res.json()
    for (const a of list) if (!a.cfi && a.highlighted_text?.startsWith(HIGHLIGHT_START)) a.cfi = HIGHLIGHT_CFI
    await route.fulfill({ response: res, json: list })
  })
}

// Jump to a TOC entry by label and wait for the page to settle.
async function goToToc(page, label) {
  await page.evaluate(async (label) => {
    const v = document.querySelector('foliate-view')
    const item = v.book.toc.find(t => t.label.trim() === label)
    await v.goTo(item.href)
  }, label)
  await page.waitForTimeout(1200)
}

// Page-space rect of the first occurrence of `needle` in the visible section.
async function textRect(page, needle, { select = false, length } = {}) {
  return page.evaluate(({ needle, select, length }) => {
    const v = document.querySelector('foliate-view')
    for (const { doc } of v.renderer.getContents()) {
      const walker = doc.createTreeWalker(doc.body, NodeFilter.SHOW_TEXT)
      let n
      while ((n = walker.nextNode())) {
        const i = n.data.indexOf(needle)
        if (i < 0) continue
        const r = doc.createRange()
        r.setStart(n, i); r.setEnd(n, Math.min(n.data.length, i + (length ?? needle.length)))
        if (select) { const s = doc.getSelection(); s.removeAllRanges(); s.addRange(r) }
        const f = doc.defaultView.frameElement.getBoundingClientRect()
        const b = r.getClientRects()[0]
        return { x: f.x + b.x, y: f.y + b.y, width: b.width, height: b.height }
      }
    }
    return null
  }, { needle, select, length })
}

// The runner pads a crop by 48px; clamp a box so its padded bottom stops 14px above the
// reader footer ("Letter 1 · ~10 min left", progress bar) instead of slicing through it.
const PAD = 48
async function aboveFooter(page, box) {
  const f = await page.getByText(/min left/).first().boundingBox()
  if (!f) return box
  const maxBottom = f.y - 14 - PAD
  return { ...box, height: Math.min(box.height, maxBottom - box.y) }
}

export const scenes = [
  {
    name: 'reader-epub', route: FRANKENSTEIN, before: readerTheme, settle: 2500,
    setup: async ({ page }) => { await goToToc(page, 'Chapter 5') },
  },
  {
    name: 'reader-annotations', route: FRANKENSTEIN, before: paintSeededHighlight, settle: 2500,
    setup: async ({ page }) => { await goToToc(page, 'Letter 1'); await page.waitForTimeout(800) },
  },
  {
    name: 'reader-annotation-create', route: FRANKENSTEIN, before: readerTheme, settle: 2500,
    setup: async ({ page }) => {
      await goToToc(page, 'Letter 1')
      // Select a sentence inside the section iframe; the reader's selectionchange
      // listener pops the colour picker. Nothing is saved: no colour is clicked.
      await page.evaluate(() => {
        const v = document.querySelector('foliate-view')
        v.renderer.getContents()[0]?.doc.defaultView.focus()
      })
      await textRect(page, 'What may not be expected', { select: true, length: 55 })
      await page.waitForTimeout(600)
    },
    clip: async ({ page }) => {
      const bar = await page.getByText('Highlight', { exact: true }).locator('..').boundingBox()
      return aboveFooter(page, { x: 90, y: bar.y - 330, width: 1420, height: bar.height + 330 })
    },
  },
  {
    name: 'reader-annotation-card', route: FRANKENSTEIN, before: paintSeededHighlight, settle: 2500,
    setup: async ({ page }) => {
      await goToToc(page, 'Letter 1'); await page.waitForTimeout(800)
      // Tap the painted highlight on its second line, clear of the page-turn zones.
      const r = await textRect(page, 'regarded with such')
      await page.mouse.click(r.x + r.width / 2, r.y + r.height / 2)
      await page.waitForTimeout(700)
    },
    // The card plus the painted passage it belongs to.
    clip: async ({ page }) => {
      const card = await page.locator('div.ring-panel-ring', { hasText: 'Synced from KOReader' }).last().boundingBox()
      const hl = await textRect(page, HIGHLIGHT_START)
      return aboveFooter(page, { x: 90, y: hl.y - 24, width: 1420, height: card.y + card.height - hl.y + 24 })
    },
  },
  {
    name: 'mobile-reader', route: FRANKENSTEIN, viewport: 'phone', before: readerTheme, settle: 2500,
    setup: async ({ page }) => { await goToToc(page, 'Chapter 5') },
  },
]

// Preview mode keeps the data untouched; the chip itself is not part of the docs' story.
const hidePreviewChip = (page) => page.evaluate(() => document.querySelectorAll('[title^="Preview:"]').forEach(el => el.remove()))
for (const s of scenes) {
  const orig = s.setup
  s.setup = async (ctx) => { if (orig) await orig(ctx); await hidePreviewChip(ctx.page); await ctx.page.waitForTimeout(150) }
}
