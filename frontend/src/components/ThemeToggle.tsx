import { Moon, Sun } from 'lucide-react'
import { useState } from 'react'
import { cn } from '@/lib/utils'
import { applyTheme, getStoredTheme, THEMES } from '@/lib/theme'

export function ThemeToggle({ className }: { className?: string }) {
  const [themeId, setThemeId] = useState(getStoredTheme)

  function toggle() {
    const def = THEMES.find(t => t.id === themeId)
    const next = def?.dark ? 'light' : 'dark'
    applyTheme(next)
    setThemeId(next)
  }

  const isDark = THEMES.find(t => t.id === themeId)?.dark ?? false

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
