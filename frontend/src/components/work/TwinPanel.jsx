/**
 * TwinPanel.jsx — the twin, alongside the procedure you are running against it.
 *
 * A drill that teaches "isolate the asset and verify zero energy" while showing
 * nothing but text is a quiz. Run it next to the actual machine — its live
 * findings, its health, its 3-D model — and the same steps become a rehearsal on
 * the plant the operator is about to touch. That is the whole reason this panel
 * exists, and why it is present in BOTH modes:
 *
 *   fixing a real fault   the task's own tenant and asset, with the work order
 *                         the twin's agents wrote for it
 *   practising a drill    the twin currently open, so the procedure still runs
 *                         against something real rather than against nothing
 *
 * WHICH TWIN. A task carries `tenant_id`, and that is authoritative — the fault
 * is on THAT plant, not on whatever the operator last had open. Only when the
 * task has no tenant (a job raised by hand) does this fall back to the active
 * twin, and it says so rather than implying the two are the same.
 *
 * Everything here degrades. No tenant, no graph, no agents — each is a missing
 * section with an explanation, never a blank panel and never a crash. The
 * procedure does not depend on any of it.
 */

import { useState } from 'react'
import api from '../../api/client'
import Scene3D from '../Scene3D'
import TurbineModel from '../TurbineModel'
import { Card } from '../ui/Card'
import { useTwin } from '../../context/TwinContext'
import { usePolling } from '../../hooks/useApi'
import { domainMeta, hColor, isMachineDomain } from '../../lib/machine'

const SEV_CLS = { critical: 'badge-red', serious: 'badge-amber', warning: 'badge-amber', info: 'badge-blue' }

/** The 3-D model where the domain has one, a labelled hero card where it does not. */
function TwinVisual({ domain, name, latest, health }) {
  if (domain === 'turbine-engine') {
    return <TurbineModel latest={latest} health={health} height={230} />
  }
  if (isMachineDomain(domain)) {
    return <Scene3D domain={domain} machine={name} live={latest || {}} height={230} />
  }
  const meta = domainMeta(domain)
  return (
    <div className="twin-hero">
      <i className={`ti ${meta.icon}`} aria-hidden="true" />
      <div className="twin-hero-name">{name || meta.label}</div>
      <div className="twin-hero-domain">{meta.label}</div>
    </div>
  )
}

export default function TwinPanel({ task, order, onGenerate, generating, canGenerate }) {
  const { activeTenant, activeTwin } = useTwin()
  const [open, setOpen] = useState(true)

  // The task's tenant wins. See the note above on why.
  const tenant = task?.tenant_id || activeTenant
  const usingFallback = !task?.tenant_id && Boolean(activeTenant)
  const domain = activeTwin?.domain

  const { data: stats } = usePolling(
    () => api.stats(tenant), 5000, [tenant], { skip: !tenant })
  const { data: findingsData } = usePolling(
    () => api.findings(tenant, 6), 5000, [tenant], { skip: !tenant })
  const { data: runtime } = usePolling(
    () => api.twinRuntimeState(tenant), 5000, [tenant],
    { skip: !tenant || !isMachineDomain(domain) })

  const findings = findingsData?.findings || []
  const health = runtime?.health
  const latest = runtime?.latest

  const wo = order?.work_order
  const arSteps = order?.ar_steps || []

  return (
    <aside className={`twin-panel ${open ? '' : 'twin-panel-closed'}`}>
      <button className="twin-panel-toggle" onClick={() => setOpen(!open)}>
        <i className={`ti ${open ? 'ti-layout-sidebar-right-collapse' : 'ti-layout-sidebar-right-expand'}`}
           aria-hidden="true" />
        <span>{open ? 'Hide the twin' : 'Show the twin'}</span>
      </button>

      {!open ? null : !tenant ? (
        <Card title="The twin">
          <div className="empty" style={{ padding: 20 }}>
            <i className="ti ti-plug-connected-x" style={{ fontSize: 22, display: 'block', marginBottom: 8 }} />
            No twin is open, so there is nothing live to run this against.
            <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 7, lineHeight: 1.55 }}>
              Pick one from <b>Twins</b> and the procedure will run beside its
              live state.
            </div>
          </div>
        </Card>
      ) : (
        <>
          <Card title={<><i className="ti ti-box" aria-hidden="true" /> {activeTwin?.name || tenant}</>}>
            {usingFallback && (
              <div className="twin-fallback-note">
                This job carries no tenant of its own, so the procedure is running
                against the twin you have open.
              </div>
            )}

            <TwinVisual domain={domain} name={activeTwin?.name || task?.asset_name}
                        latest={latest} health={health} />

            <div className="twin-stats">
              <div className="twin-stat">
                <span className="twin-stat-k">Entities</span>
                <span className="twin-stat-v">{stats?.total_entities ?? '—'}</span>
              </div>
              <div className="twin-stat">
                <span className="twin-stat-k">Open findings</span>
                <span className="twin-stat-v" style={{ color: findings.length ? 'var(--accent-red)' : undefined }}>
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
          </Card>

          <Card title={<><i className="ti ti-alert-triangle" aria-hidden="true" /> Live findings</>}>
            {findings.length === 0 ? (
              <div className="empty" style={{ padding: 18 }}>Nothing open on this twin.</div>
            ) : (
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
          </Card>

          {/* The work order: what is wrong, why, and the ordered fix. Only ever
              present for a real task — a practice drill has no fault to write
              an order about. */}
          {task && (
            <Card title={<><i className="ti ti-clipboard-text" aria-hidden="true" /> Work order</>}>
              {wo ? (
                <>
                  <div className="twin-wo-line">
                    <span className="task-code">{wo.wo_number}</span>
                    <span className="nav-badge badge-amber">{wo.priority}</span>
                  </div>
                  {wo.root_cause && (
                    <>
                      <div className="wo-sub">Root cause</div>
                      <p className="twin-wo-text">{wo.root_cause}</p>
                    </>
                  )}
                  {(wo.steps || []).length > 0 && (
                    <>
                      <div className="wo-sub">Ordered fix</div>
                      <ol className="twin-wo-steps">
                        {wo.steps.map((s) => (
                          <li key={s.step}>
                            {s.action}
                            {s.criteria && <div className="twin-wo-crit">Accept: {s.criteria}</div>}
                          </li>
                        ))}
                      </ol>
                    </>
                  )}
                </>
              ) : (
                <div className="twin-wo-empty">
                  <p>
                    No work order written yet. Generating one runs an AI agent and
                    bills the account, so it is not done automatically.
                  </p>
                  {canGenerate && (
                    <button className="btn btn-primary btn-sm" disabled={generating}
                            onClick={onGenerate}>
                      {generating ? 'Writing…' : 'Generate work order'}
                    </button>
                  )}
                </div>
              )}

              {arSteps.length > 0 && (
                <>
                  <div className="wo-sub">On the asset</div>
                  <ol className="twin-wo-steps">
                    {arSteps.map((s) => (
                      <li key={s.n}>
                        <b>{s.title}</b>
                        <div className="twin-wo-crit">{s.instruction}</div>
                      </li>
                    ))}
                  </ol>
                </>
              )}
            </Card>
          )}
        </>
      )}
    </aside>
  )
}
