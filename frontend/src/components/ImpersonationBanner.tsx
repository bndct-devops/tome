import { UserX } from 'lucide-react'
import { useAuth } from '@/contexts/AuthContext'

export function ImpersonationBanner() {
  const { isImpersonating, impersonatedUsername, exitImpersonation } = useAuth()

  if (!isImpersonating) return null

  return (
    <div className="fixed bottom-0 inset-x-0 z-50 flex items-center justify-center gap-3 px-4 py-2.5 bg-amber-500 text-amber-950 text-sm font-medium shadow-lg">
      <UserX className="w-4 h-4 shrink-0" />
      <span>Viewing as <strong>{impersonatedUsername}</strong></span>
      <button
        onClick={exitImpersonation}
        className="ml-2 px-3 py-1 rounded-md bg-amber-950/15 hover:bg-amber-950/25 transition-colors text-xs font-semibold"
      >
        Exit impersonation
      </button>
    </div>
  )
}
