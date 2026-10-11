// "Ask about what you have read": one question, one paid call, answered from
// the chapters before the reader's position. Fires only on Ask or Enter, one
// question at a time; the last answer stays until the next question or Clear.
import { useEffect, useRef, useState } from 'react'
import { Loader2, MessageCircleQuestion } from 'lucide-react'
import { Trans, useLingui } from '@lingui/react/macro'
import { askBook, isAlreadyRunning, ASK_MAX_CHARS, type AskResult, type SeriesPlace } from '@/lib/ai'

type Props = {
  bookId: number
  /** "vol 1, ch 12 · Title" for a quote's place. */
  placeLabel: (p: SeriesPlace) => string
  /** Upper-bound USD cost of one question; absent on older servers. */
  costHintUsd?: number
}

export function AskBox({ bookId, placeLabel, costHintUsd }: Props) {
  const { t, i18n } = useLingui()
  const [question, setQuestion] = useState('')
  const [asking, setAsking] = useState(false)
  const [result, setResult] = useState<AskResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const busy = useRef(false)
  // The book on screen now; a result for another book is dropped.
  const currentBook = useRef(bookId)
  const alive = useRef(true)
  useEffect(() => {
    alive.current = true
    return () => { alive.current = false }
  }, [])

  // A new book: nothing from the old one carries over.
  useEffect(() => {
    currentBook.current = bookId
    setQuestion('')
    setResult(null)
    setError(null)
    setNotice(null)
  }, [bookId])

  const trimmed = question.trim()

  async function ask() {
    if (!trimmed || busy.current) return
    const askedFor = bookId
    busy.current = true
    setAsking(true)
    setError(null)
    setNotice(null)
    const current = () => alive.current && currentBook.current === askedFor
    try {
      const r = await askBook(askedFor, trimmed.slice(0, ASK_MAX_CHARS))
      if (current()) setResult(r)
    } catch (e) {
      if (!current()) return
      if (isAlreadyRunning(e)) setNotice(t`A question is already being answered. Try again in a moment.`)
      else setError(e instanceof Error ? e.message : t`Could not answer that`)
    } finally {
      busy.current = false
      if (alive.current) setAsking(false)
    }
  }

  function clear() {
    setResult(null)
    setError(null)
    setNotice(null)
  }

  const hint = costHintUsd != null && Number.isFinite(costHintUsd) && costHintUsd > 0
    ? new Intl.NumberFormat(i18n.locale, {
        style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: 2,
      }).format(costHintUsd)
    : null

  return (
    <div className="space-y-2">
      <form
        className="flex items-center gap-2"
        onSubmit={e => { e.preventDefault(); void ask() }}
      >
        <div className="relative flex-1 min-w-0">
          <MessageCircleQuestion className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground/60" />
          <input
            type="text"
            value={question}
            onChange={e => setQuestion(e.target.value)}
            maxLength={ASK_MAX_CHARS}
            placeholder={t`Ask about what you have read`}
            aria-label={t`Ask about what you have read`}
            className="w-full rounded-lg border border-border bg-background pl-8 pr-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-ring"
          />
        </div>
        <button
          type="submit"
          disabled={asking || !trimmed}
          title={t`Sends the chapters it needs, all before your position, to Anthropic`}
          className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium border border-border bg-card text-foreground hover:bg-muted transition-colors disabled:opacity-60 shrink-0"
        >
          {asking && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
          {asking ? <Trans>Asking…</Trans> : <Trans>Ask</Trans>}
        </button>
      </form>
      <p className="text-xs text-muted-foreground/70">
        {hint
          ? <Trans>Reads the chapters it needs, up to about {hint} a question. Never past your position.</Trans>
          : <Trans>Reads the chapters it needs, around 10 to 30 cents a question. Never past your position.</Trans>}
      </p>
      {notice && <p className="text-xs text-muted-foreground">{notice}</p>}
      {error && <p className="text-xs text-destructive">{error}</p>}
      {result && (
        <div className="rounded-lg border border-border bg-muted/40 px-3 py-2.5 space-y-1.5">
          <p className="text-sm text-foreground leading-relaxed whitespace-pre-line">{result.answer}</p>
          {result.found && result.quotes.map((q, i) => {
            const where = placeLabel(q)
            return (
              <div key={`${q.book_id}:${q.chapter_idx}:${i}`} className="space-y-0.5">
                <p className="text-xs text-muted-foreground italic border-l-2 border-primary/40 pl-2.5">{q.text}</p>
                <p className="text-xs text-muted-foreground/70 pl-3">{where}</p>
              </div>
            )
          })}
          <div className="flex justify-end">
            <button
              type="button"
              onClick={clear}
              className="text-xs text-muted-foreground hover:text-foreground underline-offset-2 hover:underline"
            >
              <Trans>Clear</Trans>
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

export default AskBox
