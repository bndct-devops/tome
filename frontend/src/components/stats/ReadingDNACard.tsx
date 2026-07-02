import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Sparkles, Moon, Sunrise, Zap, BookMarked, Compass, ChevronDown, ChevronUp, type LucideIcon } from 'lucide-react'
import type { ReadingDNA, ReadingDNATrait } from './shared'

const COLLAPSE_KEY = 'tome_dna_collapsed'

/** A pole-to-pole spectrum bar with the reader's position marked. */
function TraitBar({ trait }: { trait: ReadingDNATrait }) {
  const s = Math.max(0, Math.min(100, trait.score))
  const high = s >= 50
  return (
    <div>
      <div className="flex items-center justify-between text-[11px] mb-1.5">
        <span className={high ? 'text-muted-foreground' : 'text-foreground font-semibold'}>{trait.low}</span>
        <span className={high ? 'text-foreground font-semibold' : 'text-muted-foreground'}>{trait.high}</span>
      </div>
      <div className="relative h-1.5 rounded-full bg-muted">
        <span className="absolute top-[-2px] bottom-[-2px] w-px bg-border left-1/2" />
        <span
          className="absolute top-0 bottom-0 rounded-full bg-primary/45"
          style={{ left: `${Math.min(s, 50)}%`, width: `${Math.abs(s - 50)}%` }}
        />
        <span
          className="absolute top-1/2 w-3 h-3 rounded-full bg-primary border-2 border-card shadow-[0_0_0_1px_var(--border)] -translate-x-1/2 -translate-y-1/2"
          // Clamp the marker inside the track — at 0/100 the dot half-clipped.
          style={{ left: `${Math.min(Math.max(s, 3), 97)}%` }}
        />
      </div>
    </div>
  )
}

/** Pick a glyph that echoes the reader's most-defining trait. */
function archetypeIcon(archetype: string | null): LucideIcon {
  const a = (archetype || '').toLowerCase()
  if (a.includes('night')) return Moon
  if (a.includes('dawn') || a.includes('early')) return Sunrise
  if (a.includes('devourer') || a.includes('voracious')) return Zap
  if (a.includes('wanderer') || a.includes('roving')) return Compass
  if (a.includes('devotee') || a.includes('loyal') || a.includes('specialist')) return BookMarked
  return Sparkles
}

/** The card's content — archetype, trait spectra, one-line summary — shared by
 *  the Home rail card and the Stats page's "Reading DNA" tile. `showLink`
 *  appends the "Full breakdown →" link (Home only; the Stats tile IS the
 *  breakdown). */
export function ReadingDNABody({ dna, showLink = false }: { dna: ReadingDNA; showLink?: boolean }) {
  const ArcheIcon = archetypeIcon(dna.archetype)
  return (
    <div>
      {dna.archetype && (
        <div className="flex items-center gap-3">
          <span className="w-11 h-11 rounded-xl bg-primary text-primary-foreground flex items-center justify-center shrink-0">
            <ArcheIcon className="w-[23px] h-[23px]" />
          </span>
          <span className="font-display text-[17px] leading-[1.12] text-foreground">{dna.archetype}</span>
        </div>
      )}

      <div className="mt-5 flex flex-col gap-3.5">
        {dna.traits.map((t) => <TraitBar key={t.key} trait={t} />)}
      </div>

      {dna.summary && (
        <p className="mt-4 pt-3 border-t border-border text-[11px] text-muted-foreground text-center leading-relaxed">
          {dna.summary}
        </p>
      )}

      {showLink && (
        <Link
          to="/stats"
          className="mt-3 block text-[11px] text-muted-foreground hover:text-foreground transition-colors text-center"
        >
          Full breakdown →
        </Link>
      )}
    </div>
  )
}

export function ReadingDNACard({ dna }: { dna: ReadingDNA }) {
  const [collapsed, setCollapsed] = useState(() => localStorage.getItem(COLLAPSE_KEY) === '1')

  if (!dna.ready || dna.traits.length === 0) return null

  const toggle = () => {
    setCollapsed((c) => {
      const next = !c
      localStorage.setItem(COLLAPSE_KEY, next ? '1' : '0')
      return next
    })
  }

  return (
    <section className="p-4">
      <button
        type="button"
        onClick={toggle}
        className="flex w-full items-center justify-between gap-2"
        aria-expanded={!collapsed}
      >
        <span className="flex items-center gap-2 text-sm font-semibold min-w-0">
          <Sparkles className="w-[15px] h-[15px] text-primary/75 shrink-0" />
          <span className="shrink-0">Reading DNA</span>
          {collapsed && dna.archetype && (
            <span className="text-[13px] text-muted-foreground truncate">
              · <span className="text-foreground font-semibold">{dna.archetype}</span>
            </span>
          )}
        </span>
        {collapsed
          ? <ChevronDown className="w-4 h-4 text-muted-foreground shrink-0" />
          : <ChevronUp className="w-4 h-4 text-muted-foreground shrink-0" />}
      </button>

      {!collapsed && (
        <div className="mt-4">
          <ReadingDNABody dna={dna} showLink />
        </div>
      )}
    </section>
  )
}
