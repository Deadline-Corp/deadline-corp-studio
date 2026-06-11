import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// base: SPA живёт под /admin/ui/ внутри FastAPI (StaticFiles mount, same-origin).
export default defineConfig({
  plugins: [react()],
  base: '/admin/ui/',
  server: {
    // Локальная разработка: vite dev → проксируем API на локальный uvicorn.
    proxy: {
      '/admin/api': 'http://localhost:8000',
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
  },
})
