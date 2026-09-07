/**
 * client.js — the single API surface the whole frontend talks to.
 *
 * All calls hit the FastAPI server under /api/v1. In dev, Vite proxies that to
 * :8080 (see vite.config.js); in prod, FastAPI serves both the app and the API
 * from the same origin, so relative paths just work.
 *
 * Auth: a signed-in user's access token is attached automatically as
 * `Authorization: Bearer` (held in memory — see api/session.js). A 401 triggers
 * ONE transparent refresh-and-retry, so a token expiring under an open dashboard
 * is invisible rather than a screenful of failed panels.
 *
 * A machine credential still works: put it in localStorage as `nxr_api_key` and
 * it is sent as X-API-Key whenever there is no user session.
 */

import { ensureToken, getToken, isExpired, refresh } from './session'

// Resolve the API base at RUNTIME so ONE build serves both the standalone app
// (defaults to /api/v1) and the hub, where the host sets
// window.__NXR_API_BASE__ = '/api/twin' before mounting the federated remote.
function apiBase() {
  if (typeof window !== 'undefined' && window.__NXR_API_BASE__) return window.__NXR_API_BASE__
  return (import.meta.env && import.meta.env.VITE_API_BASE) || '/api/v1'
}
export const getApiBase = apiBase

/**
 * Re-base an asset URL the BACKEND handed us onto the API base this build is actually
 * talking to.
 *
 * The server returns absolute paths rooted at its own mount, e.g.
 *   scene_result.model_url = "/api/v1/threed/api/jobs/<id>/file/artifacts/export/model.glb"
 *
 * Standalone that is already right — the app and the API share an origin. Federated into
 * the hub it is NOT: the browser resolves "/api/v1/..." against the HUB's origin (:8090),
 * where nothing serves it, so the fetch 404s. For a <GlbViewer>, that means the canvas
 * mounts and the model silently never arrives — a black 3-D panel with no error, which is
 * exactly how the reconstructed Gas Turbine looked inside the hub while working standalone.
 *
 * Swapping the server's "/api/v1" prefix for apiBase() sends it through the hub gateway
 * ("/api/twin/threed/...") instead, which proxies to the twin and returns the file.
 * Absolute (http://…) and data: URLs are passed through untouched.
 */
