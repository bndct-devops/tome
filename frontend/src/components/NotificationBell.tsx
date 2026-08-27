import { useState, useEffect, useRef, useCallback } from 'react'
import { AnimatePresence, m } from 'motion/react'
import { Bell, Check, BookOpen, Target, Timer } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { cn } from '@/lib/utils'
import {
  listNotifications,
  markRead,
  markAllRead,
  type NotificationOut,
} from '@/lib/notifications'
import { Trans, useLingui } from '@lingui/react/macro'
import { t } from '@lingui/core/macro'

const POLL_INTERVAL_MS = 60_000 // 1 minute

// Uses the core t macro (global i18n) — called during render, so a locale
// switch re-renders it through I18nProvider like everything else.
function relativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime()
  const mins = Math.floor(diff / 60_000)
  if (mins < 1) return t`just now`
  if (mins < 60) return t`${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return t`${hrs}h ago`
  const days = Math.floor(hrs / 24)
  return t`${days}d ago`
}

export function NotificationBell() {
  const { t } = useLingui()
  const [open, setOpen] = useState(false)
  const [notifications, setNotifications] = useState<NotificationOut[]>([])
  const [unreadCount, setUnreadCount] = useState(0)
  const [markingAll, setMarkingAll] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const navigate = useNavigate()

  const fetchUnread = useCallback(async () => {
    try {
      const items = await listNotifications(true)
      setUnreadCount(items.length)
    } catch {
      // silently ignore — bell is non-critical
    }
  }, [])

  const fetchAll = useCallback(async () => {
    try {
      const items = await listNotifications()
      setNotifications(items)
      setUnreadCount(items.filter(n => !n.read).length)
    } catch {
      // silently ignore
    }
  }, [])

  // Poll for unread count
  useEffect(() => {
    fetchUnread()
    const interval = setInterval(fetchUnread, POLL_INTERVAL_MS)
    return () => clearInterval(interval)
  }, [fetchUnread])

  // When dropdown opens, load full list
  useEffect(() => {
    if (open) {
      fetchAll()
    }
  }, [open, fetchAll])

  // Close on outside click
  useEffect(() => {
    function handler(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  async function handleClickNotification(n: NotificationOut) {
    if (!n.read) {
      try {
        await markRead(n.id)
        setNotifications(prev => prev.map(x => x.id === n.id ? { ...x, read: true } : x))
        setUnreadCount(c => Math.max(0, c - 1))
      } catch {
        // best-effort
      }
    }
    if (n.link) {
      setOpen(false)
      // Internal links start with /
      if (n.link.startsWith('/')) {
        navigate(n.link)
      } else {
        window.open(n.link, '_blank', 'noopener,noreferrer')
      }
    }
  }

  async function handleMarkAll() {
    setMarkingAll(true)
    try {
      await markAllRead()
      setNotifications(prev => prev.map(n => ({ ...n, read: true })))
      setUnreadCount(0)
    } catch {
      // best-effort
    } finally {
      setMarkingAll(false)
    }
  }

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen(o => !o)}
        title={t`Notifications`}
        aria-label={t`Notifications`}
        className={cn(
          'relative flex items-center justify-center w-8 h-8 rounded-lg transition-colors',
          open
            ? 'bg-accent text-foreground'
            : 'text-muted-foreground hover:text-foreground hover:bg-accent'
        )}
      >
        <Bell className="w-4 h-4" />
        {unreadCount > 0 && (
          <span className="absolute top-0.5 right-0.5 flex items-center justify-center min-w-[14px] h-[14px] rounded-full bg-primary text-primary-foreground text-[9px] font-bold leading-none px-0.5">
            {unreadCount > 99 ? '99+' : unreadCount}
          </span>
        )}
      </button>

      <AnimatePresence>
      {open && (
        <m.div
          initial={{ opacity: 0, scale: 0.95, y: -4 }}
          animate={{ opacity: 1, scale: 1, y: 0 }}
          exit={{ opacity: 0, scale: 0.95, y: -4 }}
          transition={{ duration: 0.14, ease: [0.2, 0.8, 0.2, 1] }}
          style={{ transformOrigin: 'top right' }}
          className="absolute right-0 top-full mt-1.5 z-50 w-80 max-h-96 flex flex-col bg-card border border-border rounded-xl shadow-xl shadow-accent-soft overflow-hidden">
          {/* Header */}
          <div className="flex items-center justify-between px-3 py-2.5 border-b border-border shrink-0">
            <span className="text-xs font-semibold text-foreground"><Trans>Notifications</Trans></span>
            {unreadCount > 0 && (
              <button
                onClick={handleMarkAll}
                disabled={markingAll}
                className="flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground transition-colors disabled:opacity-50"
              >
                <Check className="w-3 h-3" />
                <Trans>Mark all read</Trans>
              </button>
            )}
          </div>

          {/* List */}
          <div className="flex-1 overflow-y-auto">
            {notifications.length === 0 ? (
              <div className="flex flex-col items-center gap-2 py-8 text-center">
                <Bell className="w-8 h-8 text-muted-foreground/30" />
                <p className="text-xs text-muted-foreground"><Trans>No notifications yet</Trans></p>
              </div>
            ) : (
              notifications.map(n => (
                <button
                  key={n.id}
                  onClick={() => handleClickNotification(n)}
                  className={cn(
                    'w-full flex items-start gap-3 px-3 py-2.5 text-left transition-colors hover:bg-accent/50 border-b border-border last:border-0',
                    !n.read && 'bg-primary/5'
                  )}
                >
                  <div className={cn(
                    'mt-0.5 flex items-center justify-center w-6 h-6 rounded-full shrink-0',
                    n.kind === 'wish_fulfilled' || n.kind === 'goal_reached'
                      ? 'bg-success/10 text-success'
                      : 'bg-muted text-muted-foreground'
                  )}>
                    {n.kind === 'goal_reached' ? <Target className="w-3 h-3" /> : n.kind === 'session_suspect' ? <Timer className="w-3 h-3" /> : <BookOpen className="w-3 h-3" />}
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className={cn(
                      'text-xs leading-snug line-clamp-2',
                      !n.read ? 'font-medium text-foreground' : 'text-foreground/80'
                    )}>
                      {n.title}
                    </p>
                    {n.body && (
                      <p className="text-[11px] text-muted-foreground line-clamp-2 mt-0.5">{n.body}</p>
                    )}
                    <p className="text-[10px] text-muted-foreground/60 mt-1">{relativeTime(n.created_at)}</p>
                  </div>
                  {!n.read && (
                    <span className="mt-1.5 w-1.5 h-1.5 rounded-full bg-primary shrink-0" />
                  )}
                </button>
              ))
            )}
          </div>
        </m.div>
      )}
      </AnimatePresence>
    </div>
  )
}
