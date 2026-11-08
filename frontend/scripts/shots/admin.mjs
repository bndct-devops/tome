// Admin page shots. Admin is one page with a section list; sections switch by clicking the list item.
import { cropBox } from './settings.mjs'

export const openAdminSection = (id) => async ({ page }) => {
  await page.locator(`[data-section="${id}"]:visible`).first().click()
  await page.waitForLoadState('networkidle').catch(() => {})
  await page.waitForTimeout(900)
}

// Real users on the showcase copy: a member "mika" and a guest "sam" next to the admin.
async function ensureUsers(api) {
  const users = await api('/users')
  const have = new Set((Array.isArray(users) ? users : []).map(u => u.username))
  if (!have.has('mika')) await api('/users', { method: 'POST', body: JSON.stringify({ username: 'mika', email: 'mika@example.com', password: 'showcase-reader', role: 'member' }) })
  if (!have.has('sam')) await api('/users', { method: 'POST', body: JSON.stringify({ username: 'sam', email: 'sam@example.com', password: 'showcase-visitor', role: 'guest' }) })
}

// The panel to the right of the section list: from the page header down to the end of the content.
const panelBox = async (page) => {
  const head = await page.locator('main h1').first().boundingBox()
  const body = await page.locator('main .flex.flex-col.gap-12').first().boundingBox()
  return cropBox(body, head.y, body.y + body.height, 40, 32)
}

export const scenes = [
  {
    name: 'admin-page', route: '/admin',
    before: async ({ api }) => { await ensureUsers(api) },
  },
  {
    name: 'users-list', route: '/admin',
    before: async ({ api }) => { await ensureUsers(api) },
    clip: async ({ page }) => panelBox(page),
  },
  {
    name: 'users-create-modal', route: '/admin',
    before: async ({ api }) => { await ensureUsers(api) },
    setup: async ({ page }) => {
      await page.getByRole('button', { name: 'New user' }).click()
      await page.waitForTimeout(700)
    },
    clip: async ({ page }) => page.locator('[role="dialog"]'),
  },
  {
    // MOCK: the copy holds the result of an earlier backfill run (placeholder EPUBs, 40
    // "could not parse"), so /admin/word-count/status is answered as idle. The preflight
    // counts (EPUB total, already counted, pending) are real.
    name: 'admin-word-counts', route: '/admin',
    before: async ({ page }) => {
      await page.route('**/api/admin/word-count/status', r => r.fulfill({ json: {
        status: 'idle', started_at: null, finished_at: null, triggered_by: null, total_files: 0, total_bytes: 0,
        done_files: 0, done_bytes: 0, counted: 0, failed: 0, words_total: 0, current_file: null, issues: [],
        error: null, elapsed_seconds: 0, eta_seconds: null,
      } }))
    },
    setup: openAdminSection('wordcount'),
    clip: async ({ page }) => panelBox(page),
  },
  {
    // MOCK: email delivery is not configured on the showcase copy, so /admin/smtp-status is
    // answered with a configured server. Devices and send history are real.
    name: 'admin-email-configured', route: '/admin',
    before: async ({ page }) => {
      await page.route('**/api/admin/smtp-status', r => r.fulfill({ json: { configured: true, host: 'smtp.example.com', port: 587, from_address: 'tome@example.com' } }))
    },
    setup: openAdminSection('email'),
  },
]
