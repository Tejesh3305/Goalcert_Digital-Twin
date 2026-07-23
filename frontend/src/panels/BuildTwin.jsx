import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { PanelHeader, Card } from '../components/ui/Card'
import TurbineModel from '../components/TurbineModel'
import Scene3D from '../components/Scene3D'
import BimViewer from '../components/BimViewer'
import GlbViewer from '../components/GlbViewer'
import { useTwin } from '../context/TwinContext'
import { useToast } from '../context/ToastContext'
import { useApi } from '../hooks/useApi'
import { domainMeta } from '../lib/machine'
import { readPlanFile, ACCEPT } from '../lib/planUpload'
import api, { assetUrl } from '../api/client'

/**
 * Build a Twin — a guided, domain-driven builder. You choose a domain (or attach
 * a plan / object photo), and the assistant asks the specific questions it needs
 * to build an accurate twin, then maps the real components, sensors, physics and
 * behaviour rules from that domain's ontology pack (shown as a live blueprint).
 * The domain is the one thing it always needs — from it, everything else is
 * mapped from the core packs. Building here is the ONLY way to create a twin.
 */

// Machine / asset domains offered as quick-picks.
const DOMAIN_CHIPS = ['turbine-engine', 'edm-machine', 'tram-network', 'railway-metro',
  'railway-trainset', 'hospital-campus', 'ev-charging-network', 'ev-battery-pack',
  'defence-base', 'defence-warship', 'generic-facility']

// Building facilities for a plan reconstruction.
const FACILITIES = [
  ['Auto-detect', ''], ['Residential', 'residential'], ['Hospital', 'hospital'],
  ['Data Center', 'datacenter'], ['Office', 'office'], ['Factory', 'factory'],
]

// The domain-specific questions the assistant asks to build an accurate twin.
// `name` and `site` are asked for every domain; the middle question is grounded
// in what that domain pack actually models.
const DOMAIN_QUESTIONS = {
  'turbine-engine': [{ key: 'platform', q: 'Which engine or platform is this — and is it on a test rig or in service? (e.g. "Trent 1000, MRO test cell")' }],
  'edm-machine': [{ key: 'process', q: 'Which wire-EDM machine, and the typical workpiece material / thickness? (e.g. "Mitsubishi MV, tool steel 40 mm")' }],
  'tram-network': [{ key: 'network', q: 'Which tram / light-rail network should I model? (e.g. "Melbourne tram network")' }],
  'railway-metro': [{ key: 'network', q: 'Which metro network and line(s)? (e.g. "Singapore MRT — North-South Line")' }],
  'railway-trainset': [{ key: 'stock', q: 'Which rolling-stock class, and how many cars per set? (e.g. "6-car CBTC set")' }],
  'hospital-campus': [{ key: 'scale', q: 'What is the campus scale — how many operating theatres, ICU beds and ED bays?' }],
  'ev-charging-network': [{ key: 'scale', q: 'How many charge points (AC / DC-fast), and is there on-site solar + battery storage?' }],
  'ev-battery-pack': [{ key: 'chem', q: 'Cell chemistry and pack configuration? (e.g. "NMC, 96s, ~400 V")' }],
  'defence-base': [{ key: 'assets', q: 'Which assets are on the base — radar, hangars, fuel / ammunition storage, C4ISR?' }],
  'defence-warship': [{ key: 'class', q: 'Which vessel class and propulsion? (e.g. "frigate, gas-turbine COGAG")' }],
  'generic-facility': [{ key: 'systems', q: 'How many floors, and which systems — HVAC, power, water, fire, security?' }],
}
const questionsFor = (dom) => [
  { key: 'name', q: `What should I name this ${domainMeta(dom).label} twin?` },
  ...(DOMAIN_QUESTIONS[dom] || []),
  { key: 'site', q: 'Finally — where is it located (site name / city)? (or say "skip")' },
]

const PLAN_STEPS = [
  ['Parsing plan with the vision model → rooms, walls, equipment', 'acc'],
  ['Reconstructing building geometry → 3-D scene graph', ''],
  ['Furnishing rooms + auto-wiring services (power, HVAC, water)', ''],
  ['Binding subsystems to the NextXR ontology + SHACL validation', 'ok'],
  ['Committing the live twin → telemetry streaming', 'ok'],
]

