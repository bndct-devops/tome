import fs from 'node:fs'
import path from 'node:path'
import { test, expect, type Locator, type Page } from '@playwright/test'
import { seed, login, BINDERY_DIR, MEMBER, GUEST } from './helpers'

// The top-level nav renders three ways: expanded sidebar and collapsed rail on
// desktop, drawer on phones. All three must offer the same entries in the same
// order and behave the same when tapped.
const ADMIN_NAV = [
  { role: 'button', name: 'Home', url: '/' },
  { role: 'button', name: 'All Books', url: '/?tab=books' },
  { role: 'button', name: 'Series', url: '/?tab=series' },
  { role: 'link', name: 'Stats', url: '/stats' },
  { role: 'link', name: 'Highlights', url: '/highlights' },
  { role: 'link', name: 'Wishlist', url: '/wishlist' },
  { role: 'link', name: 'Hardcover', url: '/hardcover' },
  { role: 'link', name: 'Bindery', url: '/bindery' },
] as const

type NavEntry = (typeof ADMIN_NAV)[number]

const sidebarNav = (page: Page) => page.locator('aside nav > div').first()
const railNav = (page: Page) => page.locator('aside > div').first()
const drawerNav = (page: Page) => page.getByRole('navigation').locator('> div').first()

function navSnapshot(entries: readonly NavEntry[]) {
  return ['- /children: equal', ...entries.map(e => `- ${e.role} "${e.name}"`)].join('\n')
}

function entryIn(nav: Locator, entry: NavEntry) {
  return nav.getByRole(entry.role, { name: entry.name, exact: true })
}

async function expectActive(nav: Locator, entry: NavEntry) {
  await expect(entryIn(nav, entry)).toHaveClass(/bg-primary\/10/)
}

test.describe('sidebar nav on desktop', () => {
  test.beforeEach(async ({ page }) => {
    seed('reset')
    await login(page)
  })

  test('expanded sidebar lists every entry and each one navigates', async ({ page }) => {
    const nav = sidebarNav(page)
    await expect(nav).toMatchAriaSnapshot(navSnapshot(ADMIN_NAV))

    for (const entry of ADMIN_NAV) {
      await entryIn(nav, entry).click()
      await expect(page).toHaveURL(entry.url)
      await expectActive(nav, entry)
    }
  })

  test('collapsed rail lists every entry and each one navigates', async ({ page }) => {
    await page.getByRole('button', { name: 'Collapse sidebar' }).click()
    const nav = railNav(page)
    await expect(nav).toMatchAriaSnapshot(navSnapshot(ADMIN_NAV))

    for (const entry of ADMIN_NAV) {
      await entryIn(nav, entry).click()
      await expect(page).toHaveURL(entry.url)
      await expectActive(nav, entry)
    }
  })
})

test.describe('sidebar nav on a phone', () => {
  test.use({ viewport: { width: 390, height: 844 } })

  test.beforeEach(async ({ page }) => {
    seed('reset')
    await login(page)
  })

  test('drawer lists every entry, each one navigates and closes the drawer', async ({ page }) => {
    const nav = drawerNav(page)
    const openDrawer = async () => {
      await page.getByRole('button', { name: 'Open navigation' }).click()
      await expect(nav).toBeInViewport()
    }

    await openDrawer()
    await expect(nav).toMatchAriaSnapshot(navSnapshot(ADMIN_NAV))
    await page.getByRole('button', { name: 'Close navigation' }).click()
    await expect(nav).not.toBeInViewport()

    for (const entry of ADMIN_NAV) {
      await openDrawer()
      await entryIn(nav, entry).click()
      await expect(page).toHaveURL(entry.url)
      await expect(nav).not.toBeInViewport()

      // Tapping the page you are already on changes no URL; the drawer must
      // still close.
      await openDrawer()
      await expectActive(nav, entry)
      await entryIn(nav, entry).click()
      await expect(nav).not.toBeInViewport()
    }
  })
})

test.describe('sidebar nav by role', () => {
  const roles = [
    { user: MEMBER, entries: ADMIN_NAV.filter(e => e.name !== 'Bindery') },
    { user: GUEST, entries: ADMIN_NAV.filter(e => !['Wishlist', 'Hardcover', 'Bindery'].includes(e.name)) },
  ]

  for (const { user, entries } of roles) {
    test(`${user.username} sees ${entries.length} entries in sidebar, rail and drawer`, async ({ page }) => {
      seed('reset')
      await login(page, user)

      await expect(sidebarNav(page)).toMatchAriaSnapshot(navSnapshot(entries))
      await page.getByRole('button', { name: 'Collapse sidebar' }).click()
      await expect(railNav(page)).toMatchAriaSnapshot(navSnapshot(entries))
      await page.setViewportSize({ width: 390, height: 844 })
      await expect(drawerNav(page)).toMatchAriaSnapshot(navSnapshot(entries))
    })
  }
})

test.describe('bindery badge', () => {
  // seed('reset') leaves the bindery alone, so each test removes its file.
  const waiting = path.join(BINDERY_DIR, 'waiting.epub')

  test.beforeEach(async ({ page }) => {
    seed('reset')
    await login(page)
  })

  test.afterEach(() => fs.rmSync(waiting, { force: true }))

  test('sidebar, rail and drawer show files waiting in the bindery', async ({ page }) => {
    const sidebarBadge = sidebarNav(page).getByRole('link', { name: /^Bindery/ }).getByText('1', { exact: true })
    await expect(sidebarBadge).toHaveCount(0)

    fs.writeFileSync(waiting, '')
    // The count is fetched on mount (then every 30s).
    await page.reload()
    await expect(sidebarBadge).toBeVisible()

    await page.getByRole('button', { name: 'Collapse sidebar' }).click()
    await expect(railNav(page).getByRole('link', { name: 'Bindery' }).locator('span')).toBeVisible()

    await page.setViewportSize({ width: 390, height: 844 })
    await page.getByRole('button', { name: 'Open navigation' }).click()
    await expect(drawerNav(page).getByRole('link', { name: /^Bindery/ }).getByText('1', { exact: true })).toBeVisible()
  })
})
