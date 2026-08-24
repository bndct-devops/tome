// Compiled message catalogs — @lingui/vite-plugin turns a `.po` import into
// `{ messages }` at build time.
declare module '*.po' {
  import type { Messages } from '@lingui/core'
  export const messages: Messages
}
