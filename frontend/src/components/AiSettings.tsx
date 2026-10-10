// Settings card for the AI provider: the user's own key, the admin's instance
// settings (master switch, instance key, sharing, threshold, per-feature
// switches and models) and this month's spend. The parent section renders
// this only when useAiStatus() says AI exists for this user at all
// (TOME_AI_ENABLED on, member or admin, and for members the instance switch on).
// One exception: a member who stored a key while AI was on gets ``keyOnly``
// after an admin switches it off, so they can still delete their secret.
import { useEffect, useState } from 'react'
import { Trans, Plural, useLingui } from '@lingui/react/macro'
import { AlertTriangle, ExternalLink, Key, Loader2, Server, Sparkles } from 'lucide-react'
import { useToast } from '@/contexts/ToastContext'
import { cn } from '@/lib/utils'
import {
  aiFeatureDescription, aiFeatureLabel, getUsage, removeKey, setKey, updateInstance,
  type AiFeatureKey, type AiInstanceUpdate, type AiStatus, type AiUsageSummary, type AiUsageTotals,
} from '@/lib/ai'
import { AiBadge } from '@/components/AiBadge'

const KEY_CONSOLE_URL = 'https://console.anthropic.com/settings/keys'
const DISCUSSIONS_URL = 'https://github.com/bndct-devops/tome/discussions'

export function AiSettings({ status, isAdmin, keyOnly = false }: { status: AiStatus; isAdmin: boolean; keyOnly?: boolean }) {
  const { t } = useLingui()
  const { toast } = useToast()
  const provider = status.provider_label

  if (keyOnly) {
    return (
      <div className="mt-4 rounded-xl border border-border bg-card p-5 space-y-3">
        <p className="text-xs text-muted-foreground">
          <Trans>AI features are switched off on this server. You can still remove your stored key.</Trans>
        </p>
        <KeyRow
          icon={<Key className="w-3 h-3" />}
          title={t`Your API key`}
          isSet={status.has_own_key}
          suffix={status.own_key_suffix}
          setAt={status.own_key_set_at}
          onSave={async () => {}}
          onRemove={async () => { await removeKey() }}
          removeLabel={t`Remove`}
          canReplace={false}
        />
      </div>
    )
  }

  async function saveInstance(body: AiInstanceUpdate, failure: string): Promise<boolean> {
    try {
      await updateInstance(body)
      return true
    } catch (err) {
      toast.error(err instanceof Error ? err.message : failure)
      return false
    }
  }

  return (
    <>
      <div className="mt-4 rounded-xl border border-border bg-card divide-y divide-border overflow-hidden">

        {/* Explanation */}
        <div className="p-5 space-y-3">
          <div className="flex items-start justify-between gap-3">
            <div className="flex items-start gap-3">
              <div className="p-1.5 rounded-lg bg-primary/10 mt-0.5 shrink-0">
                <Sparkles className="w-3.5 h-3.5 text-primary" />
              </div>
              <div>
                <p className="text-sm font-medium text-foreground flex items-center gap-2">
                  <Trans>AI features</Trans> <AiBadge />
                </p>
                <p className="text-xs text-muted-foreground mt-0.5">
                  <Trans>Optional helpers that propose metadata for you to confirm. Nothing is ever changed without your click. They run on {provider} with an API key: your own, or one your admin shares.</Trans>{' '}
                  <Trans>Titles, authors and a few pages of text are sent to {provider} when you use these features. Nothing is sent until you click an AI button.</Trans>
                </p>
              </div>
            </div>
            <a
              href={KEY_CONSOLE_URL}
              target="_blank"
              rel="noopener noreferrer"
              className="shrink-0 inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-primary transition-colors"
            >
              <Trans>Get a key</Trans> <ExternalLink className="w-3 h-3" />
            </a>
          </div>

          {isAdmin && status.instance_enabled === false && (
            <div className="flex items-center gap-2 rounded-lg bg-warning/10 border border-warning/20 px-3 py-2">
              <AlertTriangle className="w-3.5 h-3.5 text-warning shrink-0" />
              <p className="text-xs text-warning font-medium">
                <Trans>AI features are switched off for everyone. Members do not see this section or any AI button until you turn them back on below.</Trans>
              </p>
            </div>
          )}
        </div>

        {/* Own key */}
        <div className="p-5 space-y-2">
          <KeyRow
            icon={<Key className="w-3 h-3" />}
            title={t`Your API key`}
            isSet={status.has_own_key}
            suffix={status.own_key_suffix}
            setAt={status.own_key_set_at}
            onSave={async key => { await setKey(key) }}
            onRemove={async () => { await removeKey() }}
            removeLabel={t`Remove`}
          />
          {!status.has_own_key && (
            <p className="text-xs text-muted-foreground">
              {status.key_source === 'instance'
                ? (isAdmin
                  ? <Trans>Your calls use the instance key below. Add your own key to bill your own {provider} account instead.</Trans>
                  : <Trans>You are using the instance key your admin shares. Add your own key to bill your own {provider} account instead.</Trans>)
                : <Trans>Add a key to turn on the AI buttons. It is checked with {provider} before it is saved and stored encrypted.</Trans>}
            </p>
          )}
        </div>

        {/* Instance (admins) */}
        {isAdmin && (
          <InstanceSettings status={status} save={saveInstance} />
        )}

        {/* This month */}
        <UsageBlock isAdmin={isAdmin} provider={provider} />
      </div>

      <p className="text-xs text-muted-foreground mt-3">
        <Trans>Want a hosted option that needs no key? <a href={DISCUSSIONS_URL} target="_blank" rel="noopener noreferrer" className="text-primary hover:underline">Say so in the discussion.</a></Trans>
      </p>
    </>
  )
}

