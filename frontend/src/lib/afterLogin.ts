// Where to go after signing in, for the one page that people reach signed
// out with something to finish: linking a reader from the QR code on its
// screen. Only that page is ever restored, built from a checked code, so a
// stored value can never send anyone somewhere else.

const KEY = 'tome_link_reader_code'
const CODE = /^[A-Z0-9]{6}$/

export function normaliseReaderCode(raw: string | null | undefined): string {
  return (raw ?? '').toUpperCase().replace(/[^A-Z0-9]/g, '')
}

export function rememberReaderCode(code: string): void {
  try {
    if (CODE.test(code)) sessionStorage.setItem(KEY, code)
  } catch { /* private window: the user types the code after signing in */ }
}

/** The page to open after sign-in: the reader link when one is waiting, else Home.
 *  Reading does not clear it (this runs while rendering); the link page clears it on arrival. */
export function afterLoginPath(): string {
  try {
    const code = sessionStorage.getItem(KEY)
    if (code && CODE.test(code)) return `/link-reader?code=${code}`
  } catch { /* fall through */ }
  return '/'
}

export function forgetReaderCode(): void {
  try { sessionStorage.removeItem(KEY) } catch { /* nothing stored */ }
}
