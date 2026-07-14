# NextXR Digital Twin — Hub Integration API

This platform is **Platform 1 — NextXR Digital Twin** in the Integration Hub.
It owns the live, physics-grounded model of each asset: telemetry, findings,
health, prediction, the knowledge graph and BIM semantics. Every endpoint below
is served under `/api/v1` by the FastAPI app (`nextxr-ontology/server/main.py`).

The hub calls these **server-to-server** (never a browser), mounting them behind
its gateway prefix `/api/twin/*`. If any endpoint returns non-200 or is
unreachable, the hub falls through to its simulator — so partial rollout is safe.

---

## Cross-cutting contract

| Requirement | Status | Where |
|---|---|---|
| Fast `GET /api/v1/health` (200 in < 3 s) — flips SIM→LIVE | ✅ | `server/query_api.py` |
| Validate injected key in `X-API-Key` | ✅ | `server/auth.py` (`AuthMiddleware`) |
| Honor identity headers `X-Goalcert-User/Role/Org` | ✅ | `auth.py` → `request.state.identity` |
| SSE returns `text/event-stream` | ✅ | `GET /api/v1/bus/stream` |
| No CORS needed (server-to-server) | ✅ | CORS is open but unused by the hub |
| Fall-through on non-200 | ✅ | global DB-down handler returns clean 503 |

**Auth:** dev-permissive — with no `NXR_API_KEYS` env set, requests without a key
are allowed. Set `NXR_API_KEYS` (JSON) in production; the gateway injects the key
as `X-API-Key`. Identity headers are captured for scoping/audit and never gate
access on their own.

---

## Endpoint map (hub path → upstream path)

| Hub call | Upstream `/api/v1` path | Purpose | Response shape |
|---|---|---|---|
| `GET /api/twin/health` | `GET /health` | Live probe (< 3 s) | `{status:"ok", ...}` |
| `POST /api/twin/twins` | `POST /twins` | Create/open a twin | `{tenant_id, tenant, id, twin:{…}}` |
| `GET /api/twin/twins/{t}` | `GET /twins/{t}` | Live twin state (poll 2 s) | `{latest:{sig:num}, findings:[{id,displayName,severity}], health:0..1, twin:{…}}` |
| `GET /api/twin/twins` | `GET /twins` | List twins | `{count, twins:[{tenant_id,name,domain,summary}]}` |
| `GET /api/twin/twins/templates` | `GET /twins/templates` | Template catalog | `{templates:[{key,label,description,…}]}` |
| `POST /api/twin/feed/start?tenant=&mode=` | `POST /feed/start` | Start telemetry feed | `200` |
| `POST /api/twin/feed/stop` | `POST /feed/stop` | Stop feed | `200` |
| `GET /api/twin/feed/status` | `GET /feed/status` | Feed state | `{running,tenant,mode}` |
| `GET /api/twin/topology?tenant=` | `GET /topology` | Asset graph | `{nodes,edges}` |
| `GET /api/twin/findings?tenant=&limit=` | `GET /findings` | Findings | `{findings:[{id,displayName,severity,…}]}` |
| `GET /api/twin/stats?tenant=` | `GET /stats` | Dashboard KPIs | `{total_entities, total_findings, finding_severity, …}` |
| `GET /api/twin/bus/stream?tenant=` | `GET /bus/stream` | SSE live frames | `text/event-stream` |
| `POST /api/twin/predict` | `POST /predict` | Forecast + RUL | `{trajectory,rul,severity,kind}` |
| `POST /api/twin/agents/ops/diagnose` | `POST /agents/ops/diagnose` | Root-cause | `{session_id, state}` |
| `POST /api/twin/agents/ops/analysis` | `POST /agents/ops/analysis` | 6-hour outlook | `{report, result}` (markdown) |
| `POST /api/twin/agents/ops/cascade` | `POST /agents/ops/cascade` | Fault-propagation | `{report, result, affected}` |
| `GET /api/twin/assets/{id}/ar-overlay?tenant=` | `GET /assets/{id}/ar-overlay` | Versioned AR steps | `{version, steps:[…], asset, findings}` |

Machine-twin runtime extras (already served): `GET /twins/{t}/state`,
`/diagnostics`, `/predict`, `/network`, `POST /twins/{t}/project`, `/simulate`,
`/running`, `GET /twins/domains`.

---

## New / changed endpoints (this integration)

### `POST /api/v1/predict`
```jsonc
// request
{ "tenant": "twin-abc", "horizon_min": 360, "points": 60 }
// machine twin →
{ "kind":"machine", "tenant":"twin-abc", "trajectory":[{"t":0,"egt":812,"health":0.97}, …],
  "rul":[{"component":"hot_section","minutes":320}], "severity":"warning", "generated_at":"…" }
// facility twin → findings-driven outlook
{ "kind":"facility", "tenant":"twin-abc", "trajectory":[], "severity":"nominal",
  "rul":[{"component":"…","minutes":2880,"severity":"critical"}], "findings_outlook":[…] }
```

### `POST /api/v1/agents/ops/analysis`
`{ "tenant": "...", "horizon_min": 360 }` → `{ "report": "## Operational outlook …",
"result": "…", "kind": "machine|facility" }`. Grounded in the twin's physics
forecast (machine) or live findings (facility). Degrades gracefully if the DB is off.

### `POST /api/v1/agents/ops/cascade`
`{ "tenant": "...", "entity_id"?: "...", "fault"?: "..." }` → `{ "report": "##
Fault-propagation …", "result": "…", "source":{id,displayName}, "affected":[{id,
displayName,type}] }`. Traces downstream dependents via `DEPENDS_ON`/`FED_BY`. If
`entity_id` is omitted it anchors on the first critical/warning finding's entity.

### `GET /api/v1/assets/{id}/ar-overlay?tenant=&version=`
→ `{ "asset_id","tenant","version","asset":{id,displayName,type}, "steps":[{n,title,
instruction,anchor,severity?}], "findings":[…], "generated_at" }`. A guided AR
maintenance procedure tailored to the asset type and its active findings.

### `GET /api/v1/twins/{tenant}` — augmented
Now returns the hub-shaped live snapshot at the top level **in addition to** the
existing `twin` metadata object:
```jsonc
{ "twin": { "tenant_id":"…", "name":"…", "domain":"…", "summary":{…} },
  "latest": { "egt": 812.4, "n1": 98.1 },        // {signal: number}
  "findings": [ { "id":"…", "displayName":"…", "severity":"critical" } ],
  "health": 0.86 }                                // 0..1
```

### `POST /api/v1/twins` — augmented
Adds top-level `tenant_id`, `tenant`, `id` (all the same value) so the hub can read
the new twin's id from any of them, alongside the existing `{status, twin}`.

---

## Notes for deployment (AWS)
- Serve behind the gateway on the container's port; the frontend build (`dist/`) is
  served by the same FastAPI app at `/`, so one container serves app + API.
- Set `NXR_API_KEYS` and the vision key (`ANTHROPIC_API_KEY` or `OPENAI_API_KEY`,
  used by `Build a Twin → plan reconstruction`).
- Neo4j must be reachable for graph-backed endpoints (twin create, topology,
  findings, cascade, ar-overlay). Prediction, analysis and the plan 3-D render
  degrade gracefully without it.
