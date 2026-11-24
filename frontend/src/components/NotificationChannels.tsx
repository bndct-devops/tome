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
import { Input, Pill, Select, Swap } from '@/components/page'
import { AnimatePresence, m } from 'motion/react'
import { listItem, Unfold } from '@/components/motion'
import { Note, SameWidth } from '@/components/FormMotion'

interface Channel {
  id: number
  kind: 'ntfy' | 'gotify' | 'webhook'
  url: string
  has_token: boolean
  enabled: boolean
  blocked?: boolean
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
  const [addError, setAddError] = useState('')
  const [testState, setTestState] = useState<Record<number, 'testing' | 'ok' | string>>({})

  const load = () => {
    api.get<Channel[]>('/notification-channels').then(setChannels).catch(() => {})
  }
  useEffect(load, [])

  async function add() {
    if (!url.trim() || adding) return
    setAdding(true)
    setAddError('')
    try {
      await api.post('/notification-channels', {
        kind, url: url.trim(), token: token.trim() || null,
      })
      setUrl('')
      setToken('')
      load()
    } catch (e) {
      // The server says why it refused the channel (English, like all
      // backend errors); without this the button did nothing visible.
      setAddError(e instanceof Error ? e.message : String(e))
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
    <div>
      <p className="-mt-3 text-[14px] text-muted-foreground mb-5 max-w-[70ch]">
        <Trans>Push Tome&apos;s notifications (wish fulfilled, new volume detected, reading
        goals) to ntfy, Gotify, or any webhook the moment they happen; the bell
        above only rings when you visit. Each channel can be tested, paused, or
        removed; tokens are stored server-side and never shown again.</Trans>
      </p>

      <Unfold open={channels.length > 0}>
        <ul className="mb-5 flex flex-col border-y border-border/70 divide-y divide-border/70">
          <AnimatePresence initial={false}>
          {channels.map(c => {
            const testResult = testState[c.id]
            return (
              <m.li key={c.id} {...listItem} className="flex items-center gap-2 py-2.5">
                <span className="w-16 shrink-0 text-[14px] font-semibold text-foreground">{KIND_LABEL[c.kind]}</span>
                <div className="min-w-0 flex-1">
                  <span className={cn('block truncate text-[13px] transition-colors duration-150', c.enabled ? 'text-muted-foreground' : 'text-muted-foreground/50 line-through')}>
                    {c.url}
                  </span>
                  {c.blocked && (
                    <span className="mt-0.5 flex items-center gap-1.5 text-[12px] text-destructive">
                      <XCircle className="h-3.5 w-3.5 shrink-0" />
                      <Trans>Not sent: this is not a public internet address. An admin can allow this host.</Trans>
                    </span>
                  )}
                </div>
                {/* The result's slot is always there, so the row does not shift when it arrives. */}
                <Swap id={testResult === 'ok' ? 'ok' : testResult && testResult !== 'testing' ? 'failed' : 'none'} className="flex w-3.5 shrink-0">
                  {testResult === 'ok' && <CheckCircle className="h-3.5 w-3.5 shrink-0 text-success" />}
                  {testResult && testResult !== 'ok' && testResult !== 'testing' && (
                    <span title={testResult}><XCircle className="h-3.5 w-3.5 shrink-0 text-destructive" /></span>
                  )}
                </Swap>
                <Pill
                  icon
                  variant="quiet"
                  onClick={() => test(c)}
                  disabled={testResult === 'testing'}
                  title={t`Send a test notification`}
                  aria-label={t`Send a test notification`}
                >
                  {testResult === 'testing' ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
                </Pill>
                <Pill
                  onClick={() => api.post(`/notification-channels/${c.id}/toggle`).then(load)}
                  variant="secondary"
                >
                  <SameWidth active={c.enabled ? 0 : 1}>{[t`Pause`, t`Resume`]}</SameWidth>
                </Pill>
                <Pill
                  icon
                  variant="destructive"
                  onClick={() => api.delete(`/notification-channels/${c.id}`).then(load)}
                  title={t`Remove channel`}
                  aria-label={t`Remove channel`}
                >
                  <Trash2 className="h-4 w-4" />
                </Pill>
              </m.li>
            )
          })}
          </AnimatePresence>
        </ul>
      </Unfold>

      <div className="flex flex-wrap items-center gap-2">
        <Select
          value={kind}
          onChange={e => setKind(e.target.value as Channel['kind'])}
          aria-label={t`Channel type`}
          className="w-auto min-w-[8rem]"
        >
          <option value="ntfy">ntfy</option>
          {/* eslint-disable-next-line lingui/no-unlocalized-strings -- product names */}
          <option value="gotify">Gotify</option>
          {/* eslint-disable-next-line lingui/no-unlocalized-strings -- product names */}
          <option value="webhook">Webhook</option>
        </Select>
        <Input
          value={url}
          onChange={e => setUrl(e.target.value)}
          placeholder={URL_HINT[kind]}
          aria-label={t`Channel URL`}
          className="w-auto min-w-[220px] flex-1"
        />
        {kind !== 'webhook' && (
          <Input
            value={token}
            onChange={e => setToken(e.target.value)}
            placeholder={i18n._(TOKEN_HINT[kind])}
            aria-label={t`Token`}
            className="w-auto min-w-[180px] flex-1 sm:flex-none animate-in fade-in-40 duration-150 motion-reduce:animate-none"
          />
        )}
        <Pill
          onClick={add}
          disabled={!url.trim() || adding}
          variant="primary"
        >
          {adding ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Plus className="h-3.5 w-3.5" />}
          <Trans>Add channel</Trans>
        </Pill>
      </div>
      <Note value={addError}>{e => (
        <p role="alert" className="pt-2 flex items-center gap-1.5 text-[13px] text-destructive">
          <XCircle className="h-3.5 w-3.5 shrink-0" />
          {e}
        </p>
      )}</Note>
    </div>
  )
}
