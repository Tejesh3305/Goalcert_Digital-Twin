import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { PanelHeader, Card } from '../components/ui/Card'
import TurbineModel from '../components/TurbineModel'
import Scene3D from '../components/Scene3D'
import BimViewer from '../components/BimViewer'
import GlbViewer from '../components/GlbViewer'
import { useTwin } from '../context/TwinContext'
import { useToast } from '../context/ToastContext'
import { domainMeta } from '../lib/machine'
import { readPlanFile, ACCEPT } from '../lib/planUpload'
import api, { assetUrl } from '../api/client'

/**
 * Build a Twin — ONE chat that does three jobs:
 *   • attach a 2-D floor plan → we vision-parse it, reconstruct the building in
 *     3-D and commit it as a live digital twin (graph + physics + telemetry);
 *   • attach a photo of an object → we reconstruct it in 3-D with TRELLIS
 *     (RunPod) — the upload is auto-routed server-side (plan vs. photo), no
 *     separate button needed;
 *   • or pick / describe a domain → we wire a live physics twin around its stock
 *     model (turbine, EDM, rail, hospital, EV, defence, generic facility).
 * The assistant figures out which path you mean from what you attach / say.
 */

// Machine / asset domains offered as quick-picks (tram removed).
const DOMAIN_CHIPS = ['turbine-engine', 'edm-machine', 'railway-metro', 'railway-trainset',
  'hospital-campus', 'ev-charging-network', 'ev-battery-pack', 'defence-base',
  'defence-warship', 'generic-facility']

// Building facilities for a plan reconstruction.
const FACILITIES = [
  ['Auto-detect', ''], ['Residential', 'residential'], ['Hospital', 'hospital'],
  ['Data Center', 'datacenter'], ['Office', 'office'], ['Factory', 'factory'],
]

const PLAN_STEPS = [
  ['Parsing plan with the vision model → rooms, walls, equipment', 'acc'],
  ['Reconstructing building geometry → 3-D scene graph', ''],
  ['Furnishing rooms + auto-wiring services (power, HVAC, water)', ''],
  ['Binding subsystems to the NextXR ontology + SHACL validation', 'ok'],
  ['Committing the live twin → telemetry streaming', 'ok'],
]
const DOMAIN_STEPS = [
  ['Vectorising asset → geometry, subsystems, sensors', 'acc'],
  ['Binding subsystems to the NextXR ontology', ''],
  ['Validating against SHACL shapes … passed', 'ok'],
  ['Wiring physics model + 3-tier behaviour rules', ''],
  ['Digital twin ready — sensors streaming', 'ok'],
]

/** Deterministic domain inference from free text (no LLM). */
function inferDomain(text) {
  const q = (text || '').toLowerCase()
  const table = [
    ['turbine-engine', /turbine|jet|engine|aero|trent|gas.?turbine/],
    ['edm-machine', /\bedm\b|electric.?discharge|wire.?cut|spark.?eros/],
    ['railway-metro', /metro|mrt|subway|rail network|underground|transit line/],
    ['railway-trainset', /train.?set|rolling.?stock|carriage|bogie/],
    ['hospital-campus', /hospital|clinic|ward|icu|theatre|campus|patient/],
    ['ev-charging-network', /charg|ev network|charging network|charge point/],
    ['ev-battery-pack', /battery|cell|pack|soc|soh|thermal runaway/],
    ['defence-base', /base|c4isr|garrison|radar|military base/],
    ['defence-warship', /warship|ship|naval|frigate|vessel|destroyer/],
    ['generic-facility', /facility|building|plant|office|warehouse|data.?cent/],
  ]
  for (const [key, re] of table) if (re.test(q)) return key
  return null
}

/** Facility inference from a plan filename / text (for the plan path). */
function inferFacility(text) {
  const q = (text || '').toLowerCase()
  if (/hospital|clinic|ward|icu/.test(q)) return 'hospital'
  if (/data.?cent|server/.test(q)) return 'datacenter'
  if (/office|corporate/.test(q)) return 'office'
  if (/factory|plant|warehouse|manufact/.test(q)) return 'factory'
  if (/home|house|apartment|residen|villa|flat/.test(q)) return 'residential'
  return ''
}

const BUILD_INTENT = /\b(build|create|go|make|generate|start|do it|reconstruct|yes)\b/

