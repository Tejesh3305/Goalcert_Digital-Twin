import { PanelHeader, Card } from '../components/ui/Card'
import { DemoNote } from '../components/ui/Modal'
import NoTwin from '../components/NoTwin'
import { usePolling } from '../hooks/useApi'
import { useTwin } from '../context/TwinContext'
import api from '../api/client'

/**
 * Twin Health — PARTIAL.
 *  - REAL: entity counts by category come from the graph; we derive a simple,
 *    honest "coverage" from what's actually present.
 *  - MOCK: the weighted completeness/accuracy score and the freshness rows for
 *    BIM / regulatory feeds are illustrative (see PLACEHOLDERS.md → "Twin
 *    health scoring").
 */
export default function TwinHealth() {
  const { activeTenant, activeTwin } = useTwin()
  const { data: stats } = usePolling(
    () => api.stats(activeTenant), 4000, [activeTenant], { skip: !activeTenant },
  )

  if (!activeTenant) return <NoTwin />

  const counts = stats?.entity_counts || {}
  const has = (k) => (counts[k] || 0) > 0
  // Honest, simple coverage: which structural pieces of a twin exist.
  const checks = [
    { label: 'Has assets', ok: has('PhysicalAsset') },
    { label: 'Has locations', ok: has('Location') },
    { label: 'Has observations / findings', ok: has('Finding') || has('Observation') },
    { label: 'Has reasoning output', ok: has('Document') || has('Incident') },
  ]
  const score = Math.round((checks.filter((c) => c.ok).length / checks.length) * 100)
  const color = score >= 75 ? 'var(--accent-green)' : score >= 50 ? 'var(--accent-amber)' : 'var(--accent-red)'

  return (
    <div className="panel">
      <PanelHeader
        title="Digital Twin Health"
        subtitle={`${activeTwin?.name || activeTenant} · completeness from the live graph`}
      />
      <div className="grid-2">
        <Card title="Structural Coverage" style={{ textAlign: 'center' }}>
          <div style={{ fontSize: 44, fontWeight: 600, fontFamily: 'var(--mono)', color }}>{score}%</div>
          <div style={{ marginTop: 14, textAlign: 'left' }}>
            {checks.map((c) => (
              <div key={c.label} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, padding: '4px 0' }}>
                <span className="muted">{c.label}</span>
                <i className={`ti ${c.ok ? 'ti-circle-check' : 'ti-circle-x'}`}
                   style={{ color: c.ok ? 'var(--accent-green)' : 'var(--hint)' }} />
              </div>
            ))}
          </div>
        </Card>

        <Card title={<>Data Freshness <DemoNote>partly illustrative</DemoNote></>}>
          <div style={{ fontSize: 12, lineHeight: 1.9 }}>
            <Row k="Telemetry feed" v={stats ? 'Live' : '—'} c="var(--ok)" />
            <Row k="Graph (Neo4j)" v={stats ? 'Live' : '—'} c="var(--ok)" />
            <Row k="Change log" v="Live" c="var(--ok)" />
            <Row k="BIM model" v="Updated 3 months ago" c="var(--accent-amber)" mock />
            <Row k="Regulatory standards" v="Updated 47 days ago" c="var(--accent-amber)" mock />
          </div>
        </Card>
      </div>
    </div>
  )
}

function Row({ k, v, c, mock }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between' }}>
      <span className="muted">{k} {mock && <span className="hint" style={{ fontSize: 9 }}>(demo)</span>}</span>
      <b style={{ color: c }}>{v}</b>
    </div>
  )
}
