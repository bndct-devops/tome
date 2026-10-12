import { api } from '@/lib/api'

// Marks inbox files as "in review" on the server for 30 minutes so the
// auto-import loop leaves them alone. Best effort: a failure never blocks
// the UI, the hold only narrows a race.
export function holdBinderyFiles(paths: string[]): void {
  if (paths.length === 0) return
  api.post<{ held: number }>('/bindery/hold', { paths }).catch(err => {
    console.debug('bindery hold failed', err)
  })
}