/** Run a build via the async start+poll endpoints so a minutes-long TRELLIS
 * cold start can't hit an HTTP/proxy timeout; falls back to the one-shot call. */
async function runBuildJob(body) {
  let started
  try { started = await api.buildFromPlanStart(body) }
  catch (e) {
    if (e.status === 404 || e.status === 405) return api.buildFromPlan(body)
    throw e
  }
  const t0 = Date.now()
  let misses = 0
  while (Date.now() - t0 < 32 * 60 * 1000) {
    await new Promise((r) => setTimeout(r, 3000))
    let s
    try { s = await api.buildFromPlanStatus(started.build_id); misses = 0 }
    catch (e) {
      if (e.status === 404) throw new Error('The server restarted mid-build — please build again.')
      if (++misses > 20) throw e
      continue
    }
    if (s.status === 'done') return s.result
    if (s.status === 'error') throw new Error(s.error || 'build failed')
  }
  throw new Error('Timed out waiting for the 3-D reconstruction.')
}

export default function BuildTwin() {
  const nav = useNavigate()
  const toast = useToast()
  const { refreshTwins, setActiveTenant } = useTwin()

  const [messages, setMessages] = useState([{ role: 'ai',
    text: "Hi — I'm the Twin Builder. Attach a 2-D floor plan for a live building twin, attach a photo of an object to reconstruct it in 3-D with TRELLIS (RunPod), or pick a domain below (or just describe your asset) and I'll wire a live physics twin around it." }])
  const [input, setInput] = useState('')
  const [plan, setPlan] = useState(null)          // { dataUrl, filename }
  const [facility, setFacility] = useState('')     // building facility ('' = auto)
  const [domain, setDomain] = useState(null)       // selected machine/asset domain
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)
  const [log, setLog] = useState([])
  const [scene, setScene] = useState(null)         // reconstructed plan scene
  const [created, setCreated] = useState(null)     // { tenant, name, domain, kind }
  const fileRef = useRef(null)
  const endRef = useRef(null)

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [messages, busy])

  const say = (role, text) => setMessages((m) => [...m, { role, text }])

  const attachPlan = async (file) => {
    if (!file) return
    try {
      const { dataUrl, filename } = await readPlanFile(file)
      // Keep any selected domain — it maps an object photo onto that domain.
      setPlan({ dataUrl, filename }); setScene(null); setCreated(null)
      const f = inferFacility(filename); if (f) setFacility(f)
      say('ai', `Attached — **${filename}**. If it's a floor plan I'll reconstruct a ${f || 'building'} twin; if it's a photo of an object I'll reconstruct it in 3-D with TRELLIS (RunPod). **Pick a domain below** to tell me what the object is (turbine, EDM, …) and I'll build a live twin of that domain around the reconstructed model — with its physics, sensors and components. Then say “build”.`)
    } catch (e) { toast.err('Could not read file', e.message); say('ai', `I couldn't read that file: ${e.message}`) }
  }

  const pickDomain = (key) => {
    // Don't clear an attached photo — the domain maps the photo onto that domain.
    setDomain(key); setScene(null); setCreated(null)
    const m = domainMeta(key)
    if (!name) setName(m.label)
    if (plan) say('ai', `Got it — I'll map your photo as a **${m.label}** and build a live twin of that domain around the reconstructed 3-D model. Say “build”.`)
    else say('ai', `Great — a **${m.label}** twin. ${m.blurb || ''} Attach a photo of one to reconstruct its real model, or say “build” for the stock model.`)
  }

  const send = () => {
    const text = input.trim(); if (!text) return
    say('user', text); setInput('')
    // Name capture from short phrases.
    if (!name && text.length < 40 && !BUILD_INTENT.test(text)) setName(text)
    const inferred = inferDomain(text)
    if (inferred && inferred !== domain) { setDomain(inferred) }
    const wantsBuild = BUILD_INTENT.test(text)
    setTimeout(() => {
      if (wantsBuild && (plan || domain || inferred)) { build(inferred || domain) }
      else if (plan) say('ai', "Got it. When you're ready, say “build” and I'll reconstruct the plan into a live twin.")
      else if (inferred) { const m = domainMeta(inferred); say('ai', `A **${m.label}** twin it is. Say “build” to wire it live, or attach a plan instead.`) }
      else say('ai', "Tell me what to model — pick a domain chip, describe your asset, or attach a 2-D floor plan.")
    }, 220)
  }

  const animateLog = (steps) => {
    setLog([]); let i = 0
    const tk = setInterval(() => {
      if (i >= steps.length - 1) { clearInterval(tk); return }
      const [t, cls] = steps[i++]
      setLog((l) => [...l, { t: (cls === 'ok' ? '✓ ' : '> ') + t, cls }])
    }, 460)
    return () => clearInterval(tk)
  }

  const build = async (domainOverride) => {
    const dom = domainOverride || domain
    if (!plan && !dom) { say('ai', 'Attach a plan or pick a domain first, then I can build.'); return }
    setBusy(true); setScene(null); setCreated(null)

    if (plan) {
      // ── Plan path: attach a floor plan → building twin, or an object photo
      // → TRELLIS/RunPod reconstruction. The backend auto-classifies which. ──
      const stop = animateLog(PLAN_STEPS)
      say('ai', 'Working on your upload — reconstructing it in 3-D…')
      try {
        const r = await runBuildJob({ data: plan.dataUrl, filename: plan.filename,
          name: name.trim() || undefined, facility: facility || undefined, floors: 1,
          domain: dom || undefined })
        stop()

        if (r.kind === 'object') {
          const mapped = r.domain && r.domain !== 'scanned-object'  // mapped onto a physics domain
          const dm = mapped ? domainMeta(r.domain) : null
          setLog((l) => [...l, { t: `✓ TRELLIS (RunPod) reconstruction${dm ? ` → ${dm.label} twin` : ''}`, cls: 'ok' }])
          // Only expose the tenant for "open dashboard" when the twin committed.
          setCreated({ tenant: r.committed ? r.tenant : null, name: r.twin_name,
            domain: r.domain, kind: 'object', modelUrl: assetUrl(r.model_url), mapped })
          if (r.committed) await refreshTwins()
          const q = r.mesh_quality?.quality_score
          const qtxt = q != null ? ` — mesh quality ${q}/100` : ''
          const live = r.committed
            ? (mapped
              ? ` It’s committed as a live **${dm.label}** twin — physics, sensors and components are streaming, and its dashboard shows this exact model. Open it to monitor and inject faults.`
              : ' It’s committed as a twin — open its dashboard to see the same model live.')
            : ''
          say('ai', `Done — reconstructed **${r.twin_name}** from your photo with TRELLIS (RunPod)${qtxt}. Drag to rotate/zoom the model.${live}`)
          toast.ok(mapped ? `${dm.label} twin created` : '3-D model generated', r.twin_name)
          return
        }

        setScene(r.scene)
        const desc = r.synthesized
          ? `I couldn't fully read the drawing, so I reconstructed a representative ${r.facility} layout${r.parse_note ? ` (${r.parse_note})` : ''}.`
          : `Reconstructed with ${r.scene?.vision_backend || 'vision'} — ${r.scene?.nodes?.length || 0} elements.`
        if (r.committed) {
          setLog((l) => [...l, { t: '✓ ' + PLAN_STEPS[PLAN_STEPS.length - 1][0], cls: 'ok' }])
          await refreshTwins()
          setCreated({ tenant: r.tenant, name: r.twin_name, domain: r.facility, kind: 'building' })
          say('ai', `Done — **${r.twin_name}** is live. ${desc} Physics, behaviours and telemetry are streaming. Open its dashboard to monitor, predict and inject faults.`)
          toast.ok('Digital twin created', r.twin_name)
        } else {
          setLog((l) => [...l, { t: '⚠ 3-D reconstructed — live twin not committed (start the database)', cls: 'warn' }])
          say('ai', `${desc} I rendered the 3-D model, but couldn't commit the live twin — start the database (./start.ps1) and build again to make it live.`)
          toast.info('3-D model ready', 'Live twin needs the database')
        }
      } catch (e) {
        stop(); setLog((l) => [...l, { t: 'Build failed: ' + e.message, cls: 'warn' }])
        say('ai', `The build failed: ${e.message}`); toast.err('Build failed', e.message)
      } finally { setBusy(false) }
      return
    }

    // ── Domain path: wire a live physics twin around a stock model ──
    const m = domainMeta(dom)
    const stop = animateLog(DOMAIN_STEPS)
    say('ai', `Wiring a live ${m.label} twin…`)
    try {
      const res = await api.createTwin({ name: name.trim() || m.label, domain: dom })
      stop(); setLog((l) => [...l, { t: '✓ ' + DOMAIN_STEPS[DOMAIN_STEPS.length - 1][0], cls: 'ok' }])
      await refreshTwins()
      setCreated({ tenant: res.twin.tenant_id, name: res.twin.name, domain: dom, kind: 'machine' })
      say('ai', `**${res.twin.name}** is live — physics, behaviours and sensor telemetry are streaming now. Open its dashboard to watch it.`)
      toast.ok('Twin created', `${res.twin.name} is live`)
    } catch (e) {
      stop(); setLog((l) => [...l, { t: 'Build failed: ' + e.message, cls: 'warn' }])
      say('ai', `The build failed: ${e.message}`); toast.err('Build failed', e.message)
    } finally { setBusy(false) }
  }

  const openDashboard = () => { if (created) { setActiveTenant(created.tenant); nav('/') } }
  const canBuild = !!(plan || domain) && !busy
  const meta = domain ? domainMeta(domain) : null

  return (
    <div className="panel">
      <PanelHeader title="Build a Twin"
        subtitle="One chat, two ways in — attach a 2-D floor plan to reconstruct a building twin, or pick a domain to wire a physics twin. Both become live digital twins." />

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14, alignItems: 'start' }}>
        {/* ── Left: the chat ── */}
        <Card style={{ display: 'flex', flexDirection: 'column' }}>
          <div className="chat-messages" style={{ minHeight: 300, maxHeight: 380, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 10, paddingRight: 4 }}>
            {messages.map((m, i) => (
              <div key={i} style={{ alignSelf: m.role === 'user' ? 'flex-end' : 'flex-start', maxWidth: '88%',
                padding: '10px 13px', borderRadius: 14, fontSize: 12.5, lineHeight: 1.55, whiteSpace: 'pre-wrap',
                background: m.role === 'user' ? 'var(--gradient)' : 'var(--surface2)',
                color: m.role === 'user' ? '#fff' : 'var(--text)', border: m.role === 'user' ? 'none' : '1px solid var(--border)' }}>
                {m.role === 'ai' && <div style={{ fontSize: 10, color: 'var(--muted)', marginBottom: 3, fontWeight: 600 }}>Twin Builder</div>}
                <Rich text={m.text} />
              </div>
            ))}
            {busy && <div style={{ alignSelf: 'flex-start', padding: '10px 13px', borderRadius: 14, background: 'var(--surface2)', border: '1px solid var(--border)', fontSize: 12.5 }}><span className="spinner" /> working…</div>}
            <div ref={endRef} />
          </div>

          {/* Domain quick-chips — with a photo attached they say "what is this?"
              and map the reconstruction onto that domain's physics/sensors. */}
          <div style={{ marginTop: 12 }}>
            <div className="card-label">{plan ? 'What is this? — map the photo to a domain' : 'Pick a domain'}</div>
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
              {DOMAIN_CHIPS.map((k) => {
                const m = domainMeta(k); const on = domain === k
                return (
                  <button key={k} className={`btn ${on ? 'btn-primary' : ''}`} style={{ fontSize: 11, ...(on ? { background: m.accent, borderColor: 'transparent' } : {}) }}
                    onClick={() => pickDomain(k)}>
                    <i className={`ti ${m.icon}`} /> {m.label}
                  </button>
                )
              })}
            </div>
          </div>

          {/* Facility chips (only when a plan is attached) */}
          {plan && (
            <div style={{ marginTop: 10 }}>
              <div className="card-label">Plan facility <span className="muted" style={{ fontWeight: 400 }}>({plan.filename})</span></div>
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                {FACILITIES.map(([label, key]) => (
                  <button key={key || 'auto'} className={`btn ${facility === key ? 'btn-primary' : ''}`} style={{ fontSize: 11 }}
                    onClick={() => setFacility(key)}>{label}</button>
                ))}
              </div>
            </div>
          )}

          {/* Attached-file preview (thumbnail so you can see what's attached) */}
          {plan && (
            <div style={{ marginTop: 12, display: 'flex', alignItems: 'center', gap: 10,
              padding: 8, borderRadius: 12, border: '1px solid var(--border)', background: 'var(--surface2)' }}>
              <img src={plan.dataUrl} alt={plan.filename}
                style={{ width: 56, height: 56, objectFit: 'cover', borderRadius: 8, border: '1px solid var(--border)', background: '#fff' }} />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 12.5, fontWeight: 600, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                  <i className="ti ti-photo" style={{ marginRight: 4, color: 'var(--accent-green)' }} />{plan.filename}
                </div>
                <div className="muted" style={{ fontSize: 11 }}>Attached · floor plan or object photo (auto-detected on build)</div>
              </div>
              <button className="btn" title="Remove attachment" disabled={busy}
                onClick={() => { setPlan(null); setScene(null); setCreated(null) }}>
                <i className="ti ti-x" />
              </button>
            </div>
          )}

          {/* Input row */}
          <div style={{ display: 'flex', gap: 8, marginTop: 12 }}
            onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => { e.preventDefault(); attachPlan(e.dataTransfer.files?.[0]) }}>
            <button className="btn" title="Attach a 2-D floor plan or a photo of an object (PNG/JPG/PDF)" onClick={() => fileRef.current?.click()} disabled={busy}>
              <i className={`ti ${plan ? 'ti-file-check' : 'ti-paperclip'}`} style={plan ? { color: 'var(--accent-green)' } : undefined} />
            </button>
            <input ref={fileRef} type="file" accept={ACCEPT} style={{ display: 'none' }} onChange={(e) => attachPlan(e.target.files?.[0])} />
            <input className="input" value={input} disabled={busy}
              placeholder={plan ? 'Say “build” to reconstruct — or describe the building…' : 'Describe your asset, or drop a plan / object photo…'}
              onChange={(e) => setInput(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && send()} />
            <button className="btn btn-primary" onClick={send} disabled={busy || !input.trim()}><i className="ti ti-send" /></button>
          </div>

          <button className="btn btn-primary" style={{ width: '100%', justifyContent: 'center', padding: '11px 0', marginTop: 10 }}
            onClick={() => build()} disabled={!canBuild}>
            {busy ? <><span className="spinner" /> Building…</>
              : plan ? <><i className="ti ti-cube-3d-sphere" /> Reconstruct 3-D &amp; Generate Twin</>
                : <><i className="ti ti-wand" /> Build {meta ? meta.label : 'Twin'}</>}
          </button>
        </Card>

        {/* ── Right: the 3-D model / result ── */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <Card title={<><i className="ti ti-cube" /> 3-D Model
            {created && <span className="pill pill-green" style={{ marginLeft: 'auto' }}>live twin</span>}</>}
            style={{ padding: 0, overflow: 'hidden' }}>
            <Preview scene={scene} created={created} domain={domain} />
          </Card>

          {log.length > 0 && (
            <Card title={<><i className="ti ti-terminal-2" /> Build Log</>}>
              <div className="mono" style={{ fontSize: 11.5, maxHeight: 170, overflowY: 'auto', lineHeight: 1.9 }}>
                {log.map((l, i) => (
                  <div key={i} style={{ padding: '1px 0',
                    color: l.cls === 'ok' ? 'var(--accent-green)' : l.cls === 'warn' ? 'var(--accent-amber)' : l.cls === 'acc' ? 'var(--brand)' : 'var(--muted)' }}>{l.t}</div>
                ))}
              </div>
            </Card>
          )}

          {created?.kind === 'object' ? (
            <Card style={{ borderColor: 'rgba(22,163,74,.4)', background: 'rgba(22,163,74,.06)' }}>
              <div style={{ fontWeight: 700, color: 'var(--accent-green)' }}>
                <i className="ti ti-circle-check" /> {created.mapped ? `${domainMeta(created.domain).label} twin created` : '3-D model generated'}</div>
              <div style={{ fontSize: 12.5, marginTop: 4, color: 'var(--muted)' }}>
                {!created.tenant
                  ? 'Reconstructed from your photo with TRELLIS (RunPod). Start the database (./start.ps1) and rebuild to commit it as a live twin.'
                  : created.mapped
                    ? `Reconstructed from your photo and mapped onto the ${domainMeta(created.domain).label} domain — live physics, sensors and components are streaming, and the dashboard shows this exact model.`
                    : 'Reconstructed from your photo with TRELLIS (RunPod) and committed as a twin — its dashboard shows this exact mesh.'}
                <div style={{ marginTop: 6 }}>Model: <a href={created.modelUrl} target="_blank" rel="noreferrer"><i className="ti ti-download" /> download .glb</a></div>
              </div>
              {created.tenant && (
                <button className="btn btn-primary" style={{ marginTop: 12 }} onClick={openDashboard}>
                  <i className="ti ti-layout-dashboard" /> Open twin dashboard</button>
              )}
            </Card>
          ) : created ? (
            <Card style={{ borderColor: 'rgba(22,163,74,.4)', background: 'rgba(22,163,74,.06)' }}>
              <div style={{ fontWeight: 700, color: 'var(--accent-green)' }}><i className="ti ti-circle-check" /> Live digital twin generated</div>
              <div style={{ fontSize: 12.5, marginTop: 4, color: 'var(--muted)' }}>
                {created.kind === 'building'
                  ? 'The building was reconstructed from your plan and committed to the graph — physics, behaviours and telemetry are streaming now.'
                  : 'Physics, behaviours and sensor telemetry are wired and streaming now.'}</div>
              <button className="btn btn-primary" style={{ marginTop: 12 }} onClick={openDashboard}>
                <i className="ti ti-layout-dashboard" /> Open live dashboard</button>
            </Card>
          ) : (
            <Card style={{ background: 'var(--surface2)' }}>
              <div className="card-title" style={{ fontSize: 12 }}><i className="ti ti-info-circle" /> How it works</div>
              <div style={{ fontSize: 11.5, lineHeight: 1.9, color: 'var(--muted)' }}>
                <div><b>Plan →</b> attach a 2-D floor plan; we vision-parse it, reconstruct the building in 3-D, furnish &amp; auto-wire services, then commit a live twin.</div>
                <div><b>Photo →</b> attach a photo of an object; we reconstruct it in 3-D with TRELLIS (RunPod).</div>
                <div><b>Domain →</b> pick a chip or describe your asset; we wire a live physics twin (telemetry, 3-tier behaviours, RUL).</div>
                <div style={{ marginTop: 4 }}>Then open its dashboard to monitor, predict and inject faults.</div>
              </div>
            </Card>
          )}
        </div>
      </div>
    </div>
  )
}

