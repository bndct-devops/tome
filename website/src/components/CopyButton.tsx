import { useState } from 'react'
import { Copy, Check } from 'lucide-react'

interface Props {
  text: string
  className?: string
}

export function CopyButton({ text, className }: Props) {
  const [copied, setCopied] = useState(false)

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      // ignore — fallback could prompt the user
    }
  }

  return (
    <button
      type="button"
      onClick={handleCopy}
      aria-label={copied ? 'Copied' : 'Copy to clipboard'}
      className={[
        'inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-xs font-medium',
        'bg-[var(--card)] border border-[var(--border)]',
        'text-[var(--muted)] hover:text-[var(--fg)] hover:bg-[var(--bg)]',
        'transition-colors',
        className,
      ].filter(Boolean).join(' ')}
    >
      {copied ? <Check className="w-3.5 h-3.5 text-emerald-500" /> : <Copy className="w-3.5 h-3.5" />}
      {copied ? 'Copied' : 'Copy'}
    </button>
  )
}
