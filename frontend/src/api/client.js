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

  // ── Feed ──
  feedStatus: () => request('/feed/status'),
  startFeed: (tenant) => request(`/feed/start?${qs({ tenant })}`, { method: 'POST' }),
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
