// Settings -> Import reading history -> CrossPoint Sync: read a CrossPoint
// Sync account export (JSON) into this account. Two steps over one server code
// path: the preview runs the whole import and rolls it back, the import
// commits it. The report is the product here: it says what happened to every
// book and why anything was skipped, and can be copied as plain text.
import { useRef, useState } from 'react'
import { AlertTriangle, Check, CheckCircle2, Copy, FileUp, Loader2, Upload } from 'lucide-react'
import { api } from '@/lib/api'
import { cn } from '@/lib/utils'
import { Plural, Trans, useLingui } from '@lingui/react/macro'
import { msg } from '@lingui/core/macro'
import { i18n, type MessageDescriptor } from '@lingui/core'
import { Pill } from '@/components/page'

interface SkipReason {
  reason: string
  count: number
  examples: Record<string, string | number | null>[]
}

interface Tally {
  in_file: number
  counts: Record<string, number>
  skipped: number
  skip_reasons: SkipReason[]
}

interface BookRow {
  document: string
  documents: string[]
  title: string | null
  author: string | null
  outcome: string
  reason: string | null
  book_id: number | null
  book_title: string | null
  file_less: boolean
  conflict: boolean
  sessions_in_file: number
  seconds_in_file: number
  sessions_new: number
  seconds_new: number
  clippings_new: number
  status_change: string | null
  source_note: string | null
  reader_pages: number | null
}

interface Report {
  applied: boolean
  nothing_new: boolean
  file: {
    exported_at: string | null
    source_username: string | null
    bytes: number
    sections: Record<string, number>
    unknown_sections: Record<string, number>
    unknown_columns: Record<string, string[]>
  }
  books: Tally
  sessions: Tally
  statuses: Tally
  clippings: Tally
  daily: Tally
  devices: Tally
  book_pages: Tally
  also_written: Tally
  progress: Tally
  seconds_added: number
  first_session: string | null
  last_session: string | null
  book_list: BookRow[]
  not_imported: { section: string; rows: number; why: string }[]
  notes: string[]
  text: string
}

const OUTCOME: Record<string, MessageDescriptor> = {
  already_linked: msg`Already linked to`,
  already_linked_more: msg`Already linked to`,
  hash_link: msg`Linked (a file Tome served) to`,
  matched: msg`Linked to library book`,
  file_less_existing: msg`Linked to your earlier book without a file`,
  created: msg`New book without a file`,
  unlinked: msg`Left unlinked`,
}

function hm(seconds: number): string {
  const total = Math.floor(seconds / 60)
  if (total < 60) return i18n._(msg`${total} min`)
  const h = Math.floor(total / 60)
  const m = String(total % 60).padStart(2, '0')
  return i18n._(msg`${h} h ${m} min`)
}

