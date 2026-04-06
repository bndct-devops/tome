import { Moon, Sun } from 'lucide-react'
import { useState } from 'react'
import { cn } from '@/lib/utils'
import { applyTheme, getStoredTheme, THEMES, loadCustomThemes } from '@/lib/theme'

export function ThemeToggle({ className }: { className?: string }) {
  const [themeId, setThemeId] = useState(getStoredTheme)

  function toggle() {
    // Determine whether the current theme is "dark"
    const builtIn = THEMES.find(t => t.id === themeId)
    let isDark = builtIn?.dark ?? false
    if (!builtIn && themeId.startsWith('custom-')) {
      const custom = loadCustomThemes().find(t => t.id === themeId)
      isDark = custom?.dark ?? false
    }
    const next = isDark ? 'light' : 'dark'
    applyTheme(next)
    setThemeId(next)
  }

  const builtIn = THEMES.find(t => t.id === themeId)
  let isDark = builtIn?.dark ?? false
  if (!builtIn && themeId.startsWith('custom-')) {
    const custom = loadCustomThemes().find(t => t.id === themeId)
    isDark = custom?.dark ?? false
  }

  return (
    <button
      onClick={toggle}
      className={cn(
        'p-2 rounded-lg text-muted-foreground hover:text-foreground hover:bg-muted',
        'transition-all duration-200 hover:scale-110',
        className
      )}
      aria-label="Toggle theme"
    >
      {isDark ? <Sun className="w-4 h-4" /> : <Moon className="w-4 h-4" />}
    </button>
  )
}
