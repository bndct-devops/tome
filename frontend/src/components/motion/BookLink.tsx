/**
 * Opening a book or a series the one way: the book page paints its hero at
 * once from what the caller knows, and the cover grows into it when one is
 * on screen (lib/coverTransition.ts).
 *
 * A link (keeps cmd/ctrl-click, middle click and "open in new tab"):
 *   <BookLink book={{ id, title, author, series, series_index, coverColor }} className="…">{title}</BookLink>
 *
 * From code (a row's onClick, a keyboard handler):
 *   const openBook = useBookNav()
 *   openBook({ id, title }, coverEl)
 *
 * The cover to grow from: `cover` (prop or second argument) is an element or a
 * ref; leave it out and the on-screen element with `data-cover-id={id}`
 * nearest the link is used, so a title link next to a marked cover needs
 * nothing more. Pass `null` for no cover (the page still paints at once).
 * Put `data-cover-id={id}` on the box that shows the cover (the rounded,
 * clipped one), never on a wrapper that tilts.
 *
 * `book.cover` is the thumbnail the hero starts with; left out, it is taken
 * from the cover element's <img>.
 *
 * Series, the same way (covers carry `data-series-cover={name}`):
 *   const openSeries = useSeriesNav()
 *   openSeries(name, coverEl?)
 *   <SeriesLink name={name} className="…">{name}</SeriesLink>
 */
import { Link, type LinkProps } from 'react-router-dom'
import { isPlainClick, previewState, type BookPreview } from '@/lib/coverTransition'
import { bookPath, seriesPath, useBookNav, useSeriesNav, type CoverSource } from './nav'

export function BookLink({ book, cover, onClick, ...rest }: Omit<LinkProps, 'to' | 'state'> & {
  book: BookPreview
  cover?: CoverSource
}) {
  const open = useBookNav()
  return (
    <Link
      to={bookPath(book.id)}
      state={previewState(book)}
      onClick={e => {
        onClick?.(e)
        if (!isPlainClick(e)) return
        e.preventDefault()
        open(book, cover, { replace: rest.replace, near: e.currentTarget })
      }}
      {...rest}
    />
  )
}

export function SeriesLink({ name, cover, onClick, ...rest }: Omit<LinkProps, 'to'> & {
  name: string
  cover?: CoverSource
}) {
  const open = useSeriesNav()
  return (
    <Link
      to={seriesPath(name)}
      onClick={e => {
        onClick?.(e)
        if (!isPlainClick(e)) return
        e.preventDefault()
        open(name, cover, { near: e.currentTarget })
      }}
      {...rest}
    />
  )
}
