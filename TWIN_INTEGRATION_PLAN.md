# Digital Twin ⇄ Hub integration (Phase 1 of the pivot) — detailed, AWS-targeted

> Focused execution plan for integrating **only the Digital Twin (NextXR)** into the hub as a **native Module-Federation remote**, deployed on **AWS**. This is Phase 1/2 of the broader pivot in `PIVOT_PLAN.md` (Scenario + Agentic follow later). Do this one first because it is the lowest-risk slice.

## Context — why twin first, and why it's low-risk

The hub currently ships a **hand-ported copy** of the NextXR twin UI (`hub/web/src/modules/twin/**`, ~5,075 lines incl. the 3-D `scene/` + 20 per-domain `scene/views/`). Every NextXR change must be re-ported; the ports drift → the recurring runtime bugs (jitter, "twin is not defined", context-split breakage). We replace that ported copy with NextXR's **real** pages, rendered **natively** in the hub via Module Federation, data flowing through the hub gateway. Update NextXR → hub reflects it with no hub change.

Twin is the ideal first integration because the stars align:
- **Same stack** — hub and NextXR are both **React 18.3 + Vite 5 + three 0.169 / @react-three/fiber 8**. No version-alignment spike (that blocker is AutoMind-only). Federation `shared` singletons line up.
- **Clean shell/page split** — `frontend/src/main.jsx` mounts `BrowserRouter > ToastProvider > TwinProvider > App`; `App.jsx` owns the chrome (Topbar/Sidebar) + `<Routes>`; the panels in `frontend/src/panels/` render only their own body. We expose the routed content, the hub supplies the chrome.
- **One API choke point** — `frontend/src/api/client.js` has a single `const BASE = '/api/v1'`; repointing to the hub gateway `/api/twin` is a one-line change.
- **Procedural 3-D** — `frontend/src/three/*` scenes are built in code (no asset/network loads); per-domain dashboards (`Defence*/Hospital*/EV*/Railway*`) are pure props-driven SVG. Almost nothing to fix there.
- **No auth gate / no 401 redirects** in the NextXR frontend — nothing fights the host.

The one genuine coupling to design around: the hub's **`hub/web/src/hub/twinState.jsx`** (the app-wide `useTwin()`/`useTwinFrame()` active-twin context) is consumed by ~12 non-twin surfaces (AILayer, Overview, FrontlineFlow, HiveMind, SimulationWorkspace, ContentStudio, SupervisorDashboard, AssetPicker, Scenario/Trainer, the Shell). **It cannot be deleted with the twin UI** — we keep it as the source of truth for "which twin is active" and bridge it to NextXR's own `TwinContext`.

---

## Target architecture (twin slice)

```
  Browser ── CloudFront (single app origin) ─────────────────────────────┐
     │  /                → S3  hub shell (React host)                     │
     │  /remotes/twin/*  → S3  NextXR remoteEntry.js + chunks (the remote)│
     │  /api/*           → ALB → hub-gateway (ECS)                        │
     └───────────────────────────────────────────────────────────────────┘
                                   │
   HUB HOST (hub/web) mounts the remote NATIVELY:
     App.jsx twin routes ──► <TwinRemoteHost> (new)
                               • sets window.__NXR_API_BASE__='/api/twin'
                               • <ToastProvider><TwinProvider>  (remote-exposed)
                               • <MemoryRouter> driving remote <TwinRoutes/>
                               • bridges hub twinState.active.tenant ⇄ TwinContext.activeTenant
                                   │ data calls (fetch + EventSource) → /api/twin/*
                                   ▼
   HUB GATEWAY (hub/backend/gateway.py, unchanged pattern)
     /api/twin/{path:path} → injects X-API-Key, X-Goalcert-User/Role/Org, strips JWT
                                   │
                                   ▼
   NextXR API (ECS Fargate) ── Neo4j Aura ── twin registry store (EFS/RDS)
```

Standalone mode ("open Digital Twin standalone") = the **same remote build** served as a full app at `twin.<domain>` (its own `index.html`, `BASE` defaults to `/api/v1` against the NextXR API), reached via an SSO handoff so there's no second login.

---

## Part 1 — NextXR side (make it a federation remote)

All paths under `c:\Users\Admin\Tejesh\Next XR\nextxr-ontology-v3\frontend`.

