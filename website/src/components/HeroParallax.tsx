import { useEffect, useRef } from 'react'

interface Props {
  children: React.ReactNode
  className?: string
  intensity?: number  // 0..1, default 0.02 (2% of viewport width travel max)
}

/** Wraps the hero screenshot. Subtle 2-axis parallax follows mouse + scroll. */
export function HeroParallax({ children, className, intensity = 0.02 }: Props) {
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const el = ref.current
    if (!el) return
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return

    let rafId = 0
    const apply = (x: number, y: number) => {
      cancelAnimationFrame(rafId)
      rafId = requestAnimationFrame(() => {
        el.style.transform = `translate3d(${x}px, ${y}px, 0)`
      })
    }

    const onMove = (e: MouseEvent) => {
      const cx = window.innerWidth / 2
      const cy = window.innerHeight / 2
      apply((e.clientX - cx) * intensity, (e.clientY - cy) * intensity)
    }
    window.addEventListener('mousemove', onMove, { passive: true })

    return () => {
      window.removeEventListener('mousemove', onMove)
      cancelAnimationFrame(rafId)
    }
  }, [intensity])

  return <div ref={ref} className={className} style={{ willChange: 'transform' }}>{children}</div>
}
