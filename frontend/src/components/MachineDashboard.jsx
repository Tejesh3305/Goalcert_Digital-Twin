/**
 * MachineDashboard — the unified live dashboard for a machine-domain twin
 * (turbine / EDM / tram). One page, collins-demo style: 3D/network scene on top,
 * KPIs (health ring, risk, findings), an always-on AI co-pilot chat, live
 * telemetry with sparklines, subsystem condition, active findings and a
 * generated maintenance work order. Everything polls the machine-twin runtime.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { Card } from './ui/Card'
import { Empty } from './ui/States'
import { HealthRing, Sparkline } from './ui/Viz'
import RailwayNetworkMap from './RailwayNetworkMap'
import RailwayDepotBoard from './RailwayDepotBoard'
import HospitalCampusViews from './HospitalCampusViews'
import EVNetworkViews from './EVNetworkViews'
import EVBatteryHeatmap from './EVBatteryHeatmap'
import DefenceBaseViews from './DefenceBaseViews'
import DefenceDamageControl from './DefenceDamageControl'
import TurbineModel from './TurbineModel'
import Scene3D from './Scene3D'
import { usePolling, useApi } from '../hooks/useApi'
import {
  domainMeta, statusColor, healthBand, riskFromHealth, hColor,
  stubNarration, stubReply, isNetworkDomain,
} from '../lib/machine'
import { localName } from '../lib/format'
import api from '../api/client'

const sevClass = { critical: 'ev-crit', warning: 'ev-warn', info: 'ev-info', ok: 'ev-ok' }
const fmt = (v) => (typeof v !== 'number' ? (v ?? '—') : Number.isInteger(v) ? v : v.toFixed(1))

export default function MachineDashboard({ tenant, domain, name }) {
  const meta = domainMeta(domain)
  const { data: state, refetch } = usePolling(() => api.twinRuntimeState(tenant), 1500, [tenant], { skip: !tenant })
  const { data: diag } = usePolling(() => api.twinDiagnostics(tenant), 3000, [tenant], { skip: !tenant })
  const { data: net } = usePolling(() => api.twinNetwork(tenant).catch(() => null), 2000, [tenant],
    { skip: !tenant || !isNetworkDomain(domain) })
  const { data: domains } = useApi(() => api.machineDomains(), [])

  const health = state?.health
  const running = state?.running
  const latest = state?.latest || {}
  const findings = state?.findings || []
  const components = diag?.components || []
  const sensors = diag?.sensors || []
  const risk = riskFromHealth(health)
  const faults = useMemo(
    () => (domains?.domains || []).find((d) => d.key === domain)?.faults || [], [domains, domain])

  // rolling history for sparklines
  const hist = useRef({})
  useEffect(() => {
    for (const [k, v] of Object.entries(latest)) {
      if (typeof v !== 'number') continue
      ;(hist.current[k] ||= []).push(v)
      if (hist.current[k].length > 30) hist.current[k].shift()
    }
  }, [latest])

  const inject = async (f) => { try { await api.twinSimulate(tenant, { fault: f, severity: 0.9 }); refetch() } catch { /* */ } }
  const toggleRunning = async () => { try { await api.twinRunning(tenant, !running); refetch() } catch { /* */ } }

  return (
    <div className="panel">
      <div className="panel-header">
        <div>
          <div className="panel-title">{name || meta.label}</div>
          <div className="panel-subtitle">{meta.tag} · live physics twin · streaming telemetry</div>
        </div>
        <div className="panel-actions">
          <button className="btn" onClick={toggleRunning}>
            <i className={`ti ${running ? 'ti-player-pause' : 'ti-player-play'}`} /> {running ? 'Stop twin' : 'Start twin'}
          </button>
          {faults.length > 0 && (
            <select className="select" style={{ width: 'auto', minWidth: 150 }} value=""
              onChange={(e) => e.target.value && inject(e.target.value)}>
              <option value="">Inject fault…</option>
              <option value="none">✓ Healthy (clear)</option>
              {faults.map((f) => <option key={f} value={f}>⚠ {localName(f).replace(/_/g, ' ')}</option>)}
            </select>
          )}
        </div>
      </div>

      {/* KPI row */}
      <div className="grid-4 section-gap">
        <div className="card kpi">
          <div className="card-label">Twin Health</div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <HealthRing value={health} size={54} />
            <div>
              <div className="card-value" style={{ color: hColor(health), fontSize: 20 }}>{healthBand(health).label}</div>
              <div className="card-change">physics index</div>
            </div>
          </div>
        </div>
        <div className="card kpi">
          <div className="card-label">Risk Score</div>
          <div className="card-value">{risk.score ?? '—'}</div>
          <div className="card-change" style={{ color: risk.color, fontWeight: 600 }}>{risk.label}</div>
        </div>
        <div className="card kpi">
          <div className="card-label">Active Findings</div>
          <div className="card-value" style={{ color: findings.length ? 'var(--accent-red)' : 'var(--accent-green)' }}>{findings.length}</div>
          <div className="card-change">{state?.incidents?.length || 0} incident(s)</div>
        </div>
        <div className="card kpi">
          <div className="card-label">{sensors[0]?.name || 'Signal'}</div>
          <div className="card-value" style={{ fontSize: 22 }}>
            {fmt(sensors[0]?.value)}<span style={{ fontSize: 13 }}>{sensors[0]?.unit ? ' ' + sensors[0].unit : ''}</span>
          </div>
          <div className="card-change">{state?.frames ?? 0} frames</div>
        </div>
      </div>

      {/* Hospital campus: the five clinical views (own cards) */}
      {domain === 'hospital-campus' ? (
        <div className="section-gap"><HospitalCampusViews net={net} /></div>
      ) : domain === 'ev-charging-network' ? (
        <div className="section-gap"><EVNetworkViews net={net} /></div>
      ) : domain === 'ev-battery-pack' ? (
        <Card title={<><i className="ti ti-grid-dots" /> Battery Cell Heatmap</>}
          action={<span className="pill pill-green">● live</span>} className="section-gap">
          {net ? <EVBatteryHeatmap net={net} /> : <Empty label="Loading cells…" icon="ti-loader" />}
        </Card>
      ) : domain === 'defence-base' ? (
        <div className="section-gap"><DefenceBaseViews net={net} /></div>
      ) : domain === 'defence-warship' ? (
        <Card title={<><i className="ti ti-ship" /> Damage Control</>}
          action={net?.ship?.capsize_risk ? <span className="pill pill-red">capsize risk</span> : <span className="pill pill-green">● live</span>}
          className="section-gap">
          {net ? <DefenceDamageControl net={net} /> : <Empty label="Loading…" icon="ti-loader" />}
        </Card>
      ) : (
        /* 3D / network scene */
        <Card title={<><i className={`ti ${meta.icon}`} /> {domain === 'railway-metro' ? 'Live Metro Network' : '3-D Twin'}</>}
          action={isNetworkDomain(domain) && net?.blocked?.length
            ? <span className="pill pill-red">{net.blocked.length} {domain === 'railway-metro' ? 'line' : 'route'} blocked</span>
            : <span className="pill pill-green">● live</span>}
          className="section-gap">
          {domain === 'railway-metro'
            ? (net ? <RailwayNetworkMap net={net} /> : <Empty label="Loading network…" icon="ti-loader" />)
            : domain === 'turbine-engine'
              ? <TurbineModel latest={latest} health={health} height={340} />
              : domain === 'edm-machine'
                ? <Scene3D domain="edm-machine" machine={name || meta.label} live={latest} height={340} />
                : <MachineHero meta={meta} name={name || meta.label} health={health} latest={latest} />}
        </Card>
      )}

      {/* Depot board (metro only) */}
      {domain === 'railway-metro' && net?.depots && (
        <Card title={<><i className="ti ti-building-warehouse" /> Depot Board</>}
          action={<span className="pill pill-surface">predicted availability</span>} className="section-gap">
          <RailwayDepotBoard depots={net.depots} />
        </Card>
      )}

      {/* AI Co-Pilot */}
      <CoPilot tenant={tenant} state={{ ...state, name: name || meta.label }} className="section-gap" />

      {/* Live telemetry */}
      <Card title={<><i className="ti ti-activity" /> Live Telemetry</>}
        action={<span className="pill pill-green">● streaming</span>} className="section-gap">
        {sensors.length === 0 ? <Empty label="Warming up…" icon="ti-loader" /> : (
          <div className="sensor-grid">
            {sensors.map((s) => {
              const cls = s.status === 'critical' ? 'sensor-crit' : s.status === 'warning' ? 'sensor-warn' : ''
              const col = s.status === 'critical' ? '#e11d48' : s.status === 'warning' ? '#d97706' : meta.accent
              return (
                <div key={s.signal} className={`sensor-card ${cls}`} style={{ position: 'relative' }}>
                  <span className="live-indicator" />
                  <div className="sensor-label">{s.name}</div>
                  <div><span className="sensor-value">{fmt(s.value)}</span><span className="sensor-unit">{s.unit}</span></div>
                  <Sparkline data={hist.current[s.signal]} color={col} />
                </div>
              )
            })}
          </div>
        )}
      </Card>

      <div className="grid-2 section-gap">
        {/* Subsystem condition */}
        <Card title={<><i className="ti ti-subtask" /> Subsystem Condition</>}>
          {components.length === 0 ? <Empty label="Warming up…" icon="ti-loader" /> : components.map((c) => {
            const pct = c.health != null ? Math.round(c.health * 100) : 0
            return (
              <div key={c.name} className="bar-row">
                <div className="bar-label"><span>{c.name}</span><b style={{ color: statusColor(c.status) }}>{pct}%</b></div>
                <div className="bar-track"><div className="bar-fill" style={{ width: `${pct}%`, background: statusColor(c.status) }} /></div>
              </div>
            )
          })}
        </Card>

        {/* Active findings */}
        <Card title={<><i className="ti ti-alert-triangle" /> Active Findings</>}
          action={<span className={`pill ${findings.length ? 'pill-red' : 'pill-surface'}`}>{findings.length}</span>}>
          {findings.length === 0
            ? <Empty label="No findings — within limits." icon="ti-shield-check" />
            : (
              <div className="event-list" style={{ maxHeight: 280, overflowY: 'auto' }}>
                {findings.map((f, i) => (
                  <div key={i} className="event-item">
                    <div className={`event-icon ${sevClass[f.severity] || 'ev-info'}`}><i className="ti ti-alert-triangle" /></div>
                    <div className="event-body">
                      <div className="event-title">{f.message}</div>
                      <div className="event-meta">Tier {f.tier} · {f.behaviorId}</div>
                    </div>
                    <span className="event-time" style={{ color: statusColor(f.severity) }}>{f.severity}</span>
                  </div>
                ))}
              </div>
            )}
        </Card>
      </div>

      {/* Maintenance work order */}
      <WorkOrder domain={domain} name={name || meta.label} findings={findings} />
    </div>
  )
}