export function assetUrl(url) {
  if (!url) return url
  if (/^(https?:)?\/\//.test(url) || url.startsWith('data:') || url.startsWith('blob:')) return url
  const base = apiBase()
  // Strip whichever server-side mount prefix the URL carries, then re-root it.
  const rooted = url.replace(/^\/api\/v1(?=\/|$)/, '')
  if (rooted !== url) return `${base}${rooted}`
  // Not an /api/v1 path (e.g. a bundled /assets/... file) — leave it alone.
  return url
}

function headers() {
  const h = { 'Content-Type': 'application/json' }

  // The signed-in user's access token. Held in memory (see api/session.js) — it
  // is deliberately NOT in localStorage, so an XSS cannot walk off with a
  // working credential.
  const token = getToken()
  if (token) h['Authorization'] = `Bearer ${token}`

  // A machine credential, for a browser driving the API with a service key
  // rather than a login. Still read from localStorage because that is what a
  // key IS — a long-lived secret the operator pasted in — and it is only ever
  // used when there is no user session.
  const key = localStorage.getItem('nxr_api_key')
  if (key && !token) h['X-API-Key'] = key

  // When federated into the hub, the host injects its auth (Bearer JWT and/or
  // CSRF token) so calls to the hub gateway (/api/twin/*) authenticate. Same-
  // origin cookies also ride automatically via `credentials` below.
  if (typeof window !== 'undefined' && typeof window.__NXR_AUTH__ === 'function') {
    Object.assign(h, window.__NXR_AUTH__() || {})
  }
  return h
}

/**
 * One API call, with a single transparent retry after a token refresh.
 *
 * WHY THE RETRY EXISTS: access tokens last ~15 minutes. Without this, a user who
 * left a dashboard open comes back to a wall of failed panels and has to reload.
 * With it, the first call after expiry silently renews and succeeds.
 *
 * It retries ONCE, and only on 401. A second 401 after a fresh token means the
 * session is genuinely gone (revoked, password changed, signed out elsewhere),
 * and retrying again would be an infinite loop against a server that has already
 * given its answer.
 */
async function request(path, options = {}, { retryOn401 = true } = {}) {
  // Refresh proactively when the token is known to be stale, so the common case
  // costs one request rather than a 401 plus a retry.
  if (!path.startsWith('/auth/') && getToken() && isExpired()) {
    await ensureToken()
  }

  const res = await fetch(`${apiBase()}${path}`, {
    // `include`, not `same-origin`: the refresh cookie has to ride along when
    // the app is served from a different origin than the API (CloudFront in
    // front of an ALB). Same-origin deployments are unaffected.
    credentials: 'include',
    headers: headers(),
    ...options,
  })

  if (res.status === 401 && retryOn401 && !path.startsWith('/auth/')) {
    const renewed = await refresh()
    if (renewed) return request(path, options, { retryOn401: false })
  }

  if (!res.ok) {
    let detail
    try { detail = (await res.json()).detail } catch { detail = res.statusText }
    const err = new Error(typeof detail === 'string' ? detail : JSON.stringify(detail))
    err.status = res.status
    err.detail = detail
    // Surfaced so a caller can distinguish "slow down" from "broken" — the
    // rate limiter returns this and the UI should say when to try again.
    if (res.status === 429) err.retryAfter = Number(res.headers.get('Retry-After') || 0)
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
  entityTelemetry: (id, tenant) => request(`/entities/${id}/telemetry?${qs({ tenant })}`),
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

  // ── Machine twins (live physics runtime: turbine / EDM / rail / hospital / EV / defence) ──
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
  streamUrl: (tenant) => `${apiBase()}/bus/stream?${qs({ tenant })}`,

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

  // Build a Twin from a 2-D plan: parse → 3-D scene → live digital twin (one shot).
  // body: { data (image data URL), filename, name?, facility?, floors? }
  buildFromPlan: (body) =>
    request('/agents/twin/build-from-plan', { method: 'POST', body: JSON.stringify(body) }),
  // Async build (start + poll): a TRELLIS reconstruction on a cold GPU runs for
  // minutes — longer than proxies keep a synchronous response open in the cloud.
  buildFromPlanStart: (body) =>
    request('/agents/twin/build-from-plan/start', { method: 'POST', body: JSON.stringify(body) }),
  buildFromPlanStatus: (buildId) =>
    request(`/agents/twin/build-from-plan/status/${encodeURIComponent(buildId)}`),
  // No-LLM sample building for a facility (renders with no API key).
  sampleScene: (facility, floors = 1) =>
    request(`/agents/twin/sample-scene/${encodeURIComponent(facility)}?${qs({ floors })}`),

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
  // Ops reasoning: 6-hour outlook + fault-propagation (markdown report/result).
  opsAnalysis: (tenant, horizon_min = 360) =>
    request('/agents/ops/analysis', { method: 'POST', body: JSON.stringify({ tenant, horizon_min }) }),
  opsCascade: (tenant, body = {}) =>
    request('/agents/ops/cascade', { method: 'POST', body: JSON.stringify({ tenant, ...body }) }),

  // ── Hub-facing surfaces (top-level) ──
  // Forecast + RUL for a tenant (physics for machine twins, findings outlook for facilities).
  predict: (tenant, horizon_min = 360, points = 60) =>
    request('/predict', { method: 'POST', body: JSON.stringify({ tenant, horizon_min, points }) }),
  // Versioned AR maintenance steps for an asset, grounded in its live findings.
  arOverlay: (assetId, tenant, version) =>
    request(`/assets/${encodeURIComponent(assetId)}/ar-overlay?${qs({ tenant, version })}`),

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

  // ── Copilot: the embedded agent layer (/api/v1/copilot) ──────────────
  //
  // These agents run IN the twin process against the machine-twin runtime, so
  // they reason over the same live physics the 3-D scene renders.
  //
  // Every call takes a TwinContext: pass `{ tenant }` to run against a live
  // twin, or `{ machine, domain, latest, findings }` to run the identical agent
  // on a telemetry snapshot. The response echoes `source` ('live'|'snapshot')
  // and an `ai` block — ALWAYS surface `ai.backend`, because a stubbed answer
  // looks exactly like a real one otherwise. See <AiBadge>.
  copilot: {
    health: () => request('/copilot/health'),

    // Live monitoring
    narrateLive: (tenant, machine) =>
      request(`/copilot/narrate/${encodeURIComponent(tenant)}?${qs({ machine })}`),
    narrate: (ctx) => request('/copilot/narrate', { method: 'POST', body: JSON.stringify(ctx) }),
    asset: (ctx) => request('/copilot/asset', { method: 'POST', body: JSON.stringify(ctx) }),
    predictAlert: (ctx) => request('/copilot/predict-alert', { method: 'POST', body: JSON.stringify(ctx) }),

    // Diagnosis & prediction
    diagnosis: (ctx) => request('/copilot/diagnosis', { method: 'POST', body: JSON.stringify(ctx) }),
    analysis: (ctx) => request('/copilot/analysis', { method: 'POST', body: JSON.stringify(ctx) }),
    cascade: (ctx) => request('/copilot/cascade', { method: 'POST', body: JSON.stringify(ctx) }),

    // Maintenance & compliance output
    workOrder: (ctx) => request('/copilot/work-order', { method: 'POST', body: JSON.stringify(ctx) }),
    procurement: (ctx) => request('/copilot/procurement', { method: 'POST', body: JSON.stringify(ctx) }),
    incidentReport: (ctx) => request('/copilot/incident-report', { method: 'POST', body: JSON.stringify(ctx) }),
    procedure: (body) => request('/copilot/procedure', { method: 'POST', body: JSON.stringify(body) }),

    // Conversational
    troubleshoot: (body) => request('/copilot/troubleshoot', { method: 'POST', body: JSON.stringify(body) }),
    dashboardChat: (body) => request('/copilot/dashboard-chat', { method: 'POST', body: JSON.stringify(body) }),

    // Twin provisioning
    buildTwinMessage: (history, message) =>
      request('/copilot/build-twin/message', { method: 'POST', body: JSON.stringify({ history, message }) }),
    buildTwinSpec: (body) =>
      request('/copilot/build-twin/spec', { method: 'POST', body: JSON.stringify(body) }),
  },

  // ── Work: dispatch (/api/v1/work) ────────────────────────────────────
  //
  // The fault -> supervisor -> operator loop. Which of these a given account may
  // actually call is decided by its PERSONA (supervisor | frontline), not by its
  // data role — see nextxr-ontology/work/authority.py. Read `work.me()` first
  // and render from the `capabilities` it returns rather than hardcoding a menu
  // per persona: that way the buttons drawn and the actions the API honours come
  // from one table and cannot drift.
  work: {
    // Persona, capabilities and XP standing. The UI's source of truth for
    // "what may this person do".
    me: () => request('/work/me'),

    // Supervisor
    queue: (tenant) => request(`/work/queue?${qs({ tenant })}`),
    board: (tenant) => request(`/work/board?${qs({ tenant })}`),
    // `scope` is 'team' (who you may assign to) or 'all' (every operator in the
    // org, each row flagged `assignable`). The widening is a VIEW only — the
    // assign endpoint still enforces team scope, so an unassignable row is
    // somebody you can see the load of and cannot dispatch to.
    roster: (scope = 'team') => request(`/work/roster?${qs({ scope })}`),
    teamStats: (days = 14) => request(`/work/team-stats?${qs({ days })}`),
    createTask: (body) => request('/work/tasks', { method: 'POST', body: JSON.stringify(body) }),
    assign: (taskId, assignee_id, note = '') =>
      request(`/work/tasks/${encodeURIComponent(taskId)}/assign`,
        { method: 'POST', body: JSON.stringify({ assignee_id, note }) }),
    unassign: (taskId, note = '') =>
      request(`/work/tasks/${encodeURIComponent(taskId)}/unassign`,
        { method: 'POST', body: JSON.stringify({ note }) }),
    close: (taskId, note = '') =>
      request(`/work/tasks/${encodeURIComponent(taskId)}/close`,
        { method: 'POST', body: JSON.stringify({ note }) }),

    // Operator. There is deliberately no user id on `inbox` — an operator reads
    // their own work and nobody else's, and the server takes it from the session.
    inbox: () => request('/work/inbox'),
    // Everything the dashboard's numbers and charts are drawn from, in ONE
    // payload. Five endpoints for five cards would let them disagree the moment
    // one of them was slow, and the dashboard's whole job is a coherent answer
    // to "where do I stand".
    myStats: (days = 14) => request(`/work/stats/me?${qs({ days })}`),
    history: (limit = 50) => request(`/work/history?${qs({ limit })}`),
    start: (taskId) =>
      request(`/work/tasks/${encodeURIComponent(taskId)}/start`, { method: 'POST' }),
    block: (taskId, reason) =>
      request(`/work/tasks/${encodeURIComponent(taskId)}/block`,
        { method: 'POST', body: JSON.stringify({ reason }) }),
    // `body` is { run_id, passed?, resolution? } — deliberately NOT a score.
    // The server reads that from the run (work.service._verified_outcome), so a
    // page cannot assert its own result.
    complete: (taskId, body) =>
      request(`/work/tasks/${encodeURIComponent(taskId)}/complete`,
        { method: 'POST', body: JSON.stringify(body) }),

    // Shared
    task: (taskId) => request(`/work/tasks/${encodeURIComponent(taskId)}`),
    // The work order + on-asset steps the twin's own agents generate for this
    // fault, from LIVE diagnostics rather than a copy frozen at dispatch time.
    // `generate` costs money: it runs an LLM agent. Default false, so opening
    // a job is free and only an explicit click bills the account.
    workorder: (taskId, generate = false) =>
      request(`/work/tasks/${encodeURIComponent(taskId)}/workorder?${qs({ generate })}`),
    comment: (taskId, text) =>
      request(`/work/tasks/${encodeURIComponent(taskId)}/comment`,
        { method: 'POST', body: JSON.stringify({ text }) }),
    xp: () => request('/work/xp'),
    teams: () => request('/work/teams'),
  },

  // ── Scenario: the "fix the issue" run (/api/v1/scenario) ─────────────
  //
  // Grading is SERVER-SIDE. These endpoints never return the answer key for a
  // step the operator has not answered yet, so nothing here can be used to
  // pre-compute a score — see nextxr-ontology/scenario/guided.py.
  scenario: {
    procedures: (domain) => request(`/scenario/procedures?${qs({ domain })}`),
    procedure: (id) => request(`/scenario/procedures/${encodeURIComponent(id)}`),

    // Starts a NEW run, or resumes this operator's unfinished one on the same
    // task. Resumption is the default: a locked phone mid-procedure must not
    // reset the score.
    startRun: (task_id, scenario_id = '') =>
      request('/scenario/runs', { method: 'POST', body: JSON.stringify({ task_id, scenario_id }) }),

    // Training, with no assigned fault behind it. A separate endpoint rather
    // than a flag, because /runs requires a task assigned to you and practice
    // has none — see the note on the server handler.
    startPractice: (scenario_id) =>
      request('/scenario/practice', { method: 'POST', body: JSON.stringify({ scenario_id }) }),
    run: (runId) => request(`/scenario/runs/${encodeURIComponent(runId)}`),
    answer: (runId, step_index, choice) =>
      request(`/scenario/runs/${encodeURIComponent(runId)}/answer`,
        { method: 'POST', body: JSON.stringify({ step_index, choice }) }),
    hint: (runId) =>
      request(`/scenario/runs/${encodeURIComponent(runId)}/hint`, { method: 'POST' }),
    result: (runId) => request(`/scenario/runs/${encodeURIComponent(runId)}/result`),
    runs: (limit = 50) => request(`/scenario/runs?${qs({ limit })}`),
  },
}

export default api
