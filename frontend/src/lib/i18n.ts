import { i18n } from '@lingui/core'

// UI language. Mirrors theme.ts: the preference is client-side (localStorage),
// resolved once at boot and switchable from Settings without a reload.
//
// Adding a language: create src/locales/<code>/messages.po (see docs/translating.md),
// then add it to LOCALES below. Codes are BCP-47 (`zh-CN`, not `zh_CN`).

export interface LocaleDefinition {
  code: string
  /** Native name, shown untranslated in the picker. */
  label: string
}

export const LOCALES: LocaleDefinition[] = [
  { code: 'en', label: 'English' },
  { code: 'zh-CN', label: '简体中文' },
]

export const DEFAULT_LOCALE = 'en'
const STORAGE_KEY = 'tome_locale'

function isSupported(code: string | null | undefined): code is string {
  return !!code && LOCALES.some(l => l.code === code)
}

export function getStoredLocale(): string | null {
  const stored = localStorage.getItem(STORAGE_KEY)
  return isSupported(stored) ? stored : null
}

/** Match a browser language tag against LOCALES: exact first, then by primary subtag. */
function matchBrowserLocale(tags: readonly string[]): string | null {
  for (const tag of tags) {
    if (isSupported(tag)) return tag
  }
  for (const tag of tags) {
    const primary = tag.split('-')[0]
    const hit = LOCALES.find(l => l.code.split('-')[0] === primary)
    if (hit) return hit.code
  }
  return null
}

/** Stored preference → browser language → English. */
export function detectLocale(): string {
  return getStoredLocale() ?? matchBrowserLocale(navigator.languages ?? [navigator.language]) ?? DEFAULT_LOCALE
}

/**
 * Load a catalog and make it the active language. Safe to call repeatedly.
 * Never rejects: a catalog that fails to load activates with no messages, so the
 * UI renders the source (English) strings instead of a blank page.
 */
export async function activateLocale(code: string): Promise<void> {
  const locale = isSupported(code) ? code : DEFAULT_LOCALE
  let messages = {}
  try {
    ;({ messages } = await import(`../locales/${locale}/messages.po`))
  } catch (err) {
    console.error(`[i18n] failed to load catalog for ${locale}`, err)
  }
  i18n.load(locale, messages)
  i18n.activate(locale)
  document.documentElement.lang = locale
}

/** Persist the choice and switch immediately. */
export function setLocale(code: string): Promise<void> {
  localStorage.setItem(STORAGE_KEY, code)
  return activateLocale(code)
}

export function getActiveLocale(): string {
  return i18n.locale || DEFAULT_LOCALE
}

export { i18n }
