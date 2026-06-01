# NextXR Frontend

React (Vite) frontend for the NextXR Universal Modular Digital Twin platform.
Talks to the FastAPI backend under `/api/v1`. Create digital twins, add
ontology-validated assets, watch the live event-bus stream, and inspect the
tamper-evident change log — all against the real platform.

## Structure

```
src/
  api/client.js          single API surface (all backend calls)
  hooks/
    useApi.js            useApi / usePolling
    useEventStream.js    SSE subscription to the event bus
  context/
    TwinContext.jsx      active twin (tenant), persisted; twin list
    ToastContext.jsx     notifications
  components/
    layout/              Topbar, Sidebar, TwinSwitcher
    ui/                  Card, Modal, States, MockBanner (reusable primitives)
    FeedControls.jsx     start/stop the live feed
    AddAssetModal.jsx    create any entity via the validated write path
    NoTwin.jsx           empty-state gate
  panels/                one file per sidebar route (Dashboard, Twins, …)
  lib/format.js          shared formatting + label metadata
  styles/                theme.css (tokens) + app.css (layout/components)
  nav.js                 sidebar config + per-panel "backing" status
```

`PLACEHOLDERS.md` documents exactly what's live vs. mocked and how to make each
placeholder real.

## Develop

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173, proxies /api -> :8000
```

Run the backend in another terminal:

```bash
cd nextxr-ontology
python -m server.main      # http://localhost:8000
```

## Build for production

```bash
cd frontend
npm run build      # emits dist/, which FastAPI serves at /
```

Then just open `http://localhost:8000` — the backend serves the built app and
the API from the same origin (no proxy needed).

## Auth

The backend is dev-permissive (no key needed unless `NXR_API_KEYS` is set). If
you enable keys, store one in the browser:
`localStorage.setItem('nxr_api_key', 'nxr-demo-key')`.
