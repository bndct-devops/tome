import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'path'
import { VitePWA } from 'vite-plugin-pwa'

export default defineConfig({
  // Some deps (react-draggable, prop-types — pulled in by react-grid-layout) read
  // process.env.NODE_ENV at runtime. Vite replaces it in production builds but not in
  // dev, so without this the browser hits an undefined `process` on first interaction.
  define: {
    'process.env.NODE_ENV': JSON.stringify(process.env.NODE_ENV || 'development'),
  },
  plugins: [
    react(),
    tailwindcss(),
    VitePWA({
      registerType: 'autoUpdate',
      includeAssets: ['favicon.svg', 'apple-touch-icon.png'],
      manifest: {
        name: 'Tome',
        short_name: 'Tome',
        description: 'Self-hosted ebook library',
        theme_color: '#863bff',
        background_color: '#09090b',
        display: 'standalone',
        start_url: '/',
        icons: [
          {
            src: '/pwa-192x192.png',
            sizes: '192x192',
            type: 'image/png',
          },
          {
            src: '/pwa-512x512.png',
            sizes: '512x512',
            type: 'image/png',
          },
          {
            src: '/pwa-512x512-maskable.png',
            sizes: '512x512',
            type: 'image/png',
            purpose: 'maskable',
          },
        ],
      },
      workbox: {
        // The SPA navigation fallback serves index.html for navigations — but it
        // must NOT swallow full-page navigations to server routes, or the service
        // worker returns the app shell instead of letting them reach the backend.
        // This broke the OIDC handshake (window.location → /api/auth/oidc/login
        // and the IdP's redirect to /callback both got the SPA shell), and would
        // do the same to any /api or /opds navigation (downloads, plugin, feeds).
        navigateFallbackDenylist: [/^\/api\//, /^\/opds\//],
        // Cache static assets; skip large book files
        globPatterns: ['**/*.{js,css,html,svg,png,woff2}'],
        maximumFileSizeToCacheInBytes: 5 * 1024 * 1024, // 5 MB
        runtimeCaching: [
          {
            // Cache API responses for book metadata (not downloads)
            urlPattern: /^\/api\/books(\?.*)?$/,
            handler: 'NetworkFirst',
            options: {
              cacheName: 'api-books',
              networkTimeoutSeconds: 10,
              expiration: {
                maxEntries: 50,
                maxAgeSeconds: 60 * 60, // 1 hour
              },
            },
          },
          {
            // Cache cover images aggressively
            urlPattern: /^\/api\/books\/\d+\/cover/,
            handler: 'CacheFirst',
            options: {
              cacheName: 'book-covers',
              expiration: {
                maxEntries: 500,
                maxAgeSeconds: 7 * 24 * 60 * 60, // 7 days
              },
            },
          },
        ],
      },
    }),
  ],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    host: true,
    port: Number(process.env.VITE_PORT) || 5173,
    proxy: {
      '/api': {
        target: process.env.VITE_API_TARGET || 'http://localhost:8080',
        changeOrigin: true,
      },
      '/opds': {
        target: process.env.VITE_API_TARGET || 'http://localhost:8080',
        changeOrigin: true,
      },
    },
  },
})
