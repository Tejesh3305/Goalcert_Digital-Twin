import { useState } from 'react'
import { PanelHeader, Card } from '../components/ui/Card'
import MockBanner from '../components/ui/MockBanner'
import { Empty } from '../components/ui/States'
import NoTwin from '../components/NoTwin'
import { useTwin } from '../context/TwinContext'
import { usePolling } from '../hooks/useApi'
import { isMachineDomain } from '../lib/machine'
import { localName } from '../lib/format'
import api from '../api/client'

/** Predictive Intelligence.
 *  Machine-domain twins get a real physics forecast: per-signal projection charts,
 *  an overall health trajectory, and per-subsystem remaining-useful-life. Facility
 *  twins keep the illustrative mock. */

const MOCK = [
  { title: 'AHU-01 — bearing wear trend', meta: 'Remaining useful life: ~14 days · 68% confidence', sev: 'ev-warn', tag: 'HIGH', color: 'var(--accent-amber)', t: '14d' },
  { title: 'Chiller-02 — efficiency drift', meta: 'Remaining useful life: ~31 days · 44% confidence', sev: 'ev-info', tag: 'MEDIUM', color: 'var(--accent-blue)', t: '31d' },
  { title: 'Filter bank — clog risk', meta: 'Service recommended within 7 days · 72% confidence', sev: 'ev-warn', tag: 'HIGH', color: 'var(--accent-amber)', t: '7d' },
]

const HORIZONS = [['1 hour', 60], ['2 hours', 120], ['6 hours', 360], ['24 hours', 1440], ['3 days', 4320]]
const SERIES_COLORS = ['#e11d48', '#2563eb', '#0d9488', '#d97706', '#7c3aed']

/** A line chart of one signal (or health) across the forecast trajectory. */
function SignalChart({ trajectory, sigKey, color, height = 150 }) {
  if (!trajectory || trajectory.length < 2) return <div style={{ height }} />
  const W = 620, pad = 8
  const H = height
  const ts = trajectory.map((p) => p.t)
  const tMax = Math.max(...ts) || 1
  const vals = trajectory.map((p) => p[sigKey]).filter((v) => typeof v === 'number')
  const min = Math.min(...vals), max = Math.max(...vals)
  const span = max - min || 1
  const pts = trajectory.map((p) => {
    const x = pad + (p.t / tMax) * (W - 2 * pad)
    const y = pad + (1 - (p[sigKey] - min) / span) * (H - 2 * pad)
    return [x, y]
  })
  const d = pts.map((p) => p.join(',')).join(' ')
  const area = `${pad},${H - pad} ${d} ${W - pad},${H - pad}`
  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', height: 'auto', display: 'block' }}>
      <polyline points={area} fill={`${color}14`} stroke="none" />
      <polyline points={d} fill="none" stroke={color} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
      <line x1={pad} y1={H - pad} x2={W - pad} y2={H - pad} stroke="var(--border)" strokeWidth="1" />
      <text x={pad} y={pad + 9} fontSize="10" fill="var(--muted)" fontFamily="var(--mono)">{max.toFixed(1)}</text>
      <text x={pad} y={H - pad - 2} fontSize="10" fill="var(--muted)" fontFamily="var(--mono)">{min.toFixed(1)}</text>
    </svg>
  )
}

