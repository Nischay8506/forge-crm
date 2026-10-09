import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'path'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  // /api goes to the backend, so a phone on the same network can use the app through this
  // one address (set VITE_API_URL=/api/v1 and run `npm run dev -- --host`; see docs/USABILITY_TEST.md).
  server: { port: 5173, proxy: { '/api': 'http://localhost:8000' } },
  resolve: {
    alias: { '@': path.resolve(import.meta.dirname, './src') },
  },
})