// ── key row (own key and instance key) ───────────────────────────────────────

function KeyRow({
  icon, title, isSet, suffix, setAt, sourceNote, onSave, onRemove, removeLabel, canRemove = true,
  canReplace = true,
}: {
  icon: React.ReactNode
  title: string
  isSet: boolean
  suffix: string | null | undefined
  setAt?: string | null
  sourceNote?: string
  onSave: (key: string) => Promise<void>
  onRemove: () => Promise<void>
  removeLabel: string
  canRemove?: boolean
  canReplace?: boolean
}) {
  const { t, i18n } = useLingui()
  const [editing, setEditing] = useState(false)
  const [value, setValue] = useState('')
  const [saving, setSaving] = useState(false)
  const [removing, setRemoving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const showInput = !isSet || editing

  async function save() {
    const key = value.trim()
    if (!key || saving) return
    setSaving(true)
    setError(null)
    try {
      await onSave(key)
      setValue('')
      setEditing(false)
    } catch (err) {
      setError(err instanceof Error ? err.message : t`Could not save the key`)
    } finally {
      setSaving(false)
    }
  }

  async function remove() {
    setRemoving(true)
    setError(null)
    try {
      await onRemove()
    } catch (err) {
      setError(err instanceof Error ? err.message : t`Could not remove the key`)
    } finally {
      setRemoving(false)
    }
  }

  function cancel() {
    setEditing(false)
    setValue('')
    setError(null)
  }

  return (
    <div className="space-y-2">
      <p className="text-xs font-medium text-muted-foreground flex items-center gap-1.5">
        {icon} {title}
      </p>

      {!showInput ? (
        <div className="flex items-center justify-between gap-3">
          <p className="text-sm text-foreground flex flex-wrap items-baseline gap-x-2">
            <code className="font-mono text-sm">{suffix}</code>
            {setAt && (() => { const when = new Date(setAt).toLocaleDateString(i18n.locale); return (
              <span className="text-xs text-muted-foreground"><Trans>added {when}</Trans></span>
            ) })()}
            {sourceNote && <span className="text-xs text-muted-foreground">{sourceNote}</span>}
          </p>
          <div className="flex items-center gap-3 shrink-0">
            {canReplace && (
              <button
                onClick={() => { setEditing(true); setError(null) }}
                className="text-xs text-primary hover:opacity-80 transition-opacity"
              >
                <Trans>Replace</Trans>
              </button>
            )}
            {canRemove && (
              <button
                onClick={() => void remove()}
                disabled={removing}
                className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-destructive transition-colors disabled:opacity-50"
              >
                {removing && <Loader2 className="w-3 h-3 animate-spin" />}
                {removeLabel}
              </button>
            )}
          </div>
        </div>
      ) : (
        <div>
          <div className="flex gap-2">
            <input
              type="password"
              value={value}
              onChange={e => { setValue(e.target.value); setError(null) }}
              onKeyDown={e => {
                if (e.key === 'Enter') void save()
                if (e.key === 'Escape' && isSet) cancel()
              }}
              placeholder={t`Paste your API key`}
              autoComplete="off"
              spellCheck={false}
              aria-invalid={error ? true : undefined}
              className={cn(
                'flex-1 min-w-0 rounded-md border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground/60 focus:outline-none',
                error ? 'border-destructive focus:border-destructive' : 'border-border focus:border-primary',
              )}
            />
            <button
              onClick={() => void save()}
              disabled={saving || !value.trim()}
              className="px-3 py-2 rounded-md bg-primary text-primary-foreground text-sm font-medium hover:opacity-90 disabled:opacity-50 transition-opacity inline-flex items-center gap-2 shrink-0"
            >
              {saving && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
              {saving ? t`Checking…` : t`Save key`}
            </button>
            {isSet && (
              <button
                onClick={cancel}
                disabled={saving}
                className="px-3 py-2 rounded-md border border-border text-sm text-muted-foreground hover:text-foreground hover:bg-muted transition-colors disabled:opacity-50 shrink-0"
              >
                <Trans>Cancel</Trans>
              </button>
            )}
          </div>
        </div>
      )}
      {error && <p className="text-xs text-destructive">{error}</p>}
    </div>
  )
}

// ── instance settings (admins) ───────────────────────────────────────────────

function InstanceSettings({
  status, save,
}: {
  status: AiStatus
  save: (body: AiInstanceUpdate, failure: string) => Promise<boolean>
}) {
  const { t } = useLingui()
  const provider = status.provider_label
  const [busy, setBusy] = useState<string | null>(null)

  const thresholdPct = Math.round(status.confidence_threshold * 100)
  const [threshold, setThreshold] = useState(String(thresholdPct))
  // Follow the saved value when it changes underneath us (render-time sync).
  const [syncedPct, setSyncedPct] = useState(thresholdPct)
  if (syncedPct !== thresholdPct) {
    setSyncedPct(thresholdPct)
    setThreshold(String(thresholdPct))
  }

  async function run(id: string, body: AiInstanceUpdate) {
    setBusy(id)
    try {
      await save(body, t`Could not save AI settings`)
    } finally {
      setBusy(null)
    }
  }

  async function commitThreshold() {
    const n = Number(threshold)
    if (threshold.trim() === '' || !Number.isFinite(n)) {
      setThreshold(String(thresholdPct))
      return
    }
    const pct = Math.min(100, Math.max(0, Math.round(n)))
    setThreshold(String(pct))
    if (pct !== thresholdPct) await run('threshold', { confidence_threshold: pct / 100 })
  }

  const envKey = status.instance_key_source === 'env'
  const featureKeys = Object.keys(status.features) as AiFeatureKey[]

  return (
    <div className="p-5 space-y-5">
      <p className="text-xs font-medium text-muted-foreground flex items-center gap-1.5">
        <Server className="w-3 h-3" /> <Trans>Instance settings</Trans>
        <span className="text-muted-foreground/60 font-normal"><Trans>(admins only)</Trans></span>
      </p>

      <SettingRow
        title={t`AI features on`}
        description={t`Off hides every AI button and this section from members. Keys and usage are kept.`}
      >
        <Switch
          checked={status.instance_enabled !== false}
          busy={busy === 'enabled'}
          label={t`AI features on`}
          onChange={v => void run('enabled', { enabled: v })}
        />
      </SettingRow>

      <KeyRow
        icon={<Key className="w-3 h-3" />}
        title={t`Instance key`}
        isSet={!!status.instance_key_set}
        suffix={status.instance_key_suffix}
        sourceNote={envKey ? t`from TOME_ANTHROPIC_API_KEY` : undefined}
        canRemove={!envKey}
        removeLabel={t`Remove`}
        onSave={async key => {
          await updateInstance({ instance_key: key })
        }}
        onRemove={async () => {
          await updateInstance({ clear_instance_key: true })
        }}
      />
      {!status.instance_key_set && (
        <p className="text-xs text-muted-foreground -mt-3">
          <Trans>Used by admins without their own key, and by members when you share it below. You can also set it with TOME_ANTHROPIC_API_KEY.</Trans>
        </p>
      )}

      <SettingRow
        title={t`Let members use the instance key`}
        description={t`Members without their own key use the instance key, billed to its ${provider} account.`}
      >
        <Switch
          checked={!!status.share_instance_key}
          busy={busy === 'share'}
          label={t`Let members use the instance key`}
          onChange={v => void run('share', { share_instance_key: v })}
        />
      </SettingRow>

      <SettingRow
        title={t`Confidence threshold`}
        description={t`"Accept the obvious" applies every proposal at or above this confidence. Proposals below it stay in the review list.`}
      >
        <div className="flex items-center gap-1.5 shrink-0">
          <input
            type="number"
            min={0}
            max={100}
            step={1}
            inputMode="numeric"
            value={threshold}
            onChange={e => setThreshold(e.target.value)}
            onBlur={() => void commitThreshold()}
            onKeyDown={e => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur() }}
            disabled={busy === 'threshold'}
            aria-label={t`Confidence threshold`}
            className="w-16 h-8 rounded-md border border-border bg-background px-2 text-sm text-right tabular-nums focus:outline-none focus:ring-1 focus:ring-ring disabled:opacity-50"
          />
          <span className="text-sm text-muted-foreground">%</span>
        </div>
      </SettingRow>

      {/* Per-feature table */}
      <div className="space-y-2">
        <p className="text-xs font-medium text-muted-foreground"><Trans>Features</Trans></p>
        <div className="rounded-lg border border-border overflow-hidden text-xs divide-y divide-border">
          <div className="hidden sm:grid grid-cols-[1fr_4rem_12rem] gap-3 px-3 py-2 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground bg-muted/40">
            <span><Trans>Feature</Trans></span>
            <span><Trans>On</Trans></span>
            <span><Trans>Model</Trans></span>
          </div>
          {featureKeys.map(key => {
            const f = status.features[key]
            const label = aiFeatureLabel(key, f.label)
            const defaultModel = f.default_model
            return (
              <div key={key} className="grid grid-cols-[1fr_auto] sm:grid-cols-[1fr_4rem_12rem] gap-x-3 gap-y-2 items-center px-3 py-2.5">
                <div className="min-w-0">
                  <p className="text-sm text-foreground">{label}</p>
                  <p className="text-xs text-muted-foreground mt-0.5">{aiFeatureDescription(key, f.description)}</p>
                </div>
                <div>
                  <Switch
                    checked={f.enabled}
                    busy={busy === `${key}.enabled`}
                    label={label}
                    onChange={v => void run(`${key}.enabled`, { features: { [key]: { enabled: v } } })}
                  />
                </div>
                <select
                  value={f.model ?? 'default'}
                  disabled={busy === `${key}.model`}
                  onChange={e => void run(`${key}.model`, { features: { [key]: { model: e.target.value } } })}
                  aria-label={t`Model for ${label}`}
                  className="col-span-2 sm:col-span-1 h-8 w-full rounded-md border border-border bg-background px-2 text-xs font-mono focus:outline-none focus:ring-1 focus:ring-ring disabled:opacity-50"
                >
                  <option value="default">{t`Default (${defaultModel})`}</option>
                  {status.models.map(m => <option key={m} value={m}>{m}</option>)}
                </select>
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}

// ── this month's spend ───────────────────────────────────────────────────────

function UsageBlock({ isAdmin, provider }: { isAdmin: boolean; provider: string }) {
  const { t, i18n } = useLingui()
  const [allUsers, setAllUsers] = useState(false)
  const [data, setData] = useState<AiUsageSummary | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let cancelled = false
    getUsage(undefined, isAdmin && allUsers)
      .then(d => { if (!cancelled) { setData(d); setFailed(false) } })
      .catch(() => { if (!cancelled) setFailed(true) })
    return () => { cancelled = true }
  }, [isAdmin, allUsers])

  const money = (n: number) => new Intl.NumberFormat(i18n.locale, {
    style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: n > 0 && n < 1 ? 3 : 2,
  }).format(n)
  const num = (n: number) => n.toLocaleString(i18n.locale)
  const monthName = data ? (() => {
    const [y, m] = data.month.split('-').map(Number)
    return new Date(y, (m || 1) - 1, 1).toLocaleDateString(i18n.locale, { month: 'long', year: 'numeric' })
  })() : null

  const empty = !!data && data.total.calls === 0

  return (
    <div className="p-5 space-y-3">
      <div className="flex items-center justify-between gap-3">
        <p className="text-xs font-medium text-muted-foreground">
          <Trans>This month</Trans>
          {monthName && <span className="font-normal text-muted-foreground/70"> · {monthName}</span>}
        </p>
        {isAdmin && (
          <label className="flex items-center gap-2 text-xs text-muted-foreground cursor-pointer select-none shrink-0">
            <button
              type="button"
              role="switch"
              aria-checked={allUsers}
              onClick={() => { setAllUsers(v => !v); setData(null) }}
              className={cn(
                'relative w-8 h-[18px] rounded-full transition-colors shrink-0',
                allUsers ? 'bg-primary' : 'bg-muted-foreground/30'
              )}
            >
              <span className={cn(
                'absolute left-0 top-0.5 w-3.5 h-3.5 rounded-full bg-white transition-transform',
                allUsers ? 'translate-x-[16px]' : 'translate-x-0.5'
              )} />
            </button>
            <Trans>All users</Trans>
          </label>
        )}
      </div>

      {failed ? (
        <p className="text-xs text-muted-foreground"><Trans>Could not load usage.</Trans></p>
      ) : !data ? (
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <Loader2 className="w-3.5 h-3.5 animate-spin" /> <Trans>Loading…</Trans>
        </div>
      ) : empty ? (
        <p className="text-xs text-muted-foreground"><Trans>No usage this month</Trans></p>
      ) : (
        <div className="space-y-3">
          <div className="flex items-baseline gap-3">
            <span className="text-2xl font-display text-foreground tabular-nums">{money(data.total.cost_usd)}</span>
            <span className="text-xs text-muted-foreground">
              <Plural value={data.total.calls} one="# call" other="# calls" />
              {' · '}
              {(() => { const tokens = num(data.total.input_tokens + data.total.output_tokens); return <Trans>{tokens} tokens</Trans> })()}
            </span>
          </div>
          <p className="text-[11px] text-muted-foreground/70">
            <Trans>Estimated from list prices. Your {provider} invoice is the source of truth.</Trans>
          </p>

          <UsageTable
            heading={t`Feature`}
            rows={data.by_feature.map(r => ({ id: r.feature, name: aiFeatureLabel(r.feature, r.label), ...r }))}
            money={money}
            num={num}
          />

          {data.scope === 'all' && data.by_user && data.by_user.length > 0 && (
            <UsageTable
              heading={t`User`}
              rows={data.by_user.map(r => ({
                id: String(r.user_id ?? 'deleted'),
                name: r.username ?? t`Deleted user`,
                ...r,
              }))}
              money={money}
              num={num}
            />
          )}
        </div>
      )}
    </div>
  )
}

function UsageTable({
  heading, rows, money, num,
}: {
  heading: string
  rows: (AiUsageTotals & { id: string; name: string })[]
  money: (n: number) => string
  num: (n: number) => string
}) {
  if (rows.length === 0) return null
  return (
    <div className="rounded-lg border border-border overflow-hidden text-xs divide-y divide-border">
      <div className="grid grid-cols-[1fr_4rem_5rem] sm:grid-cols-[1fr_4rem_6rem_6rem_5rem] gap-3 px-3 py-2 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground bg-muted/40">
        <span>{heading}</span>
        <span className="text-right"><Trans>Calls</Trans></span>
        <span className="text-right hidden sm:block"><Trans>Tokens in</Trans></span>
        <span className="text-right hidden sm:block"><Trans>Tokens out</Trans></span>
        <span className="text-right"><Trans>Cost</Trans></span>
      </div>
      {rows.map(r => (
        <div key={r.id} className="grid grid-cols-[1fr_4rem_5rem] sm:grid-cols-[1fr_4rem_6rem_6rem_5rem] gap-3 px-3 py-2 tabular-nums">
          <span className="text-foreground truncate">{r.name}</span>
          <span className="text-right text-muted-foreground">{num(r.calls)}</span>
          <span className="text-right text-muted-foreground hidden sm:block">{num(r.input_tokens)}</span>
          <span className="text-right text-muted-foreground hidden sm:block">{num(r.output_tokens)}</span>
          <span className="text-right text-foreground">{money(r.cost_usd)}</span>
        </div>
      ))}
    </div>
  )
}

// ── small pieces ─────────────────────────────────────────────────────────────

function SettingRow({ title, description, children }: {
  title: string
  description: string
  children: React.ReactNode
}) {
  return (
    <div className="flex items-center justify-between gap-3">
      <div>
        <p className="text-sm text-foreground">{title}</p>
        <p className="text-xs text-muted-foreground mt-0.5">{description}</p>
      </div>
      {children}
    </div>
  )
}

function Switch({ checked, onChange, busy = false, label }: {
  checked: boolean
  onChange: (v: boolean) => void
  busy?: boolean
  label: string
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={busy}
      onClick={() => onChange(!checked)}
      className={cn(
        'relative w-9 h-5 rounded-full transition-colors shrink-0 disabled:opacity-60',
        checked ? 'bg-primary' : 'bg-muted-foreground/30'
      )}
    >
      <span className={cn(
        'absolute left-0 top-0.5 w-4 h-4 rounded-full bg-white transition-transform',
        checked ? 'translate-x-[18px]' : 'translate-x-0.5'
      )} />
    </button>
  )
}
