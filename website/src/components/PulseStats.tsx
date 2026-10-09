// The public numbers from tome-pulse, fetched in the browser so the page is
// live without a rebuild. Everything shown here is an aggregate over the
// latest report of each instance that reported in the last 90 days.
import { useEffect, useState } from 'react'

const DEFAULT_ENDPOINT = 'https://pulse.tome.bndct.sh/v1/aggregate'
// ?pulse=<url> lets a dev build read a local receiver; production ignores it unless asked.
const endpoint = () => (typeof window !== 'undefined' && new URLSearchParams(window.location.search).get('pulse')) || DEFAULT_ENDPOINT

interface Aggregate {
  generated: string
  active_instances: number
  reported_this_month: number
  new_instances_30d: number
  hours_read_30d_estimate: number
  docker: number
  features_30d: Record<string, number>
  versions: Record<string, number>
  platforms: Record<string, number>
  plugin_builds: Record<string, number>
  install_age: Record<string, number>
  users: Record<string, number>
  readers_30d: Record<string, number>
  books: Record<string, number>
  sessions_30d: Record<string, number>
  hours_30d: Record<string, number>
  pages_30d: Record<string, number>
  finished_30d: Record<string, number>
  highlights: Record<string, number>
  book_types: Record<string, number>
  formats: Record<string, number>
  languages: Record<string, number>
}

const FEATURE_LABELS: Record<string, string> = {
  koreader_sync: 'KOReader sync',
  web_reader: 'Web reader',
  kindle_sync_code: 'Kindle sync code',
  send_to_device: 'Send to device',
  bindery: 'Bindery',
  wishlist: 'Wishlist',
  hardcover: 'Hardcover sync',
  opds: 'OPDS',
  sso_users: 'Single sign-on',
  api_tokens: 'API tokens',
  reading_goals: 'Reading goals',
  series_ratings: 'Series ratings',
  arcs: 'Arcs',
}

// Buckets sort by their lower bound, not alphabetically.
const bucketOrder = (b: string) => (b.endsWith('+') ? Number(b.slice(0, -1)) : Number(b.split('-')[0]))

function Bars({ title, data, labels, sortBy = 'value', top }: {
  title: string; data: Record<string, number>; labels?: Record<string, string>; sortBy?: 'value' | 'bucket'; top?: number
}) {
  let rows = Object.entries(data)
  rows = sortBy === 'bucket' ? rows.sort((a, b) => bucketOrder(a[0]) - bucketOrder(b[0])) : rows.sort((a, b) => b[1] - a[1])
  if (top) rows = rows.slice(0, top)
  const max = Math.max(...rows.map(r => r[1]), 0.0001)
  return (
    <div className="pulse-card">
      <h3>{title}</h3>
      {rows.length === 0 && <p className="pulse-empty">Nothing yet.</p>}
      <ul>
        {rows.map(([k, v]) => (
          <li key={k}>
            <span className="pulse-label">{labels?.[k] ?? k}</span>
            <span className="pulse-track"><span className="pulse-fill" style={{ width: `${(v / max) * 100}%` }} /></span>
            <span className="pulse-pct">{Math.round(v * 100)}%</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

export function PulseStats() {
  const [data, setData] = useState<Aggregate | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    fetch(endpoint()).then(r => (r.ok ? r.json() : Promise.reject(r.status))).then(setData).catch(() => setFailed(true))
  }, [])

  if (failed) return <p className="pulse-empty">The numbers are not reachable right now. Try again in a minute.</p>
  if (!data) return <p className="pulse-empty">Loading…</p>

  const generated = new Date(data.generated).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
  return (
    <div className="pulse">
      <div className="pulse-headline">
        <div><strong>{data.active_instances.toLocaleString()}</strong><span>instances reporting</span></div>
        <div><strong>{data.hours_read_30d_estimate.toLocaleString()}</strong><span>hours read in the last 30 days, estimated</span></div>
        <div><strong>{data.new_instances_30d.toLocaleString()}</strong><span>new in the last 30 days</span></div>
        <div><strong>{Math.round(data.docker * 100)}%</strong><span>run in Docker</span></div>
      </div>
      <p className="pulse-generated">Aggregated {generated}. Counts are instances active in the last 90 days.</p>
      <div className="pulse-grid">
        <Bars title="Features used in the last 30 days" data={data.features_30d} labels={FEATURE_LABELS} />
        <Bars title="Versions" data={data.versions} top={8} />
        <Bars title="What people read" data={data.book_types} />
        <Bars title="Formats" data={data.formats} />
        <Bars title="Languages" data={data.languages} />
        <Bars title="Books per instance" data={data.books} sortBy="bucket" />
        <Bars title="Readers per instance, last 30 days" data={data.readers_30d} sortBy="bucket" />
        <Bars title="Hours read per instance, last 30 days" data={data.hours_30d} sortBy="bucket" />
        <Bars title="Books finished per instance, last 30 days" data={data.finished_30d} sortBy="bucket" />
        <Bars title="Install age, months" data={data.install_age} sortBy="bucket" />
        <Bars title="Platforms" data={data.platforms} />
        <Bars title="KOReader plugin builds" data={data.plugin_builds} top={6} />
      </div>
    </div>
  )
}
