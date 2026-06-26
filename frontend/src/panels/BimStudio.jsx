import { useState } from 'react'
import { PanelHeader, Card } from '../components/ui/Card'
import DemoTwin from '../components/DemoTwin'

/**
 * BIM Studio — 3-D showcase (demo scenes, no backend).
 *
 * The live 2-D→3-D plan-import pipeline has been split into its own dedicated
 * workspace (`apps/2d-to-3d`) where it's being perfected. Here in the platform
 * we preview the pre-baked archviz scenes per facility — fully offline.
 */
const FACILITIES = [
  ['Residential', 'residential'], ['Hospital', 'hospital'],
  ['Data Center', 'datacenter'], ['Office', 'office'], ['Factory', 'factory'],
]

export default function BimStudio() {
  const [facility, setFacility] = useState('residential')

  return (
    <div className="panel">
      <PanelHeader title="BIM Studio"
                   subtitle="3-D twin showcase — pre-rendered demo scenes per facility." />

      <div style={{ background: 'var(--surface2)', border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)',
                    padding: '10px 14px', marginBottom: 12, fontSize: 12.5, color: 'var(--muted)' }}>
        <i className="ti ti-info-circle" style={{ color: 'var(--accent-blue)', marginRight: 6 }} />
        Live 2-D plan → 3-D import now runs in the dedicated <b>apps/2d-to-3d</b> workspace
        (being perfected separately). This panel previews the demo scenes — no backend needed.
      </div>

      <div className="chat-quick" style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 12 }}>
        {FACILITIES.map(([label, key]) => (
          <div key={key} className="quick-chip"
               onClick={() => setFacility(key)}
               style={facility === key ? { borderColor: 'var(--accent-blue)', color: 'var(--accent-blue)' } : undefined}>
            {label}
          </div>
        ))}
      </div>

      <Card title="Interactive 3-D twin"
            action={<span className="pill pill-blue" style={{ fontSize: 10 }}>DEMO SCENE</span>}
            style={{ padding: 0, overflow: 'hidden' }}>
        <DemoTwin domain={facility} />
      </Card>
    </div>
  )
}
