import { PanelHeader, Card } from '../components/ui/Card'
import MockBanner from '../components/ui/MockBanner'
import NoTwin from '../components/NoTwin'
import { useTwin } from '../context/TwinContext'

/** Predictive Intelligence — MOCK. Illustrative RUL forecasts. Real version
 *  needs a Tier-B/ML model writing FailureMode + Recommendation entities with
 *  predicted remaining-useful-life (see PLACEHOLDERS.md → "Predictive engine"). */
const FORECASTS = [
  { title: 'AHU-01 — bearing wear trend', meta: 'Remaining useful life: ~14 days · 68% confidence', sev: 'ev-warn', tag: 'HIGH', color: 'var(--accent-amber)', t: '14d' },
  { title: 'Chiller-02 — efficiency drift', meta: 'Remaining useful life: ~31 days · 44% confidence', sev: 'ev-info', tag: 'MEDIUM', color: 'var(--accent-blue)', t: '31d' },
  { title: 'Filter bank — clog risk', meta: 'Service recommended within 7 days · 72% confidence', sev: 'ev-warn', tag: 'HIGH', color: 'var(--accent-amber)', t: '7d' },
]

export default function Predict() {
  const { activeTenant } = useTwin()
  if (!activeTenant) return <NoTwin />
  return (
    <div className="panel">
      <PanelHeader title="Predictive Intelligence" subtitle="AI-generated failure forecasts · next 30 days" />
      <MockBanner what="Forecasts are illustrative; a learned model would write predicted RUL onto FailureMode entities through the same write path." />
      <div className="event-list">
        {FORECASTS.map((f) => (
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
