import { useEffect, useState } from 'react'
import type { LucideIcon, LucideProps } from 'lucide-react'
import {
  Library, Bookmark, BookOpen, BookMarked, Layers, Scroll, Book, BookText,
  Star, Heart, Folder, Tag, Sparkles, Archive,
} from 'lucide-react'

/** Libraries, shelves and book types store a Lucide icon *name*, and the icon
 * picker offers the whole set (~1,600 icons). Importing the set eagerly put
 * all of it in the main bundle, so the full set is its own chunk now: the
 * seeded defaults and a few common picks render at once, any other name
 * renders as soon as that chunk has loaded (a blank of the same size until
 * then). */

const COMMON: Record<string, LucideIcon> = {
  Library, Bookmark, BookOpen, BookMarked, Layers, Scroll, Book, BookText,
  Star, Heart, Folder, Tag, Sparkles, Archive,
}

function isLucideIcon(val: unknown): val is LucideIcon {
  return typeof val === 'object' && val !== null && 'render' in val
}

let all: Record<string, LucideIcon> | null = null
let loading: Promise<Record<string, LucideIcon>> | null = null

/** Every icon by name, loaded once. */
export function loadAllIcons(): Promise<Record<string, LucideIcon>> {
  loading ??= import('virtual:lucide-all').then(mod => {
    const { aliases } = mod
    const icons: Record<string, LucideIcon> = {}
    for (const [name, val] of Object.entries(mod as Record<string, unknown>)) {
      if (/^[A-Z]/.test(name) && isLucideIcon(val)) icons[name] = val
    }
    for (const [alias, primary] of Object.entries(aliases)) if (icons[primary] && !alias.endsWith('Icon')) icons[alias] = icons[primary]
    all = icons
    return all
  })
  return loading
}

export function NamedIcon({ icon: name, fallback = Library, ...props }: { icon?: string | null; fallback?: LucideIcon } & Omit<LucideProps, "name">) {
  const known = name ? COMMON[name] ?? all?.[name] : undefined
  const [, setLoaded] = useState(0)
  const needsSet = !!name && !known && !all
  useEffect(() => {
    if (!needsSet) return
    let live = true
    loadAllIcons().then(() => { if (live) setLoaded(n => n + 1) }).catch(() => {})
    return () => { live = false }
  }, [needsSet])
  if (needsSet) return <span aria-hidden className={props.className} style={{ display: 'inline-block' }} />
  const Comp = known ?? fallback
  return <Comp {...props} />
}
