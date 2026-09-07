/**
 * TaskTwinCard.jsx — the machine behind the job, on the operator's dashboard.
 *
 * An operator reading "TSK-104 · bearing temperature rising" has to decide
 * whether to walk to the machine. A line of text does not help them; the asset
 * does. This card puts the twin's live 3-D model next to the selected job, with
 * its current health and open findings, and makes the model itself the door to
 * the full twin view.
 *
 * WHICH TWIN. The task's own `tenant_id` is authoritative — the fault is on THAT
 * plant, not on whatever the operator last had open. Clicking through therefore
 * SWITCHES the active twin before navigating, which is the behaviour that makes
 * "click the turbine, see what is wrong with the turbine" true rather than
 * approximately true. A task with no tenant (one raised by hand) falls back to
 * the open twin and says so.
 *
 * WHY IT IS NOT `TwinPanel`. That component is the full-height sidebar on the
 * drill and repair pages: findings, work order, AR steps, a collapse toggle. On
 * a dashboard the operator is choosing between jobs, not executing one, so this
 * is deliberately the short version — the model, the vitals, and the three
 * places worth going next.
 */

import { useNavigate } from 'react-router-dom'
import api from '../../api/client'
import Scene3D from '../Scene3D'
import TurbineModel from '../TurbineModel'
import { Card } from '../ui/Card'
import { useTwin } from '../../context/TwinContext'
import { usePolling } from '../../hooks/useApi'
import { domainMeta, hColor, isMachineDomain } from '../../lib/machine'
import '../../styles/work-dashboard.css'

const SEV_CLS = {
  critical: 'badge-red', serious: 'badge-amber', warning: 'badge-amber', info: 'badge-blue',
}

/** The 3-D model where the domain has one, a labelled hero card where it does not. */
function TwinVisual({ domain, name, latest, health, height = 200 }) {
  if (domain === 'turbine-engine') {
    return <TurbineModel latest={latest} health={health} height={height} />
  }
  if (isMachineDomain(domain)) {
    return <Scene3D domain={domain} machine={name} live={latest || {}} height={height} />
  }
  const meta = domainMeta(domain)
  return (
    <div className="twin-hero" style={{ minHeight: height }}>
      <i className={`ti ${meta.icon}`} aria-hidden="true" />
      <div className="twin-hero-name">{name || meta.label}</div>
      <div className="twin-hero-domain">{meta.label}</div>
    </div>
  )
}

export default function TaskTwinCard({ task, onRepair, onFix, onWorkOrder }) {
  const navigate = useNavigate()
  const { activeTenant, activeTwin, twins, setActiveTenant } = useTwin()

  const tenant = task?.tenant_id || activeTenant
  const twin = twins.find((t) => t.tenant_id === tenant) || activeTwin
  const domain = twin?.domain
  const usingFallback = Boolean(task) && !task.tenant_id && Boolean(activeTenant)

  const { data: stats } = usePolling(
    () => api.stats(tenant), 8000, [tenant], { skip: !tenant })
  const { data: findingsData } = usePolling(
    () => api.findings(tenant, 4), 8000, [tenant], { skip: !tenant })
  const { data: runtime } = usePolling(
    () => api.twinRuntimeState(tenant), 6000, [tenant],
    { skip: !tenant || !isMachineDomain(domain) })

  const findings = findingsData?.findings || []
  const health = runtime?.health

  /** Switch to the job's own twin, then open it. See the note above. */
  const openTwin = () => {
    if (tenant && tenant !== activeTenant) setActiveTenant(tenant)
    navigate('/twin')
  }

  if (!tenant) {
    return (
      <Card title="The machine">
        <div className="empty" style={{ padding: 22 }}>
          <i className="ti ti-plug-connected-x"
             style={{ fontSize: 22, display: 'block', marginBottom: 8 }} />
          No twin is open, so there is nothing live to show.
          <div style={{ marginTop: 10 }}>
            <button className="btn btn-primary btn-sm" onClick={() => navigate('/twin-library')}>
              Open the twin library
            </button>
          </div>
        </div>
      </Card>
    )
  }

  return (
    <Card
      title={<><i className="ti ti-box" aria-hidden="true" /> {twin?.name || tenant}</>}
      action={<button className="btn btn-ghost btn-sm" onClick={openTwin}>Open the twin</button>}
    >
      {usingFallback && (
        <div className="twin-fallback-note">
          This job carries no plant of its own, so this is the twin you have open.
        </div>
      )}

      {/* The model IS the link. An operator who has been told "the fault is in
          the turbine" should be able to click the turbine. */}
      <button className="twin-visual-link" onClick={openTwin}
              title={`Open ${twin?.name || tenant}`}>
        <TwinVisual domain={domain} name={twin?.name || task?.asset_name}
                    latest={runtime?.latest} health={health} />
        <span className="twin-visual-hint">
          <i className="ti ti-arrow-up-right" aria-hidden="true" /> Open the twin
        </span>
      </button>

      {task?.asset_name && (
        <div className="twin-asset-line">
          <i className="ti ti-target-arrow" aria-hidden="true" />
          <span>Your job is on <b>{task.asset_name}</b></span>
        </div>
      )}

      <div className="twin-stats">
        <div className="twin-stat">
          <span className="twin-stat-k">Entities</span>
          <span className="twin-stat-v">{stats?.total_entities ?? '—'}</span>
        </div>
        <div className="twin-stat">
          <span className="twin-stat-k">Open findings</span>
          <span className="twin-stat-v"
                style={{ color: findings.length ? 'var(--accent-red)' : undefined }}>
            {stats?.total_findings ?? findings.length}
          </span>
        </div>
        {health !== undefined && health !== null && (
          <div className="twin-stat">
            <span className="twin-stat-k">Health</span>
            <span className="twin-stat-v" style={{ color: hColor(health) }}>
              {Math.round(health * 100)}%
            </span>
          </div>
        )}
      </div>

      {findings.length > 0 && (
        <div className="twin-findings">
          {findings.map((f) => (
            <div key={f.id} className="twin-finding">
              <span className={`nav-badge ${SEV_CLS[f.severity] || 'badge-blue'}`}>
                {f.severity}
              </span>
              <span className="twin-finding-name">{f.displayName || f.message}</span>
            </div>
          ))}
        </div>
      )}

      {/* The three places worth going from here, in the order the shift needs
          them: what is it, how do I fix it, then do it for score. */}
      {task && (
        <div className="twin-card-actions">
          {onWorkOrder && (
            <button className="btn btn-ghost btn-sm" onClick={onWorkOrder}>
              <i className="ti ti-clipboard-text" aria-hidden="true" /> Work order
            </button>
          )}
          {onRepair && task.scenario_id && (
            <button className="btn btn-ghost btn-sm" onClick={onRepair}>
              <i className="ti ti-sparkles" aria-hidden="true" /> Repair with AI
            </button>
          )}
          {onFix && task.scenario_id && (
            <button className="btn btn-primary btn-sm" onClick={onFix}>
              <i className="ti ti-tool" aria-hidden="true" /> Fix the fault
            </button>
          )}
        </div>
      )}
    </Card>
  )
}
