import { useEffect, useState } from 'react'
import { PanelHeader, KpiCard, Card } from '../components/ui/Card'
import { SeverityPill } from '../components/ui/Modal'
import NoTwin from '../components/NoTwin'
import FeedControls from '../components/FeedControls'
import DemoTwin from '../components/DemoTwin'
import BimViewer from '../components/BimViewer'
import GlbViewer from '../components/GlbViewer'
import MachineDashboard from '../components/MachineDashboard'
import { usePolling } from '../hooks/useApi'
import { useTwin } from '../context/TwinContext'
import { isMachineDomain } from '../lib/machine'
import { timeOf } from '../lib/format'
import api from '../api/client'

/** The twin's home dashboard. Machine-domain twins (turbine / EDM / tram) get the
 *  unified live machine dashboard; facility twins keep the graph-driven overview. */
export default function Dashboard() {
  const { activeTenant, activeTwin } = useTwin()

  if (activeTenant && isMachineDomain(activeTwin?.domain)) {
    return <MachineDashboard tenant={activeTenant} domain={activeTwin.domain} name={activeTwin.name} />
  }

  return <FacilityDashboard />
}

function FacilityDashboard() {
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
  const ec = stats?.entity_counts || {}
  const risk = computeRisk(sev)

  return (
    <div className="panel">
      <PanelHeader
        title="Operations Overview"
        subtitle={`${activeTwin?.name || activeTenant} · live from the graph`}
      >
        <FeedControls />
      </PanelHeader>

      {/* Live interactive 3-D twin — the twin's real reconstructed scene from its
          own graph (live status overlaid); a demo scene only if it has no geometry. */}
      <Card title="3-D Twin" className="section-gap"
            action={<span className="pill pill-green" style={{ fontSize: 10 }}>● live</span>}
            style={{ padding: 0, overflow: 'hidden' }}>
        <TwinScene tenant={activeTenant} domain={activeTwin?.domain} name={activeTwin?.name} />
      </Card>

      <div className="grid-4 section-gap">
        <KpiCard label="Risk Score" value={risk.score}
                 valueColor={risk.color} change={risk.label} />
        <KpiCard label="Total Entities" value={stats?.total_entities ?? '—'}
                 change={`${ec.PhysicalAsset || 0} assets · ${ec.Location || 0} spaces`} changeDir="up" />
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

      {stats?.total_entities > 0 && (
      <div className="grid-2">
        <Card title="Entity Breakdown">
          <div>
            {[
              { label: 'Physical Assets', key: 'PhysicalAsset', icon: 'ti-cpu', color: 'var(--accent-blue)' },
              { label: 'Locations', key: 'Location', icon: 'ti-map-pin', color: 'var(--accent-teal)' },
              { label: 'Findings', key: 'Finding', icon: 'ti-alert-triangle', color: 'var(--accent-amber)' },
              { label: 'Incidents', key: 'Incident', icon: 'ti-urgent', color: 'var(--accent-red)' },
              { label: 'Processes', key: 'Process', icon: 'ti-arrows-right-left', color: 'var(--accent-purple)' },
              { label: 'Documents', key: 'Document', icon: 'ti-file-text', color: 'var(--accent-green)' },
              { label: 'Actors', key: 'Actor', icon: 'ti-users', color: 'var(--muted)' },
            ].filter(r => ec[r.key]).map(r => {
              const max = Math.max(...Object.values(ec).filter(v => typeof v === 'number'), 1)
              const pct = Math.round((ec[r.key] / max) * 100)
              return (
                <div key={r.key} className="bar-row">
                  <div className="bar-label">
                    <span><i className={`ti ${r.icon}`} style={{ color: r.color, marginRight: 6 }} />{r.label}</span>
                    <b style={{ color: r.color }}>{ec[r.key]}</b>
                  </div>
                  <div className="bar-track"><div className="bar-fill" style={{ width: `${pct}%`, background: r.color }} /></div>
                </div>
              )
            })}
          </div>
        </Card>

        <Card title="System Health">
          <div className="muted" style={{ fontSize: 12, marginBottom: 8 }}>Entity counts by taxonomy category from the live graph.</div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
            {[
              { label: 'Observations', key: 'Observation', icon: 'ti-chart-line' },
              { label: 'Capabilities', key: 'Capability', icon: 'ti-puzzle' },
              { label: 'Mobile Assets', key: 'MobileAsset', icon: 'ti-truck' },
              { label: 'Change Log', key: 'ChangeLog', icon: 'ti-history' },
            ].map(r => (
              <div key={r.key} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 0' }}>
                <i className={`ti ${r.icon}`} style={{ color: 'var(--muted)', fontSize: 16 }} />
                <span style={{ fontSize: 12, color: 'var(--muted)' }}>{r.label}</span>
                <b style={{ marginLeft: 'auto', fontFamily: 'var(--mono)', fontSize: 13 }}>{ec[r.key] || 0}</b>
              </div>
            ))}
          </div>
        </Card>
      </div>
      )}
    </div>
  )
}

/** The twin's real 3-D scene, rebuilt from its own graph geometry (live status
 *  overlaid by tenant). Falls back to a demo scene only if the twin has no
 *  geometry at all, so the hero never renders blank. */
function TwinScene({ tenant, domain, name }) {
  const [scene, setScene] = useState(undefined)  // undefined = loading, null = none
  useEffect(() => {
    if (!tenant) return
    let alive = true
    setScene(undefined)
    api.twinSceneByTenant(tenant)
      .then((r) => {
        const s = r.scene_result
        // Keep a scene if it has BIM geometry OR is a reconstructed-object scan.
        if (alive) setScene(s?.nodes?.length || s?.model_url ? s : null)
      })
      .catch(() => { if (alive) setScene(null) })
    return () => { alive = false }
  }, [tenant])

  if (scene === undefined) return <div className="bim-loading"><span className="spinner" /> Reconstructing scene…</div>
  // Object-scan twin: the reconstructed GLB is the model.
  if (scene?.model_url) return <GlbViewer url={scene.model_url} height={420} />
  if (scene) return <BimViewer scene={scene} tenant={tenant} />
  return <DemoTwin domain={domain} name={name} />   // graceful fallback: no geometry
}

const sevIcon = (s) => ({ critical: 'ev-crit', warning: 'ev-warn', info: 'ev-info' }[s] || 'ev-info')

function computeRisk(sev) {
  const score = Math.min(100, (sev.critical || 0) * 25 + (sev.warning || 0) * 8 + (sev.info || 0) * 2)
  if (score >= 70) return { score, label: 'HIGH', color: 'var(--accent-red)' }
  if (score >= 35) return { score, label: 'ELEVATED', color: 'var(--accent-amber)' }
  return { score, label: score ? 'LOW' : 'NOMINAL', color: 'var(--accent-green)' }
}
