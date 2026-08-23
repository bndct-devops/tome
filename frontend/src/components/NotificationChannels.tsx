// Settings → Notifications: per-user outbound channels (ntfy / Gotify /
// webhook). The in-app bell only fires on visit; a channel pushes the same
// events (wish fulfilled, new volume detected, goals) the moment they happen.
import { useEffect, useState } from 'react'
import { CheckCircle, Loader2, Plus, Send, Trash2, XCircle } from 'lucide-react'
import { api } from '@/lib/api'
import { cn } from '@/lib/utils'
import { Trans } from '@lingui/react/macro'
import { t, msg } from '@lingui/core/macro'
import { i18n } from '@lingui/core'
import type { MessageDescriptor } from '@lingui/core'

interface Channel {
  id: number
  kind: 'ntfy' | 'gotify' | 'webhook'
  url: string
  has_token: boolean
  enabled: boolean
}

// eslint-disable-next-line lingui/no-unlocalized-strings -- product names
const KIND_LABEL = { ntfy: 'ntfy', gotify: 'Gotify', webhook: 'Webhook' } as const
const URL_HINT: Record<Channel['kind'], string> = {
  ntfy: 'https://ntfy.sh/your-topic',
  gotify: 'https://gotify.example.org',
  webhook: 'https://example.org/hook',
}
const TOKEN_HINT: Record<Channel['kind'], MessageDescriptor> = {
  ntfy: msg`Access token (only for protected topics)`,
  gotify: msg`Application token (required)`,
  webhook: msg`Not used`,
}

export function NotificationChannels() {
  const [channels, setChannels] = useState<Channel[]>([])
  const [kind, setKind] = useState<Channel['kind']>('ntfy')
  const [url, setUrl] = useState('')
  const [token, setToken] = useState('')
  const [adding, setAdding] = useState(false)
  const [testState, setTestState] = useState<Record<number, 'testing' | 'ok' | string>>({})

  const load = () => {
    api.get<Channel[]>('/notification-channels').then(setChannels).catch(() => {})
  }
  useEffect(load, [])

  async function add() {
    if (!url.trim() || adding) return
    setAdding(true)
    try {
      await api.post('/notification-channels', {
        kind, url: url.trim(), token: token.trim() || null,
      })
      setUrl('')
      setToken('')
      load()
    } finally {
      setAdding(false)
    }
  }

  async function test(c: Channel) {
    setTestState(prev => ({ ...prev, [c.id]: 'testing' }))
    try {
      const r = await api.post<{ ok: boolean; error?: string }>(`/notification-channels/${c.id}/test`)
      setTestState(prev => ({ ...prev, [c.id]: r.ok ? 'ok' : (r.error || t`failed`) }))
    } catch {
      setTestState(prev => ({ ...prev, [c.id]: t`request failed` }))
    }
  }

  return (
    <div className="mt-3 rounded-xl border border-border/60 bg-card/50 p-5">
      <p className="text-xs text-muted-foreground mb-4">
        <Trans>Push Tome&apos;s notifications (wish fulfilled, new volume detected, reading
        goals) to ntfy, Gotify, or any webhook the moment they happen — the bell
        above only rings when you visit. Each channel can be tested, paused, or
        removed; tokens are stored server-side and never shown again.</Trans>
      </p>

      {channels.length > 0 && (
        <ul className="mb-4 flex flex-col gap-2">
          {channels.map(c => {
            const testResult = testState[c.id]
            return (
              <li key={c.id} className="flex items-center gap-2 rounded-lg border border-border bg-card px-3 py-2">
                <span className="w-16 shrink-0 text-xs font-semibold text-foreground">{KIND_LABEL[c.kind]}</span>
                <span className={cn('min-w-0 flex-1 truncate text-xs', c.enabled ? 'text-muted-foreground' : 'text-muted-foreground/50 line-through')}>
                  {c.url}
                </span>
                {testResult === 'ok' && <CheckCircle className="h-3.5 w-3.5 shrink-0 text-success" />}
                {testResult && testResult !== 'ok' && testResult !== 'testing' && (
                  <span title={testResult}><XCircle className="h-3.5 w-3.5 shrink-0 text-destructive" /></span>
                )}
                <button
                  onClick={() => test(c)}
                  disabled={testResult === 'testing'}
                  title={t`Send a test notification`}
                  className="rounded-md border border-border p-1.5 text-muted-foreground transition-colors hover:text-foreground disabled:opacity-50"
                >
                  {testResult === 'testing' ? <Loader2 className="h-3 w-3 animate-spin" /> : <Send className="h-3 w-3" />}
                </button>
                <button
                  onClick={() => api.post(`/notification-channels/${c.id}/toggle`).then(load)}
                  className="rounded-md border border-border px-2 py-1 text-[11px] text-muted-foreground transition-colors hover:text-foreground"
                >
                  {c.enabled ? t`Pause` : t`Resume`}
                </button>
                <button
                  onClick={() => api.delete(`/notification-channels/${c.id}`).then(load)}
                  title={t`Remove channel`}
                  className="rounded-md border border-border p-1.5 text-muted-foreground transition-colors hover:text-destructive"
                >
                  <Trash2 className="h-3 w-3" />
                </button>
              </li>
            )
          })}
        </ul>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <select
          value={kind}
          onChange={e => setKind(e.target.value as Channel['kind'])}
          className="rounded-md border border-border bg-background px-2 py-1.5 text-xs focus:outline-none focus:ring-1 focus:ring-ring"
        >
          {/* eslint-disable-next-line lingui/no-unlocalized-strings -- product names */}
          <option value="ntfy">ntfy</option>
          {/* eslint-disable-next-line lingui/no-unlocalized-strings -- product names */}
          <option value="gotify">Gotify</option>
          {/* eslint-disable-next-line lingui/no-unlocalized-strings -- product names */}
          <option value="webhook">Webhook</option>
        </select>
        <input
          value={url}
          onChange={e => setUrl(e.target.value)}
          placeholder={URL_HINT[kind]}
          className="min-w-[220px] flex-1 rounded-md border border-border bg-background px-2.5 py-1.5 text-xs focus:outline-none focus:ring-1 focus:ring-ring"
        />
        {kind !== 'webhook' && (
          <input
            value={token}
            onChange={e => setToken(e.target.value)}
            placeholder={i18n._(TOKEN_HINT[kind])}
            className="min-w-[180px] rounded-md border border-border bg-background px-2.5 py-1.5 text-xs focus:outline-none focus:ring-1 focus:ring-ring"
          />
        )}
        <button
          onClick={add}
          disabled={!url.trim() || adding}
          className="flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-xs font-semibold text-primary-foreground transition-all hover:bg-primary/90 disabled:opacity-50"
        >
          {adding ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Plus className="h-3.5 w-3.5" />}
          <Trans>Add channel</Trans>
        </button>
      </div>
    </div>
  )
}
