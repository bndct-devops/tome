// #9 — Interactive roles/visibility demo. Toggle role → filtered book list.
import { useState } from 'react'

type Role = 'admin' | 'member' | 'guest'

interface Book {
  title: string
  uploader: 'admin' | 'me' | 'other'
  library: string | null   // null = not in any library
  libraryPublic: boolean
  myLibraryAccess: boolean  // the member owns or is assigned to this library
}

// "member" perspective is "me". Library membership is the gate: a private
// library hides its books from everyone except the owner/assigned/admins,
// regardless of uploader. Unfiled admin books fall back to being public.
const BOOKS: Book[] = [
  { title: 'Berserk, Vol. 1',        uploader: 'admin', library: 'Manga',      libraryPublic: true,  myLibraryAccess: false },
  { title: 'Frankenstein',           uploader: 'admin', library: 'Classics',   libraryPublic: true,  myLibraryAccess: false },
  { title: 'Project Hail Mary',      uploader: 'me',    library: 'My sci-fi',  libraryPublic: false, myLibraryAccess: true },
  { title: "Stefan's secret diary",  uploader: 'other', library: 'Private',    libraryPublic: false, myLibraryAccess: false },
  { title: 'The Three-Body Problem', uploader: 'admin', library: 'Restricted', libraryPublic: false, myLibraryAccess: false },
  { title: "Family album '24",       uploader: 'admin', library: null,         libraryPublic: false, myLibraryAccess: false },
]

function canSee(role: Role, b: Book): boolean {
  if (role === 'admin') return true
  const unfiledShared = b.library === null && b.uploader === 'admin'
  if (role === 'member') return b.uploader === 'me' || b.libraryPublic || b.myLibraryAccess || unfiledShared
  return b.libraryPublic || unfiledShared
}

export function RolesVisibilityDemo() {
  const [role, setRole] = useState<Role>('admin')

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-center gap-2">
        <span className="text-xs text-[var(--muted)]">You are:</span>
        {(['admin', 'member', 'guest'] as Role[]).map(r => (
          <button
            key={r}
            onClick={() => setRole(r)}
            className="px-3 py-1.5 rounded-md text-xs font-medium capitalize transition-all"
            style={{
              background: role === r ? 'var(--accent)' : 'var(--card)',
              color: role === r ? 'var(--accent-fg)' : 'var(--fg)',
              border: `1px solid ${role === r ? 'var(--accent)' : 'var(--border)'}`,
            }}
          >{r}</button>
        ))}
      </div>
      <div className="rounded-xl border border-[var(--border)] bg-[var(--card)] divide-y divide-[var(--border)] overflow-hidden">
        {BOOKS.map(b => {
          const visible = canSee(role, b)
          return (
            <div
              key={b.title}
              className="px-4 py-3 flex items-center justify-between"
              style={{
                opacity: visible ? 1 : 0.25,
                background: visible ? 'transparent' : 'color-mix(in oklab, var(--border), transparent 70%)',
                transition: 'opacity 280ms, background 280ms',
              }}
            >
              <div>
                <div className="text-sm font-medium">{b.title}</div>
                <div className="text-[11px] text-[var(--muted)]">
                  {b.library === null ? 'no library' : `${b.library} (${b.libraryPublic ? 'public' : 'private'})`} · uploaded by {b.uploader}
                </div>
              </div>
              <div className="text-[11px]" style={{ color: visible ? 'var(--accent)' : 'var(--muted)' }}>
                {visible ? '✓ visible' : '— hidden'}
              </div>
            </div>
          )
        })}
      </div>
      <p className="text-[11px] text-[var(--muted)] text-center">
        Try the role toggles — visibility is enforced server-side, not just in the UI.
      </p>
    </div>
  )
}