function MachineForecast({ tenant }) {
  const [hmin, setHmin] = useState(120)
  const { data: pred } = usePolling(() => api.twinPredict(tenant, hmin, 60), 6000, [tenant, hmin], { skip: !tenant })

  const sev = pred?.severity || 'nominal'
  const sevColor = { critical: 'var(--accent-red)', warning: 'var(--accent-amber)', nominal: 'var(--ok)' }[sev]
  const traj = pred?.trajectory || []
  const rul = pred?.rul || []
  const sigKeys = traj.length ? Object.keys(traj[0]).filter((k) => k !== 't' && k !== 'health') : []

  return (
    <>
      <div className="panel-header" style={{ marginTop: 4 }}>
        <div className="panel-subtitle">Where each signal and subsystem is heading over the selected horizon.</div>
        <div className="panel-actions">
          <span className="muted" style={{ alignSelf: 'center', fontSize: 12 }}>Horizon:</span>
          <select className="select" style={{ width: 'auto' }} value={hmin} onChange={(e) => setHmin(Number(e.target.value))}>
            {HORIZONS.map(([label, m]) => <option key={m} value={m}>{label}</option>)}
          </select>
          {pred && <span className={`pill ${sev === 'critical' ? 'pill-red' : sev === 'warning' ? 'pill-amber' : 'pill-green'}`}
            style={{ alignSelf: 'center', textTransform: 'capitalize' }}>{sev}</span>}
        </div>
      </div>

      {!pred ? <Empty label="Computing forecast…" icon="ti-loader" /> : (
        <>
          <div className="grid-2 section-gap">
            {sigKeys.map((k, i) => (
              <Card key={k} title={<><i className="ti ti-chart-line" /> {localName(k)}</>}>
                <SignalChart trajectory={traj} sigKey={k} color={SERIES_COLORS[i % SERIES_COLORS.length]} />
              </Card>
            ))}
            <Card title={<><i className="ti ti-activity-heartbeat" /> Overall Health</>}>
              <SignalChart trajectory={traj.map((p) => ({ t: p.t, health: p.health * 100 }))} sigKey="health" color="#16a34a" />
            </Card>
          </div>

          <Card title={<><i className="ti ti-clock-bolt" /> Remaining Useful Life — time to limit</>}>
            {rul.length === 0
              ? <Empty label="No subsystem projected to reach a limit within this horizon." icon="ti-shield-check" />
              : (
                <div className="event-list">
                  {rul.map((r) => {
                    const mins = r.minutes
                    const label = mins >= 60 ? `~${(mins / 60).toFixed(1)} h` : `~${Math.round(mins)} min`
                    const color = mins <= hmin * 0.34 ? 'var(--accent-red)' : 'var(--accent-amber)'
                    return (
                      <div key={r.component} className="event-item" style={{ borderLeft: `3px solid ${color}` }}>
                        <div className="event-icon" style={{ background: `${color}22`, color }}><i className="ti ti-trending-down" /></div>
                        <div className="event-body">
                          <div className="event-title" style={{ textTransform: 'capitalize' }}>{r.component.replace(/_/g, ' ')}</div>
                          <div className="event-meta">Projected to reach its limit within the horizon</div>
                        </div>
                        <div className="event-time" style={{ color }}>{label}</div>
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

export default function Predict() {
  const { activeTenant, activeTwin } = useTwin()
  if (!activeTenant) return <NoTwin />

  if (isMachineDomain(activeTwin?.domain)) {
    return (
      <div className="panel">
        <PanelHeader title="Prediction"
          subtitle={`${activeTwin.name} · physics-based forecast + remaining-useful-life`} />
        <MachineForecast tenant={activeTenant} />
      </div>
    )
  }

  return (
    <div className="panel">
      <PanelHeader title="Predictive Intelligence" subtitle="AI-generated failure forecasts · next 30 days" />
      <MockBanner what="Forecasts are illustrative; a learned model would write predicted RUL onto FailureMode entities through the same write path." />
      <div className="event-list">
        {MOCK.map((f) => (
          <div key={f.title} className="event-item" style={{ borderLeft: `3px solid ${f.color}` }}>
            <div className={`event-icon ${f.sev}`}><i className="ti ti-trending-down" /></div>
            <div className="event-body">
              <div className="event-title">{f.title}</div>
              <div className="event-meta">{f.meta}</div>
            </div>
            <div style={{ textAlign: 'right' }}>
              <div className="event-time">{f.t}</div>
              <div style={{ fontSize: 10, marginTop: 2, color: f.color }}>{f.tag}</div>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
