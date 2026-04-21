import { useState } from 'react'
import { BookOpen } from 'lucide-react'
import { cn } from '@/lib/utils'

interface CoverImageProps {
  src: string | null | undefined
  alt: string
  loading?: 'lazy' | 'eager'
  iconClassName?: string
  imgClassName?: string
}

export function CoverImage({
  src,
  alt,
  loading = 'lazy',
  iconClassName,
  imgClassName,
}: CoverImageProps) {
  const [loaded, setLoaded] = useState(false)
  const [errored, setErrored] = useState(false)

  const hasImg = !!src && !errored

  return (
    <>
      {!!src && !loaded && !errored && (
        <div
          className="absolute inset-0 bg-muted"
          style={{
            backgroundImage:
              'linear-gradient(90deg, transparent 25%, rgba(255,255,255,0.05) 50%, transparent 75%)',
            backgroundSize: '200% 100%',
            animation: 'shimmer 1.5s infinite',
          }}
        />
      )}
      {hasImg && (
        <img
          src={src!}
          alt={alt}
          loading={loading}
          onLoad={() => setLoaded(true)}
          onError={() => setErrored(true)}
          className={cn(
            'w-full h-full object-cover transition-[opacity,transform] duration-300',
            loaded ? 'opacity-100' : 'opacity-0',
            imgClassName,
          )}
        />
      )}
      {(!src || errored) && (
        <div className="absolute inset-0 flex items-center justify-center bg-muted">
          <BookOpen className={cn('text-muted-foreground/30 w-8 h-8', iconClassName)} />
        </div>
      )}
    </>
  )
}
