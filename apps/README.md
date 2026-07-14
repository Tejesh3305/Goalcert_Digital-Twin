# apps/ — the split

The 2-D→3-D feature has been separated from the digital-twin platform so each can
be worked on independently.

| | What it is | Where | Backend |
|---|---|---|---|
| **2-D → 3-D** | Standalone app: upload a plan → reconstructed, furnished 3-D model. The place to perfect parsing/visuals. | `apps/2d-to-3d/` (its own `.env` with the API key) | Slim FastAPI (`/api/parse`), no Neo4j/ontology |
| **Digital twin** | The full platform (ontology, physics/dynamics, behaviours, agents, telemetry, monitoring) + the real React app. | the repo root (`nextxr-ontology/` + `frontend/`) | Neo4j + FastAPI |

## Now merged into the digital-twin repo (integrated)
This `apps/` folder now lives **inside** the digital-twin repository — one repo for
the whole platform. The 2-D→3-D pipeline is **integrated** into the platform's
**Build a Twin**: attach a floor plan there and the platform vision-parses it,
reconstructs the building in 3-D (live `BimViewer`) and commits it as a live twin
(`POST /api/v1/agents/twin/build-from-plan`). The Dashboard 3-D hero renders the
twin's real reconstructed scene (demo scenes remain only as a no-geometry fallback).

`apps/2d-to-3d/` is kept as a **standalone sandbox** for iterating on parsing/visuals
in isolation; `apps/3d-platform/` and `apps/trellis-worker/` are the image→3-D
(mesh generation / RunPod) workers, not yet wired into the twin flow.

`node_modules/`, `dist/` and runtime `data/` are gitignored — run `npm install` in a
frontend folder to restore its deps.

## Run the standalone 2-D→3-D app
```
cd apps/2d-to-3d/backend  && pip install -r requirements.txt && uvicorn app:app --port 8000
cd apps/2d-to-3d/frontend && npm install && npm run dev      # http://localhost:5174
```
See `apps/2d-to-3d/README.md`. For best parsing of dense plans, set `ANTHROPIC_API_KEY`
in `apps/2d-to-3d/backend/.env` (Claude vision); else it uses OpenAI gpt-4o.
