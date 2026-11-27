import { test, expect } from '@playwright/test'
import { seed, login } from './helpers'

// #338: a book opened from the "No series" list has a crumb back to it,
// still there after Edit > Save.
test('a book opened from No series has a No series crumb that leads back', async ({ page }) => {
  seed('many', 4) // odd-numbered books have no series
  await login(page)
  await page.getByRole('button', { name: 'Series', exact: true }).first().click()
  await page.getByRole('button').filter({ hasText: 'No series' }).click()
  await page.waitForURL(/no_series=true/)

  await page.getByText('Seeded Book 001').click()
  await page.waitForURL(/\/books\/\d+$/)
  const crumb = page.getByRole('navigation', { name: 'Breadcrumb' }).getByRole('link', { name: 'No series' })
  await expect(crumb).toBeVisible()

  await page.getByRole('button', { name: 'Edit' }).click()
  await page.getByRole('button', { name: 'Save' }).click()
  await expect(page.getByText('Metadata saved')).toBeVisible()
  await expect(crumb).toBeVisible()

  await crumb.click()
  await page.waitForURL(/no_series=true/)
  await expect(page.getByRole('button', { name: 'Remove filter' })).toBeVisible()
  await expect(page.getByText('Seeded Book 001')).toBeVisible()
  await expect(page.getByText('Seeded Book 002')).toHaveCount(0)
})