/** A lightweight animated hero for non-network machine twins (turbine / EDM). */
function MachineHero({ meta, name, health, latest }) {
  const keys = Object.keys(latest).slice(0, 3)
  return (
    <div style={{ position: 'relative', height: 300, borderRadius: 12, overflow: 'hidden',
      background: `radial-gradient(circle at 50% 30%, ${meta.accent}22, var(--surface2) 70%)`,
      border: '1px solid var(--border)', display: 'flex', flexDirection: 'column',
      alignItems: 'center', justifyContent: 'center', gap: 14 }}>
      <div style={{ position: 'absolute', inset: 0, opacity: 0.08,
        background: `repeating-linear-gradient(45deg, ${meta.accent}, ${meta.accent} 1px, transparent 1px, transparent 14px)` }} />
      <div style={{ width: 96, height: 96, borderRadius: '50%', display: 'flex', alignItems: 'center', justifyContent: 'center',
        fontSize: 46, color: '#fff', background: `linear-gradient(135deg, ${meta.accent}, ${meta.accent}aa)`,
        boxShadow: `0 10px 40px ${meta.accent}55`, animation: 'pulse 2.4s ease-in-out infinite' }}>
        <i className={`ti ${meta.icon}`} />
      </div>
      <div style={{ fontFamily: 'var(--display)', fontSize: 18, fontWeight: 600, zIndex: 1 }}>{name}</div>
      <div style={{ display: 'flex', gap: 10, zIndex: 1, flexWrap: 'wrap', justifyContent: 'center' }}>
        {keys.map((k) => (
          <span key={k} className="pill pill-surface" style={{ fontFamily: 'var(--mono)' }}>
            {localName(k)}: <b>{fmt(latest[k])}</b>
          </span>
        ))}
      </div>
    </div>
  )
}

