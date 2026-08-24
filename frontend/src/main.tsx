import './index.css'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { I18nProvider } from '@lingui/react'
import App from './App.tsx'
import { i18n, activateLocale, detectLocale } from './lib/i18n'

// Activate the UI language before the first render so nothing flashes English
// for a user who picked another language. activateLocale never rejects: a
// missing catalog renders the source strings.
activateLocale(detectLocale())
  .then(() => {
    createRoot(document.getElementById('root')!).render(
      <StrictMode>
        <I18nProvider i18n={i18n}>
          <App />
        </I18nProvider>
      </StrictMode>,
    )
  })
