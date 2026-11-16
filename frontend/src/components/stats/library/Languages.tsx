// Reading by language (#50): all-time reading time per book language, as rows
// with thin tracks like Backlog's. Shown only when at least two known
// languages have reading time; the skeleton reserves the rows the last answer
// had (Shape.langs, 0 = not shown), so the section appears in place when the
// reader has it and takes no room when they do not.
import { useMemo } from 'react'
import { Trans } from '@lingui/react/macro'
import { t, plural } from '@lingui/core/macro'
import { cn, formatDuration } from '@/lib/utils'
import type { StatsResponse } from '@/components/stats/shared'
import { Ph } from '@/components/stats/kit/Kit'
import { HAIR } from '@/components/stats/kit/tokens'
import { ONCE, stagger, useEntry } from '@/components/stats/kit/motion'
import { tipAttrs } from '@/components/stats/kit/tip'
import { langModel, type Lang } from './model'
import { Head, Thin } from './parts'

/* eslint-disable lingui/no-unlocalized-strings -- placeholder text: drawn as blocks while loading and never read. */
const PH = { note: '3 languages · 86 books', name: 'English', books: '83 books', value: '422 hours · 98%', foot: 'And 2 more: Japanese 1h 2m, French 12m.' }
/* eslint-enable lingui/no-unlocalized-strings */

const ROW_H = 44
const ROW_H_PHONE = 54

/** `rows`: the rows the skeleton reserves (the last answer's; 0 draws nothing while loading). */
export function Languages({ stats, desktop, rows }: { stats: StatsResponse | null; desktop: boolean; rows: number }) {
  const m = useMemo(() => (stats ? langModel(stats.language) : null), [stats])
  const e = useEntry('library/languages', m ? ONCE : null)
  if (stats ? !m : rows === 0) return null
  const list: (Lang | null)[] = m ? m.rows : Array.from({ length: rows }, () => null)
  const total = m?.total ?? 0
  const max = m ? Math.max(1, m.rows[0].seconds) : 1
  const rowH = desktop ? ROW_H : ROW_H_PHONE
  const name = (l: Lang) => (l.code === 'unknown' ? t`No language set` : l.language)
  const note = m
    ? (() => { const langs = plural(m.rows.length + m.rest.length, { one: '# language', other: '# languages' }); const books = plural(m.books, { one: '# book', other: '# books' }); return `${langs} · ${books}` })()
    : <Ph on>{PH.note}</Ph>
  const foot = m
    ? m.rest.length
      ? (() => { const list = m.rest.map((l) => `${name(l)} ${formatDuration(l.seconds)}`).join(', '); const more = plural(m.rest.length, { one: '# more language', other: '# more languages' }); return t`And ${more}: ${list}.` })()
      : t`By the language set on each book you read.`
    : <Ph on>{PH.foot}</Ph>

  return (
    <section ref={e.ref} data-box="languages">
      <Head note={note}><Trans>Languages</Trans></Head>
      <div data-box="language-rows" className="k-bars">
        {list.map((l, i) => {
          const share = l && total > 0 ? l.seconds / total : 0
          const pct = Math.round(share * 100)
          const tip = l ? (() => {
            const books = plural(l.books, { one: '# book read', other: '# books read' })
            return tipAttrs({ title: name(l), value: formatDuration(l.seconds), note: t`${books} · ${pct}% of your reading time` })
          })() : undefined
          const bar = { style: stagger(i, list.length), tip }
          const label = (
            <span className="min-w-0 truncate text-[15px] font-semibold text-foreground">
              <Ph on={!l}>{l ? name(l) : PH.name}</Ph>
              <span className={cn('font-normal text-muted-foreground', desktop ? 'ml-2 text-[14px]' : 'ml-1.5 text-[13px]')}>
                <Ph on={!l}>{l ? plural(l.books, { one: '# book', other: '# books' }) : PH.books}</Ph>
              </span>
            </span>
          )
          const value = (
            <span className={cn('whitespace-nowrap text-right text-muted-foreground', desktop ? 'text-[14px]' : 'text-[13px]')}>
              {l ? <><b className="font-semibold text-foreground">{formatDuration(l.seconds)}</b> · {pct}%</> : <Ph on>{PH.value}</Ph>}
            </span>
          )
          // Tracks are scaled to the longest language, so a small second one still reads.
          const frac = l ? l.seconds / max : 0
          return desktop ? (
            <div key={l?.code ?? i} data-box={`language-${i}`} className={cn('grid grid-cols-[220px_minmax(0,1fr)_minmax(200px,auto)] items-center gap-x-6', i > 0 && cn('border-t', HAIR))} style={{ height: rowH }}>
              {label}
              <Thin frac={frac} className={!l ? 'opacity-40' : ''} {...bar} />
              {value}
            </div>
          ) : (
            <div key={l?.code ?? i} data-box={`language-${i}`} className={cn('grid grid-cols-[minmax(0,1fr)_auto] grid-rows-[20px_10px] content-center items-center gap-x-3', i > 0 && cn('border-t', HAIR))} style={{ height: rowH }}>
              {label}
              {value}
              <Thin frac={frac} className={cn('col-span-2 self-end', !l && 'opacity-40')} {...bar} />
            </div>
          )
        })}
      </div>
      <p className="mt-3 h-[18px] truncate text-[13px] leading-[18px] text-muted-foreground">{foot}</p>
    </section>
  )
}
