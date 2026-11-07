import { useState, useEffect, useId, useRef } from 'react'
import { Loader2 } from 'lucide-react'
import { IconPicker } from '@/components/Sidebar'
import { ModalShell } from '@/components/ModalShell'
import { Pill, Input, FormLabel } from '@/components/page'
import { Trans } from '@lingui/react/macro'
import { t } from '@lingui/core/macro'

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
  const nameId = useId()

  useEffect(() => {
    inputRef.current?.focus()
  }, [])

  async function handleSave() {
    if (!name.trim() || saving) return
    setSaving(true)
    setError('')
    try {
      await onSave(name.trim(), icon)
      onClose()
    } catch (e) {
      setError(e instanceof Error ? e.message : t`Failed to save`)
    } finally {
      setSaving(false)
    }
  }

  return (
    // Mounted-on-open by callers, so `open` is always true: animated enter,
    // instant exit (the tree unmounts with the caller's conditional).
    <ModalShell
      open
      onClose={onClose}
      size="sm"
      title={title}
      footer={
        <>
          <Pill variant="quiet" onClick={onClose}><Trans>Cancel</Trans></Pill>
          <Pill variant="primary" onClick={handleSave} disabled={saving || !name.trim()}>
            {saving && <Loader2 className="w-4 h-4 animate-spin" />}
            <Trans>Save</Trans>
          </Pill>
        </>
      }
    >
      <div className="flex flex-col gap-4 pt-1">
        <div>
          <FormLabel htmlFor={nameId}><Trans>Name</Trans></FormLabel>
          <Input
            id={nameId}
            ref={inputRef}
            value={name}
            onChange={e => { setName(e.target.value); setError('') }}
            onKeyDown={e => { if (e.key === 'Enter') handleSave() }}
            invalid={!!error}
          />
        </div>
        <div>
          <FormLabel><Trans>Icon</Trans></FormLabel>
          <IconPicker value={icon} onChange={setIcon} inline />
        </div>
        {error && <p className="text-[13px] text-destructive">{error}</p>}
      </div>
    </ModalShell>
  )
}
