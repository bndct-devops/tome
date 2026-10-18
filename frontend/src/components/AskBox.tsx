// "Ask about what you have read": one question, one paid call, answered from
// the chapters before the reader's position. Fires only on Ask or Enter, one
// question at a time; the last answer stays until the next question or Clear.
import { useEffect, useRef, useState } from 'react'
import { Loader2, MessageCircleQuestion } from 'lucide-react'
import { Trans, useLingui } from '@lingui/react/macro'
import { Pill } from '@/components/page'
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
    <div className="space-y-3">
      <div>
        <form
          className="flex items-center gap-2"
          onSubmit={e => { e.preventDefault(); void ask() }}
        >
          <div className="relative flex-1 min-w-0">
            <MessageCircleQuestion className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <input
              type="text"
              value={question}
              onChange={e => setQuestion(e.target.value)}
              maxLength={ASK_MAX_CHARS}
              placeholder={t`Ask about what you have read`}
              aria-label={t`Ask about what you have read`}
              className="h-9 w-full rounded-full bg-secondary pl-10 pr-4 text-[14px] text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring"
            />
          </div>
          <Pill
            type="submit"
            variant="secondary"
            disabled={asking || !trimmed}
            title={t`Sends the chapters it needs, all before your position, to Anthropic`}
            className="shrink-0"
          >
            {asking && <Loader2 className="h-4 w-4 animate-spin" />}
            {asking ? <Trans>Asking…</Trans> : <Trans>Ask</Trans>}
          </Pill>
        </form>
        <p className="mt-1.5 text-[13px] text-muted-foreground">
          {hint
            ? <Trans>Reads the chapters it needs, up to about {hint} a question. Never past your position.</Trans>
            : <Trans>Reads the chapters it needs, around 10 to 30 cents a question. Never past your position.</Trans>}
        </p>
      </div>
      {notice && <p className="text-[13px] text-muted-foreground">{notice}</p>}
      {error && <p className="text-[13px] text-destructive">{error}</p>}
      {result && (
        <div className="max-w-[70ch]">
          <p className="text-[15px] leading-relaxed text-foreground/85 whitespace-pre-line">{result.answer}</p>
          {result.found && result.quotes.length > 0 && (
            <ul className="mt-3 divide-y divide-border/70 border-t border-border/70">
              {result.quotes.map((q, i) => {
                const where = placeLabel(q)
                return (
                  <li key={`${q.book_id}:${q.chapter_idx}:${i}`} className="py-2.5">
                    <p className="text-[14px] italic leading-relaxed text-foreground/85">{q.text}</p>
                    <p className="mt-0.5 text-[13px] text-muted-foreground">{where}</p>
                  </li>
                )
              })}
            </ul>
          )}
          <div className="mt-1 flex justify-end">
            <Pill variant="quiet" className="-mr-3" onClick={clear}>
              <Trans>Clear</Trans>
            </Pill>
          </div>
        </div>
      )}
    </div>
  )
}

export default AskBox
