/**
 * Paints KOReader highlights inside the foliate web reader.
 *
 * Tome stores annotations with KOReader xPointer anchors, which mean nothing to
 * foliate (it addresses by EPUB CFI, over a differently-normalised DOM). Rather
 * than translating anchors, each highlight is re-anchored by its own text: when
 * a section loads we search its document for `highlighted_text` (using
 * foliate's own search matcher), turn the matched Range into a CFI, and draw it
 * through foliate's overlayer. Resolution is lazy and per-section - a highlight
 * in an unopened chapter costs nothing until that chapter renders, and one
 * whose text can't be found (e.g. spanning a paragraph break, which the
 * text-node haystack can't match across) simply doesn't paint - sync data is
 * never touched.
 *
 * Disambiguation: when the annotation's chapter matches a TOC label, only
 * sections under that label are searched; otherwise the first textual match in
 * any section wins. Multiple occurrences inside one section take the first  - 
 * KOReader anchors don't carry an occurrence index we could honour.
 */

export interface ReaderAnnotation {
  id: number
  anchor: string
  // Set for web-created highlights (anchor "web:…") - paints directly, no search.
  cfi?: string | null
  highlighted_text: string | null
  note: string | null
  chapter: string | null
  color: string | null
  datetime: string | null
}

export const isWebAnnotation = (a: ReaderAnnotation) => a.anchor.startsWith('web:')

/** KOReader highlight colours → translucent fills that work on all reader themes. */
const KO_FILL: Record<string, string> = {
  red: 'rgba(239,68,68,0.35)',
  orange: 'rgba(249,115,22,0.35)',
  yellow: 'rgba(234,179,8,0.35)',
  green: 'rgba(34,197,94,0.32)',
  olive: 'rgba(132,153,55,0.35)',
  cyan: 'rgba(6,182,212,0.32)',
  blue: 'rgba(59,130,246,0.32)',
  purple: 'rgba(168,85,247,0.32)',
  gray: 'rgba(148,163,184,0.38)',
  grey: 'rgba(148,163,184,0.38)',
}
export const fillForColor = (color: string | null): string =>
  KO_FILL[(color ?? '').toLowerCase()] ?? 'rgba(234,179,8,0.35)'

const normalize = (s: string) => s.replace(/\s+/g, ' ').trim().toLowerCase()

// foliate modules live in /public as plain ESM (loaded natively by the reader,
// not bundled). A Vite-transformed dynamic import 500s on public files (?import
// middleware), so this must be a NATIVE import the bundler can't see - same
// mechanism view.js itself uses for its relative imports.
// eslint-disable-next-line @typescript-eslint/no-implied-eval
const nativeImport = new Function('url', 'return import(url)') as (url: string) => Promise<unknown>
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const foliate = (name: string): Promise<any> => nativeImport(`/foliate/${name}.js`)

// Minimal view surface this module needs (the element is untyped upstream).
interface AnnotationView extends HTMLElement {
  book?: { toc?: { label: string; href: string; subitems?: unknown[] }[] } | null
  getCFI(index: number, range: Range): string
  resolveNavigation(target: string): Promise<{ index: number }> | { index: number }
  addAnnotation(annotation: { value: string; color?: string | null }, remove?: boolean): Promise<unknown>
}

type Matcher = (doc: Document, query: string) => Iterable<{ range: Range }>

export class AnnotationPainter {
  private view: AnnotationView
  private annotations: ReaderAnnotation[] = []
  private cfiById = new Map<number, string>()
  private byCfi = new Map<string, ReaderAnnotation>()
  private scannedSections = new Set<number>()
  private sectionLabels = new Map<number, string>()  // section index -> normalized TOC label
  private tocLabels = new Set<string>()              // all normalized labels
  private matcher: Matcher | null = null
  private onShow: (a: ReaderAnnotation) => void
  // Section loads can fire before the annotation fetch / TOC mapping finish  - 
  // scanning waits on this so the first chapter isn't scanned against an empty set.
  private ready: Promise<void>
  private markReady!: () => void

  constructor(view: AnnotationView, onShow: (a: ReaderAnnotation) => void) {
    this.view = view
    this.onShow = onShow
    this.ready = new Promise(res => { this.markReady = res })

    view.addEventListener('draw-annotation', (e: Event) => {
      const { draw, annotation } = (e as CustomEvent).detail as {
        draw: (fn: unknown, opts?: unknown) => void
        annotation: { value: string; color?: string | null }
      }
      const a = this.byCfi.get(annotation.value)
      foliate('overlayer').then(({ Overlayer }) => {
        draw(Overlayer.highlight, { color: fillForColor(a?.color ?? annotation.color ?? null) })
      })
    })

    view.addEventListener('show-annotation', (e: Event) => {
      const { value } = (e as CustomEvent).detail as { value: string }
      const a = this.byCfi.get(value)
      if (a) this.onShow(a)
    })

    // Overlays are per-section and rebuilt when a section (re)loads - repaint
    // everything already resolved for it (mirrors foliate's own search results).
    view.addEventListener('create-overlay', (e: Event) => {
      const { index } = (e as CustomEvent).detail as { index: number }
      this.paintResolved(index)
    })
  }