export function CrossPointImport() {
  const { t } = useLingui()
  const [file, setFile] = useState<File | null>(null)
  const [report, setReport] = useState<Report | null>(null)
  const [phase, setPhase] = useState<'idle' | 'previewing' | 'importing'>('idle')
  const [error, setError] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)
  const textRef = useRef<HTMLTextAreaElement>(null)

  async function send(f: File, apply: boolean) {
    setPhase(apply ? 'importing' : 'previewing')
    setError(null)
    setCopied(false)
    const form = new FormData()
    form.append('file', f)
    try {
      const r = await api.upload<Report>(apply ? '/import/crosspoint/apply' : '/import/crosspoint/preview', form)
      setReport(r)
    } catch (e) {
      setError(e instanceof Error ? e.message : t`Could not read that file`)
      if (!apply) setReport(null)
    } finally {
      setPhase('idle')
    }
  }

  async function copy() {
    if (!report) return
    try {
      await navigator.clipboard.writeText(report.text)
      setCopied(true)
    } catch {
      // No clipboard over plain http: select the text for a manual copy.
      const el = textRef.current
      if (el) {
        el.closest('details')?.setAttribute('open', '')
        el.focus()
        el.select()
      }
    }
  }

  function reset() {
    setReport(null)
    setFile(null)
    setError(null)
    setCopied(false)
  }

  const busy = phase !== 'idle'

  return (
    <div className="mt-8 border-t border-border/70 pt-6">
      <h3 className="text-[15px] font-semibold text-foreground"><Trans>CrossPoint Sync account</Trans></h3>
      <p className="mt-1 mb-4 max-w-[70ch] text-[14px] text-muted-foreground">
        <Trans>Moving a CrossInk reader from CrossPoint Sync to Tome? Your earlier reading sessions are only on
        that server. Export them there (Settings, Account migration, Export account data) and choose the file
        here. You see what would happen first; nothing is written until you import. Importing the same file
        again adds nothing twice, and nothing you already have in Tome is changed or lowered.</Trans>
      </p>

      <input
        ref={fileRef}
        type="file"
        accept=".json,application/json"
        className="hidden"
        onChange={e => {
          const f = e.target.files?.[0]
          e.target.value = ''
          if (f) { setFile(f); setReport(null); send(f, false) }
        }}
      />

      {!report && (
        <div className="flex flex-wrap items-center gap-3">
          <Pill onClick={() => fileRef.current?.click()} disabled={busy} variant="secondary">
            {phase === 'previewing' ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <FileUp className="h-3.5 w-3.5" />}
            <Trans>Choose export file</Trans>
          </Pill>
          {phase === 'previewing' && <span className="text-[13px] text-muted-foreground"><Trans>Reading the file...</Trans></span>}
        </div>
      )}
      {error && (
        <p className="mt-3 flex max-w-[80ch] items-start gap-1.5 text-[13px] text-destructive">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" /> <span className="break-words">{error}</span>
        </p>
      )}

      {report && (
        <div className="space-y-4">
          <ReportHeader report={report} fileName={file?.name ?? null} />
          <Summary report={report} />
          <Skips report={report} />
          <Books report={report} />
          <NotImported report={report} />

          <details className="text-[13px] text-muted-foreground">
            <summary className="cursor-pointer select-none"><Trans>Report as text</Trans></summary>
            <textarea
              ref={textRef}
              readOnly
              value={report.text}
              className="mt-2 h-56 w-full resize-y rounded-lg border border-border bg-background p-2 font-mono text-[12px] leading-snug text-foreground"
            />
          </details>

          <div className="flex flex-wrap items-center gap-2">
            {!report.applied && (
              <Pill
                variant="primary"
                onClick={() => file && send(file, true)}
                disabled={busy || report.nothing_new || !file}
              >
                {phase === 'importing' ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Upload className="h-3.5 w-3.5" />}
                <Trans>Import</Trans>
              </Pill>
            )}
            <Pill variant="secondary" onClick={copy}>
              {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
              {copied ? <Trans>Copied</Trans> : <Trans>Copy report</Trans>}
            </Pill>
            <Pill variant="quiet" onClick={reset} disabled={busy}>
              {report.applied ? <Trans>Done</Trans> : <Trans>Cancel</Trans>}
            </Pill>
          </div>
          <p className="text-[12px] text-muted-foreground">
            <Trans>The report holds book titles from your file. Copy it to the Tome developers if something looks wrong.</Trans>
          </p>
        </div>
      )}
    </div>
  )
}

function ReportHeader({ report, fileName }: { report: Report; fileName: string | null }) {
  const { t, i18n } = useLingui()
  const exported = report.file.exported_at ? new Date(report.file.exported_at) : null
  const when = exported && !isNaN(exported.getTime()) ? i18n.date(exported, { dateStyle: 'medium', timeStyle: 'short' }) : report.file.exported_at ?? t`an unknown time`
  const user = report.file.source_username ?? '?'
  const size = (report.file.bytes / 1048576).toFixed(2)
  const name = fileName ?? ''
  return (
    <div>
      {report.applied ? (
        <p className="flex items-center gap-1.5 text-[14px] font-medium text-success">
          <CheckCircle2 className="h-4 w-4" />
          {report.nothing_new ? <Trans>Nothing new was in the file.</Trans> : <Trans>Imported.</Trans>}
        </p>
      ) : report.nothing_new ? (
        <p className="text-[14px] font-medium text-foreground"><Trans>Nothing new: everything in this file is already in Tome.</Trans></p>
      ) : (
        <p className="text-[14px] font-medium text-foreground"><Trans>Preview: this is what importing would do. Nothing has been written yet.</Trans></p>
      )}
      <p className="mt-0.5 break-words text-[13px] text-muted-foreground">
        <Trans>{name} exported {when} by {user}, {size} MiB</Trans>
      </p>
      {report.notes.map((n, i) => <p key={i} className="text-[13px] text-muted-foreground">{n}</p>)}
    </div>
  )
}

