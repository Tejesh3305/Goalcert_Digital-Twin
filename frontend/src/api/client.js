/**
 * client.js — the single API surface the whole frontend talks to.
 *
 * All calls hit the FastAPI server under /api/v1. In dev, Vite proxies that to
 * :8000 (see vite.config.js); in prod, FastAPI serves both the app and the API
 * from the same origin, so relative paths just work.
 *
 * Auth: the backend is dev-permissive (no key required unless NXR_API_KEYS is
 * set). If you set a key, drop it in localStorage as `nxr_api_key` and it is
 * sent as X-API-Key automatically.
 */

const BASE = '/api/v1'

function headers() {
  const h = { 'Content-Type': 'application/json' }
  const key = localStorage.getItem('nxr_api_key')
  if (key) h['X-API-Key'] = key
  return h
}

async function request(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, { headers: headers(), ...options })
  if (!res.ok) {
    let detail
    try { detail = (await res.json()).detail } catch { detail = res.statusText }
    const err = new Error(typeof detail === 'string' ? detail : JSON.stringify(detail))
    err.status = res.status
    err.detail = detail
    throw err
  }
  if (res.status === 204) return null
  return res.json()
}

const qs = (params) =>
  Object.entries(params)
    .filter(([, v]) => v !== undefined && v !== null)
    .map(([k, v]) => `${k}=${encodeURIComponent(v)}`)
    .join('&')

