import { useState, useEffect, useRef } from 'react'
import { Loader2 } from 'lucide-react'
import { IconPicker } from '@/components/Sidebar'

interface Props {
  title: string
  initialName?: string
  initialIcon?: string
  defaultIcon?: string
  onSave: (name: string, icon: string) => Promise<void>
  onClose: () => void
}

export function EntityModal({ title, initialName = '', initialIcon, defaultIcon = 'Library', onSave, onClose }: Props) {
  const [name, setName] = useState(initialName)
  const [icon, setIcon] = useState(initialIcon ?? defaultIcon)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    inputRef.current?.focus()
  }, [])

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  async function handleSave() {
    if (!name.trim() || saving) return
    setSaving(true)
    setError('')
    try {
      await onSave(name.trim(), icon)
      onClose()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to save')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div
      className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center"
      onMouseDown={e => { if (e.target === e.currentTarget) onClose() }}
    >
      <div className="bg-card text-foreground rounded-xl shadow-xl max-w-sm w-full mx-4 p-6 space-y-4">
        <h2 className="text-base font-semibold">{title}</h2>

        <input
          ref={inputRef}
          value={name}
          onChange={e => { setName(e.target.value); setError('') }}
          onKeyDown={e => {
            if (e.key === 'Enter') handleSave()
          }}
          placeholder="Name…"
          className="w-full px-3 py-2 rounded-lg border border-border bg-background text-sm focus:outline-none focus:ring-1 focus:ring-ring"
        />

        <div className="flex items-center gap-3">
          <span className="text-sm text-muted-foreground">Icon</span>
          <IconPicker value={icon} onChange={setIcon} />
        </div>

        {error && <p className="text-xs text-destructive">{error}</p>}

        <div className="flex items-center justify-end gap-2 pt-1">
          <button
            onClick={onClose}
            className="px-3 py-1.5 rounded-lg text-sm text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={handleSave}
            disabled={saving || !name.trim()}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium bg-primary text-primary-foreground hover:opacity-90 disabled:opacity-50 transition-all"
          >
            {saving && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
            Save
          </button>
        </div>
      </div>
    </div>
  )
}
