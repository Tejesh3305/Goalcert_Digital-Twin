import { useState } from 'react'
import { PanelHeader, Card } from '../components/ui/Card'
import MockBanner from '../components/ui/MockBanner'
import NoTwin from '../components/NoTwin'
import { useApi } from '../hooks/useApi'
import { useTwin } from '../context/TwinContext'
import { localName } from '../lib/format'
import api from '../api/client'

/** Simulation / Behaviour Engine.
 *  TOP (live): the generative dynamics archetypes + monitoring kinds that drive
 *  the twin — the real behaviour engine, from /schema/archetypes.
 *  BOTTOM (mock): what-if scenarios (still scripted — see PLACEHOLDERS.md). */
const SCENARIOS = {
  power: { icon: 'ti-plug-x', label: 'Power outage', body: 'Critical systems on UPS for ~4h. HVAC shuts down — space stays habitable ~2.5h. Estimated recovery via generator: ~22 min.', tags: ['High impact', '22 min recovery', '4h UPS buffer'] },
  cooling: { icon: 'ti-snowflake', label: 'Cooling loss', body: 'AHU-01 offline. Server room temperature crosses setpoint+3°C within ~12 min; Tier-C rule would fire, Tier-B flags deviation, diagnosis escalates to incident.', tags: ['Critical', 'Finding in ~12 min', 'Auto-diagnosis'] },
  surge: { icon: 'ti-users', label: 'Load surge', body: 'Occupancy 180% of forecast. Cooling demand exceeds capacity in ~35 min; recommend pre-cooling and load shedding.', tags: ['High', 'Capacity in 35 min', 'Pre-cool advised'] },
  staff: { icon: 'ti-user-minus', label: 'Staff shortage', body: 'No certified technician available for an emergency repair. Recommend activating a pre-qualified contractor (≈2h callout).', tags: ['Medium', 'Contractor 2h', 'Defer routine'] },
}

export default function Simulation() {
  const { activeTenant } = useTwin()
  const [key, setKey] = useState('cooling')
  const { data: cat } = useApi(() => api.archetypes(), [])
  if (!activeTenant) return <NoTwin />
  const s = SCENARIOS[key]
  const dyn = cat?.dynamics || []
  const kinds = cat?.monitoring_kinds || []

  return (
    <div className="panel">
      <PanelHeader title="Behaviour Engine & Simulation"
                   subtitle="The generative dynamics + monitoring archetypes that drive every twin" />

      {/* ── LIVE: the real behaviour engine catalog ── */}
      <Card title={<><i className="ti ti-engine" style={{ marginRight: 6 }} />Dynamics Archetypes ({dyn.length})</>}>
        <div className="muted" style={{ fontSize: 12, marginBottom: 10 }}>
          Each ontology class binds to one of these parameterized physics archetypes.
          The engine resolves them per entity and couples them through the graph.
        </div>
        <div className="grid-2">
          {dyn.map((a) => (
            <div key={a.archetype} style={{ border: '1px solid var(--border)', borderRadius: 8, padding: '8px 10px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
                <span className="pill pill-blue">{a.archetype}</span>
              </div>
              {a.produces?.length > 0 && (
                <div style={{ fontSize: 10.5, color: 'var(--muted)' }}>
                  produces: {a.produces.map(localName).join(', ')}
                </div>
              )}
              {a.consumes?.length > 0 && (
                <div style={{ fontSize: 10.5, color: 'var(--hint)' }}>
                  consumes: {a.consumes.join(' · ')}
                </div>
              )}
            </div>
          ))}
        </div>
        <div style={{ marginTop: 12 }}>
          <span style={{ fontSize: 12, color: 'var(--muted)' }}>Monitoring rule kinds: </span>
          {kinds.map((k) => <span key={k} className="pill pill-surface" style={{ marginRight: 4 }}>{k}</span>)}
        </div>
      </Card>

      {/* ── MOCK: what-if scenarios ── */}
      <div className="section-gap">
        <MockBanner what="Impact analyses below are scripted; a real engine forks the twin into a scenario tenant and replays behaviours over injected conditions." />
      </div>
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
