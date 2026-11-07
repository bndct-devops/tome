// Tome brand mark - drop-in replacement for the Lucide <BookOpen> we used
// to use as the app logo. Stroke colour comes from currentColor so existing
// text-* utility classes (e.g. text-primary) still work. Stroke-width follows
// the brand size ladder: heavier at small render sizes.
import type { SVGProps } from 'react'

interface Props extends SVGProps<SVGSVGElement> {
  /** Brand spec: 7 for ≤24px, 5 for 24-48px, 4 for ≥48px. Default 5. */
  strokeWidth?: number
}

export function TomeMark({ strokeWidth = 5, className, ...rest }: Props) {
  return (
    <svg
      viewBox="0 0 64 64"
      fill="none"
      aria-hidden="true"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      {...rest}
    >
      <path
        d="M4 18 Q 4 14 8 14 L 28 18 Q 32 19 32 22 Q 32 19 36 18 L 56 14 Q 60 14 60 18 L 60 46 Q 60 49 56 49 L 36 49 Q 32 50 32 52 Q 32 50 28 49 L 8 49 Q 4 49 4 46 Z"
        stroke="currentColor"
        strokeWidth={strokeWidth}
        strokeLinejoin="round"
      />
      <line
        x1="32" y1="22" x2="32" y2="52"
        stroke="currentColor"
        strokeWidth={strokeWidth}
        strokeLinecap="round"
      />
    </svg>
  )
}
