// Bindery shots. The copy's inbox gets three files made from the seed EPUB (Frankenstein as is;
// Romeo and Juliet and The Time Machine with their own OPF title and author, so the review screen and its
// suggestions show three different public-domain books). They stay in the inbox, never accepted.
import { copyFile, access, mkdir } from 'node:fs/promises'
import { execFileSync } from 'node:child_process'
import path from 'node:path'

const __dir = path.dirname(new URL(import.meta.url).pathname)
const REPO = path.resolve(__dir, '../../..')
const INBOX = process.env.SHOT_BINDERY_DIR ?? path.join(REPO, 'bindery/showcase')
const SEED = path.join(REPO, 'docs/seed/library/frankenstein.epub')
const RETITLE = String.raw`
import sys, re, zipfile
src, dest, title, author, gid = sys.argv[1:6]
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
zout.close()
`
const FILES = [
  ['Frankenstein - Mary Shelley.epub', null],
  ['Romeo and Juliet - William Shakespeare.epub', ['Romeo and Juliet', 'William Shakespeare', '1513']],
  ['The Time Machine - H. G. Wells.epub', ['The Time Machine', 'H. G. Wells', '35']],
]

async function fillInbox() {
  await mkdir(INBOX, { recursive: true })
  for (const [f, meta] of FILES) {
    const dest = path.join(INBOX, f)
    try { await access(dest); continue } catch { /* make it */ }
    if (meta) execFileSync('python3', ['-I', '-c', RETITLE, SEED, dest, ...meta])
    else await copyFile(SEED, dest)
  }
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
    before: async () => { await fillInbox() },
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
