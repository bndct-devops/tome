import { Sun, Moon, MoonStar, Flame, Coffee } from 'lucide-react'
import { Fragment, useState } from 'react'
import { cn } from '@/lib/utils'
import { applyTheme, getStoredTheme, THEMES, loadCustomThemes, type ThemeId } from '@/lib/theme'
import { t, msg } from '@lingui/core/macro'
import { i18n } from '@lingui/core'
import type { MessageDescriptor } from '@lingui/core'

export function ThemeToggle({ className }: { className?: string }) {
  const [themeId, setThemeId] = useState(getStoredTheme)

  function toggle() {
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
        'transition-colors duration-200',
        className
      )}
      aria-label={t`Toggle theme`}
    >
      {isDark ? <Sun className="w-4 h-4" /> : <Moon className="w-4 h-4" />}
    </button>
  )
}

const BUILT_IN: { id: ThemeId; icon: typeof Sun; label: MessageDescriptor }[] = [
  { id: 'light', icon: Sun, label: msg`Light` },
  { id: 'dark', icon: Moon, label: msg`Dark` },
  { id: 'black', icon: MoonStar, label: msg`Black` },
  { id: 'amber', icon: Flame, label: msg`Amber` },
  { id: 'ember', icon: Coffee, label: msg`Ember` },
]

export function ThemePill({ className }: { className?: string }) {
  const [themeId, setThemeId] = useState(getStoredTheme)

  function pick(id: ThemeId) {
    applyTheme(id)
    setThemeId(id)
  }

  return (
    <div className={cn('inline-flex gap-0.5 p-0.5 rounded-full bg-muted/60 border border-border', className)} role="group" aria-label={t`Theme`}>
      {BUILT_IN.map(({ id, icon: Icon, label }) => (
        <Fragment key={id}>
          {/* hairline between the neutral core and the warm pair */}
          {id === 'amber' && <span className="w-px self-stretch bg-border mx-0.5" />}
          <button
            onClick={() => pick(id)}
            aria-label={(() => { const name = i18n._(label); return t`${name} theme` })()}
            className={cn(
              'w-6 h-6 rounded-full grid place-items-center transition-all duration-200',
              themeId === id
                ? 'bg-primary text-primary-foreground'
                : 'text-muted-foreground hover:text-foreground hover:bg-background'
            )}
          >
            <Icon className="w-3 h-3" />
          </button>
        </Fragment>
      ))}
    </div>
  )
}
