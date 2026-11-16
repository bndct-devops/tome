// Row actions on a reading session (trim, delete), shared by the session log
// (Boards tile, book pages) and the Sessions tab. The API calls are the ones the
// log has always made; each host decides what to refresh afterwards.
import { useState } from 'react'
import { t } from '@lingui/core/macro'
import { api } from '@/lib/api'
import type { SessionEntry } from '@/components/stats/shared'

export type SessionActions = {
  deleting: Set<number | string>
  trimId: number | string | null
  trimMinutes: string
  trimBusy: boolean
  setTrimMinutes: (v: string) => void
  toggleTrim: (s: SessionEntry) => void
  closeTrim: () => void
  applyTrim: (s: SessionEntry) => void
  remove: (s: SessionEntry) => void
}

/**
 * `onTrimmed(s, seconds)` after a trim landed, `onDeleted(s)` after a delete;
 * both run before the busy flags clear.
 */
export function useSessionActions({ onTrimmed, onDeleted }: {
  onTrimmed: (s: SessionEntry, seconds: number) => void
  onDeleted: (s: SessionEntry) => void
}): SessionActions {
  const [deleting, setDeleting] = useState<Set<number | string>>(new Set())
  const [trimId, setTrimId] = useState<number | string | null>(null)
  const [trimMinutes, setTrimMinutes] = useState('')
  const [trimBusy, setTrimBusy] = useState(false)

  const openTrim = (s: SessionEntry) => {
    setTrimId(s.id)
    const preFill = s.suggested_seconds ?? s.duration_seconds ?? 60
    setTrimMinutes(String(Math.max(1, Math.round(preFill / 60))))
  }

  const applyTrim = (s: SessionEntry) => {
    const seconds = Math.round(Number(trimMinutes) * 60)
    if (!Number.isFinite(seconds) || seconds < 60) return
    setTrimBusy(true)
    api.patch(`/stats/sessions/${s.id}`, { duration_seconds: seconds })
      .then(() => {
        setTrimId(null)
        onTrimmed(s, seconds)
      })
      .catch(() => {})
      .finally(() => setTrimBusy(false))
  }

  const remove = (s: SessionEntry) => {
    setDeleting((prev) => new Set(prev).add(s.id))
    const req =
      s.kind === 'imported'
        ? api.delete(`/stats/imported-sessions?book_id=${s.book_id}&start=${s.range_start}&end=${s.range_end}`)
        : api.delete(`/stats/sessions/${s.id}`)
    req
      .then(() => onDeleted(s))
      .catch(() => {})
      .finally(() => setDeleting((prev) => { const n = new Set(prev); n.delete(s.id); return n }))
  }

  return {
    deleting, trimId, trimMinutes, trimBusy, setTrimMinutes,
    toggleTrim: (s) => (trimId === s.id ? setTrimId(null) : openTrim(s)),
    closeTrim: () => setTrimId(null),
    applyTrim, remove,
  }
}

/** Friendly label for a session's device value, the same words the book page's "Where you read" legend uses. */
export function sourceLabel(device: string | null): string {
  if (!device) return '-'
  if (device === 'web-reader' || device === 'web') return t`Web reader`
  if (device === 'manual') return t`Manual`
  if (device === 'koreader') return 'KOReader'
  return device
}
