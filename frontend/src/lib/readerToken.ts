import { useEffect, useState } from "react"
import { api } from "./api"

// A book-scoped token for the reader's <img> page URLs, which cannot send an
// Authorization header. It opens only this book's reader files and lasts 12
// hours on the server; we mint a fresh one well before that so a long
// session never outlives it. The login token never goes into a URL.
const REFRESH_AFTER_MS = 6 * 60 * 60 * 1000
const CHECK_EVERY_MS = 5 * 60 * 1000

export function useReaderToken(bookId: string): string | null {
  const [token, setToken] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    let mintedAt = 0

    async function mint() {
      try {
        const res = await api.post<{ token: string }>(`/books/${bookId}/reader-token`)
        if (cancelled) return
        mintedAt = Date.now()
        setToken(res.token)
      } catch {
        // Leave the current token in place; the next check tries again.
      }
    }

    setToken(null)
    mint()
    // A timer check (not a single long timeout) also covers a laptop that
    // slept past the refresh point.
    const timer = window.setInterval(() => {
      if (Date.now() - mintedAt > REFRESH_AFTER_MS) mint()
    }, CHECK_EVERY_MS)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [bookId])

  return token
}

// Authorization header for the reader's own fetches (EPUB, PDF).
export function authHeaders(): Record<string, string> {
  const token = localStorage.getItem("tome_token")
  return token ? { Authorization: `Bearer ${token}` } : {}
}