function Summary({ report }: { report: Report }) {
  const { t } = useLingui()
  const rows: { label: string; tally: Tally; parts: [string, string][] }[] = [
    { label: t`Books`, tally: report.books, parts: [
      ['matched', t`linked to library books`], ['hash_link', t`linked by file`],
      ['file_less_existing', t`linked to earlier books without a file`], ['created', t`new books without a file`],
      ['already_linked', t`already linked`], ['already_linked_more', t`already linked`]] },
    { label: t`Reading sessions`, tally: report.sessions, parts: [
      ['created', t`new`], ['extended', t`longer than in Tome`], ['unchanged', t`already in Tome`],
      ['deleted_in_tome', t`deleted in Tome, left deleted`]] },
    { label: t`Read status`, tally: report.statuses, parts: [
      ['finished', t`marked finished`], ['finish_dated', t`finish date filled in`], ['progress', t`progress moved forward`],
      ['shelved', t`set aside`], ['kept', t`kept as Tome has them`], ['unchanged', t`unchanged`]] },
    { label: t`Clippings`, tally: report.clippings, parts: [
      ['created', t`new`], ['deleted', t`deleted on the reader`], ['already', t`already in Tome`], ['merged_copy', t`older copies`]] },
    { label: t`Daily counters`, tally: report.daily, parts: [
      ['created', t`new`], ['raised', t`raised`], ['unchanged', t`already in Tome`]] },
    { label: t`Device totals`, tally: report.devices, parts: [['created', t`new`], ['already', t`already in Tome`]] },
    { label: t`Book page counts`, tally: report.book_pages, parts: [
      ['created', t`new`], ['replaced', t`newer than in Tome`], ['already', t`already in Tome or older`]] },
    { label: t`Also written`, tally: report.also_written, parts: [
      ['descriptions', t`file descriptions stored`], ['earlier_sessions_linked', t`earlier sessions linked to their book`],
      ['device_names', t`device names filled in`]] },
  ]
  const time = hm(report.seconds_added)
  const from = report.first_session?.slice(0, 10)
  const to = report.last_session?.slice(0, 10)
  return (
    <div className="border-y border-border/70">
      {rows.map(({ label, tally, parts }) => {
        const merged = new Map<string, number>()
        for (const [key, text] of parts) {
          const n = tally.counts[key]
          if (n) merged.set(text, (merged.get(text) ?? 0) + n)
        }
        const skipped = tally.skipped
        return (
          <div key={label} className="flex flex-col gap-0.5 border-b border-border/70 py-2 last:border-b-0 sm:flex-row sm:gap-4">
            <span className="w-40 shrink-0 text-[13px] font-medium text-foreground">
              {label}{tally !== report.also_written && <> <span className="font-normal tabular-nums text-muted-foreground">{tally.in_file}</span></>}
            </span>
            <span className="text-[13px] text-muted-foreground">
              {[...merged].map(([text, n]) => (
                <span key={text} className="mr-3 inline-block"><span className="tabular-nums text-foreground">{n}</span> {text}</span>
              ))}
              {skipped > 0 && (
                <span className="inline-block text-warning"><span className="tabular-nums">{skipped}</span> <Trans>skipped</Trans></span>
              )}
              {merged.size === 0 && skipped === 0 && <span>-</span>}
            </span>
          </div>
        )
      })}
      <div className="flex flex-col gap-0.5 py-2 sm:flex-row sm:gap-4">
        <span className="w-40 shrink-0 text-[13px] font-medium text-foreground"><Trans>Reading time added</Trans></span>
        <span className="text-[13px] text-muted-foreground">
          <span className="tabular-nums text-foreground">{time}</span>
          {from && to && <> <Trans>from sessions between {from} and {to}</Trans></>}
        </span>
      </div>
    </div>
  )
}