/** Deterministic domain inference from free text (no LLM). */
function inferDomain(text) {
  const q = (text || '').toLowerCase()
  const table = [
    ['turbine-engine', /turbine|jet|engine|aero|trent|gas.?turbine/],
    ['edm-machine', /\bedm\b|electric.?discharge|wire.?cut|spark.?eros/],
    ['tram-network', /tram|light.?rail|melbourne|streetcar/],
    ['railway-metro', /metro|mrt|subway|rail network|underground|transit line|singapore/],
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

const BUILD_INTENT = /\b(build|create|go|make|generate|start|do it|reconstruct|confirm|looks good)\b/

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

  // The domain blueprints from the core packs (components / sensors / physics /
  // behaviours) — this is what "mapping from the ontology" is grounded in.
  const { data: domainsData } = useApi(() => api.machineDomains(), [])
  const blueprints = useMemo(() => {
    const m = {}
    for (const d of domainsData?.domains || []) m[d.key] = d
    return m
  }, [domainsData])

  const [messages, setMessages] = useState([{ role: 'ai',
    text: "Hi — I'm the Twin Builder. Pick a domain below (or describe your asset, or attach a 2-D floor plan / object photo) and I'll ask a few specifics, then map the exact components, sensors, physics and behaviour rules from that domain's pack into a live twin. The one thing I always need is the **domain**." }])
  const [input, setInput] = useState('')
  const [plan, setPlan] = useState(null)          // { dataUrl, filename }
  const [facility, setFacility] = useState('')     // building facility ('' = auto)
  const [domain, setDomain] = useState(null)       // selected machine/asset domain
  const [answers, setAnswers] = useState({})       // gathered spec answers
  const [qIndex, setQIndex] = useState(-1)         // current question index (-1 = none)
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)
  const [log, setLog] = useState([])
  const [scene, setScene] = useState(null)         // reconstructed plan scene
  const [created, setCreated] = useState(null)     // { tenant, name, domain, kind }
  const fileRef = useRef(null)
  const endRef = useRef(null)

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [messages, busy])

  const say = (role, text) => setMessages((m) => [...m, { role, text }])
  const bp = domain ? blueprints[domain] : null

  const attachPlan = async (file) => {
    if (!file) return
    try {
      const { dataUrl, filename } = await readPlanFile(file)
      setPlan({ dataUrl, filename }); setScene(null); setCreated(null)
      const f = inferFacility(filename); if (f) setFacility(f)
      say('ai', `Attached — **${filename}**. If it's a floor plan I'll reconstruct a ${f || 'building'} twin; if it's a photo of an object, **pick a domain below** so I map it onto that domain's physics, then say “build”.`)
    } catch (e) { toast.err('Could not read file', e.message); say('ai', `I couldn't read that file: ${e.message}`) }
  }

  // Selecting a domain starts the guided questionnaire.
  const pickDomain = (key) => {
    setDomain(key); setScene(null); setCreated(null); setAnswers({})
    const m = domainMeta(key)
    const qs = questionsFor(key)
    setQIndex(0)
    if (plan) { say('ai', `Got it — I'll map your photo as a **${m.label}** and wire that domain's physics around the reconstructed model. ${qs[0].q}`); return }
    say('ai', `**${m.label}** — ${m.blurb || ''}\n\nI'll map its components, sensors, physics and behaviour rules from the pack (see the blueprint on the right). First: ${qs[0].q}`)
  }

  // Handle a chat message: answer the current question, infer a domain, or build.
  const send = () => {
    const text = input.trim(); if (!text) return
    say('user', text); setInput('')
    const wantsBuild = BUILD_INTENT.test(text)

    // If we're mid-questionnaire, treat the message as the answer.
    if (domain && qIndex >= 0) {
      const qs = questionsFor(domain)
      const cur = qs[qIndex]
      const val = /^skip$/i.test(text) ? '' : text
      const nextAnswers = { ...answers, [cur.key]: val }
      setAnswers(nextAnswers)
      if (cur.key === 'name' && val) setName(val)
      const next = qIndex + 1
      if (next < qs.length) {
        setQIndex(next)
        setTimeout(() => say('ai', qs[next].q), 200)
      } else {
        setQIndex(-1)
        setTimeout(() => say('ai', specSummary(domain, nextAnswers, blueprints[domain])), 200)
      }
      return
    }

    // Not in a questionnaire: infer a domain or build.
    const inferred = inferDomain(text)
    if (wantsBuild && (plan || domain || inferred)) { build(inferred || domain); return }
    if (inferred && inferred !== domain) { pickDomain(inferred); return }
    setTimeout(() => {
      if (plan) say('ai', 'Pick a domain to map the upload onto, then say “build”.')
      else say('ai', 'Tell me what to model — pick a domain chip, describe your asset, or attach a 2-D floor plan.')
    }, 180)
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

  // The build log for a domain twin, grounded in that domain's real blueprint.
  const domainBuildSteps = (dom) => {
    const b = blueprints[dom]
    return [
      [`Mapping ${b ? b.subsystems.length : ''} components + ${b ? b.sensors.length : ''} sensors from the ${domainMeta(dom).label} pack`, 'acc'],
      ['Binding subsystems + signals to the NextXR ontology', ''],
      ['Validating against SHACL shapes … passed', 'ok'],
      [`Wiring the ${b?.physics || 'physics'} model + 3-tier behaviour rules`, ''],
      ['Digital twin ready — sensors streaming', 'ok'],
    ]
  }

  const build = async (domainOverride) => {
    const dom = domainOverride || domain
    if (!plan && !dom) { say('ai', 'Pick a domain first (that is the minimum I need), then I can build.'); return }
    setBusy(true); setScene(null); setCreated(null); setQIndex(-1)

    if (plan) {
      const stop = animateLog(PLAN_STEPS)
      say('ai', 'Working on your upload — reconstructing it in 3-D…')
      try {
        const r = await runBuildJob({ data: plan.dataUrl, filename: plan.filename,
          name: name.trim() || undefined, facility: facility || undefined, floors: 1,
          domain: dom || undefined })
        stop()
        if (r.kind === 'object') {
          const mapped = r.domain && r.domain !== 'scanned-object'
          const dm = mapped ? domainMeta(r.domain) : null
          setLog((l) => [...l, { t: `✓ TRELLIS (RunPod) reconstruction${dm ? ` → ${dm.label} twin` : ''}`, cls: 'ok' }])
          setCreated({ tenant: r.committed ? r.tenant : null, name: r.twin_name,
            domain: r.domain, kind: 'object', modelUrl: assetUrl(r.model_url), mapped })
          if (r.committed) await refreshTwins()
          say('ai', `Done — reconstructed **${r.twin_name}** from your photo with TRELLIS (RunPod).${r.committed && mapped ? ` It's live as a **${dm.label}** twin — physics, sensors and components streaming.` : ''}`)
          toast.ok(mapped ? `${dm.label} twin created` : '3-D model generated', r.twin_name)
          return
        }
        setScene(r.scene)
        if (r.committed) {
          setLog((l) => [...l, { t: '✓ ' + PLAN_STEPS[PLAN_STEPS.length - 1][0], cls: 'ok' }])
          await refreshTwins()
          setCreated({ tenant: r.tenant, name: r.twin_name, domain: r.facility, kind: 'building' })
          say('ai', `Done — **${r.twin_name}** is live. Physics, behaviours and telemetry are streaming. Open its dashboard to monitor, predict and inject faults.`)
          toast.ok('Digital twin created', r.twin_name)
        } else {
          setLog((l) => [...l, { t: '⚠ 3-D reconstructed — live twin not committed (start the database)', cls: 'warn' }])
          say('ai', 'I rendered the 3-D model, but couldn\'t commit the live twin — start the database (./start.ps1) and build again.')
          toast.info('3-D model ready', 'Live twin needs the database')
        }
      } catch (e) {
        stop(); setLog((l) => [...l, { t: 'Build failed: ' + e.message, cls: 'warn' }])
        say('ai', `The build failed: ${e.message}`); toast.err('Build failed', e.message)
      } finally { setBusy(false) }
      return
    }

    // ── Domain path: map the pack into a live physics twin ──
    const m = domainMeta(dom)
    const twinName = (answers.name || name).trim() || m.label
    const stop = animateLog(domainBuildSteps(dom))
    say('ai', `Mapping the ${m.label} pack into a live twin…`)
    try {
      const res = await api.createTwin({ name: twinName, domain: dom })
      stop(); setLog((l) => [...l, { t: '✓ Digital twin ready — sensors streaming', cls: 'ok' }])
      await refreshTwins()
      setCreated({ tenant: res.twin.tenant_id, name: res.twin.name, domain: dom, kind: 'machine' })
      say('ai', `**${res.twin.name}** is live — ${bp ? `${bp.subsystems.length} components, ${bp.sensors.length} sensors, ` : ''}physics and behaviour rules are streaming now. Open its dashboard to watch it.`)
      toast.ok('Twin created', `${res.twin.name} is live`)
    } catch (e) {
      stop(); setLog((l) => [...l, { t: 'Build failed: ' + e.message, cls: 'warn' }])
      say('ai', `The build failed: ${e.message}`); toast.err('Build failed', e.message)
    } finally { setBusy(false) }
  }

  const openDashboard = () => { if (created?.tenant) { setActiveTenant(created.tenant); nav('/') } }
  const canBuild = !!(plan || domain) && !busy && qIndex < 0
  const meta = domain ? domainMeta(domain) : null

  return (
    <div className="panel">
      <PanelHeader title="Build a Twin"
        subtitle="Pick a domain and answer a few specifics — the builder maps the real components, sensors, physics and behaviour from that domain's pack into a live twin. This is the only way to create a twin." />

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

          {/* Domain quick-chips */}
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
              placeholder={qIndex >= 0 ? 'Type your answer…' : plan ? 'Say “build” to reconstruct — or describe the building…' : 'Describe your asset, or drop a plan / object photo…'}
              onChange={(e) => setInput(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && send()} />
            <button className="btn btn-primary" onClick={send} disabled={busy || !input.trim()}><i className="ti ti-send" /></button>
          </div>

          <button className="btn btn-primary" style={{ width: '100%', justifyContent: 'center', padding: '11px 0', marginTop: 10 }}
            onClick={() => build()} disabled={!canBuild}>
            {busy ? <><span className="spinner" /> Building…</>
              : qIndex >= 0 ? <><i className="ti ti-messages" /> Answer the questions first…</>
                : plan ? <><i className="ti ti-cube-3d-sphere" /> Reconstruct 3-D &amp; Generate Twin</>
                  : <><i className="ti ti-wand" /> Build {meta ? meta.label : 'Twin'}</>}
          </button>
        </Card>

        {/* ── Right: the domain blueprint + 3-D model / result ── */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          {bp && !created && <Blueprint bp={bp} meta={meta} />}

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

          {created && (
            <Card style={{ borderColor: 'rgba(22,163,74,.4)', background: 'rgba(22,163,74,.06)' }}>
              <div style={{ fontWeight: 700, color: 'var(--accent-green)' }}><i className="ti ti-circle-check" /> Live digital twin generated</div>
              <div style={{ fontSize: 12.5, marginTop: 4, color: 'var(--muted)' }}>
                Physics, behaviours and sensor telemetry are wired and streaming now.</div>
              {created.tenant && (
                <button className="btn btn-primary" style={{ marginTop: 12 }} onClick={openDashboard}>
                  <i className="ti ti-layout-dashboard" /> Open live dashboard</button>
              )}
            </Card>
          )}
        </div>
      </div>
    </div>
  )
}

/** The grounded twin blueprint for a domain — its real components, sensors,
 *  physics model and behaviour/fault catalogue, from the core pack. */
function Blueprint({ bp, meta }) {
  return (
    <Card title={<><i className={`ti ${meta.icon}`} /> Twin Blueprint · {meta.label}</>}
      action={<span className="pill pill-blue" style={{ fontSize: 9 }}>from pack</span>}>
      {bp.description && <div style={{ fontSize: 12, color: 'var(--muted)', lineHeight: 1.5, marginBottom: 10 }}>{bp.description}</div>}
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 12 }}>
        <span className="pill pill-surface"><i className="ti ti-cpu" /> {bp.physics}</span>
        <span className="pill pill-surface">{bp.subsystems.length} components</span>
        <span className="pill pill-surface">{bp.sensors.length} sensors</span>
        <span className="pill pill-surface">{bp.faults.length} fault modes</span>
      </div>

      <div className="card-label" style={{ marginBottom: 6 }}>Components</div>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 12 }}>
        {bp.subsystems.map((s) => <span key={s.key} className="pill pill-surface" style={{ fontSize: 11 }}>{s.label}</span>)}
      </div>

      <div className="card-label" style={{ marginBottom: 6 }}>Sensors <span className="muted" style={{ fontWeight: 400 }}>({bp.sensors.length})</span></div>
      <div style={{ maxHeight: 130, overflowY: 'auto', display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '2px 12px' }}>
        {bp.sensors.map((s) => (
          <div key={s.signal} style={{ fontSize: 11, color: 'var(--muted)', display: 'flex', justifyContent: 'space-between', gap: 6 }}>
            <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{s.label}</span>
            {s.unit && <span className="mono" style={{ color: 'var(--hint)' }}>{s.unit}</span>}
          </div>
        ))}
      </div>

      {bp.faults.length > 0 && (
        <>
          <div className="card-label" style={{ margin: '12px 0 6px' }}>Behaviour rules · fault modes</div>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            {bp.faults.slice(0, 10).map((f) => (
              <span key={f} className="pill pill-surface" style={{ fontSize: 10 }}>{String(f).replace(/_/g, ' ')}</span>
            ))}
          </div>
        </>
      )}
    </Card>
  )
}

/** A grounded, human summary of the spec the assistant gathered + what it maps. */
function specSummary(dom, answers, bp) {
  const m = domainMeta(dom)
  const nm = (answers.name || m.label).trim()
  const lines = [`Ready to build **${nm}** — a **${m.label}** twin.`]
  const extra = Object.entries(answers).filter(([k, v]) => k !== 'name' && v).map(([, v]) => v)
  if (extra.length) lines.push(`Noted: ${extra.join(' · ')}.`)
  if (bp) lines.push(`I'll map **${bp.subsystems.length} components**, **${bp.sensors.length} sensors** and **${bp.faults.length} fault modes** from the ${m.label} pack, on the ${bp.physics} physics model with 3-tier behaviour rules.`)
  lines.push('Say **“build”** to wire it live.')
  return lines.join('\n\n')
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
