/// <reference types="vitest/config" />
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// In development the browser talks to Vite, which proxies /api to the FastAPI backend,
// so no API base URL or CORS setup is needed locally.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': process.env.VITE_PROXY_TARGET ?? 'http://127.0.0.1:8000',
    },
  },
  test: {
    environment: 'jsdom',
    pool: 'threads',
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
  },
})
