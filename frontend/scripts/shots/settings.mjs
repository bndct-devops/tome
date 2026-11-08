// Settings page shots. Settings is one scrolling page; each scene scrolls <main> to its
// section (or a block inside it) and clips that element.

// Scroll <main> so the element sits `offset` px below main's top edge (room for the crop padding).
export const scrollMainTo = (selector, offset = 80) => async ({ page }) => {
  await page.locator(selector).first().waitFor()
  await page.evaluate(([sel, off]) => {
    const m = document.querySelector('main'); const el = document.querySelector(sel)
    m.scrollTo(0, m.scrollTop + el.getBoundingClientRect().top - m.getBoundingClientRect().top - off)
  }, [selector, offset])
  await page.waitForTimeout(600)
}

// A crop box whose final image (after the runner's 48px padding) shows `top` px above and
// `bottom` px below the content edges [y0, y1], with the full 48px at the sides.
export function cropBox(b, y0, y1, top = 24, bottom = 24, pad = 48) {
  return { x: b.x, y: y0 - top + pad, width: b.width, height: (y1 + bottom - pad) - (y0 - top + pad) }
}

// Idempotent data for the scenes below (real state on the showcase copy).
export async function ensureDevice(api) {
  const devs = await api('/devices')
  if (!Array.isArray(devs) || devs.length === 0) {
    await api('/devices', { method: 'POST', body: JSON.stringify({ name: 'Kindle Paperwhite', email: 'benedict_reads@kindle.com' }) })
  }
}
async function ensureToken(api) {
  const toks = await api('/tokens/')
  if (!Array.isArray(toks) || toks.length === 0) {
    await api('/tokens/', { method: 'POST', body: JSON.stringify({ name: 'Scribe', scope: 'full' }) })
  }
}

// MOCK: email delivery is not configured on the showcase copy. Send-to-device scenes report
// SMTP as configured (and the KOReader inbox beta off, its default) so the device list and
// the plain "Send to device" button render.
export async function mockSmtpConfigured(page) {
  await page.route('**/api/smtp-status', r => r.fulfill({ json: { configured: true, koreader: false } }))
}

// MOCK: the showcase has no valid Hardcover token, so /hardcover/status is answered with a
// linked account ("benedict") and counts matching the Hardcover page mock (hardcover.mjs):
// 20 books = 18 synced + 1 not matched + 1 excluded.
export async function mockHardcoverLinked(page) {
  await page.route('**/api/hardcover/status', r => r.fulfill({ json: {
    linked: true, username: 'benedict', token_status: 'ok', sync_enabled: true,
    linked_at: '2026-09-02T09:14:00', last_synced_at: '2026-11-07T18:20:00',
    matched_count: 18, unmatched_count: 1, error_count: 0, sync_running: false,
  } }))
}

export const scenes = [
  {
    name: 'settings-themes', route: '/settings', viewport: { width: 1600, height: 1200 },
    setup: scrollMainTo('#appearance'),
    clip: async ({ page }) => page.locator('#appearance'),
  },
  {
    name: 'settings-opds', route: '/settings', viewport: { width: 1600, height: 1200 },
    setup: async (ctx) => {
      await scrollMainTo('#koreader')(ctx)
      // The OPDS URL is built from window.location.origin in the page (TOME_PUBLIC_URL does
      // not reach it), so the showcase's localhost origin is swapped for a public-looking one.
      await ctx.page.evaluate((origin) => {
        const w = document.createTreeWalker(document.querySelector('#koreader'), NodeFilter.SHOW_TEXT)
        for (let n = w.nextNode(); n; n = w.nextNode()) n.nodeValue = n.nodeValue.split(origin).join('https://tome.example.com')
      }, new URL(ctx.page.url()).origin)
    },
    // Section heading + the OPDS block, stopping before the divider to Progress sync.
    clip: async ({ page }) => {
      const sec = await page.locator('#koreader').boundingBox()
      const b = await page.locator('#koreader div.py-6').filter({ hasText: 'OPDS catalog' }).first().boundingBox()
      return cropBox(b, sec.y, b.y + b.height - 24, 40, 20)
    },
  },
  {
    name: 'settings-api-tokens', route: '/settings',
    before: async ({ api }) => { await ensureToken(api) },
    setup: scrollMainTo('#api-tokens'),
    clip: async ({ page }) => page.locator('#api-tokens'),
  },
  {
    // mocked: Hardcover status (linked as benedict), see mockHardcoverLinked
    name: 'settings-hardcover', route: '/settings',
    before: async ({ page }) => { await mockHardcoverLinked(page) },
    setup: async (ctx) => {
      await scrollMainTo('#hardcover')(ctx)
      // The app prints the sync time with toLocaleString() (seconds included); drop the
      // ":00" seconds in the rendered text so the shot reads "7:20 PM".
      await ctx.page.evaluate(() => {
        const w = document.createTreeWalker(document.querySelector('#hardcover'), NodeFilter.SHOW_TEXT)
        for (let n = w.nextNode(); n; n = w.nextNode()) n.nodeValue = n.nodeValue.replace(/(\d{1,2}:\d{2}):\d{2}/g, '$1')
      })
    },
    clip: async ({ page }) => page.locator('#hardcover'),
  },
  {
    name: 'settings-quick-connect', route: '/settings',
    setup: scrollMainTo('#qc-code', 260),
    // The block only, without the divider above it (py-6 block, last:pb-0).
    clip: async ({ page }) => {
      const b = await page.locator('#account div.py-6').filter({ has: page.locator('#qc-code') }).last().boundingBox()
      return cropBox(b, b.y + 24, b.y + b.height, 20, 40)
    },
  },
  {
    // mocked: SMTP reported as configured (see mockSmtpConfigured); the device itself is real.
    name: 'settings-send-to-device-added', route: '/settings',
    before: async ({ page, api }) => { await ensureDevice(api); await mockSmtpConfigured(page) },
    setup: scrollMainTo('#send-to-device'),
    clip: async ({ page }) => page.locator('#send-to-device'),
  },
  // Send dialogs. mocked: SMTP reported as configured with the KOReader inbox beta off (see
  // mockSmtpConfigured); the device (Kindle Paperwhite) is real.
  {
    name: 'send-modal', route: '/books/1',
    before: async ({ page, api }) => { await ensureDevice(api); await mockSmtpConfigured(page) },
    setup: async ({ page }) => {
      await page.getByRole('button', { name: 'Send to device' }).first().click()
      await page.waitForTimeout(900)
    },
    clip: async ({ page }) => page.locator('[role="dialog"]'),
  },
  {
    name: 'send-modal-bulk', route: '/?tab=books',
    before: async ({ page, api }) => { await ensureDevice(api); await mockSmtpConfigured(page) },
    setup: async ({ page }) => {
      await page.getByRole('button', { name: 'Select', exact: true }).click()
      await page.waitForTimeout(500)
      await page.getByRole('button', { name: 'Deselect all' }).click()
      await page.waitForTimeout(300)
      for (const title of ['Project Hail Mary', 'Dune', 'Ready Player One']) {
        await page.locator('main').getByText(title, { exact: true }).first().click()
        await page.waitForTimeout(150)
      }
      await page.getByRole('button', { name: 'Send to device' }).first().click()
      await page.waitForTimeout(900)
    },
    clip: async ({ page }) => page.locator('[role="dialog"]'),
  },
]
