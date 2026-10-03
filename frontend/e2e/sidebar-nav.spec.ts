import { test, expect, type Locator, type Page } from '@playwright/test'
import { seed, login } from './helpers'

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
