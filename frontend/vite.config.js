import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import federation from '@originjs/vite-plugin-federation'

// NextXR frontend — runs standalone AND ships a Module-Federation remote the
// Goalcert Hub mounts natively. `npm run build` emits dist/ with
// assets/remoteEntry.js; the hub loads that URL and renders <TwinRemoteApp/>.
//
// VITE_REMOTE_BASE is critical when federated: this build's assets (the turbine
// GLB, CSS, chunks) must resolve to THIS app's origin, not the host's. Emitting
// base-relative '/assets/...' makes the browser look on the hub's origin (:8090),
// where they don't exist — so the 3-D model and styles 404. Setting an absolute
// base pins them to NextXR's own origin. Locally that's http://localhost:8080/
// (NextXR's FastAPI serves this dist); on AWS it's the S3/CloudFront remote path.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const REMOTE_BASE = env.VITE_REMOTE_BASE || '/'

  return {
    base: REMOTE_BASE,
    plugins: [
      react(),
      federation({
        name: 'nextxrTwin',
        filename: 'remoteEntry.js',
        exposes: {
          // the host mounts THIS one self-contained component (providers+router+routes)
          './TwinRemoteApp': './src/TwinRemoteApp.jsx',
          // kept available for finer-grained composition / a future state bridge
          './TwinRoutes': './src/TwinRoutes.jsx',
          './TwinProvider': './src/context/TwinContext.jsx',
          './ToastProvider': './src/context/ToastContext.jsx',
          './apiClient': './src/api/client.js',
        },
        // React must be one instance across host+remote or hooks break. three /
        // react-router live only inside this remote's tree, so it keeps its own.
        shared: ['react', 'react-dom'],
      }),
    ],
    server: {
      port: 5173,
      proxy: {
        // SSE stream — disable buffering so events push through immediately.
        '/api/v1/bus/stream': {
          target: 'http://127.0.0.1:8080',
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
          target: 'http://127.0.0.1:8080',
          changeOrigin: true,
        },
      },
    },
    build: {
      outDir: 'dist',
      emptyOutDir: true,
      target: 'esnext',     // required by module federation
      cssCodeSplit: false,  // one stylesheet the host can link deterministically
      rollupOptions: {
        output: {
          // Stable CSS filename so the hub can build its <link> href from the
          // remoteEntry URL (…/assets/remoteEntry.js → …/assets/style.css).
          assetFileNames: (info) =>
            info.name && info.name.endsWith('.css')
              ? 'assets/style.css'
              : 'assets/[name]-[hash][extname]',
        },
      },
    },
  }
})
