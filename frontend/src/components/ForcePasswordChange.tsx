import { useState } from 'react'
import { api } from '@/lib/api'
import { useAuth } from '@/contexts/AuthContext'
import { Pill } from '@/components/page'
import { AuthError, AuthField, AuthFrame, AuthInput, AuthPanel, AuthPasswordInput, Spinner } from '@/components/auth/AuthPanel'
import { Trans } from '@lingui/react/macro'
import { t } from '@lingui/core/macro'

export function ForcePasswordChange() {
  const { user, refreshUser } = useAuth()
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [confirm, setConfirm] = useState('')
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError('')
    if (next.length < 8) { setError(t`Password must be at least 8 characters.`); return }
    if (next !== confirm) { setError(t`Passwords do not match.`); return }
    setSaving(true)
    try {
      await api.put('/auth/me/password', { current_password: current, new_password: next })
      await refreshUser()
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t`Failed to update password.`)
    } finally {
      setSaving(false)
    }
  }

  const username = user?.username
  return (
    <div className="fixed inset-0 z-50 overflow-y-auto">
      <AuthFrame>
        <AuthPanel
          title={<Trans>Set your password</Trans>}
          subtitle={
            <Trans>Hi <span className="font-medium text-foreground">{username}</span>, your account was created by an admin.
            Set a personal password before continuing.</Trans>
          }
        >
          <form onSubmit={handleSubmit} className="space-y-4">
            <AuthField label={<Trans>Temporary password</Trans>}>
              <AuthPasswordInput value={current} onChange={e => setCurrent(e.target.value)} required autoComplete="current-password" toggleTabIndex={-1} />
            </AuthField>
            <AuthField label={<Trans>New password</Trans>}>
              <AuthPasswordInput value={next} onChange={e => setNext(e.target.value)} required autoComplete="new-password" toggleTabIndex={-1} />
            </AuthField>
            <AuthField label={<Trans>Confirm new password</Trans>}>
              <AuthInput type="password" value={confirm} onChange={e => setConfirm(e.target.value)} required autoComplete="new-password" />
            </AuthField>

            {error && <AuthError>{error}</AuthError>}

            <div className="pt-2">
              <Pill type="submit" variant="primary" size="lg" disabled={saving || !current || !next || !confirm} className="w-full">
                {saving && <Spinner />}
                {saving ? t`Saving…` : t`Set password & continue`}
              </Pill>
            </div>
          </form>
        </AuthPanel>
      </AuthFrame>
    </div>
  )
}
