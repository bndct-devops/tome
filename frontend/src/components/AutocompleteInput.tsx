import { useEffect, useRef, useState } from 'react'

interface AutocompleteInputProps {
  value: string
  onChange: (v: string) => void
  suggestions: string[]
  placeholder?: string
  className?: string
  onSelect?: (v: string) => void  // called when suggestion clicked
  onEnter?: (v: string) => void   // called on Enter key press
}

export function AutocompleteInput({
  value,
  onChange,
  suggestions,
  placeholder,
  className,
  onSelect,
  onEnter,
}: AutocompleteInputProps) {
  const [open, setOpen] = useState(false)
  const [activeIdx, setActiveIdx] = useState(-1)
  const containerRef = useRef<HTMLDivElement>(null)

  const filtered = suggestions
    .filter(s => value === '' || s.toLowerCase().includes(value.toLowerCase()))
    .slice(0, 8)

  useEffect(() => {
    function handleMouseDown(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', handleMouseDown)
    return () => document.removeEventListener('mousedown', handleMouseDown)
  }, [])

  function handleKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (!open) {
      if (e.key === 'ArrowDown' && filtered.length > 0) {
        setOpen(true)
        setActiveIdx(0)
        e.preventDefault()
      }
      return
    }
    if (e.key === 'ArrowDown') {
      setActiveIdx(i => Math.min(i + 1, filtered.length - 1))
      e.preventDefault()
    } else if (e.key === 'ArrowUp') {
      setActiveIdx(i => Math.max(i - 1, -1))
      e.preventDefault()
    } else if (e.key === 'Enter') {
      if (activeIdx >= 0 && filtered[activeIdx]) {
        const chosen = filtered[activeIdx]
        if (onSelect) {
          onSelect(chosen)
        } else {
          onChange(chosen)
        }
        setOpen(false)
        setActiveIdx(-1)
      } else {
        if (onEnter) onEnter(value)
        setOpen(false)
      }
      e.preventDefault()
    } else if (e.key === 'Escape' || e.key === 'Tab') {
      setOpen(false)
      setActiveIdx(-1)
    }
  }

  function pickSuggestion(s: string) {
    if (onSelect) {
      onSelect(s)
    } else {
      onChange(s)
    }
    setOpen(false)
    setActiveIdx(-1)
  }

  return (
    <div ref={containerRef} className="relative">
      <input
        type="text"
        value={value}
        onChange={e => { onChange(e.target.value); setOpen(true); setActiveIdx(-1) }}
        onFocus={() => setOpen(true)}
        onKeyDown={handleKeyDown}
        placeholder={placeholder}
        className={className ?? 'w-full bg-muted rounded-lg px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-ring'}
      />
      {open && filtered.length > 0 && (
        <div className="absolute left-0 top-full mt-1 z-50 bg-card border border-border rounded-xl shadow-xl py-1 w-full min-w-48 max-h-48 overflow-y-auto">
          {filtered.map((s, i) => (
            <div
              key={s}
              onMouseDown={e => { e.preventDefault(); pickSuggestion(s) }}
              onMouseEnter={() => setActiveIdx(i)}
              className={`flex items-center px-3 py-1.5 text-sm cursor-pointer transition-colors ${
                i === activeIdx ? 'bg-primary/10 text-primary' : 'hover:bg-muted'
              }`}
            >
              {s}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
