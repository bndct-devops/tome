import { defineConfig } from '@lingui/cli'
import { formatter } from '@lingui/format-po'

// Source strings live inline in the components (English); `npm run i18n:extract`
// pulls them into src/locales/<locale>/messages.po. The Vite plugin compiles the
// .po files on import, so no separate compile step is needed for dev or build.
export default defineConfig({
  sourceLocale: 'en',
  // File origins without line numbers: an unrelated edit above a string must not
  // rewrite the catalog (keeps translation PRs and the CI freshness check quiet).
  format: formatter({ lineNumbers: false }),
  locales: ['en', 'zh-CN'],
  catalogs: [
    {
      path: '<rootDir>/src/locales/{locale}/messages',
      include: ['src'],
    },
  ],
})
