import { useState, useEffect } from 'react'
import { api } from '@/lib/api'
import type { BookType } from '@/lib/books'

let cache: BookType[] | null = null
let inflight: Promise<BookType[]> | null = null

export function useBookTypes(): BookType[] {
  const [types, setTypes] = useState<BookType[]>(cache ?? [])

  useEffect(() => {
    if (cache) { setTypes(cache); return }
    if (!inflight) {
      inflight = api.get<BookType[]>('/book-types').then(data => {
        cache = data
        inflight = null
        return data
      }).catch(() => {
        inflight = null
        return []
      })
    }
    inflight.then(data => setTypes(data)).catch(() => {})
  }, [])

  return types
}

export function invalidateBookTypesCache() {
  cache = null
  inflight = null
}
