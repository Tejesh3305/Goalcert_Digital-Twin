import { useState } from 'react'
import { PanelHeader, Card } from '../components/ui/Card'
import { Empty } from '../components/ui/States'
import NoTwin from '../components/NoTwin'
import { useTwin } from '../context/TwinContext'
import { usePolling } from '../hooks/useApi'
import SimTwinNotice from '../components/SimTwinNotice'
import { isMachineDomain, healthBand, statusColor } from '../lib/machine'
import { isSimTenant } from '../lib/simTwins'
import { localName } from '../lib/format'
import api from '../api/client'

/** Prediction — physics-grounded forecast + a plain-language inference.
 *  Machine twins: live telemetry, subsystem health, per-signal projections,
 *  RUL, and an inferred summary (what's driving the risk + the recommended
 *  action). Facility twins: a findings-driven outlook from the live graph. */

const HORIZONS = [['1 hour', 60], ['2 hours', 120], ['6 hours', 360], ['24 hours', 1440], ['3 days', 4320]]
const SERIES_COLORS = ['#e11d48', '#2563eb', '#0d9488', '#d97706', '#7c3aed', '#0ea5e9']

const fmtMins = (m) => m >= 1440 ? `~${(m / 1440).toFixed(1)} d` : m >= 60 ? `~${(m / 60).toFixed(1)} h` : `~${Math.round(m)} min`
const fmtVal = (v) => typeof v === 'number' ? (Math.abs(v) >= 100 ? Math.round(v) : +v.toFixed(2)) : v

/** A line chart of one signal (or health) across the forecast trajectory, with a
 *  marker + label for the current (t=0) value. */
function SignalChart({ trajectory, sigKey, color, height = 140, unit = '' }) {
  if (!trajectory || trajectory.length < 2) return <div style={{ height }} />
  const W = 620, pad = 10, H = height
  const ts = trajectory.map((p) => p.t)
  const tMax = Math.max(...ts) || 1
  const vals = trajectory.map((p) => p[sigKey]).filter((v) => typeof v === 'number')
  const min = Math.min(...vals), max = Math.max(...vals)
  const span = max - min || 1
  const xy = (p) => [pad + (p.t / tMax) * (W - 2 * pad), pad + (1 - (p[sigKey] - min) / span) * (H - 2 * pad)]
  const pts = trajectory.map(xy)
  const d = pts.map((p) => p.join(',')).join(' ')
  const area = `${pad},${H - pad} ${d} ${W - pad},${H - pad}`
  const [x0, y0] = pts[0]
  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', height: 'auto', display: 'block' }}>
      <polyline points={area} fill={`${color}14`} stroke="none" />
      <polyline points={d} fill="none" stroke={color} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
      <line x1={pad} y1={H - pad} x2={W - pad} y2={H - pad} stroke="var(--border)" strokeWidth="1" />
      <circle cx={x0} cy={y0} r="3.5" fill={color} />
      <text x={pad} y={pad + 9} fontSize="10" fill="var(--muted)" fontFamily="var(--mono)">{fmtVal(max)}</text>
      <text x={pad} y={H - pad - 2} fontSize="10" fill="var(--muted)" fontFamily="var(--mono)">{fmtVal(min)}</text>
      <text x={W - pad} y={pad + 9} fontSize="10" fill={color} fontFamily="var(--mono)" textAnchor="end">now {fmtVal(trajectory[0][sigKey])}{unit}</text>
    </svg>
  )
}

