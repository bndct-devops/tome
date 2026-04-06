import { useState } from 'react'
import { Bookmark } from 'lucide-react'
import { api } from '@/lib/api'
import { EntityModal } from '@/components/EntityModal'

interface Props {
  params: Record<string, string>
  onSaved: () => void
}

export function SaveFilterButton({ params, onSaved }: Props) {
  const [modalOpen, setModalOpen] = useState(false)

  const hasFilters = Object.values(params).some(v => !!v)
  if (!hasFilters) return null

  async function handleSave(name: string, icon: string) {
    await api.post('/saved-filters', { name, icon, params })
    onSaved()
  }

  return (
    <>
      {modalOpen && (
        <EntityModal
          title="Add Shelf"
          defaultIcon="Bookmark"
          onSave={handleSave}
          onClose={() => setModalOpen(false)}
        />
      )}
      <button
        onClick={() => setModalOpen(true)}
        className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium border border-border bg-card text-muted-foreground hover:text-primary hover:border-primary/30 transition-all"
        title="Save current filters as a shelf"
      >
        <Bookmark className="w-3.5 h-3.5" />
        <span className="hidden sm:inline">Add shelf</span>
      </button>
    </>
  )
}