  /** Feed the annotation set and map the TOC; unblocks section scanning. */
  async start(annotations: ReaderAnnotation[]) {
    this.annotations = annotations.filter(a => a.highlighted_text)
    // Web-created highlights carry their own CFI - register + paint directly.
    for (const a of this.annotations) {
      if (a.cfi) this.register(a, a.cfi)
    }
    try {
      await this.buildSectionLabels()
    } finally {
      this.markReady()
    }
  }

  /** Paint a highlight just created in this session (already has its CFI). */
  addLocal(a: ReaderAnnotation) {
    if (!a.cfi) return
    this.annotations.push(a)
    this.register(a, a.cfi)
  }

  /** Remove a highlight's overlay + tracking (after a server-side delete). */
  removeById(id: number) {
    const cfi = this.cfiById.get(id)
    this.annotations = this.annotations.filter(a => a.id !== id)
    if (!cfi) return
    this.cfiById.delete(id)
    this.byCfi.delete(cfi)
    void this.view.addAnnotation({ value: cfi }, true)
  }

  /** Reflect an edited note/colour on the tracked annotation object. */
  updateLocal(a: ReaderAnnotation) {
    const cfi = this.cfiById.get(a.id)
    if (cfi) this.byCfi.set(cfi, a)
    this.annotations = this.annotations.map(x => (x.id === a.id ? a : x))
    // Repaint so a colour change shows immediately.
    if (cfi) void this.view.addAnnotation({ value: cfi, color: a.color })
  }

  private register(a: ReaderAnnotation, cfi: string) {
    this.cfiById.set(a.id, cfi)
    this.byCfi.set(cfi, a)
    void this.view.addAnnotation({ value: cfi, color: a.color })
  }

  /** Map TOC entries to section indexes so chapter names can gate the search. */
  private async buildSectionLabels() {
    const toc = this.view.book?.toc ?? []
    const walk = async (items: { label: string; href: string; subitems?: unknown[] }[]) => {
      for (const item of items) {
        try {
          const resolved = await this.view.resolveNavigation(item.href)
          const label = normalize(item.label ?? '')
          if (label && !this.sectionLabels.has(resolved.index)) {
            this.sectionLabels.set(resolved.index, label)
          }
          if (label) this.tocLabels.add(label)
        } catch { /* unresolvable TOC entry - skip */ }
        if (item.subitems?.length) {
          await walk(item.subitems as { label: string; href: string; subitems?: unknown[] }[])
        }
      }
    }
    await walk(toc)
  }

  /** Called on foliate's `load` event: resolve + paint this section's highlights. */
  async onSectionLoad(doc: Document, index: number) {
    if (this.scannedSections.has(index)) {
      this.paintResolved(index)
      return
    }
    await this.ready
    if (this.scannedSections.has(index)) return  // raced with a concurrent load
    this.scannedSections.add(index)
    const matcher = await this.getMatcher()
    const sectionLabel = this.sectionLabels.get(index)

    for (const a of this.annotations) {
      if (this.cfiById.has(a.id) || a.cfi) continue
      const chapter = a.chapter ? normalize(a.chapter) : null
      // If the annotation names a chapter we know from the TOC, only search
      // sections under that label; unknown/absent chapters search everywhere.
      if (chapter && this.tocLabels.has(chapter) && chapter !== sectionLabel) continue

      const text = a.highlighted_text!.replace(/\s+/g, ' ').trim()
      if (text.length < 2) continue
      try {
        const match = matcher(doc, text)[Symbol.iterator]().next()
        if (match.done || !match.value) continue
        const cfi = this.view.getCFI(index, match.value.range)
        this.cfiById.set(a.id, cfi)
        this.byCfi.set(cfi, a)
        this.view.addAnnotation({ value: cfi, color: a.color })
      } catch { /* malformed range - leave unresolved */ }
    }
  }

  private paintResolved(index: number) {
    for (const [cfi] of this.byCfi) {
      // addAnnotation resolves the CFI itself and no-ops for other sections.
      void this.view.addAnnotation({ value: cfi, color: this.byCfi.get(cfi)?.color })
    }
    void index
  }

  private async getMatcher(): Promise<Matcher> {
    if (this.matcher) return this.matcher
    const [{ searchMatcher }, { textWalker }] = await Promise.all([
      foliate('search'),
      foliate('text-walker'),
    ])
    this.matcher = searchMatcher(textWalker, {
      defaultLocale: 'en',
      matchCase: false,
      matchDiacritics: true,
      matchWholeWords: false,
    }) as Matcher
    return this.matcher
  }
}