1. **Runtime-configurable API base** — `src/api/client.js`:
   ```js
   const BASE = (typeof window !== 'undefined' && window.__NXR_API_BASE__)
     || import.meta.env.VITE_API_BASE || '/api/v1'
   ```
   `streamUrl()` derives from `BASE`, so SSE moves automatically. One build serves both hub (host sets `window.__NXR_API_BASE__='/api/twin'` before mount) and standalone (defaults `/api/v1`).

2. **Extract routed content** — pull the `<Routes>…</Routes>` out of `src/App.jsx` into a new `src/TwinRoutes.jsx` (Dashboard `/`, BuildTwin `/build`, Twins `/twins`, Predict `/predict`, Agents `/agents`, Changelog `/changelog`). `App.jsx` becomes `Topbar + Sidebar + <TwinRoutes/>`. This lets NextXR keep owning its internal `navigate('/')` while the hub embeds `<TwinRoutes/>` — the cleanest way to avoid rewriting every panel's `useNavigate` calls.

3. **Fix absolute asset paths** (break when served off-origin/sub-path):
   - `src/components/TurbineModel.jsx`: `'/models/turbine.glb'` → `import turbineUrl from '../assets/turbine.glb?url'` (move the file under `src/assets/`), and drop the `useGLTF.preload('/models/...')` literal.
   - `src/components/ui/Logo.jsx`: `/goalcert-*` images → bundled imports or `import.meta.env.BASE_URL`-prefixed. (Logo already `onError`-degrades, so low risk.)
   - Confirm backend-returned `model_url` (from `buildFromPlan`/`scene`) is reachable from the hub origin — route it through the gateway (`twinAssetUrl` already rewrites `/api/v1/*`→`/api/twin/*` on the hub side).

4. **Add federation** — `vite.config.js` add `@originjs/vite-plugin-federation`:
   ```js
   federation({
     name: 'nextxrTwin',
     filename: 'remoteEntry.js',
     exposes: {
       './TwinRoutes':    './src/TwinRoutes.jsx',
       './TwinProvider':  './src/context/TwinContext.jsx',
       './ToastProvider': './src/context/ToastContext.jsx',
       './apiClient':     './src/api/client.js',
     },
     shared: ['react','react-dom','react-router-dom',
              'three','@react-three/fiber','@react-three/drei','@react-three/postprocessing'],
   })
   ```
   Pin `three` to the exact same version the hub uses (0.169) — two three.js copies break R3F (`instanceof THREE.*`). Set `build.target:'esnext'` (federation requirement).

