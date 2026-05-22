import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { X } from 'lucide-react'
import { readTheme, subscribe, type Theme } from './theme'

interface Props {
  name: string
  alt: string
  className?: string
  fallback?: Theme
  clickable?: boolean        // default true — click to open lightbox
}

export function ThemedShot({ name, alt, className, fallback = 'light', clickable = true }: Props) {
  const [theme, setTheme] = useState<Theme>('light')
  const [open, setOpen] = useState(false)

  useEffect(() => {
    setTheme(readTheme())
    return subscribe(setTheme)
  }, [])

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('keydown', onKey)
    document.body.style.overflow = 'hidden'
    return () => {
      document.removeEventListener('keydown', onKey)
      document.body.style.overflow = ''
    }
  }, [open])

  const variant = theme || fallback
  const src = `/shots/${variant}/${name}.png`

  return (
    <>
      <img
        src={src}
        alt={alt}
        loading="lazy"
        onClick={clickable ? () => setOpen(true) : undefined}
        className={['fade-in', className].filter(Boolean).join(' ')}
        key={src}
      />
      {open && typeof document !== 'undefined' && createPortal(
        <div
          className="lightbox-backdrop"
          onClick={() => setOpen(false)}
          role="dialog"
          aria-modal="true"
          aria-label={alt}
        >
          <button
            type="button"
            className="lightbox-close"
            aria-label="Close"
            onClick={(e) => { e.stopPropagation(); setOpen(false) }}
          >
            <X className="w-5 h-5" />
          </button>
          <img
            src={src}
            alt={alt}
            className="lightbox-content"
            onClick={(e) => e.stopPropagation()}
          />
        </div>,
        document.body,
      )}
    </>
  )
}