/** Deterministic inference from the physics forecast + live diagnostics. */
function infer({ pred, diag, hmin }) {
  const healthNow = diag?.overall_health ?? diag?.machine?.health ?? null
  const sev = pred?.severity || 'nominal'
  const rul = [...(pred?.rul || [])].sort((a, b) => a.minutes - b.minutes)
  const driver = rul[0] || null
  const findings = diag?.findings || []
  const crit = findings.find((f) => f.severity === 'critical')
  const worst = crit || findings[0] || null
  const comps = (diag?.components || []).filter((c) => typeof c.health === 'number')
  const weakest = comps.length ? comps.reduce((a, b) => (a.health <= b.health ? a : b)) : null

  let headline, action
  if (driver) {
    const comp = driver.component.replace(/_/g, ' ')
    headline = `${comp} is projected to reach its operating limit in ${fmtMins(driver.minutes)} — the earliest constraint, so it sets the maintenance window.`
    action = `Inspect the ${comp} and schedule service before ${fmtMins(driver.minutes)}.`
  } else if (worst) {
    headline = `No subsystem is projected to breach a limit within ${HORIZONS.find(([, m]) => m === hmin)?.[0] || 'the horizon'}, but there ${findings.length > 1 ? 'are' : 'is'} ${findings.length} active finding${findings.length > 1 ? 's' : ''}.`
    action = `Address: ${worst.message}`
  } else if (weakest && weakest.health < 0.72) {
    headline = `All limits hold for now; the ${weakest.name} subsystem is the weakest link at ${Math.round(weakest.health * 100)}% health and worth watching.`
    action = `Keep monitoring the ${weakest.name}; no immediate action required.`
  } else {
    headline = 'Every signal and subsystem stays within limits across the horizon — nominal operation.'
    action = 'No action needed. Continue monitoring.'
  }

  // Confidence: higher when a concrete limit breach is projected close-in.
  let confidence
  if (driver) confidence = Math.min(96, Math.round(55 + (1 - driver.minutes / hmin) * 40))
  else if (sev === 'critical') confidence = 88
  else if (sev === 'warning' || (healthNow != null && healthNow < 0.7)) confidence = 66
  else confidence = 82

  return { healthNow, sev, driver, worst, weakest, headline, action, confidence, rul }
}

function StatTile({ label, value, unit, status }) {
  const col = statusColor(status)
  return (
    <div style={{ border: '1px solid var(--border)', borderRadius: 10, padding: '9px 11px', background: 'var(--surface2)', minWidth: 0 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 10.5, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '.03em' }}>
        <span style={{ width: 7, height: 7, borderRadius: '50%', background: col, flexShrink: 0 }} />
        <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{label}</span>
      </div>
      <div className="mono" style={{ fontSize: 16, fontWeight: 600, marginTop: 3, color: 'var(--text)' }}>
        {value == null ? '—' : fmtVal(value)}<span style={{ fontSize: 10, color: 'var(--muted)', marginLeft: 3 }}>{unit}</span>
      </div>
    </div>
  )
}

