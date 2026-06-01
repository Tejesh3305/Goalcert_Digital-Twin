import { useState } from 'react'
import { PanelHeader, Card } from '../components/ui/Card'
import MockBanner from '../components/ui/MockBanner'
import NoTwin from '../components/NoTwin'
import { useTwin } from '../context/TwinContext'

/** Simulation — MOCK. What-if scenarios with canned impact analysis. A real
 *  engine would branch the graph into a scenario tenant and run behaviours over
 *  injected conditions (see PLACEHOLDERS.md → "Simulation / what-if"). */
const SCENARIOS = {
  power: { icon: 'ti-plug-x', label: 'Power outage', body: 'Critical systems on UPS for ~4h. HVAC shuts down — space stays habitable ~2.5h. Estimated recovery via generator: ~22 min.', tags: ['High impact', '22 min recovery', '4h UPS buffer'] },
  cooling: { icon: 'ti-snowflake', label: 'Cooling loss', body: 'AHU-01 offline. Server room temperature crosses setpoint+3°C within ~12 min; Tier-C rule would fire, Tier-B flags deviation, diagnosis escalates to incident.', tags: ['Critical', 'Finding in ~12 min', 'Auto-diagnosis'] },
  surge: { icon: 'ti-users', label: 'Load surge', body: 'Occupancy 180% of forecast. Cooling demand exceeds capacity in ~35 min; recommend pre-cooling and load shedding.', tags: ['High', 'Capacity in 35 min', 'Pre-cool advised'] },
  staff: { icon: 'ti-user-minus', label: 'Staff shortage', body: 'No certified technician available for an emergency repair. Recommend activating a pre-qualified contractor (≈2h callout).', tags: ['Medium', 'Contractor 2h', 'Defer routine'] },
}

export default function Simulation() {
  const { activeTenant } = useTwin()
  const [key, setKey] = useState('cooling')
  if (!activeTenant) return <NoTwin />
  const s = SCENARIOS[key]
  return (
    <div className="panel">
      <PanelHeader title="Infrastructure Simulation" subtitle="What-if scenarios · impact modelling" />
      <MockBanner what="Impact analyses are scripted; a real engine forks the twin into a scenario tenant and replays behaviours over injected conditions." />
      <div className="grid-2 section-gap">
        {Object.entries(SCENARIOS).map(([k, v]) => (
          <button key={k} className="btn" style={{
            justifyContent: 'flex-start', padding: '10px 12px',
            borderColor: k === key ? 'var(--accent-amber)' : 'var(--border)',
            background: k === key ? 'rgba(224,150,47,.06)' : 'var(--surface)',
          }} onClick={() => setKey(k)}>
            <i className={`ti ${v.icon}`} /> {v.label}
          </button>
        ))}
      </div>
      <Card title={`Scenario: ${s.label}`}>
        <div style={{ fontSize: 12, lineHeight: 1.7, color: 'var(--muted)' }}>{s.body}</div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 12 }}>
          {s.tags.map((t) => <span key={t} className="pill pill-surface">{t}</span>)}
          <span className="pill pill-surface">Confidence ~74%</span>
        </div>
      </Card>
    </div>
  )
}
