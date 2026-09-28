import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// Dev-server-only proxy to the backend on :8000 (production nginx does the
// equivalent split - see deploy/nginx-identity-system.conf). Longer than a
// typical single '/api' entry because most of this backend's routes are
// still bare paths (/verify, /logout, /me, ...) rather than namespaced -
// see CLAUDE.md on why those can't move.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': 'http://localhost:8000',
      '/verify': 'http://localhost:8000',
      '/logout': 'http://localhost:8000',
      '/me': 'http://localhost:8000',
      '/profile': 'http://localhost:8000',
      '/password': 'http://localhost:8000',
      '/2fa': 'http://localhost:8000',
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test-setup.ts'],
  },
})
