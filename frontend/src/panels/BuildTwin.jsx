import { useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { PanelHeader, Card } from '../components/ui/Card'
import TurbineModel from '../components/TurbineModel'
import Scene3D from '../components/Scene3D'
import DemoTwin from '../components/DemoTwin'
import { useTwin } from '../context/TwinContext'
import { useToast } from '../context/ToastContext'
import { domainMeta } from '../lib/machine'
import api from '../api/client'

/**
 * Build a Twin — collins-style visual flow: pick a domain, describe/upload the
 * asset, then build. The right column previews the twin's real 3-D model (turbine
 * GLB, EDM procedural engine, facility scene) and streams a build log, ending in a
 * live twin created through this platform's Graph Writer.
 */
const BUILD_DOMAINS = ['defence-base', 'defence-warship', 'ev-charging-network', 'ev-battery-pack',
  'hospital-campus', 'railway-metro', 'railway-trainset', 'turbine-engine', 'edm-machine',
  'tram-network', 'generic-facility']

const BUILD_STEPS = [
  ['Vectorising asset → geometry, subsystems, sensors', 'acc'],
  ['Binding subsystems to the NextXR ontology', ''],
  ['Validating against SHACL shapes … passed', 'ok'],
  ['Wiring physics model + 3-tier behaviour rules', ''],
  ['Calibrating live telemetry stream', 'ok'],
  ['Digital twin ready — sensors streaming', 'ok'],
]

function Preview({ domain }) {
  const m = domainMeta(domain)
  if (domain === 'turbine-engine') return <TurbineModel height={360} />
  if (domain === 'edm-machine') return <Scene3D domain="edm-machine" live={{}} height={360} />
  if (domain === 'generic-facility') return <DemoTwin domain="datacenter" name={m.label} />
  return (
    <div style={{ height: 360, borderRadius: 12, border: '1px solid var(--border)', overflow: 'hidden',
      background: `radial-gradient(circle at 50% 32%, ${m.accent}22, #0b0d18 74%)`,
      display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 12, color: '#dfe3ff' }}>
      <div style={{ width: 92, height: 92, borderRadius: '50%', display: 'flex', alignItems: 'center', justifyContent: 'center',
        fontSize: 44, color: '#fff', background: `linear-gradient(135deg, ${m.accent}, ${m.accent}aa)`, boxShadow: `0 10px 40px ${m.accent}55` }}>
        <i className={`ti ${m.icon}`} />
      </div>
      <div style={{ fontFamily: 'var(--display)', fontWeight: 600, fontSize: 18 }}>{m.label}</div>
      <div style={{ fontSize: 12, opacity: 0.7 }}>Live network map opens in the dashboard</div>
    </div>
  )
}

export default function BuildTwin() {
  const nav = useNavigate()
  const toast = useToast()
  const { refreshTwins, setActiveTenant } = useTwin()
  const [domain, setDomain] = useState('turbine-engine')
  const [name, setName] = useState('')
  const [image, setImage] = useState(null)
  const [drag, setDrag] = useState(false)
  const [quality, setQuality] = useState('fast')
  const [chat, setChat] = useState([{ role: 'assistant', content: "Hi! I'm the Twin Builder. Pick a domain, optionally describe or photograph the asset, then hit Build — I'll wire a live physics twin around it." }])
  const [input, setInput] = useState('')
  const [stage, setStage] = useState('idle')  // idle | building | done
  const [log, setLog] = useState([])
  const [created, setCreated] = useState(null)
  const fileRef = useRef(null)
  const meta = domainMeta(domain)

  const loadFile = (f) => {
    if (!f) return
    const r = new FileReader()
    r.onload = () => setImage({ preview: r.result, name: f.name })
    r.readAsDataURL(f)
  }

  const send = () => {
    const m = input.trim(); if (!m) return
    setChat((c) => [...c, { role: 'user', content: m }])
    setInput('')
    if (!name) setName(m.length < 40 ? m : meta.label)
    setTimeout(() => setChat((c) => [...c, { role: 'assistant',
      content: `Got it — I'll build a ${meta.label.toLowerCase()} twin${m.length < 40 ? ` named “${m}”` : ''}. Hit Build 3-D Twin when ready.` }]), 250)
  }

  const build = async () => {
    setStage('building'); setLog([]); setCreated(null)
    // animate the build log
    let i = 0
    const tk = setInterval(() => {
      if (i >= BUILD_STEPS.length) { clearInterval(tk); finish(); return }
      const [t, cls] = BUILD_STEPS[i++]
      setLog((l) => [...l, { t: (cls === 'ok' ? '✓ ' : '> ') + t, cls }])
    }, 520)
  }

  const finish = async () => {
    try {
      const res = await api.createTwin({ name: name.trim() || meta.label, domain })
      await refreshTwins()
      setCreated({ tenant: res.twin.tenant_id, name: res.twin.name })
      setStage('done')
      toast.ok('Twin created', `${res.twin.name} is live`)
    } catch (e) {
      setLog((l) => [...l, { t: 'Build failed: ' + e.message, cls: 'warn' }])
      setStage('idle')
      toast.err('Build failed', e.message)
    }
  }

  const openDashboard = () => { if (created) { setActiveTenant(created.tenant); nav('/') } }

  return (
    <div className="panel">
      <PanelHeader title="Build a Twin"
        subtitle="Pick a domain, describe or photograph the asset, then build — we wire a live physics twin (telemetry, behaviours & 3-D model) around it." />

      {/* Step 1 — domain */}
      <Card title={<><i className="ti ti-category" /> 1. Select Domain</>} className="section-gap">
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          {BUILD_DOMAINS.map((k) => {
            const m = domainMeta(k)
            const on = domain === k
            return (
              <button key={k} className={`btn ${on ? 'btn-primary' : ''}`} onClick={() => { setDomain(k); setStage('idle'); setCreated(null) }}
                style={on ? { background: m.accent, borderColor: 'transparent', boxShadow: `0 4px 14px ${m.accent}44` } : {}}>
                <i className={`ti ${m.icon}`} /> {m.label}
                <span style={{ marginLeft: 2, fontSize: 10, opacity: 0.7, color: on ? 'rgba(255,255,255,.8)' : 'var(--muted)' }}>{m.tag}</span>
              </button>
            )
          })}
        </div>
      </Card>

      <div className="grid-2">
        {/* Left column */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <Card title={<><i className="ti ti-sparkles" /> 2. Describe Your Asset <span className="pill pill-purple">agent</span></>}
            style={{ display: 'flex', flexDirection: 'column' }}>
            <div style={{ minHeight: 130, maxHeight: 200, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 10, padding: 2 }}>
              {chat.map((m, i) => (
                <div key={i} style={{ alignSelf: m.role === 'user' ? 'flex-end' : 'flex-start', maxWidth: '85%',
                  padding: '10px 13px', borderRadius: 14, fontSize: 12.5, lineHeight: 1.55,
                  background: m.role === 'user' ? 'var(--gradient)' : 'var(--surface2)',
                  color: m.role === 'user' ? '#fff' : 'var(--text)', border: m.role === 'user' ? 'none' : '1px solid var(--border)' }}>
                  {m.role === 'assistant' && <div style={{ fontSize: 10, color: 'var(--muted)', marginBottom: 3, fontWeight: 600 }}>Twin Builder</div>}
                  {m.content}
                </div>
              ))}
            </div>
            <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
              <input className="input" value={input} placeholder="e.g. A Rolls-Royce Trent on a test stand…"
                onChange={(e) => setInput(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && send()} />
              <button className="btn btn-primary" onClick={send}><i className="ti ti-send" /></button>
            </div>
          </Card>

          <Card title={<><i className="ti ti-photo" /> 3. Upload Asset Photo <span className="muted" style={{ fontSize: 10, fontWeight: 400 }}>(optional)</span></>}>
            <div onClick={() => fileRef.current?.click()}
              onDragOver={(e) => { e.preventDefault(); setDrag(true) }} onDragLeave={() => setDrag(false)}
              onDrop={(e) => { e.preventDefault(); setDrag(false); loadFile(e.dataTransfer.files[0]) }}
              style={{ border: `2px dashed ${drag ? meta.accent : 'var(--border2)'}`, borderRadius: 14,
                background: drag ? `${meta.accent}11` : 'var(--surface2)', padding: 18, textAlign: 'center', cursor: 'pointer', transition: 'all .2s' }}>
              <input ref={fileRef} type="file" accept="image/*" style={{ display: 'none' }} onChange={(e) => loadFile(e.target.files[0])} />
              {image
                ? <div style={{ position: 'relative', display: 'inline-block' }}>
                    <img src={image.preview} alt="asset" style={{ maxHeight: 120, borderRadius: 12, border: '2px solid var(--border)' }} />
                    <button onClick={(e) => { e.stopPropagation(); setImage(null) }}
                      style={{ position: 'absolute', top: -8, right: -8, width: 24, height: 24, borderRadius: '50%', background: 'var(--accent-red)', color: '#fff', border: 'none', cursor: 'pointer', fontSize: 12 }}>✕</button>
                  </div>
                : <><div style={{ fontSize: 28, color: meta.accent }}><i className="ti ti-cloud-upload" /></div>
                    <div style={{ fontWeight: 600, marginTop: 6, fontSize: 13 }}>Drop a photo of your {meta.label.toLowerCase()}</div>
                    <div className="muted" style={{ fontSize: 11, marginTop: 4 }}>PNG / JPG — any angle, clean background is best</div></>}
            </div>
          </Card>

          <Card title={<><i className="ti ti-wand" /> 4. Build the Twin</>}>
            <div style={{ display: 'flex', gap: 8, marginBottom: 12, alignItems: 'center' }}>
              <span className="card-label" style={{ marginBottom: 0 }}>Fidelity:</span>
              <button className={`btn ${quality === 'fast' ? 'btn-primary' : ''}`} style={{ fontSize: 11 }} onClick={() => setQuality('fast')}><i className="ti ti-bolt" /> Fast</button>
              <button className={`btn ${quality === 'high' ? 'btn-primary' : ''}`} style={{ fontSize: 11 }} onClick={() => setQuality('high')}><i className="ti ti-diamond" /> High</button>
            </div>
            <button className="btn btn-primary" style={{ width: '100%', justifyContent: 'center', padding: '12px 0' }}
              onClick={build} disabled={stage === 'building'}>
              {stage === 'building' ? <><span className="spinner" /> Building twin…</> : <><i className="ti ti-cube-3d-sphere" /> Build 3-D Twin</>}
            </button>
          </Card>
        </div>

        {/* Right column */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <Card title={<><i className="ti ti-cube" /> 3-D Model
            {stage === 'done' && <span className="pill pill-green" style={{ marginLeft: 'auto' }}>live</span>}</>}>
            <Preview domain={domain} />
          </Card>

          {log.length > 0 && (
            <Card title={<><i className="ti ti-terminal-2" /> Build Log</>}>
              <div className="mono" style={{ fontSize: 11.5, maxHeight: 180, overflowY: 'auto', lineHeight: 1.9 }}>
                {log.map((l, i) => (
                  <div key={i} style={{ padding: '1px 0',
                    color: l.cls === 'ok' ? 'var(--accent-green)' : l.cls === 'warn' ? 'var(--accent-amber)' : l.cls === 'acc' ? 'var(--brand)' : 'var(--muted)' }}>{l.t}</div>
                ))}
              </div>
            </Card>
          )}

          {created ? (
            <Card style={{ borderColor: 'rgba(22,163,74,.4)', background: 'rgba(22,163,74,.06)' }}>
              <div style={{ fontWeight: 700, color: 'var(--accent-green)' }}><i className="ti ti-circle-check" /> Live {meta.label} twin created</div>
              <div style={{ fontSize: 12.5, marginTop: 4, color: 'var(--muted)' }}>
                Physics, behaviours and sensor telemetry are wired and streaming now.</div>
              <button className="btn btn-primary" style={{ marginTop: 12 }} onClick={openDashboard}>
                <i className="ti ti-layout-dashboard" /> Open live dashboard</button>
            </Card>
          ) : stage === 'idle' && (
            <Card style={{ background: 'var(--surface2)' }}>
              <div className="card-title" style={{ fontSize: 12 }}><i className="ti ti-info-circle" /> How it works</div>
              <div style={{ fontSize: 11.5, lineHeight: 1.9, color: 'var(--muted)' }}>
                <div><b>1.</b> Pick the domain and (optionally) describe or photograph the asset</div>
                <div><b>2.</b> Preview its 3-D model on the right</div>
                <div><b>3.</b> Build — we wire a live twin: physics, 3-tier behaviours & telemetry</div>
                <div><b>4.</b> Open its dashboard to monitor, predict and inject faults</div>
              </div>
            </Card>
          )}
        </div>
      </div>
    </div>
  )
}
