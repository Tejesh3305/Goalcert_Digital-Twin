import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// During `npm run dev`, Vite serves the app on :5173 and proxies API + SSE
// calls to the FastAPI server on :8000. In production, `npm run build` emits
// to dist/, which FastAPI serves directly (see server/main.py).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // SSE stream — disable buffering so events push through immediately.
      '/api/v1/bus/stream': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        selfHandleResponse: false,
        configure: (proxy) => {
          proxy.on('proxyRes', (proxyRes) => {
            proxyRes.headers['x-accel-buffering'] = 'no'
          })
        },
      },
      // All other API calls.
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
})
