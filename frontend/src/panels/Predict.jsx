import { PanelHeader, Card } from '../components/ui/Card'
import MockBanner from '../components/ui/MockBanner'
import { Empty } from '../components/ui/States'
import NoTwin from '../components/NoTwin'
import { useTwin } from '../context/TwinContext'
import { usePolling } from '../hooks/useApi'
import { isMachineDomain, statusColor } from '../lib/machine'
import api from '../api/client'

/** Predictive Intelligence.
 *  Machine-domain twins (turbine / EDM / tram) get a REAL forecast from the
 *  physics runtime's prediction engine: forward health trajectory + per-subsystem
 *  remaining-useful-life (time-to-limit). Facility twins keep the illustrative
 *  mock until a learned model writes predicted RUL onto FailureMode entities. */

const MOCK = [
  { title: 'AHU-01 — bearing wear trend', meta: 'Remaining useful life: ~14 days · 68% confidence', sev: 'ev-warn', tag: 'HIGH', color: 'var(--accent-amber)', t: '14d' },
  { title: 'Chiller-02 — efficiency drift', meta: 'Remaining useful life: ~31 days · 44% confidence', sev: 'ev-info', tag: 'MEDIUM', color: 'var(--accent-blue)', t: '31d' },
  { title: 'Filter bank — clog risk', meta: 'Service recommended within 7 days · 72% confidence', sev: 'ev-warn', tag: 'HIGH', color: 'var(--accent-amber)', t: '7d' },
]

function HealthTrajectory({ trajectory }) {
  if (!trajectory || trajectory.length < 2) return null
  const W = 640, H = 120, pad = 6
  const ts = trajectory.map((p) => p.t)
  const tMax = Math.max(...ts) || 1
  const pts = trajectory.map((p) => {
    const x = pad + (p.t / tMax) * (W - 2 * pad)
    const y = pad + (1 - Math.max(0, Math.min(1, p.health))) * (H - 2 * pad)
    return [x, y]
  })
  const d = pts.map((p) => p.join(',')).join(' ')
  const areaD = `${pad},${H - pad} ${d} ${W - pad},${H - pad}`
  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', height: 'auto', display: 'block' }}>
      <polyline points={areaD} fill="var(--brand-soft)" stroke="none" />
      <polyline points={d} fill="none" stroke="var(--brand)" strokeWidth="2"
        strokeLinejoin="round" strokeLinecap="round" />
      <line x1={pad} y1={H - pad} x2={W - pad} y2={H - pad} stroke="var(--border)" strokeWidth="1" />
    </svg>
  )
}

function MachineForecast({ tenant }) {
  const { data: pred } = usePolling(
    () => api.twinPredict(tenant, 120, 60), 5000, [tenant], { skip: !tenant })

  if (!pred) return <Empty label="Computing forecast…" icon="ti-loader" />
  const sev = pred.severity || 'nominal'
  const sevColor = { critical: 'var(--accent-red)', warning: 'var(--accent-amber)', nominal: 'var(--ok)' }[sev]
  const rul = pred.rul || []

  return (
    <>
      <div className="section-gap" style={{ display: 'flex', alignItems: 'center', gap: 12,
        padding: '12px 16px', borderRadius: 12, background: 'var(--surface2)',
        border: `1px solid ${sevColor}55` }}>
        <i className="ti ti-chart-dots-3" style={{ fontSize: 22, color: sevColor }} />
        <div>
          <div style={{ fontWeight: 600, color: sevColor, textTransform: 'capitalize' }}>{sev}</div>
          <div className="muted" style={{ fontSize: 12 }}>
            Forecast over next {pred.horizon_min ?? 120} min · {rul.length} subsystem(s) trending to limit
          </div>
        </div>
      </div>

      <Card title={<><i className="ti ti-activity-heartbeat" /> Projected Health Trajectory</>} className="section-gap">
        <HealthTrajectory trajectory={pred.trajectory} />
        <div className="muted" style={{ fontSize: 11, marginTop: 4 }}>Overall health, 100% (top) → 0% (bottom)</div>
      </Card>

      <Card title={<><i className="ti ti-hourglass" /> Remaining Useful Life — time to limit</>}>
        {rul.length === 0
          ? <Empty label="No subsystem projected to reach a limit within the horizon." icon="ti-shield-check" />
          : (
            <div className="event-list">
              {rul.map((r) => {
                const mins = r.minutes
                const label = mins >= 60 ? `~${(mins / 60).toFixed(1)} h` : `~${Math.round(mins)} min`
                const color = mins <= pred.horizon_min * 0.34 ? 'var(--accent-red)' : 'var(--accent-amber)'
                return (
                  <div key={r.component} className="event-item" style={{ borderLeft: `3px solid ${color}` }}>
                    <div className="event-icon" style={{ background: `${color}22`, color }}>
                      <i className="ti ti-trending-down" />
                    </div>
                    <div className="event-body">
                      <div className="event-title" style={{ textTransform: 'capitalize' }}>{r.component.replace(/_/g, ' ')}</div>
                      <div className="event-meta">Projected to reach its limit</div>
                    </div>
                    <div style={{ textAlign: 'right' }}>
                      <div className="event-time" style={{ color }}>{label}</div>
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

  if (isMachineDomain(activeTwin?.domain)) {
    return (
      <div className="panel">
        <PanelHeader title="Predictive Intelligence"
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