export const api = {
  // ── Health / bus ──
  health: () => request('/health'),
  busStats: () => request('/bus/stats'),

  // ── Twins ──
  listTwins: () => request('/twins'),
  twinTemplates: () => request('/twins/templates'),
  getTwin: (tenant) => request(`/twins/${tenant}`),
  createTwin: (body) => request('/twins', { method: 'POST', body: JSON.stringify(body) }),
  deleteTwin: (tenant) => request(`/twins/${tenant}`, { method: 'DELETE' }),

  // ── Entities (read) ──
  listEntities: (tenant, label = 'PhysicalAsset', limit = 100) =>
    request(`/entities?${qs({ tenant, label, limit })}`),
  getEntity: (id, tenant) => request(`/entities/${id}?${qs({ tenant })}`),
  topology: (tenant) => request(`/topology?${qs({ tenant })}`),
  stats: (tenant) => request(`/stats?${qs({ tenant })}`),
  findings: (tenant, limit = 50) => request(`/findings?${qs({ tenant, limit })}`),
  changelog: (tenant, limit = 50) => request(`/changelog?${qs({ tenant, limit })}`),

  // ── Entities (write) ──
  createEntity: (body) => request('/entities', { method: 'POST', body: JSON.stringify(body) }),
  updateEntity: (id, body) => request(`/entities/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  deleteEntity: (id, tenant, actor = 'ui') =>
    request(`/entities/${id}?${qs({ tenant, actor })}`, { method: 'DELETE' }),

  // ── Schema ──
  assetTypes: () => request('/schema/asset-types'),
  schemaVersion: () => request('/schema/version'),
  classProperties: (name) => request(`/schema/class/${encodeURIComponent(name)}/properties`),
  // How a class behaves: dynamics archetype + params + monitoring rules (binding layer).
  classBehavior: (name) => request(`/schema/class/${encodeURIComponent(name)}/behavior`),
  // The behaviour archetype catalog (generative dynamics archetypes + monitoring kinds).
  archetypes: () => request('/schema/archetypes'),

  // ── Machine twins (live physics runtime: turbine / EDM / tram-fleet) ──
  // Which domains the machine-twin runtime can twin.
  machineDomains: () => request('/twins/domains'),
  // Latest frame + health + recent findings.
  twinRuntimeState: (tenant) => request(`/twins/${encodeURIComponent(tenant)}/state`),
  // Subsystems + sensors + machine health.
  twinDiagnostics: (tenant) => request(`/twins/${encodeURIComponent(tenant)}/diagnostics`),
  // Forward trajectory + RUL.
  twinPredict: (tenant, horizon_min = 120, points = 120) =>
    request(`/twins/${encodeURIComponent(tenant)}/predict?${qs({ horizon_min, points })}`),
  // Non-destructive what-if (fault/control projection).
  twinProject: (tenant, body) =>
    request(`/twins/${encodeURIComponent(tenant)}/project`, { method: 'POST', body: JSON.stringify(body || {}) }),
  // Fleet network map (geometry + live vehicles + per-route status).
  twinNetwork: (tenant) => request(`/twins/${encodeURIComponent(tenant)}/network`),
  // Start/stop the live ticker for this twin.
  twinRunning: (tenant, running = true) =>
    request(`/twins/${encodeURIComponent(tenant)}/running?${qs({ running })}`, { method: 'POST' }),
  // Advance one step with a control input / injected fault (live perturbation).
  twinSimulate: (tenant, body) =>
    request(`/twins/${encodeURIComponent(tenant)}/simulate`, { method: 'POST', body: JSON.stringify(body || {}) }),

  // ── Feed ──
  feedStatus: () => request('/feed/status'),
  // mode: 'scripted' (canned profiles) | 'dynamics' (generative coupled engine).
  // speed: dynamics time multiplier (sim-seconds per real second).
  startFeed: (tenant, mode = 'scripted', speed = 60) =>
    request(`/feed/start?${qs({ tenant, mode, speed })}`, { method: 'POST' }),
  stopFeed: () => request('/feed/stop', { method: 'POST' }),

  // ── Live event stream URL (for EventSource) ──
  streamUrl: (tenant) => `${BASE}/bus/stream?${qs({ tenant })}`,

  // ── Agents (agentic core) ──
  agentInfo: () => request('/agents/info'),
  // Twin-building (Concierge chat that builds a real twin)
  twinAgentStart: (body) => request('/agents/twin/start', { method: 'POST', body: JSON.stringify(body || {}) }),
  twinAgentMessage: (session_id, message) =>
    request('/agents/twin/message', { method: 'POST', body: JSON.stringify({ session_id, message }) }),
  twinAgentState: (session_id) => request(`/agents/twin/${session_id}`),
  // Twin-building: expand an existing twin (add assets conversationally)
  twinAgentExpand: (tenant, message) =>
    request('/agents/twin/expand', { method: 'POST', body: JSON.stringify({ tenant, message }) }),

  // Twin-building: file upload for Vision Agent
  twinAgentUpload: (session_id, url, filename) =>
    request('/agents/twin/upload', { method: 'POST', body: JSON.stringify({ session_id, url, filename }) }),
  twinAgentUploadData: (session_id, data, filename) =>
    request('/agents/twin/upload', { method: 'POST', body: JSON.stringify({ session_id, data, filename }) }),
  // Twin-building: scene generation
  twinAgentScene: (session_id) =>
    request('/agents/twin/scene', { method: 'POST', body: JSON.stringify({ session_id }) }),
  // BIM: rebuild a twin's 3-D scene straight from its graph (no session needed)
  twinSceneByTenant: (tenant) => request(`/agents/twin/scene/${encodeURIComponent(tenant)}`),

  // Bundle Author (author a new vertical, human-gated publish)
  bundleStart: (body) => request('/agents/bundle/start', { method: 'POST', body: JSON.stringify(body || {}) }),
  bundleMessage: (session_id, message) =>
    request('/agents/bundle/message', { method: 'POST', body: JSON.stringify({ session_id, message }) }),
  bundleApprove: (session_id) =>
    request('/agents/bundle/approve', { method: 'POST', body: JSON.stringify({ session_id }) }),
  bundleState: (session_id) => request(`/agents/bundle/${session_id}`),

  // Operational (Diagnosis + Recommender — Team 2)
  opsDiagnose: (body) => request('/agents/ops/diagnose', { method: 'POST', body: JSON.stringify(body) }),
  opsState: (session_id) => request(`/agents/ops/${session_id}`),

  // Plugin Scaffolder (Team 4)
  pluginStart: () => request('/agents/plugin/start', { method: 'POST' }),
  pluginMessage: (session_id, message) =>
    request('/agents/plugin/message', { method: 'POST', body: JSON.stringify({ session_id, message }) }),
  pluginState: (session_id) => request(`/agents/plugin/${session_id}`),

  // Accelerator Pack Composer (Team 4)
  accelStart: (body) => request('/agents/accelerator/start', { method: 'POST', body: JSON.stringify(body || {}) }),
  accelMessage: (session_id, message) =>
    request('/agents/accelerator/message', { method: 'POST', body: JSON.stringify({ session_id, message }) }),
  accelState: (session_id) => request(`/agents/accelerator/${session_id}`),
}

export default api
