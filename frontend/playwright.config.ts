import { defineConfig } from '@playwright/test'

// E2E suite: boots a real backend (uvicorn, throwaway SQLite + library under
// e2e/.data) serving the freshly built frontend, then drives it like a user.
// Specs share one server and one DB, so they run serially and each test seeds
// its own scenario via e2e/seed.py.
export default defineConfig({
  testDir: './e2e',
  workers: 1,
  fullyParallel: false,
  timeout: 60_000,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [['list'], ['github']] : [['list']],
  use: {
    baseURL: 'http://127.0.0.1:8199',
    trace: 'retain-on-failure',
  },
  webServer: {
    command: 'bash e2e/start.sh',
    url: 'http://127.0.0.1:8199/api/health',
    reuseExistingServer: !process.env.CI,
    timeout: 180_000,
    stdout: 'ignore',
    stderr: 'pipe',
  },
})
