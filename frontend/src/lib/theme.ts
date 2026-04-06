export type ThemeId = 'light' | 'dark' | 'amber' | `custom-${string}`

export interface ThemeDefinition {
  id: ThemeId
  label: string
  dark: boolean
  preview: {
    bg: string
    card: string
    primary: string
    text: string
  }
}

export interface CustomTheme {
  id: string        // "custom-{timestamp}"
  label: string     // user-given name
  dark: boolean     // controls Tailwind dark: variants
  colors: string    // 10 comma-separated hex values
}

export const THEMES: ThemeDefinition[] = [
  { id: 'light', label: 'Light', dark: false, preview: { bg: '#ffffff',  card: '#f1f5f9', primary: '#1a1a1a',  text: '#1a1a1a' } },
  { id: 'dark',  label: 'Dark',  dark: true,  preview: { bg: '#09090b',  card: '#18181b', primary: '#fafafa',   text: '#fafafa' } },
  { id: 'amber', label: 'Amber', dark: false, preview: { bg: '#f9f4ec',  card: '#fffef9', primary: '#8c5c2a',   text: '#2e1f10' } },
]

// The 10 color positions map to CSS variable names
const COLOR_POSITIONS: string[] = [
  '--background',
  '--foreground',
  '--card',
  '--primary',
  '--primary-foreground',
  '--muted',
  '--muted-foreground',
  '--accent',
  '--border',
  '--destructive',
]

// CSS variables that are derived from the 10 inputs
function getDerivedVars(values: string[]): Record<string, string> {
  return {
    '--card-foreground':        values[1],  // = --foreground
    '--popover':                values[2],  // = --card
    '--popover-foreground':     values[1],  // = --foreground
    '--secondary':              values[5],  // = --muted
    '--secondary-foreground':   values[1],  // = --foreground
    '--accent-foreground':      values[1],  // = --foreground
    '--input':                  values[8],  // = --border
    '--ring':                   values[6],  // = --muted-foreground
    '--destructive-foreground': values[4],  // = --primary-foreground
  }
}

const CUSTOM_THEME_INLINE_VARS: string[] = [
  ...COLOR_POSITIONS,
  '--card-foreground',
  '--popover',
  '--popover-foreground',
  '--secondary',
  '--secondary-foreground',
  '--accent-foreground',
  '--input',
  '--ring',
  '--destructive-foreground',
]

const BUILT_IN_THEME_CLASSES = THEMES.map(t => `theme-${t.id}`)

// ── Custom theme localStorage helpers ─────────────────────────────────────────

const CUSTOM_THEMES_KEY = 'tome_custom_themes'

export function loadCustomThemes(): CustomTheme[] {
  try {
    const raw = localStorage.getItem(CUSTOM_THEMES_KEY)
    if (!raw) return []
    return JSON.parse(raw) as CustomTheme[]
  } catch {
    return []
  }
}

export function saveCustomTheme(theme: CustomTheme): void {
  const all = loadCustomThemes()
  const idx = all.findIndex(t => t.id === theme.id)
  if (idx >= 0) {
    all[idx] = theme
  } else {
    all.push(theme)
  }
  localStorage.setItem(CUSTOM_THEMES_KEY, JSON.stringify(all))
}

export function deleteCustomTheme(id: string): void {
  const all = loadCustomThemes().filter(t => t.id !== id)
  localStorage.setItem(CUSTOM_THEMES_KEY, JSON.stringify(all))
}

/**
 * Validates a comma-separated string of exactly 10 hex color values.
 * Returns a map of CSS variable name → hex value, or null if invalid.
 */
export function parseThemeColors(colors: string): Record<string, string> | null {
  const parts = colors.split(',').map(s => s.trim())
  if (parts.length !== 10) return null
  const hexRe = /^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/
  for (const p of parts) {
    if (!hexRe.test(p)) return null
  }
  const map: Record<string, string> = {}
  for (let i = 0; i < COLOR_POSITIONS.length; i++) {
    map[COLOR_POSITIONS[i]] = parts[i]
  }
  const derived = getDerivedVars(parts)
  Object.assign(map, derived)
  return map
}

// ── applyTheme ─────────────────────────────────────────────────────────────────

export function applyTheme(id: ThemeId): void {
  const html = document.documentElement

  // Clear any inline custom-theme variables first
  for (const v of CUSTOM_THEME_INLINE_VARS) {
    html.style.removeProperty(v)
  }

  const isCustom = id.startsWith('custom-')

  if (isCustom) {
    const all = loadCustomThemes()
    const custom = all.find(t => t.id === id)
    if (!custom) {
      // Fallback to light if the custom theme no longer exists
      applyTheme('light')
      return
    }

    // Remove built-in classes
    html.classList.remove(...BUILT_IN_THEME_CLASSES)

    // Set CSS variables inline
    const vars = parseThemeColors(custom.colors)
    if (vars) {
      for (const [prop, val] of Object.entries(vars)) {
        html.style.setProperty(prop, val)
      }
    }

    html.classList.toggle('dark', custom.dark)

    // Update PWA theme-color
    const metaThemeColor = document.querySelector<HTMLMetaElement>('meta[name="theme-color"]')
    if (metaThemeColor && vars) {
      metaThemeColor.content = vars['--primary'] ?? '#000000'
    }
  } else {
    const def = THEMES.find(t => t.id === id) ?? THEMES[0]

    html.classList.remove(...BUILT_IN_THEME_CLASSES)

    if (id !== 'light' && id !== 'dark') {
      html.classList.add(`theme-${id}`)
    }

    html.classList.toggle('dark', def.dark)

    const metaThemeColor = document.querySelector<HTMLMetaElement>('meta[name="theme-color"]')
    if (metaThemeColor) {
      metaThemeColor.content = def.preview.primary
    }
  }

  localStorage.setItem('tome_theme', id)
}

export function getStoredTheme(): ThemeId {
  const stored = localStorage.getItem('tome_theme') as ThemeId | null
  if (!stored) {
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
  }
  // Valid built-in
  if (THEMES.find(t => t.id === stored)) return stored
  // Valid custom
  if (stored.startsWith('custom-')) {
    const all = loadCustomThemes()
    if (all.find(t => t.id === stored)) return stored as ThemeId
  }
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}
