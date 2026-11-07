// Website screenshots: every ThemedShot the docs use, in light, dark and amber,
// written to website/public/shots/<theme>/<name>.png.
//
// Scenes live in scripts/shots/*.mjs, each exporting `scenes` (see shots/README.md).
//
// Usage:
//   scripts/run-showcase.sh                         # backend :8090 + frontend :5174
//   cd frontend && node scripts/website-shots.mjs [--theme light,dark] [--only name,name] [--list]
//
// Env: BASE (default http://localhost:5174), API (default http://localhost:8090), OUT.
import { chromium } from 'playwright'
import { readdir, mkdir } from 'node:fs/promises'
import path from 'node:path'

const __dir = path.dirname(new URL(import.meta.url).pathname)
const BASE = process.env.BASE ?? 'http://localhost:5174'
const API = process.env.API ?? 'http://localhost:8090'
const OUT = process.env.OUT ?? path.resolve(__dir, '../../website/public/shots')
const USER = process.env.SHOT_USER ?? 'benedict', PASS = process.env.SHOT_PASS ?? 'showcase'
const VIEWPORTS = { desktop: { width: 1600, height: 1000 }, phone: { width: 400, height: 850 } }
const PAD = { desktop: 48, phone: 24 }

const args = process.argv.slice(2)
const opt = (k) => { const i = args.indexOf(k); return i >= 0 ? args[i + 1] : null }
const themes = (opt('--theme') ?? 'light,dark,amber').split(',')
const only = opt('--only')?.split(',')
const list = args.includes('--list')

// load scenes
const scenes = []
for (const f of (await readdir(path.join(__dir, 'shots'))).filter(f => f.endsWith('.mjs')).sort()) {
  const mod = await import(path.join(__dir, 'shots', f))
  for (const s of mod.scenes ?? []) scenes.push({ ...s, _file: f })
}
if (list) { for (const s of scenes) console.log(`${s.name.padEnd(34)} ${s.viewport ?? 'desktop'}  ${s.route}  (${s._file})`); process.exit(0) }
const wanted = only ? scenes.filter(s => only.includes(s.name)) : scenes
const dupes = wanted.map(s => s.name).filter((n, i, a) => a.indexOf(n) !== i)
if (dupes.length) { console.error('duplicate scene names:', dupes); process.exit(1) }

const login = await fetch(`${API}/api/auth/login`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ username: USER, password: PASS }) })
const { access_token: token } = await login.json()
const api = async (p, init = {}) => {
  const r = await fetch(`${API}/api${p}`, { ...init, headers: { authorization: `Bearer ${token}`, 'content-type': 'application/json', ...(init.headers ?? {}) } })
  const text = await r.text(); try { return JSON.parse(text) } catch { return text }
}

const browser = await chromium.launch()
const failures = []
for (const theme of themes) {
  await mkdir(path.join(OUT, theme), { recursive: true })
  for (const scene of wanted) {
    const vpName = typeof scene.viewport === 'string' ? scene.viewport : 'desktop'
    const viewport = typeof scene.viewport === 'object' ? scene.viewport : VIEWPORTS[vpName]
    const ctx = await browser.newContext({ viewport, deviceScaleFactor: scene.scale ?? 1, colorScheme: theme === 'light' ? 'light' : 'dark' })
    await ctx.addInitScript(([t, th]) => {
      localStorage.setItem('tome_token', t); localStorage.setItem('tome_theme', th); localStorage.setItem('tome_sidebar', 'open')
      localStorage.setItem('tome_seen_version', '1.0.0'); localStorage.setItem('tome_stats_hint', '1'); sessionStorage.setItem('tome_follows_disabled', '1')
    }, [token, theme])
    const page = await ctx.newPage()
    const errors = []; page.on('pageerror', e => errors.push(e.message))
    try {
      if (scene.before) await scene.before({ page, ctx, api, token, theme, BASE })
      await page.goto(`${BASE}${scene.route}`); await page.waitForLoadState('networkidle').catch(() => {})
      await page.waitForTimeout(scene.settle ?? 1200)
      if (scene.setup) await scene.setup({ page, ctx, api, token, theme, BASE })
      const file = path.join(OUT, theme, `${scene.name}.png`)
      if (scene.clip) {
        const target = await scene.clip({ page })
        const box = typeof target.boundingBox === 'function' ? await target.boundingBox() : target
        if (!box) throw new Error('clip target has no box')
        const pad = scene.pad ?? PAD[vpName] ?? 32
        const vp = page.viewportSize()
        const clip = { x: Math.max(0, box.x - pad), y: Math.max(0, box.y - pad), width: Math.min(vp.width, box.x + box.width + pad) - Math.max(0, box.x - pad), height: Math.min(vp.height, box.y + box.height + pad) - Math.max(0, box.y - pad) }
        await page.screenshot({ path: file, clip })
      } else if (scene.fullPage) {
        const h = await page.evaluate(() => (document.querySelector('main')?.scrollHeight ?? document.body.scrollHeight) + 90)
        await page.setViewportSize({ width: viewport.width, height: Math.min(Math.max(h, viewport.height), scene.maxHeight ?? 2400) }); await page.waitForTimeout(400)
        await page.screenshot({ path: file })
      } else await page.screenshot({ path: file })
      console.log(`${theme.padEnd(6)} ${scene.name}${errors.length ? '  (page errors: ' + errors.join(' | ').slice(0, 120) + ')' : ''}`)
    } catch (e) {
      failures.push(`${theme}/${scene.name}: ${e.message.split('\n')[0]}`); console.log(`${theme.padEnd(6)} ${scene.name}  FAILED: ${e.message.split('\n')[0]}`)
    } finally { await ctx.close() }
  }
}
await browser.close()
if (failures.length) { console.log('\nFailures:'); failures.forEach(f => console.log(' ', f)); process.exit(1) }
