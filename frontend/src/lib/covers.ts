/** Cover URL for a book.
 *
 * `w` asks the server for a small WebP copy: 160 for list thumbnails, 400 for
 * grid cards. `v` is the book's `updated_at`; with it the browser keeps the
 * image for a year without asking again (the server bumps `updated_at` when the
 * cover changes). Without it the image is reused for 5 minutes, then shown from
 * cache while it is checked in the background. */
export function coverSrc(id: number, opts: { w?: 160 | 400; v?: string | null } = {}): string {
  const q = new URLSearchParams()
  if (opts.w) q.set('w', String(opts.w))
  if (opts.v) q.set('v', opts.v)
  const qs = q.toString()
  return `/api/books/${id}/cover${qs ? `?${qs}` : ''}`
}