/** Always-on co-pilot chat: zero-token stub narration + Q&A grounded in state. */
function CoPilot({ tenant, state, className }) {
  const [msgs, setMsgs] = useState([])
  const [input, setInput] = useState('')
  const endRef = useRef(null)
  const stRef = useRef(state); stRef.current = state

  // auto-narration every 20s
  useEffect(() => {
    const tick = () => {
      const t = stubNarration(stRef.current)
      if (t) setMsgs((p) => [...p, { role: 'auto', text: t, ts: new Date().toLocaleTimeString() }].slice(-20))
    }
    tick()
    const id = setInterval(tick, 20000)
    return () => clearInterval(id)
  }, [tenant])

  useEffect(() => { const el = endRef.current?.parentElement; if (el) el.scrollTop = el.scrollHeight }, [msgs])

  const send = () => {
    const m = input.trim(); if (!m) return
    setMsgs((p) => [...p, { role: 'user', text: m, ts: new Date().toLocaleTimeString() }])
    setInput('')
    const reply = stubReply(m, stRef.current)
    setTimeout(() => setMsgs((p) => [...p, { role: 'assistant', text: reply, ts: new Date().toLocaleTimeString() }]), 250)
  }

  return (
    <Card title={<><i className="ti ti-message-chatbot" /> AI Co-Pilot <span className="pill pill-green" style={{ fontSize: 9 }}>live</span></>}
      className={className}>
      <div style={{ maxHeight: 260, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 8, padding: '4px 0', marginBottom: 10 }}>
        {msgs.length === 0 && <div className="muted" style={{ fontSize: 12.5, padding: '8px 0' }}>
          Observing {state?.name} in real time. Auto-observations appear here, or ask a question.</div>}
        {msgs.map((m, i) => (
          <div key={i} style={{ display: 'flex', justifyContent: m.role === 'user' ? 'flex-end' : 'flex-start', gap: 8 }}>
            {m.role !== 'user' && (
              <div style={{ width: 26, height: 26, borderRadius: 8, flexShrink: 0, display: 'flex', alignItems: 'center', justifyContent: 'center',
                fontSize: 13, background: m.role === 'auto' ? 'rgba(22,163,74,.12)' : 'var(--gradient)', color: m.role === 'auto' ? 'var(--accent-green)' : '#fff' }}>
                <i className={`ti ${m.role === 'auto' ? 'ti-antenna-bars-5' : 'ti-sparkles'}`} />
              </div>
            )}
            <div style={{ maxWidth: '80%', padding: '9px 13px', fontSize: 12.5, lineHeight: 1.6,
              borderRadius: m.role === 'user' ? '14px 4px 14px 14px' : '4px 14px 14px 14px',
              background: m.role === 'user' ? 'var(--gradient)' : 'var(--surface2)',
              color: m.role === 'user' ? '#fff' : 'var(--text)', border: m.role === 'user' ? 'none' : '1px solid var(--border)' }}>
              {m.role === 'auto' && <div style={{ fontSize: 9, color: 'var(--accent-green)', fontWeight: 700, marginBottom: 3, fontFamily: 'var(--mono)' }}>OBSERVATION · {m.ts}</div>}
              {m.text}
            </div>
          </div>
        ))}
        <div ref={endRef} />
      </div>
      {msgs.filter((m) => m.role === 'user').length === 0 && (
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 8 }}>
          {['How is it doing?', "What's most concerning?", 'What should I check next?'].map((s) => (
            <button key={s} className="quick-chip" onClick={() => setInput(s)}>{s}</button>
          ))}
        </div>
      )}
      <div style={{ display: 'flex', gap: 8 }}>
        <input className="input" value={input} onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && send()} placeholder={`Ask about ${state?.name}…`} style={{ flex: 1 }} />
        <button className="btn btn-primary" onClick={send} disabled={!input.trim()}><i className="ti ti-send" /></button>
      </div>
    </Card>
  )
}

