import { PanelHeader, KpiCard, Card } from '../components/ui/Card'
import { SeverityPill } from '../components/ui/Modal'
import NoTwin from '../components/NoTwin'
import FeedControls from '../components/FeedControls'
import { usePolling } from '../hooks/useApi'
import { useTwin } from '../context/TwinContext'
import { timeOf } from '../lib/format'
import api from '../api/client'

/** Operations overview: live KPIs, active findings, and risk by asset —
 *  all from the real graph for the active twin. */
export default function Dashboard() {
  const { activeTenant, activeTwin } = useTwin()
  const { data: stats } = usePolling(
    () => api.stats(activeTenant), 2500, [activeTenant], { skip: !activeTenant },
  )
  const { data: findingsData } = usePolling(
    () => api.findings(activeTenant, 8), 2500, [activeTenant], { skip: !activeTenant },
  )

  if (!activeTenant) return <NoTwin />

  const findings = findingsData?.findings || []
  const sev = stats?.finding_severity || {}
  const risk = computeRisk(sev)

  return (
    <div className="panel">
      <PanelHeader
        title="Operations Overview"
        subtitle={`${activeTwin?.name || activeTenant} · live from the graph`}
      >
        <FeedControls />
      </PanelHeader>

      <div className="grid-4 section-gap">
        <KpiCard label="Risk Score" value={risk.score}
                 valueColor={risk.color} change={risk.label} />
        <KpiCard label="Total Entities" value={stats?.total_entities ?? '—'}
                 change={`${stats?.entity_counts?.PhysicalAsset || 0} assets`} changeDir="up" />
        <KpiCard label="Active Findings" value={stats?.total_findings ?? '—'}
                 valueColor="var(--accent-amber)"
                 change={`${sev.critical || 0} critical`} changeDir={sev.critical ? 'down' : ''} />
        <KpiCard label="Change Log Events" value={stats?.changelog_events ?? '—'}
                 change="tamper-evident" />
      </div>

      <div className="grid-2">
        <Card title="Active Findings">
          {findings.length === 0
            ? <div className="muted" style={{ fontSize: 12 }}>No findings yet. Start the feed to generate some.</div>
            : (
              <div className="event-list">
                {findings.map((f) => (
                  <div key={f.id} className="event-item">
                    <div className={`event-icon ${sevIcon(f.severity)}`}>
                      <i className="ti ti-alert-triangle" />
                    </div>
                    <div className="event-body">
                      <div className="event-title">{f.displayName || f.message || 'Finding'}</div>
                      <div className="event-meta">
                        {f.behaviorId ? `${f.behaviorId} · ` : ''}tier {f.tier || '—'}
                      </div>
                    </div>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 4, alignItems: 'flex-end' }}>
                      <SeverityPill severity={f.severity} />
                      <span className="event-time">{timeOf(f.createdAt)}</span>
                    </div>
                  </div>
                ))}
              </div>
            )}
        </Card>

        <Card title="Findings by Severity">
          {Object.keys(sev).length === 0
            ? <div className="muted" style={{ fontSize: 12 }}>No findings recorded.</div>
            : (
              <div>
                {['critical', 'warning', 'info'].filter((k) => sev[k]).map((k) => {
                  const max = Math.max(...Object.values(sev), 1)
                  const pct = Math.round((sev[k] / max) * 100)
                  const color = { critical: 'var(--accent-red)', warning: 'var(--accent-amber)', info: 'var(--accent-blue)' }[k]
                  return (
                    <div key={k} className="bar-row">
                      <div className="bar-label">
                        <span style={{ textTransform: 'capitalize' }}>{k}</span>
                        <b style={{ color }}>{sev[k]}</b>
                      </div>
                      <div className="bar-track"><div className="bar-fill" style={{ width: `${pct}%`, background: color }} /></div>
                    </div>
                  )
                })}
              </div>
            )}
        </Card>
      </div>
    </div>
  )
}

const sevIcon = (s) => ({ critical: 'ev-crit', warning: 'ev-warn', info: 'ev-info' }[s] || 'ev-info')

function computeRisk(sev) {
  const score = Math.min(100, (sev.critical || 0) * 25 + (sev.warning || 0) * 8 + (sev.info || 0) * 2)
  if (score >= 70) return { score, label: 'HIGH', color: 'var(--accent-red)' }
  if (score >= 35) return { score, label: 'ELEVATED', color: 'var(--accent-amber)' }
  return { score, label: score ? 'LOW' : 'NOMINAL', color: 'var(--accent-green)' }
}
