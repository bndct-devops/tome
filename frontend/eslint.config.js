import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'
import lingui from 'eslint-plugin-lingui'
import { defineConfig, globalIgnores } from 'eslint/config'

export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
    },
  },
  // i18n: flag user-facing strings that bypass Lingui. Warn-level while the
  // extraction sweep is in progress; flip to 'error' once pages/components are
  // fully wrapped in <Trans> / t``.
  {
    files: ['src/pages/**/*.tsx', 'src/components/**/*.tsx'],
    plugins: { lingui },
    rules: {
      'lingui/no-unlocalized-strings': ['warn', {
        ignore: [
          '^(?![A-Z])\\S+$',        // single lowercase tokens: keys, classes, slugs
          '^[A-Z0-9_-]+$',            // CONSTANTS
          '^/',                       // API and router paths
          '^tome_',                   // localStorage keys
          '^Tome$',                   // product name, never translated
          '^[^a-zA-Z]*$',             // punctuation / symbols only
        ],
        ignoreNames: [
          { regex: { pattern: 'className', flags: 'i' } },
          { regex: { pattern: '^[A-Z0-9_-]+$' } },
          'style', 'src', 'srcSet', 'href', 'type', 'id', 'key', 'name', 'role', 'iconName',
          'width', 'height', 'fill', 'stroke', 'viewBox', 'd',
          'transform', 'transformOrigin', 'transition', 'download', 'flipId',
          'value', 'to', 'method', 'accept', 'autoComplete', 'inputMode', 'keys',
          'displayName', 'Authorization', 'family', 'format', 'icon', 'defaultIcon',
          'menuItem', 'destructive',
        ],
        ignoreFunctions: [
          'cn', 'cva', 'clsx', 'Error', 'console.*', 'require',
          'api.*', 'fetch', 'localStorage.*', 'sessionStorage.*', 'URLSearchParams',
          '*.addEventListener', '*.removeEventListener', '*.getElementById',
          '*.querySelector', '*.querySelectorAll', '*.postMessage',
          '*.includes', '*.indexOf', '*.endsWith', '*.startsWith', '*.split',
          '*.replace', '*.startsWith', '*.setAttribute', '*.getAttribute',
          '*.setItem', '*.getItem', '*.removeItem', 'new Date', '*.toLocaleDateString',
          '*.toLocaleString', '*.toLocaleTimeString', '*.localeCompare',
          'useState', 'useRef', 'navigate', 'open',
          'getIcon', 'setLibModalInitialIcon', 'setModalInitialIcon', 'setModalDefaultIcon',
        ],
      }],
      'lingui/t-call-in-function': 'error',
      'lingui/no-single-variables-to-translate': 'error',
      'lingui/no-expression-in-message': 'error',
    },
  },
])