/** Minimal **bold** renderer for assistant messages. */
function Rich({ text }) {
  const parts = String(text).split(/(\*\*[^*]+\*\*)/g)
  return <>{parts.map((p, i) => p.startsWith('**') && p.endsWith('**')
    ? <b key={i}>{p.slice(2, -2)}</b> : <span key={i}>{p}</span>)}</>
}

/** The right-column preview — reconstructed scene, live twin, or a domain hero. */
function Preview({ scene, created, domain }) {
  if (created?.kind === 'object') return <GlbViewer url={created.modelUrl} height={420} />
  if (scene) return <BimViewer scene={scene} tenant={created?.kind === 'building' ? created.tenant : undefined} />
  // A committed building twin with no in-memory scene → fetch by tenant.
  if (created?.kind === 'building') return <BimViewer tenant={created.tenant} />
  if (domain === 'turbine-engine') return <TurbineModel height={420} />
  if (domain === 'edm-machine') return <div style={{ height: 420 }}><Scene3D domain="edm-machine" live={{}} height={420} /></div>
  const m = domain ? domainMeta(domain) : null
  if (m) return (
    <div style={{ height: 420, background: `radial-gradient(circle at 50% 32%, ${m.accent}22, #0b0d18 74%)`,
      display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 12, color: '#dfe3ff' }}>
      <div style={{ width: 92, height: 92, borderRadius: '50%', display: 'flex', alignItems: 'center', justifyContent: 'center',
        fontSize: 44, color: '#fff', background: `linear-gradient(135deg, ${m.accent}, ${m.accent}aa)`, boxShadow: `0 10px 40px ${m.accent}55` }}>
        <i className={`ti ${m.icon}`} />
      </div>
      <div style={{ fontFamily: 'var(--display)', fontWeight: 600, fontSize: 18 }}>{m.label}</div>
      <div style={{ fontSize: 12, opacity: 0.7 }}>Its live map / model opens in the dashboard</div>
    </div>
  )
  return (
    <div style={{ height: 420, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 10, color: 'var(--muted)', background: 'var(--surface2)' }}>
      <i className="ti ti-cube-3d-sphere" style={{ fontSize: 40, opacity: 0.5 }} />
      <div style={{ fontSize: 13 }}>Your twin's 3-D model appears here.</div>
      <div style={{ fontSize: 11 }}>Attach a plan or pick a domain to begin.</div>
    </div>
  )
}
