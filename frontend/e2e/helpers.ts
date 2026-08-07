import { execFileSync } from 'node:child_process'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import type { APIRequestContext, Page } from '@playwright/test'
import { expect } from '@playwright/test'

const E2E_DIR = path.dirname(fileURLToPath(import.meta.url))
const ROOT = path.resolve(E2E_DIR, '..', '..')
const PYTHON = process.env.E2E_PYTHON ?? path.join(ROOT, '.venv', 'bin', 'python')

export const ADMIN = { username: 'e2e', password: 'e2e-password-1' }
export const LIBRARY_DIR = path.join(E2E_DIR, '.data', 'library')

/** Reset the sandbox DB + library and load a scenario (see e2e/seed.py). */
export function seed(scenario: 'orphans' | 'duplicates' | 'many' | 'race' | 'reset', count?: number) {
  const args = [path.join(E2E_DIR, 'seed.py'), scenario]
  if (count !== undefined) args.push(String(count))
  execFileSync(PYTHON, args, { stdio: 'pipe' })
}

/** Log in through the login form, the way a user does. */
export async function login(page: Page) {
  await page.goto('/login')
  await page.fill('input[type="text"]', ADMIN.username)
  await page.fill('input[type="password"]', ADMIN.password)
  await page.click('button[type="submit"]')
  await page.waitForURL('**/')
}

/** Bearer token for API-level assertions about the end state. */
export async function apiToken(request: APIRequestContext): Promise<string> {
  const resp = await request.post('/api/auth/login', {
    data: { username: ADMIN.username, password: ADMIN.password },
  })
  expect(resp.ok()).toBeTruthy()
  return (await resp.json()).access_token
}

export async function apiGet<T>(request: APIRequestContext, token: string, url: string): Promise<{ status: number; body: T }> {
  const resp = await request.get(url, { headers: { Authorization: `Bearer ${token}` } })
  let body: T = undefined as T
  try { body = await resp.json() } catch { /* non-JSON (e.g. 404 detail) */ }
  return { status: resp.status(), body }
}