function MachineForecast({ tenant, twin }) {
  const [hmin, setHmin] = useState(120)
  const { data: pred } = usePolling(() => api.twinPredict(tenant, hmin, 60), 6000, [tenant, hmin], { skip: !tenant })
  const { data: diag } = usePolling(() => api.twinDiagnostics(tenant), 4000, [tenant], { skip: !tenant })

  const traj = pred?.trajectory || []
  const sigKeys = traj.length ? Object.keys(traj[0]).filter((k) => k !== 't' && k !== 'health') : []
  const sensors = diag?.sensors || []
  const components = diag?.components || []
  const info = pred && diag ? infer({ pred, diag, hmin }) : null
  const band = healthBand(info?.healthNow)

  // Current value + unit for a trajectory signal key, matched from live sensors.
  const sensorFor = (key) => sensors.find((s) => (s.signal || '').split(/[:#]/).pop() === key || localName(s.signal) === key)

  const sevPill = { critical: 'pill-red', warning: 'pill-amber', nominal: 'pill-green' }[info?.sev || 'nominal']

  return (
    <>
      <div className="panel-header" style={{ marginTop: 4 }}>
        <div className="panel-subtitle">Live state, where each signal is heading, and what it means over the selected horizon.</div>
        <div className="panel-actions">
          <span className="muted" style={{ alignSelf: 'center', fontSize: 12 }}>Horizon:</span>
          <select className="select" style={{ width: 'auto' }} value={hmin} onChange={(e) => setHmin(Number(e.target.value))}>
            {HORIZONS.map(([label, m]) => <option key={m} value={m}>{label}</option>)}
          </select>
          {info && <span className={`pill ${sevPill}`} style={{ alignSelf: 'center', textTransform: 'capitalize' }}>{info.sev}</span>}
        </div>
      </div>

      {!info ? <Empty label="Computing forecast…" icon="ti-loader" /> : (
        <>
          {/* ── Inference summary ── */}
          <Card className="section-gap" style={{ borderColor: `${band.color}55`, background: `${band.color}0d` }}>
            <div style={{ display: 'flex', gap: 20, flexWrap: 'wrap', alignItems: 'center' }}>
              <div style={{ textAlign: 'center', minWidth: 96 }}>
                <div className="mono" style={{ fontSize: 34, fontWeight: 700, color: band.color, lineHeight: 1 }}>
                  {info.healthNow == null ? '—' : Math.round(info.healthNow * 100)}<span style={{ fontSize: 15 }}>%</span>
                </div>
                <div style={{ fontSize: 11, color: band.color, fontWeight: 600, marginTop: 3 }}>{band.label}</div>
                <div style={{ fontSize: 10, color: 'var(--muted)' }}>health now</div>
              </div>
              <div style={{ flex: 1, minWidth: 260 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 7, marginBottom: 6 }}>
                  <i className="ti ti-bulb" style={{ color: band.color }} />
                  <b style={{ fontSize: 13 }}>Inference</b>
                  <span className="pill pill-surface" style={{ fontSize: 10, marginLeft: 'auto' }}>{info.confidence}% confidence</span>
                </div>
                <div style={{ fontSize: 12.5, lineHeight: 1.6, color: 'var(--text)' }}>{info.headline}</div>
                <div style={{ fontSize: 12.5, lineHeight: 1.6, marginTop: 8, display: 'flex', gap: 7, alignItems: 'flex-start' }}>
                  <i className="ti ti-tool" style={{ color: 'var(--accent-blue)', marginTop: 2 }} />
                  <span><b>Recommended:</b> {info.action}</span>
                </div>
              </div>
            </div>
          </Card>

          {/* ── Live telemetry ── */}
          {sensors.length > 0 && (
            <Card title={<><i className="ti ti-activity" /> Live Telemetry <span className="pill pill-green" style={{ marginLeft: 6, fontSize: 10 }}>● streaming</span></>} className="section-gap">
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(120px, 1fr))', gap: 8 }}>
                {sensors.map((s) => <StatTile key={s.signal || s.name} label={s.name} value={s.value} unit={s.unit} status={s.status} />)}
              </div>
            </Card>
          )}

          {/* ── Subsystem health ── */}
          {components.length > 0 && (
            <Card title={<><i className="ti ti-stack-2" /> Subsystem Health</>} className="section-gap">
              <div style={{ display: 'flex', flexDirection: 'column', gap: 9 }}>
                {components.map((c) => {
                  const h = typeof c.health === 'number' ? c.health : null
                  const col = statusColor(c.status)
                  return (
                    <div key={c.name} style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                      <div style={{ width: 150, fontSize: 12, textTransform: 'capitalize', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c.name}</div>
                      <div style={{ flex: 1, height: 8, borderRadius: 4, background: 'var(--surface2)', overflow: 'hidden' }}>
                        <div style={{ width: `${Math.round((h ?? 0) * 100)}%`, height: '100%', background: col, transition: 'width .5s' }} />
                      </div>
                      <div className="mono" style={{ width: 42, textAlign: 'right', fontSize: 12, color: col }}>{h == null ? '—' : Math.round(h * 100) + '%'}</div>
                    </div>
                  )
                })}
              </div>
            </Card>
          )}

          {/* ── Forecast trajectories ── */}
          <div className="grid-2 section-gap">
            {sigKeys.map((k, i) => {
              const s = sensorFor(k)
              return (
                <Card key={k} title={<><i className="ti ti-chart-line" /> {localName(k)}
                  {s && <span className="mono" style={{ marginLeft: 'auto', fontSize: 11, color: statusColor(s.status) }}>now {fmtVal(s.value)}{s.unit}</span>}</>}>
                  <SignalChart trajectory={traj} sigKey={k} color={SERIES_COLORS[i % SERIES_COLORS.length]} unit={s?.unit || ''} />
                </Card>
              )
            })}
            <Card title={<><i className="ti ti-activity-heartbeat" /> Overall Health</>}>
              <SignalChart trajectory={traj.map((p) => ({ t: p.t, health: p.health * 100 }))} sigKey="health" color="#16a34a" unit="%" />
            </Card>
          </div>

          {/* ── Remaining useful life ── */}
          <Card title={<><i className="ti ti-clock-bolt" /> Remaining Useful Life — time to limit</>}>
            {info.rul.length === 0
              ? <Empty label="No subsystem projected to reach a limit within this horizon." icon="ti-shield-check" />
              : (
                <div className="event-list">
                  {info.rul.map((r) => {
                    const color = r.minutes <= hmin * 0.34 ? 'var(--accent-red)' : 'var(--accent-amber)'
                    return (
                      <div key={r.component} className="event-item" style={{ borderLeft: `3px solid ${color}` }}>
                        <div className="event-icon" style={{ background: `${color}22`, color }}><i className="ti ti-trending-down" /></div>
                        <div className="event-body">
                          <div className="event-title" style={{ textTransform: 'capitalize' }}>{r.component.replace(/_/g, ' ')}</div>
                          <div className="event-meta">Projected to reach its limit within the horizon</div>
                        </div>
                        <div className="event-time" style={{ color }}>{fmtMins(r.minutes)}</div>
                      </div>
                    )
                  })}
                </div>
              )}
          </Card>
        </>
      )}
    </>
  )
}

/** Facility (graph-fed) twins: a findings-driven outlook — no physics forecast,
 *  but grounded in the twin's real live findings + stats. */
function FacilityForecast({ tenant, twin }) {
  const { data: stats } = usePolling(() => api.stats(tenant), 3000, [tenant], { skip: !tenant })
  const { data: fData } = usePolling(() => api.findings(tenant, 20), 3000, [tenant], { skip: !tenant })
  const findings = fData?.findings || []
  const sev = stats?.finding_severity || {}
  const crit = sev.critical || 0, warn = sev.warning || 0
  const score = Math.min(100, crit * 25 + warn * 8 + (sev.info || 0) * 2)
  const band = score >= 60 ? { label: 'HIGH', color: 'var(--accent-red)' }
    : score >= 30 ? { label: 'ELEVATED', color: 'var(--accent-amber)' }
      : { label: score ? 'LOW' : 'NOMINAL', color: 'var(--accent-green)' }

  const worst = findings.find((f) => f.severity === 'critical') || findings.find((f) => f.severity === 'warning') || findings[0]
  const headline = crit
    ? `${crit} critical finding${crit > 1 ? 's' : ''} active — the twin is trending toward a service event; address the critical items first.`
    : warn
      ? `${warn} warning-level finding${warn > 1 ? 's' : ''} active — degradation is developing; plan maintenance before it escalates.`
      : findings.length
        ? 'Only informational findings — the facility is operating normally.'
        : 'No active findings — the facility is nominal. Start the feed to exercise the twin.'
  const action = worst ? `Prioritise: ${worst.displayName || worst.message || 'the top finding'}.` : 'No action needed — keep monitoring.'

  // A heuristic urgency for each finding so the outlook has a "time-to" feel.
  const urgency = (s) => s === 'critical' ? ['~2 days', 'var(--accent-red)', 'HIGH'] : s === 'warning' ? ['~2 weeks', 'var(--accent-amber)', 'MEDIUM'] : ['~30 days', 'var(--accent-blue)', 'LOW']

  return (
    <>
      <Card className="section-gap" style={{ borderColor: `${band.color}55`, background: `${band.color}0d` }}>
        <div style={{ display: 'flex', gap: 20, flexWrap: 'wrap', alignItems: 'center' }}>
          <div style={{ textAlign: 'center', minWidth: 96 }}>
            <div className="mono" style={{ fontSize: 34, fontWeight: 700, color: band.color, lineHeight: 1 }}>{score}</div>
            <div style={{ fontSize: 11, color: band.color, fontWeight: 600, marginTop: 3 }}>{band.label}</div>
            <div style={{ fontSize: 10, color: 'var(--muted)' }}>risk score</div>
          </div>
          <div style={{ flex: 1, minWidth: 260 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 7, marginBottom: 6 }}>
              <i className="ti ti-bulb" style={{ color: band.color }} /><b style={{ fontSize: 13 }}>Inference</b>
              <span className="pill pill-surface" style={{ fontSize: 10, marginLeft: 'auto' }}>{stats?.total_findings ?? 0} findings · {stats?.total_entities ?? 0} entities</span>
            </div>
            <div style={{ fontSize: 12.5, lineHeight: 1.6 }}>{headline}</div>
            <div style={{ fontSize: 12.5, lineHeight: 1.6, marginTop: 8, display: 'flex', gap: 7, alignItems: 'flex-start' }}>
              <i className="ti ti-tool" style={{ color: 'var(--accent-blue)', marginTop: 2 }} />
              <span><b>Recommended:</b> {action}</span>
            </div>
          </div>
        </div>
      </Card>

      <Card title={<><i className="ti ti-trending-down" /> Predicted issues — from live findings</>}>
        {findings.length === 0
          ? <Empty label="No findings yet. Start the feed to generate a live outlook." icon="ti-shield-check" />
          : (
            <div className="event-list">
              {findings.map((f) => {
                const [when, color, tag] = urgency(f.severity)
                return (
                  <div key={f.id || f.displayName} className="event-item" style={{ borderLeft: `3px solid ${color}` }}>
                    <div className="event-icon" style={{ background: `${color}22`, color }}><i className="ti ti-alert-triangle" /></div>
                    <div className="event-body">
                      <div className="event-title">{f.displayName || f.message}</div>
                      <div className="event-meta">{f.severity} · service window {when}</div>
                    </div>
                    <div style={{ textAlign: 'right' }}>
                      <div className="event-time">{when}</div>
                      <div style={{ fontSize: 10, marginTop: 2, color }}>{tag}</div>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
      </Card>
    </>
  )
}

export default function Predict() {
  const { activeTenant, activeTwin } = useTwin()
  if (!activeTenant) return <NoTwin />

  // A simulated twin has no registry record, so `activeTwin` is null and this
  // used to fall through to FacilityForecast — which polled /stats and /findings
  // for a tenant no organisation owns, i.e. a 403 every three seconds for as long
  // as the panel stayed open.
  if (isSimTenant(activeTenant)) {
    return <SimTwinNotice tenant={activeTenant} title="Prediction"
                          what="Forecasting" icon="ti-chart-dots-3" />
  }

  const machine = isMachineDomain(activeTwin?.domain)
  return (
    <div className="panel">
      <PanelHeader title="Prediction"
        subtitle={`${activeTwin?.name || activeTenant} · ${machine ? 'physics-based forecast, RUL + inference' : 'findings-driven outlook from the live graph'}`} />
      {machine
        ? <MachineForecast tenant={activeTenant} twin={activeTwin} />
        : <FacilityForecast tenant={activeTenant} twin={activeTwin} />}
    </div>
  )
}
