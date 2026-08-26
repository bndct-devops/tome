// Configurable tap-to-turn zones, shared by every reader (comic / EPUB / PDF).
//
// A tap anywhere on the reading surface resolves — via the user's chosen layout —
// to one of three actions: turn back a page, turn forward a page, or toggle the
// menu/toolbar. Coordinates are normalized to [0,1] so the same layout works at
// any screen size and in fullscreen / installed-PWA mode.
//
// "prev"/"next" are READING-ORDER actions; each reader maps them to its own
// page-turn (a comic in RTL mode still advances correctly — direction is the
// reader's concern, the zone that triggers "next" is this module's concern).

export type TapLayout = 'edge' | 'edge-swapped' | 'forward' | 'off'
export type TapAction = 'prev' | 'next' | 'menu'

export const DEFAULT_TAP_LAYOUT: TapLayout = 'edge'

export const TAP_LAYOUTS: { id: TapLayout; label: string; hint: string }[] = [
  { id: 'edge', label: 'Prev · Menu · Next', hint: 'Left = back, centre = menu, right = forward' },
  { id: 'edge-swapped', label: 'Next · Menu · Prev', hint: 'Left-handed: left = forward, right = back' },
  { id: 'forward', label: 'Tap-forward', hint: 'Tap anywhere to go forward; small zone at the bottom-centre goes back' },
  { id: 'off', label: 'Off', hint: 'No tap zones — swipe / scroll only' },
]

const LEFT = 1 / 3
const RIGHT = 2 / 3

/**
 * Resolve a tap at normalized coordinates to an action, or `null` when the tap
 * should be ignored (layout 'off', or a dead area).
 *
 * @param layout the active preset
 * @param x horizontal position, 0 (far left) .. 1 (far right)
 * @param y vertical position, 0 (top) .. 1 (bottom)
 */
export function resolveTap(layout: TapLayout, x: number, y: number): TapAction | null {
  switch (layout) {
    case 'off':
      return null
    case 'edge':
      if (x < LEFT) return 'prev'
      if (x > RIGHT) return 'next'
      return 'menu'
    case 'edge-swapped':
      if (x < LEFT) return 'next'
      if (x > RIGHT) return 'prev'
      return 'menu'
    case 'forward':
      // A small "back" square at the bottom-centre; a menu strip along the top
      // centre; everything else advances. Matches the "→ ≡ → , small □ = back"
      // one-handed layout.
      if (x > 0.35 && x < 0.65 && y > 0.65) return 'prev'
      if (x > LEFT && x < RIGHT && y < 0.5) return 'menu'
      return 'next'
  }
}

/** Load the saved layout from localStorage, falling back to the default. */
export function loadTapLayout(): TapLayout {
  const v = (typeof localStorage !== 'undefined' && localStorage.getItem('reader_tap_layout')) || ''
  return (['edge', 'edge-swapped', 'forward', 'off'] as const).includes(v as TapLayout)
    ? (v as TapLayout)
    : DEFAULT_TAP_LAYOUT
}
