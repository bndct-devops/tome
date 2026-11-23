/**
 * The hooks and paths behind BookLink / SeriesLink (see BookLink.tsx for how
 * to use them).
 *
 *   const openBook = useBookNav();     openBook(preview, cover?, { replace? })
 *   const openSeries = useSeriesNav(); openSeries(name, cover?)
 */
import { useCallback, type RefObject } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  findBookCover, findSeriesCover, openBookWithCover, openSeriesWithCover, previewState, seriesPreviewState,
  type BookPreview,
} from '@/lib/coverTransition'

export type CoverSource = HTMLElement | RefObject<HTMLElement | null> | null | undefined

function resolve(cover: CoverSource, find: () => HTMLElement | null): HTMLElement | null {
  if (cover === undefined) return find()
  if (cover === null) return null
  return cover instanceof HTMLElement ? cover : cover.current
}

/** The picture a cover element shows. */
export function coverThumb(cover: HTMLElement | null): string | null {
  const img = cover?.querySelector('img')
  return img?.currentSrc || img?.getAttribute('src') || null
}

function withThumb(book: BookPreview, cover: HTMLElement | null): BookPreview {
  if (book.cover !== undefined || !cover) return book
  return { ...book, cover: coverThumb(cover) }
}

export const bookPath = (id: number) => `/books/${id}`
export const seriesPath = (name: string) => `/?tab=series&series_detail=${encodeURIComponent(name)}`

/** `open(book, cover?, { replace? })`: navigates to the book page. */
export function useBookNav() {
  const navigate = useNavigate()
  return useCallback((book: BookPreview, cover?: CoverSource, opts?: { replace?: boolean; near?: Element | null }) => {
    const el = resolve(cover, () => findBookCover(book.id, opts?.near))
    const state = previewState(withThumb(book, el))
    openBookWithCover(el, book.id, () => navigate(bookPath(book.id), { state, replace: opts?.replace }))
  }, [navigate])
}

/** `open(name, cover?)`: navigates to the series page. */
export function useSeriesNav() {
  const navigate = useNavigate()
  return useCallback((name: string, cover?: CoverSource, opts?: { near?: Element | null }) => {
    const el = resolve(cover, () => findSeriesCover(name, opts?.near))
    const state = seriesPreviewState({ name, cover: coverThumb(el) })
    openSeriesWithCover(el, name, () => navigate(seriesPath(name), { state }))
  }, [navigate])
}