function Skips({ report }: { report: Report }) {
  const { t } = useLingui()
  const kinds: [string, Tally][] = [
    [t`Books`, report.books], [t`Reading sessions`, report.sessions], [t`Clippings`, report.clippings],
    [t`Daily counters`, report.daily], [t`Device totals`, report.devices], [t`Book page counts`, report.book_pages],
    [t`Positions`, report.progress],
  ]
  const any = kinds.some(([, tally]) => tally.skipped > 0)
  if (!any) return null
  return (
    <details className="text-[13px]">
      <summary className="cursor-pointer select-none text-muted-foreground"><Trans>Skipped, and why</Trans></summary>
      <ul className="mt-2 space-y-2">
        {kinds.flatMap(([kind, tally]) => tally.skip_reasons.map(r => (
          <li key={kind + r.reason}>
            <span className="text-foreground">{kind}: {r.reason}</span>{' '}
            <span className="tabular-nums text-muted-foreground">({r.count})</span>
            <ul className="mt-0.5 space-y-0.5 pl-4 text-[12px] text-muted-foreground">
              {r.examples.map((ex, i) => (
                <li key={i} className="break-all">
                  {[ex.title ? `"${ex.title}"` : null, ex.session_id, ex.clipping_id, ex.device_id, ex.date, ex.document]
                    .filter(Boolean).join(' · ')}
                </li>
              ))}
            </ul>
          </li>
        )))}
      </ul>
    </details>
  )
}

function Books({ report }: { report: Report }) {
  const { i18n } = useLingui()
  if (report.book_list.length === 0) return null
  const n = report.book_list.length
  return (
    <div>
      <p className="mb-1 text-[13px] font-medium text-foreground"><Trans>Books ({n})</Trans></p>
      <div className="max-h-80 overflow-y-auto overscroll-contain border-y border-border/70">
        {report.book_list.map(b => {
          const outcome = OUTCOME[b.outcome] ? i18n._(OUTCOME[b.outcome]) : b.outcome
          const sessions = b.sessions_in_file
          const time = hm(b.seconds_in_file)
          const added = b.sessions_new
          const merged = b.documents.length - 1
          const pages = b.reader_pages ?? 0
          return (
            <div key={b.document} className="border-b border-border/70 py-2 last:border-b-0">
              <div className="flex items-baseline gap-2">
                <span className="min-w-0 flex-1 truncate text-[14px] text-foreground">
                  {b.title ?? <span className="italic text-muted-foreground"><Trans>No title in the file</Trans></span>}
                  {b.author && <span className="text-muted-foreground"> · {b.author}</span>}
                </span>
                <span className="shrink-0 text-[12px] tabular-nums text-muted-foreground">
                  <Trans>{sessions} sessions, {time}</Trans>
                </span>
              </div>
              <div className={cn('text-[13px]', b.outcome === 'unlinked' ? 'text-warning' : 'text-muted-foreground')}>
                {outcome}
                {b.book_title && b.outcome !== 'created' && <> <span className="text-foreground">{b.book_title}</span></>}
                {b.reason && <>: {b.reason}</>}
                {added > 0 && <> · <Trans>{added} new sessions</Trans></>}
                {b.status_change && <> · {b.status_change}</>}
                {b.source_note && <> ({b.source_note})</>}
                {pages > 0 && <> · <Trans>pages: {pages} from the reader's counter</Trans></>}
                {merged > 0 && <> · <Trans>{merged} merged file</Trans></>}
                {b.conflict && <> · <Trans>its files were linked to different books before, left as they are</Trans></>}
              </div>
              <div className="truncate font-mono text-[11px] text-muted-foreground/70">{b.document}</div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

function NotImported({ report }: { report: Report }) {
  const unknownCols = Object.entries(report.file.unknown_columns).flatMap(([s, cols]) => cols.map(c => `${s}.${c}`))
  return (
    <details className="text-[13px] text-muted-foreground">
      <summary className="cursor-pointer select-none"><Trans>Not imported</Trans></summary>
      <ul className="mt-1 space-y-0.5 pl-4">
        {report.not_imported.map(n => {
          const section = n.section
          const rows = n.rows
          return <li key={section}>{section} (<Plural value={rows} one="# row" other="# rows" />): {n.why}</li>
        })}
        <li><Trans>Covers, service connections and profile settings are not part of the import.</Trans></li>
        {unknownCols.length > 0 && (() => {
          const cols = unknownCols.join(', ')
          return <li><Trans>Columns this Tome does not know, ignored: {cols}</Trans></li>
        })()}
      </ul>
    </details>
  )
}
