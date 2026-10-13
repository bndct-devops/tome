import { useSyncExternalStore } from 'react'
import { t } from '@lingui/core/macro'
import { api } from '@/lib/api'

/** One of the user's KOReader devices and the Tome books on it, as the
 *  TomeSync plugin (build 50+) last reported. GET /api/device-books. */
export interface DeviceInventory {
  name: string
  synced_at: string
  plugin_build: number | null
  book_count: number
  book_ids: number[]
}

/** A list older than this is shown muted with its age: the device has not
 *  synced in a while, so the books on it may have changed. */
export const DEVICE_STALE_MS = 3 * 24 * 3600_000

const EMPTY: DeviceInventory[] = []
let cache: DeviceInventory[] | null = null
let fetchedAt = 0
let inflight: Promise<DeviceInventory[]> | null = null
const listeners = new Set<() => void>()
// Lists change when a device syncs; a minute is fresh enough for pills and
// keeps a grid of cards to one request.
const TTL_MS = 60_000

function load(): Promise<DeviceInventory[]> {
  if (!inflight) {
    inflight = api.get<{ devices: DeviceInventory[] }>('/device-books')
      .then(r => {
        cache = r.devices
        fetchedAt = Date.now()
        listeners.forEach(fn => fn())
        return r.devices
      })
      .catch(() => cache ?? EMPTY)
      .finally(() => { inflight = null })
  }
  return inflight
}

function subscribe(onChange: () => void): () => void {
  listeners.add(onChange)
  if (!cache || Date.now() - fetchedAt > TTL_MS) void load()
  return () => { listeners.delete(onChange) }
}

function snapshot(): DeviceInventory[] {
  return cache ?? EMPTY
}

/** The user's devices (newest sync first). Empty until loaded, and for
 *  anyone without a TomeSync device - every surface renders nothing then. */
export function useDeviceInventories(): DeviceInventory[] {
  return useSyncExternalStore(subscribe, snapshot)
}

/** Reload the lists (after forgetting a device). */
export function refreshDeviceInventories(): Promise<DeviceInventory[]> {
  fetchedAt = 0
  return load()
}

const idSets = new WeakMap<DeviceInventory, Set<number>>()

/** The devices a book is on, in the list's order. */
export function devicesFor(devices: DeviceInventory[], bookId: number): DeviceInventory[] {
  return devices.filter(d => {
    let ids = idSets.get(d)
    if (!ids) { ids = new Set(d.book_ids); idSets.set(d, ids) }
    return ids.has(bookId)
  })
}

export function isStale(d: DeviceInventory): boolean {
  return Date.now() - new Date(d.synced_at).getTime() > DEVICE_STALE_MS
}

export function syncedAgo(iso: string): string {
  const ts = new Date(iso).getTime()
  if (Number.isNaN(ts)) return t`Never`
  const diff = Date.now() - ts
  if (diff < 60_000) return t`Just synced`
  const mins = Math.floor(diff / 60_000)
  if (mins < 60) return t`${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return t`${hrs}h ago`
  const days = Math.floor(hrs / 24)
  if (days === 1) return t`Yesterday`
  if (days < 7) return t`${days}d ago`
  return new Date(ts).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}
