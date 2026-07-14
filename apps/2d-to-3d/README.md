# NextXR — 2-D → 3-D (standalone)

A self-contained app that turns a 2-D floor-plan (image or PDF) into a navigable,
furnished 3-D model. **No Neo4j, no ontology, no digital-twin backend** — just
`vision parse → geometry → scene → render`. This is the place to perfect the
2-D→3-D feature on its own; once it's solid we integrate it back into the platform.

```
2d-to-3d/
  backend/      FastAPI: /api/parse (plan image -> nxr-scene/1), /api/sample/{facility}
    gateway.py        LLM gateway — Claude (if ANTHROPIC_API_KEY) else gpt-4o, keyless-safe
    bim_support.py    geometry brain: parse-normalise, furnish, scene-graph builder
    plan_parser.py    vision parse + scene assembly
    app.py            the server
    .env              API keys (copied from the platform)
  frontend/     Vite + React + react-three-fiber viewer (dollhouse, roof toggle, floors)
```

## Run

**Backend** (from `backend/`):
```
pip install -r requirements.txt
uvicorn app:app --reload --port 8000
```
Check `http://localhost:8000/api/health` — it reports which vision model is active.

**Frontend** (from `frontend/`):
```
npm install
npm run dev        # http://localhost:5174  (proxies /api -> :8000)
```

## Use
- Drop a plan (PNG/JPG/PDF) → pick the facility type → **Build 3-D**.
- Or click a **sample** facility — renders with no API key (uses the synthesizer).
- In the viewer: orbit/zoom, **Dollhouse** toggle (roof off), floor isolation, click an element.

## Vision model
- Best results: set `ANTHROPIC_API_KEY` in `.env` (uses Claude — strongest on dense
  architectural plans). Optionally `NXR_VISION_MODEL=claude-sonnet-4-6` (or
  `claude-opus-4-8`).
- Otherwise it uses OpenAI `gpt-4o` (needs `OPENAI_API_KEY`). High-detail images +
  large output + JSON salvage are already enabled.
- With no key at all, only the **sample** twins render.
