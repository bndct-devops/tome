// Bindery shots. The copy's inbox gets three files made from the seed EPUB (Frankenstein as is;
// Romeo and Juliet and The Time Machine with their own OPF title and author, so the review screen and its
// suggestions show three different public-domain books; The Time Machine is padded so the
// two copies differ in size). They stay in the inbox, never accepted.
import { copyFile, access, mkdir } from 'node:fs/promises'
import { execFileSync } from 'node:child_process'
import path from 'node:path'

const __dir = path.dirname(new URL(import.meta.url).pathname)
const REPO = path.resolve(__dir, '../../..')
const INBOX = process.env.SHOT_BINDERY_DIR ?? path.join(REPO, 'bindery/showcase')
const SEED = path.join(REPO, 'docs/seed/library/frankenstein.epub')
const RETITLE = String.raw`
import sys, re, zipfile
src, dest, title, author, gid, pad = sys.argv[1:7]
zin = zipfile.ZipFile(src); zout = zipfile.ZipFile(dest, 'w')
for item in zin.infolist():
    data = zin.read(item.filename)
    if item.filename.endswith('.opf'):
        s = data.decode()
        first, last = ' '.join(author.split()[:-1]), author.split()[-1]
        s = s.replace('Shelley, Mary Wollstonecraft', last + ', ' + first).replace('Mary Wollstonecraft Shelley', author)
        s = re.sub(r'<dc:title>.*?</dc:title>', '<dc:title>' + title + '</dc:title>', s)
        s = re.sub(r'\s*<dc:subject>.*?</dc:subject>', '', s)
        s = s.replace('gutenberg.org/84<', 'gutenberg.org/' + gid + '<')
        data = s.encode()
    zout.writestr(item, data, compress_type=zipfile.ZIP_STORED if item.filename == 'mimetype' else zipfile.ZIP_DEFLATED)
if int(pad):
    # a harmless extra entry (readers ignore unlisted files) so this copy's size differs
    import random
    zout.writestr('META-INF/padding.bin', random.Random(gid).randbytes(int(pad) * 1024), compress_type=zipfile.ZIP_STORED)
zout.close()
`
// [file, [title, author, gutenberg id, extra KB]]: the two retitled copies would otherwise
// share one size, so The Time Machine carries 23 KB of padding.
// Books the showcase library does not have, so the queue reads as new arrivals.
const FILES = [
  ['Dracula - Bram Stoker (1897).epub', ['Dracula', 'Bram Stoker', '345', '11']],
  ['Pride and Prejudice - Jane Austen (1813).epub', ['Pride and Prejudice', 'Jane Austen', '1342', '0']],
  ['The Time Machine - H. G. Wells (1895).epub', ['The Time Machine', 'H. G. Wells', '35', '23']],
]
const HAS_PADDING = "import sys, zipfile; sys.exit(0 if 'META-INF/padding.bin' in zipfile.ZipFile(sys.argv[1]).namelist() else 1)"

async function fillInbox() {
  await mkdir(INBOX, { recursive: true })
  for (const [f, meta] of FILES) {
    const dest = path.join(INBOX, f)
    let exists = true
    try { await access(dest) } catch { exists = false }
    if (exists && meta && meta[3] !== '0') {
      try { execFileSync('python3', ['-I', '-c', HAS_PADDING, dest]) } catch { exists = false } // made before padding: remake
    }
    if (exists) continue
    if (meta) execFileSync('python3', ['-I', '-c', RETITLE, SEED, dest, ...meta])
    else await copyFile(SEED, dest)
  }
}

// MOCK (cosmetic): the review screen's suggestions are live Open Library / Google Books
// results, passed through with two clean-ups: raw catalogue subject headings ("Shelley,
// mary wollstonecraft , 1797-1851", "Frankenstein, victor", "Scientists--fiction") are dropped from the tags, and
// the list is cut to four candidates so the last one is not sliced by the viewport edge.
const RAW_SUBJECT = /\d{4}|\s,|,\s*[a-z]|--/
async function cleanSuggestions(page) {
  await page.route('**/api/bindery/preview', async (route) => {
    const res = await route.fetch()
    const data = await res.json().catch(() => null)
    if (!data?.candidates) return route.fulfill({ response: res })
    const OURS = ['dracula', 'pride and prejudice', 'the time machine']
    const exact = (c) => OURS.includes(String(c.title ?? '').trim().toLowerCase()) ? 0 : 1
    data.candidates = data.candidates
      .filter((c) => c.year !== 0 && !/https?:\/\//.test(c.description ?? ''))
      .map((c) => ({ ...c, tags: (c.tags ?? []).filter((t) => !RAW_SUBJECT.test(t)) }))
      // the exact edition first, so the prefilled title is the book's own and not a ranking accident
      .sort((a, b) => exact(a) - exact(b))
      .slice(0, 4)
    await route.fulfill({ response: res, json: data })
  })
}

const refresh = async ({ page }) => {
  await page.getByRole('button', { name: 'Refresh' }).first().click()
  await page.waitForLoadState('networkidle').catch(() => {})
  await page.waitForTimeout(900)
}

export const scenes = [
  {
    name: 'bindery', route: '/bindery',
    before: async () => { await fillInbox() },
    setup: refresh,
  },
  {
    name: 'bindery-review', route: '/bindery', settle: 1500,
    before: async ({ page }) => { await fillInbox(); await cleanSuggestions(page) },
    // Frankenstein sorts first, so it is the file the suggestions panel targets.
    setup: async (ctx) => {
      const { page } = ctx
      await refresh(ctx)
      await page.getByRole('button', { name: 'Select all' }).click()
      await page.waitForTimeout(300)
      await page.getByRole('button', { name: 'Review', exact: true }).click()
      await page.waitForLoadState('networkidle').catch(() => {})
      // Metadata suggestions come from the online sources; give them time.
      await page.getByText('Metadata suggestions').first().waitFor({ timeout: 20000 }).catch(() => {})
      await page.waitForTimeout(6000)
    },
  },
]
