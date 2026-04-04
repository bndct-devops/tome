export type ThemeId =
  | 'light' | 'dark' | 'amber'
  | 'catppuccin-latte' | 'catppuccin-frappe' | 'catppuccin-macchiato' | 'catppuccin-mocha'
  | 'nord' | 'neon' | '8bit'

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

export const THEMES: ThemeDefinition[] = [
  { id: 'light',                  label: 'Light',                dark: false, preview: { bg: '#ffffff',  card: '#f1f5f9', primary: '#1a1a1a',  text: '#1a1a1a' } },
  { id: 'dark',                   label: 'Dark',                 dark: true,  preview: { bg: '#09090b',  card: '#18181b', primary: '#fafafa',   text: '#fafafa' } },
  { id: 'amber',                  label: 'Amber',                dark: false, preview: { bg: '#f9f4ec',  card: '#fffef9', primary: '#8c5c2a',   text: '#2e1f10' } },
  { id: 'catppuccin-latte',       label: 'Catppuccin Latte',     dark: false, preview: { bg: '#eff1f5',  card: '#e6e9ef', primary: '#8839ef',   text: '#4c4f69' } },
  { id: 'catppuccin-frappe',      label: 'Catppuccin Frappé',    dark: true,  preview: { bg: '#303446',  card: '#292c3c', primary: '#ca9ee6',   text: '#c6d0f5' } },
  { id: 'catppuccin-macchiato',   label: 'Catppuccin Macchiato', dark: true,  preview: { bg: '#24273a',  card: '#1e2030', primary: '#c6a0f6',   text: '#cad3f5' } },
  { id: 'catppuccin-mocha',       label: 'Catppuccin Mocha',     dark: true,  preview: { bg: '#1e1e2e',  card: '#181825', primary: '#cba6f7',   text: '#cdd6f4' } },
  { id: 'nord',                   label: 'Nord',                 dark: true,  preview: { bg: '#2e3440',  card: '#3b4252', primary: '#88c0d0',   text: '#eceff4' } },
  { id: 'neon',                   label: 'Neon',                 dark: true,  preview: { bg: '#06020e',  card: '#0d0619', primary: '#ff2d78',   text: '#f0e6ff' } },
  { id: '8bit',                   label: '8-bit',                dark: true,  preview: { bg: '#0d0d0d',  card: '#111111', primary: '#33ff33',   text: '#33ff33' } },
]

const THEME_CLASSES = THEMES.map(t => `theme-${t.id}`)

export function applyTheme(id: ThemeId): void {
  const html = document.documentElement
  const def = THEMES.find(t => t.id === id) ?? THEMES[0]

  html.classList.remove(...THEME_CLASSES)

  if (id !== 'light' && id !== 'dark') {
    html.classList.add(`theme-${id}`)
  }

  html.classList.toggle('dark', def.dark)

  // Keep the PWA theme-color meta in sync with the active theme
  const metaThemeColor = document.querySelector<HTMLMetaElement>('meta[name="theme-color"]')
  if (metaThemeColor) {
    metaThemeColor.content = def.preview.primary
  }

  localStorage.setItem('tome_theme', id)
}

export function getStoredTheme(): ThemeId {
  const stored = localStorage.getItem('tome_theme') as ThemeId | null
  if (stored && THEMES.find(t => t.id === stored)) return stored
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}
