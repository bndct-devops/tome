// The expanded part of a glossary entry. Entries from an entity-glossary
// server (v2) show status, facts and a timeline; entries without any v2 field
// (an older server) show exactly the old expand: `more`, the quote, first seen
// and as of.
//
// The list carries only the last few timeline lines; the full entry is
// fetched once on expand (this component mounts on expand) and its timeline
// replaces the list's lines. While loading or on an error the list's lines
// stay.
import { useEffect, useState } from 'react'
import { Trans, useLingui } from '@lingui/react/macro'
import { getGlossaryEntry, type GlossaryEntry, type GlossaryFactField, type GlossaryFactHistoryItem, type SeriesPlace } from '@/lib/ai'

/** True when the entry carries any of the entity-glossary fields. */
function hasEntityFields(e: GlossaryEntry): boolean {
  return Boolean(
    e.identity
    || (e.aliases && e.aliases.length > 0)
    || e.status
    || (e.facts && Object.keys(e.facts).length > 0)
    || (e.timeline && e.timeline.length > 0),
  )
}

/** The earlier values of a fact (newest first). Read defensively: older
 *  servers send no `history`. */
function factHistory(fact: unknown): GlossaryFactHistoryItem[] {
  const h = (fact as { history?: unknown } | null | undefined)?.history
  if (!Array.isArray(h)) return []
  return h.filter((x): x is GlossaryFactHistoryItem =>
    x != null && typeof x === 'object'
    && typeof (x as { value?: unknown }).value === 'string'
    && typeof (x as { book_id?: unknown }).book_id === 'number'
    && typeof (x as { chapter_idx?: unknown }).chapter_idx === 'number')
}

type Props = {
  bookId: number
  entry: GlossaryEntry
  firstSeen: string
  lastSeen: string | null
  /** "vol 1, ch 12" (or "ch 12" without a volume number). */
  shortPlace: (p: SeriesPlace) => string
}

export function GlossaryEntryDetail({ bookId, entry: e, firstSeen, lastSeen, shortPlace }: Props) {
  const { t } = useLingui()
  const v2 = hasEntityFields(e)
  const entityId = e.entity_id ?? null
  const name = e.name
  const kind = e.kind
  const fetchKey = `${bookId}:${entityId ?? ''}:${kind}:${name}`
  // Keyed, so a stale entry from another book or name never shows.
  const [fetched, setFetched] = useState<{ key: string; entry: GlossaryEntry } | null>(null)
  const full = fetched?.key === fetchKey ? fetched.entry : null

  useEffect(() => {
    if (!v2) return
    let live = true
    getGlossaryEntry(bookId, entityId != null ? { entity_id: entityId } : { name, kind })
      .then(r => { if (live) setFetched({ key: fetchKey, entry: r.entry }) })
      .catch(() => { /* keep the list's lines */ })
    return () => { live = false }
  }, [bookId, entityId, name, kind, v2, fetchKey])

  // A server before the rank/powers split sends `powers_rank` instead of
  // `powers`; it shows under "Powers and titles" only when `powers` is absent.
  const factsSource = full?.facts ?? e.facts
  const powersField: GlossaryFactField = factsSource?.powers?.value ? 'powers' : 'powers_rank'
  const factLabels: [Exclude<GlossaryFactField, 'status'>, string][] = [
    ['appearance', t`Appearance`],
    ['rank', t`Rank`],
    [powersField, t`Powers and titles`],
    ['affiliations', t`Affiliations`],
    ['relationships', t`Relationships`],
    ['location', t`Location`],
  ]

  // The list's facts have no history; the opened entry's do.
  const statusFact = factsSource?.status
  const statusText = e.status || statusFact?.value || null
  const statusAt = statusFact ? shortPlace(statusFact) : null
  // "Tier two · vol 6, ch 57 · Tier one · vol 3, ch 44"
  const earlierText = (fact: unknown): string =>
    factHistory(fact).map(h => `${h.value} · ${shortPlace(h)}`).join(' · ')
  const statusEarlier = earlierText(statusFact)
  const facts = factLabels
    .map(([field, label]) => ({ field, label, fact: factsSource?.[field] }))
    .filter(x => x.fact && x.fact.value)
  const fullTimeline = full?.timeline
  const useFull = fullTimeline != null && fullTimeline.length > 0
  const timeline = useFull ? fullTimeline : (e.timeline ?? [])
  const truncated = useFull ? full?.timeline_truncated : e.timeline_truncated

  return (
    <div className="px-3 pb-2.5 -mt-0.5 space-y-1.5">
      {!v2 && e.more && <p className="text-sm text-foreground leading-relaxed">{e.more}</p>}
      {v2 && statusText && (
        <p className="text-sm text-foreground leading-snug">
          <span className="text-muted-foreground"><Trans>Status:</Trans></span> {statusText}
          {statusAt && (
            <span className="text-xs text-muted-foreground/70"> <Trans>as of {statusAt}</Trans></span>
          )}
          {statusEarlier && (
            <span className="block text-xs text-muted-foreground/70">
              <Trans>earlier: {statusEarlier}</Trans>
            </span>
          )}
        </p>
      )}
      {v2 && facts.length > 0 && (
        <dl className="space-y-0.5">
          {facts.map(({ field, label, fact }) => {
            const at = shortPlace(fact!)
            const earlier = earlierText(fact)
            return (
              <div key={field} className="text-sm leading-snug">
                <dt className="inline text-muted-foreground">{label}: </dt>
                <dd className="inline text-foreground">
                  {fact!.value}
                  <span className="text-xs text-muted-foreground/70"> · {at}</span>
                  {earlier && (
                    <span className="block text-xs text-muted-foreground/70">
                      <Trans>earlier: {earlier}</Trans>
                    </span>
                  )}
                </dd>
              </div>
            )
          })}
        </dl>
      )}
      {v2 && timeline.length > 0 && (
        <div className="space-y-0.5">
          {truncated && (
            <p className="text-xs text-muted-foreground/70"><Trans>Earlier lines not shown.</Trans></p>
          )}
          <ul className="space-y-0.5">
            {timeline.map((item, i) => (
              <li key={`${item.book_id}:${item.chapter_idx}:${i}`} className="text-xs text-muted-foreground leading-snug">
                <span className="text-muted-foreground/70">{shortPlace(item)}</span> · {item.line}
              </li>
            ))}
          </ul>
        </div>
      )}
      {e.evidence && (
        <p className="text-xs text-muted-foreground italic border-l-2 border-primary/40 pl-2.5">
          {e.evidence}
        </p>
      )}
      <p className="text-xs text-muted-foreground/70">
        <Trans>First seen: {firstSeen}</Trans>
      </p>
      {lastSeen && (
        <p className="text-xs text-muted-foreground/70">
          <Trans>As of: {lastSeen}</Trans>
        </p>
      )}
    </div>
  )
}
