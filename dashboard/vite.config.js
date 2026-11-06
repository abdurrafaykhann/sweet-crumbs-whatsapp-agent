import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// The built app is served by FastAPI under /admin; in development `npm run dev` proxies /api to the backend.
export default defineConfig({
  base: '/admin/',
  plugins: [react(), tailwindcss()],
  server: { port: 5173, proxy: { '/api': 'http://127.0.0.1:8000' } },
})