/** Deterministic maintenance work order generated from the top finding. */
function WorkOrder({ domain, name, findings }) {
  const [wo, setWo] = useState(null)
  if (findings.length === 0) return null
  const gen = () => {
    const top = findings.find((f) => f.severity === 'critical') || findings[0]
    const num = 'WO-' + Math.abs(hashStr(name + top.message)).toString().slice(0, 5)
    setWo({
      wo_number: num,
      priority: top.severity === 'critical' ? 'Critical' : 'Routine',
      fault: top.message,
      root_cause: `Behaviour ${top.behaviorId} (Tier ${top.tier}) flagged this on ${name}.`,
      steps: [
        'Isolate and lock out the affected subsystem per site safety procedure.',
        `Inspect the component behind "${top.message.split('—')[0].trim()}".`,
        'Replace/service the worn or out-of-limit part; verify against spec.',
        'Return to service and confirm the finding clears on the live twin.',
      ],
      parts: domain === 'turbine-engine' ? ['Bearing kit', 'Oil filter', 'Seal set']
        : domain === 'edm-machine' ? ['Wire spool', 'Dielectric filter', 'Ion-exchange resin']
          : domain === 'railway-metro' ? ['Third-rail shoe', 'PSD actuator', 'HVAC filter bank', 'Rectifier module']
            : domain === 'railway-trainset' ? ['Wheelset', 'Brake pads', 'Traction motor bearing', 'Door drive']
              : domain === 'hospital-campus' ? ['AHU filter set', 'Medical gas regulator', 'UPS battery string', 'TMV / flush valve']
                : domain === 'ev-charging-network' ? ['Connector cable', 'Charger power module', 'Transformer tap', 'Contactor']
                  : domain === 'ev-battery-pack' ? ['Cell module', 'BMS slave board', 'Coolant pump', 'Balancing resistor']
                    : domain === 'defence-base' ? ['Radar TWT', 'Sensor node', 'Magazine cooling unit', 'Fuel bladder']
                      : domain === 'defence-warship' ? ['GT hot section', 'Bilge pump', 'Watertight door seal', 'Hull plate']
                        : ['Brake pads', 'Pantograph carbon', 'OHL section'],
    })
  }
  return (
    <Card title={<><i className="ti ti-file-certificate" /> Maintenance Work Order</>}
      action={<span className="pill pill-surface" style={{ fontSize: 9 }}>generated</span>} className="section-gap">
      {!wo ? (
        <>
          <div className="muted" style={{ fontSize: 12.5, marginBottom: 12 }}>
            Generate a maintenance work order from the current diagnosis.</div>
          <button className="btn btn-primary" onClick={gen} style={{ width: '100%' }}>
            <i className="ti ti-file-certificate" /> Generate Work Order</button>
        </>
      ) : (
        <div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 10, marginBottom: 14 }}>
            <div className="kpibox"><div className="card-label">WO Number</div><div style={{ fontFamily: 'var(--mono)', fontWeight: 700 }}>{wo.wo_number}</div></div>
            <div className="kpibox"><div className="card-label">Priority</div><div><span className={`pill ${wo.priority === 'Critical' ? 'pill-red' : 'pill-surface'}`}>{wo.priority}</span></div></div>
            <div className="kpibox"><div className="card-label">Steps</div><div style={{ fontWeight: 700 }}>{wo.steps.length}</div></div>
          </div>
          <div style={{ background: 'rgba(225,29,72,.04)', border: '1px solid rgba(225,29,72,.12)', borderRadius: 12, padding: '12px 14px', marginBottom: 12 }}>
            <div className="card-label" style={{ color: 'var(--accent-red)' }}>Fault</div>
            <div style={{ fontSize: 12.5, lineHeight: 1.6, marginTop: 4 }}>{wo.fault}</div>
            <div className="card-label" style={{ marginTop: 10 }}>Root cause</div>
            <div style={{ fontSize: 12.5, lineHeight: 1.6, marginTop: 4 }}>{wo.root_cause}</div>
          </div>
          <div className="card-label" style={{ marginBottom: 8 }}>Repair Procedure</div>
          <div className="event-list">
            {wo.steps.map((s, i) => (
              <div key={i} className="event-item">
                <div className="event-icon" style={{ background: 'var(--brand-soft)', color: 'var(--brand)' }}>{i + 1}</div>
                <div className="event-body"><div className="event-title" style={{ fontWeight: 500 }}>{s}</div></div>
              </div>
            ))}
          </div>
          <div style={{ marginTop: 12 }}>
            <div className="card-label" style={{ marginBottom: 8 }}>Parts Required</div>
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
              {wo.parts.map((p) => <span key={p} className="pill pill-blue">{p}</span>)}
            </div>
          </div>
        </div>
      )}
    </Card>
  )
}

function hashStr(s) {
  let h = 0
  for (let i = 0; i < s.length; i++) { h = (h << 5) - h + s.charCodeAt(i); h |= 0 }
  return h
}
