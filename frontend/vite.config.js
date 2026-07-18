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
    // Keep exactly one physical copy of React & the two renderers in this bundle,
    // so @react-three/fiber's react-reconciler and react-dom bind to the very same
    // React the component hooks use. Combined with the self-mounting `./mount`
    // entry below (which renders this remote under its OWN react-dom root, NOT the
    // host's), the entire twin subtree — including R3F's renderer — lives on ONE
    // React instance. That is the fix for the 3-D canvas going blank under
    // federation (React #321): with the old component-export model the host
    // rendered our tree, so R3F hooks resolved React via `importShared` (the host's
    // copy) while react-reconciler stayed bound to ours → two dispatchers → #321.
    resolve: {
      dedupe: ['react', 'react-dom', 'react-reconciler', 'scheduler'],
    },
    plugins: [
      react(),
      federation({
        name: 'nextxrTwin',
        filename: 'remoteEntry.js',
        exposes: {
          // PRIMARY host entry: mount(el, props) spins up our own react-dom root
          // inside a host-provided <div>, isolating our React from the host's by a
          // DOM boundary. See src/mount.jsx.
          './mount': './src/mount.jsx',
          // Legacy component mount (still exported for standalone/embedding use);
          // the hub no longer renders this directly — see the #321 note above.
          './TwinRemoteApp': './src/TwinRemoteApp.jsx',
          // kept available for finer-grained composition / a future state bridge
          './TwinRoutes': './src/TwinRoutes.jsx',
          './TwinProvider': './src/context/TwinContext.jsx',
          './ToastProvider': './src/context/ToastContext.jsx',
          './apiClient': './src/api/client.js',
        },
        // Deliberately share NOTHING. The remote owns its whole React runtime and
        // renders itself via `./mount`; nothing React-shaped crosses the federation
        // boundary (the host just hands us a DOM node). Sharing react here is what
        // forced R3F's reconciler onto a different React copy than the hooks. This
        // is a build-time change only — same `npm run build` locally and on AWS,
        // no infra / env / hub-config change.
        shared: [],
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
