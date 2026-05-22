import { useEffect, useState } from 'react'
import { Sun, Moon, Flame } from 'lucide-react'
import { readTheme, setTheme, subscribe, type Theme } from './theme'

const OPTIONS: { value: Theme; label: string; icon: typeof Sun }[] = [
  { value: 'light', label: 'Light', icon: Sun },
  { value: 'dark', label: 'Dark', icon: Moon },
  { value: 'amber', label: 'Amber', icon: Flame },
]

export function ThemeToggle() {
  const [theme, setLocal] = useState<Theme>('light')

  useEffect(() => {
    setLocal(readTheme())
    return subscribe(setLocal)
  }, [])

  return (
    <div className="inline-flex items-center gap-1 rounded-full border bg-[var(--card)] p-1">
      {OPTIONS.map(({ value, label, icon: Icon }) => {
        const active = theme === value
        return (
          <button
            key={value}
            type="button"
            onClick={(e) => {
              const rect = (e.currentTarget as HTMLElement).getBoundingClientRect()
              setTheme(value, rect.left + rect.width / 2, rect.top + rect.height / 2)
            }}
            aria-label={`${label} theme`}
            aria-pressed={active}
            className={[
              'inline-flex items-center justify-center w-8 h-8 rounded-full transition-all',
              active
                ? 'bg-[var(--accent)] text-[var(--accent-fg)] scale-105'
                : 'text-[var(--muted)] hover:text-[var(--fg)] hover:bg-[var(--bg)]',
            ].join(' ')}
          >
            <Icon className="w-4 h-4" />
          </button>
        )
      })}
    </div>
  )
}