5. **Deploy artifacts** — NextXR now produces **two** outputs: (a) the **backend API image** (existing `Dockerfile`, stays backend-only — that's fine now), (b) the **frontend remote build** (`dist/` with `remoteEntry.js`) published to **S3**. The current Dockerfile's missing `COPY frontend/dist` (which today makes the container serve a placeholder) becomes a non-issue: the UI is served from S3, not the Python container. *(Alternative if you want a self-contained standalone container instead of S3: add a Node build stage + `COPY frontend/dist` so `server/main.py` L737-747 mounts it.)*

---

## Part 2 — Hub host side (mount the remote, retire the port)

All paths under `c:\Users\Admin\Tejesh\Goalcert_Hub\hub\web`.

1. **Federation host config** — `vite.config.js` add the federation plugin with
   `remotes: { nextxrTwin: import.meta.env.VITE_TWIN_REMOTE || '/remotes/twin/remoteEntry.js' }`
   and the same `shared` singletons list. Keep the existing `/api` dev proxy.

2. **New `src/hub/TwinRemoteHost.jsx`** — the wrapper + state bridge:
   - Before first mount: `window.__NXR_API_BASE__ = '/api/twin'`.
   - Lazy-load the remote: `const TwinRoutes = lazy(()=>import('nextxrTwin/TwinRoutes'))`, plus the remote `TwinProvider`/`ToastProvider`.
   - Render `<ToastProvider><TwinProvider><MemoryRouter><TwinRoutes/></MemoryRouter></ToastProvider>`.
   - **Bridge to hub `twinState`**: an effect pushes hub `active.tenant` → the remote `TwinContext.setActiveTenant`; and translate the hub sidebar's twin nav (`go('twins'|'dashboard'|'build'|'predict')`) into `MemoryRouter` navigations (`/twins`, `/`, `/build`, `/predict`) via a small history ref. The hub stays the single source of truth for the active twin.

3. **Swap the route ladder** — `src/App.jsx` (twin branches ~L319-346): replace the imports of `TwinsLibrary/LiveDashboard/MachineDashboard/BuildTwin/Prediction/Trainer` and their JSX with a single `<TwinRemoteHost route={route} />` for `route ∈ {twins,dashboard,build,predict}`. The machine-vs-facility branch (`isMachineDomain(serviceDomain(active.domain))`) is **deleted** — NextXR's `Dashboard.jsx` already routes machine vs facility internally. `train`/`Trainer` is a guided-repair drill bound to the active twin; fold it into the twin remote or keep as a thin hub-native surface (confirm — see Open decisions).

4. **Keep + trim `src/hub/twinState.jsx`** — it stays (app-wide). Trim its direct `api.js` usage to the **twin-lifecycle calls only**: `health()`, `list()`, `create()`, `state()` (the 2s live poll), `feedStart()`. Everything else the pages need now comes from the **remote's** client through the gateway.

5. **Retire the port**:
   - Delete `src/modules/twin/**` (all ~5k lines incl. `scene/` + `scene/views/`) and `src/modules/scenario/Trainer.jsx` (only if folded into the remote).
   - Drop `three`, `@react-three/fiber`, `@react-three/drei`, `@react-three/postprocessing`, `pdfjs-dist` from `package.json` — grep confirms they're used **only** under `modules/twin/scene/`.
   - In `src/api.js`, remove the ~23 page-level `API.twin.*` methods (topology, findings, stats, predict, arOverlay, analysis, scene, entity, buildFromPlan*, machineDomains, runtimeState, diagnostics, machinePredict, network, runningToggle, simulate, feedStop, feedStatus, streamUrl, templates, remove). **Keep** `health/list/create/state/feedStart` for `twinState`. This is the **"one API per page"** payoff: the hub stops maintaining a 28-method twin client; the federated page owns its own data via the single `/api/twin/*` gateway.
   - `Scenario.jsx` (270 lines) also uses `useTwin` but belongs to the Scenario engine surface — **do not** delete in this phase.

---

## Part 3 — Authentication & session (HttpOnly cookie + CSRF) — chosen posture

The session JWT moves **out of `localStorage` into an `HttpOnly; Secure; SameSite=Strict` cookie**, so JS — including federated remote code running in the hub's origin — can **never read the token** (kills the XSS token-theft vector that federation elevates), and `EventSource` SSE authenticates automatically. Mutations are protected with a CSRF token.

**Backend (`hub/backend/`):**
- `auth_routes.py` `POST /login` — in addition to (or instead of) the JSON `token`, `Set-Cookie: gc_session=<JWT>; HttpOnly; Secure; SameSite=Strict; Path=/api; Max-Age=<JWT_EXPIRE>`. Add `POST /logout` that clears it.
- `deps.py` `resolve_user_from_token()` — accept the JWT from the **cookie** first, falling back to the `Authorization: Bearer` header (keeps server-to-server + migration working). `security.py` is unchanged (same HS256 token).
- **CSRF:** issue a non-HttpOnly `gc_csrf` cookie at login; require a matching `X-CSRF-Token` header on every non-GET under `/api/*` (double-submit pattern). GET/SSE are safe (read-only) and `SameSite=Strict` already blocks cross-site sends. Add this check in the gateway/deps layer.
- The gateway (`gateway.py`) is otherwise **unchanged** — it still strips inbound auth (extend `_HOP_BY_HOP`/cookie handling so `gc_session`/`gc_csrf` are **not** forwarded upstream) and injects `X-API-Key` + `X-Goalcert-*` server-side. Twin config (L33-39) is already correct.

**Frontend (`hub/web/src/api.js`):** `fetch(..., { credentials: 'same-origin' })`, drop the `Authorization` header + the `localStorage['gc_hub_token']` read/write (`authHeaders()`), and attach `X-CSRF-Token` (read from the `gc_csrf` cookie) on non-GET. `auth.jsx` resumes session via `GET /api/auth/me` (cookie-authenticated) instead of a stored token.

**SSE:** NextXR's facility Dashboard opens `EventSource` (`src/hooks/useEventStream.js` → `${BASE}/bus/stream`) — with the cookie it authenticates natively, no header needed. Ensure CloudFront/ALB **don't buffer** `/api/twin/bus/stream` (the gateway already streams it unbuffered, L137-146). Machine-domain twins use polling, so they work regardless.

---

## Part 4 — AWS deployment (twin slice; replaces Render)

Single origin so federation + same-origin data + cookie SSO all work:

- **CloudFront distribution** (one app domain) with behaviors:
  - `/` + hub assets → **S3** (hub shell build)
  - `/remotes/twin/*` → **S3** (NextXR remote `remoteEntry.js` + chunks) — *this is where "publish NextXR → hub auto-picks-up" happens*
  - `/api/*` → **ALB → hub-gateway (ECS Fargate)**; disable buffering on `/api/twin/bus/stream`
- **ECS Fargate** services behind the ALB: `hub-gateway` and `nextxr-api` (internal — the gateway reaches it via service discovery / internal ALB, so `TWIN_BASE_URL` = the internal DNS). Images in **ECR**.
- **Data:** **Neo4j Aura** stays (`TWIN_BASE_URL`'s backend connects to it). Move NextXR's ephemeral twin-registry/scene-cache **SQLite → EFS mount (or RDS)** so twins survive task restarts (today they vanish on redeploy). Hub Postgres → **RDS** (schema `hub`).
- **Secrets:** `TWIN_API_KEY` (hub) + matching `NXR_API_KEYS` (NextXR) + vision key + `JWT_SECRET` → **AWS Secrets Manager / SSM Parameter Store** (replaces Render `sync:false`).
- **Standalone twin app:** `twin.<domain>` → CloudFront + S3 serving the same remote build as a full app (`BASE` defaults to `/api/v1`, pointed at the NextXR API via a twin-scoped path or its own gateway). SSO handoff token from the hub → no second login.
- **CI/CD (GitHub Actions):**
  - NextXR frontend push → `npm run build` → publish to an **immutable, hashed path** `s3://…/remotes/twin/<git-sha>/` (never overwrite), then flip the hub's remote pointer to that sha and CloudFront-invalidate. Hub loads the new `remoteEntry.js` at runtime → **zero hub deploy** (the federation payoff), while pinning keeps the publish deliberate and rollback instant.
  - NextXR backend push → build image → ECR → ECS deploy (private VPC, verified TLS).
  - Hub push → build shell → S3 sync + invalidate; hub-gateway push → ECR → ECS. Only the CI role may write the S3 remote buckets (OAC).

---

## Part 5 — Security hardening & threat model

The design keeps three strong properties and adds hardening in the two places federation and cloud introduce risk.

**Already strong (preserve):** platform keys never reach the browser (gateway injects them server-side); the user's hub JWT is stripped before reaching NextXR (platform gets only `X-Goalcert-*`); authorization is enforced **server-side per request** at the gateway (`_entitled()` — org entitlement + persona policy, incl. **verifying the requested `tenant` belongs to the user's org** before proxying twin state/SSE); single origin → no CORS surface.

**Federation supply-chain (the biggest new risk).** `remoteEntry.js` executes *as* the hub origin — a compromised remote is a full in-origin compromise, and **no auth scheme protects against code you chose to run**. The HttpOnly cookie limits token *theft*, not a malicious remote acting in-session. Controls:
- **Lock the remote origin:** S3 with **Origin Access Control**, bucket versioning, no public write; only the CI role can publish.
- **Pin remote versions** (immutable, hashed remote paths, e.g. `/remotes/twin/<git-sha>/remoteEntry.js`) so a bad publish is a deliberate pointer flip, and rollback is instant.
- **Strict CSP** on the hub: `default-src 'self'`; `script-src 'self'` (+ the remote path only); `connect-src 'self'`; `frame-ancestors 'none'`; `object-src 'none'`. This also caps XSS blast radius.
- Trust boundary = **your CI pipeline** — protect branch/deploy perms and artifact provenance; the remote is first-party code.

**Transport & upstream.** Turn the gateway's `verify=False` (`gateway.py` L120) back to **verified TLS** on AWS — services in a private VPC behind an internal ALB with a trusted CA; no upstream on the public internet. Terminate TLS at CloudFront/ALB with modern ciphers + HSTS.

**Session/CSRF (per the chosen posture).** `HttpOnly; Secure; SameSite=Strict` session cookie + double-submit CSRF token on mutations (Part 3). Short token TTL (`JWT_EXPIRE_MINUTES`, currently 720 — consider lowering + refresh). Rotate `JWT_SECRET`, `TWIN_API_KEY`/`NXR_API_KEYS`, vision keys via **Secrets Manager** (no secrets in env files or git; the current `hub/backend/.env` must not ship).

**Headers & hygiene.** Add security headers at the edge (CloudFront response-headers policy): `Strict-Transport-Security`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `X-Frame-Options: DENY`. WAF on CloudFront/ALB (rate-limit login, common rule set). Keep audit logging of gateway decisions (403/entitlement) — the hub already has an `audit` surface.

**Residual risks to accept/track:** federated remotes share the hub origin's trust (mitigated by first-party + CSP + pinning); `EventSource` reconnect carries the cookie (fine under SameSite=Strict); NextXR is dev-permissive without `NXR_API_KEYS` — **must set `NXR_API_KEYS`** in prod so the gateway's injected key is actually required.

## Sequencing
- **T0 — Prereqs:** add `@originjs/vite-plugin-federation` to both apps; implement the **HttpOnly cookie session + CSRF** (Part 3) and set the hub **CSP + security headers**; stand up the CloudFront/S3/ECS single-origin skeleton on AWS (can run alongside Render during cutover).
- **T1 — NextXR remote-ready:** API-base indirection, extract `TwinRoutes`, fix asset paths, federation `exposes`/`shared`, publish remote to S3.
- **T2 — Hub host:** `TwinRemoteHost` + state bridge, swap App.jsx twin routes, trim `twinState`/`api.js`, delete `modules/twin/**`, drop 3-D deps.
- **T3 — AWS wire-up:** ECS for gateway + nextxr-api, Neo4j Aura, EFS/RDS persistence, Secrets Manager, CloudFront behaviors, CI/CD.
- **T4 — Standalone + cutover:** `twin.<domain>` standalone + SSO handoff; decommission the Render twin path.

## Verification (end-to-end, drive the real thing)
- **Local first (Vite):** run NextXR remote (`vite` on its port) + hub host pointed at its `remoteEntry.js`; log in once → open **Twins** (federated list loads via `/api/twin/twins`) → **open a machine twin** → Live Dashboard renders the **real NextXR 3-D + telemetry** natively in the hub, `X-Gateway-Source: live`, **zero console errors**, no jitter. Then **Build a Twin** (plan upload → 3-D) and **Predict** (RUL). Confirm the hub's other `useTwin()` consumers (Overview, AILayer, Simulation) still read active-twin state through the bridge.
- **Federation proof:** push a trivial visible change to the NextXR frontend (e.g. a label) → re-sync the remote to S3 → reload the hub → change appears **with no hub rebuild/deploy**.
- **SSE:** facility twin telemetry streams through `/api/twin/bus/stream` with the cookie session (no 401), unbuffered.
- **Degrade:** stop the NextXR ECS task → hub twin pages show a clean error/empty state (decide whether to keep the `simTwin` stub fallback — see below), and the rest of the hub stays up.
- **Playwright** the above on the deployed CloudFront origin (login → each twin page → assert live data + 0 console errors), mirroring how prior twin fixes were verified.

## Open decisions to confirm during T0
- **`simTwin` fallback:** keep the hub's built-in twin simulator (in `twinState.jsx`) as an offline fallback, or drop it now that AWS ECS is always-on (no Render cold starts)? Recommend: keep the **health badge** (`services/integration.jsx` `SERVICES.twin`) but retire the `simTwin` stub telemetry.
- **`Trainer` (Train-with-AI):** fold into the NextXR twin remote, or keep as a thin hub-native surface reading `useTwin().active`?
- **State-bridge shape:** drive NextXR's `TwinContext` from hub `twinState` via the `TwinRemoteHost` bridge (recommended, least NextXR change), vs. refactor NextXR panels to be fully prop-driven.
