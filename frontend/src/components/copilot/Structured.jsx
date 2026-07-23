/**
 * Structured.jsx — renderers for the agents that return typed documents rather
 * than prose (work orders, parts lists, incident reports, repair procedures).
 *
 * These come back as validated schemas, so they are laid out as the documents
 * they are — a technician should be able to read a work order off the screen,
 * not a JSON blob.
 */

const money = (n) =>
  typeof n === 'number' && n > 0
    ? n.toLocaleString(undefined, { style: 'currency', currency: 'USD', maximumFractionDigits: 0 })
    : '—'

function Field({ label, value, wide }) {
  if (value === undefined || value === null || value === '') return null
  return (
    <div className={`doc-field ${wide ? 'doc-field-wide' : ''}`}>
      <div className="doc-field-label">{label}</div>
      <div className="doc-field-value">{value}</div>
    </div>
  )
}

const PRIORITY_PILL = {
  AOG: 'pill-red', Critical: 'pill-red', Urgent: 'pill-red',
  Routine: 'pill-blue', Scheduled: 'pill-surface',
}

// ── Work order ────────────────────────────────────────────────────────────

export function WorkOrderView({ wo }) {
  if (!wo) return null
  return (
    <div className="doc">
      <div className="doc-head">
        <div className="doc-id">{wo.wo_number}</div>
        <span className={`pill ${PRIORITY_PILL[wo.priority] || 'pill-surface'}`}>{wo.priority}</span>
        <span className="doc-head-spacer" />
        <span className="hint">{wo.estimated_hours}h estimated</span>
      </div>

      <div className="doc-fields">
        <Field label="System / chapter" value={wo.ata_chapter} />
        <Field label="Compliance" value={wo.compliance_ref} />
        <Field label="Sign-off" value={wo.sign_off} />
        <Field label="Fault" value={wo.fault_description} wide />
        <Field label="Root cause" value={wo.root_cause} wide />
      </div>

      <div className="doc-section-title">Procedure</div>
      <ol className="doc-steps">
        {(wo.steps || []).map((s) => (
          <li key={s.step} className="doc-step">
            <div className="doc-step-action">{s.action}</div>
            {s.criteria && (
              <div className="doc-step-meta">
                <i className="ti ti-checkbox" /> <span>{s.criteria}</span>
              </div>
            )}
            {s.safety && (
              <div className="doc-step-meta doc-step-safety">
                <i className="ti ti-alert-triangle" /> <span>{s.safety}</span>
              </div>
            )}
          </li>
        ))}
      </ol>

      {(wo.parts_required || []).length > 0 && (
        <>
          <div className="doc-section-title">Parts &amp; tooling</div>
          <ul className="doc-parts">
            {wo.parts_required.map((p, i) => <li key={i}>{p}</li>)}
          </ul>
        </>
      )}
    </div>
  )
}

// ── Parts procurement ─────────────────────────────────────────────────────

export function ProcurementView({ list }) {
  if (!list) return null
  return (
    <div className="doc">
      <div className="doc-head">
        <div className="doc-id">{list.work_order_ref}</div>
        <span className={`pill ${list.critical_parts_available ? 'pill-green' : 'pill-amber'}`}>
          {list.critical_parts_available ? 'Critical parts available <24h' : 'Critical parts NOT confirmed'}
        </span>
        <span className="doc-head-spacer" />
        <span className="doc-total">{money(list.total_estimated_cost)}</span>
      </div>

      <div className="doc-table-wrap">
        <table className="doc-table">
          <thead>
            <tr>
              <th>Part number</th><th>Description</th>
              <th className="num">Qty</th><th className="num">Unit</th>
              <th>Lead time</th><th>Source</th>
            </tr>
          </thead>
          <tbody>
            {(list.parts || []).map((p, i) => (
              <tr key={i}>
                <td className="mono">{p.part_number}</td>
                <td>{p.description}</td>
                <td className="num">{p.quantity}</td>
                <td className="num">{money(p.estimated_cost_usd)}</td>
                <td>{p.lead_time}</td>
                <td>{p.source}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {list.notes && <div className="doc-note"><i className="ti ti-info-circle" /> {list.notes}</div>}
    </div>
  )
}

// ── Incident report ───────────────────────────────────────────────────────

export function IncidentReportView({ report }) {
  if (!report) return null
  return (
    <div className="doc">
      <div className="doc-head">
        <div className="doc-id">{report.report_id}</div>
        <span className="doc-head-spacer" />
        <span className="hint">{report.timestamp}</span>
      </div>

      <div className="doc-fields">
        <Field label="Classification" value={report.classification} />
        <Field label="Asset" value={report.asset} />
        <Field label="Probable cause" value={report.probable_cause} wide />
        <Field label="Physics evidence" value={report.physics_evidence} wide />
      </div>

      {(report.symptoms || []).length > 0 && (
        <>
          <div className="doc-section-title">Observed symptoms</div>
          <ul className="doc-parts">{report.symptoms.map((s, i) => <li key={i}>{s}</li>)}</ul>
        </>
      )}

      <div className="doc-section-title">Corrective action</div>
      <div className="doc-pre">{report.corrective_action}</div>

      <div className="doc-fields">
        <Field label="Regulatory closure" value={report.regulatory_closure} wide />
        <Field label="Return to service" value={report.return_to_service} wide />
      </div>
    </div>
  )
}

// ── Maintenance procedure (interactive trainer) ───────────────────────────

/**
 * The procedure agent returns each step WITH the consequence of skipping it and
 * of doing it out of order. That is the whole point of the trainer, so those
 * consequences are rendered inline rather than hidden behind a toggle — and
 * prerequisites are shown so wrong ordering is visible before it happens.
 */
export function ProcedureView({ procedure, checked, onToggle }) {
  if (!procedure) return null
  const done = checked || new Set()
  const byId = Object.fromEntries((procedure.steps || []).map((s) => [s.id, s]))

  return (
    <div className="doc">
      <div className="doc-head">
        <div className="doc-id">{procedure.title}</div>
        <span className="doc-head-spacer" />
        <span className="hint">{(procedure.steps || []).length} steps</span>
      </div>
      <p className="md-p">{procedure.summary}</p>

      <ol className="proc-steps">
        {(procedure.steps || []).map((s) => {
          const unmet = (s.requires || []).filter((r) => !done.has(r))
          const blocked = unmet.length > 0 && !done.has(s.id)
          return (
            <li key={s.id} className={`proc-step ${done.has(s.id) ? 'is-done' : ''} ${blocked ? 'is-blocked' : ''}`}>
              <div className="proc-step-head">
                <button
                  className={`proc-check ${done.has(s.id) ? 'on' : ''}`}
                  onClick={() => onToggle?.(s.id)}
                  aria-label={done.has(s.id) ? 'Mark not done' : 'Mark done'}
                >
                  <i className={`ti ${done.has(s.id) ? 'ti-check' : ''}`} />
                </button>
                <span className="proc-step-id">{s.id}</span>
                <span className="proc-step-title">{s.title}</span>
                {s.safety && <span className="pill pill-red" style={{ fontSize: 10 }}>SAFETY</span>}
                {blocked && (
                  <span className="pill pill-amber" style={{ fontSize: 10 }}
                        title={`Requires ${unmet.join(', ')}`}>
                    blocked by {unmet.join(', ')}
                  </span>
                )}
              </div>
              <div className="proc-step-action">{s.action}</div>
              {s.criteria && (
                <div className="doc-step-meta"><i className="ti ti-checkbox" /> <span>{s.criteria}</span></div>
              )}
              <div className="proc-consequences">
                <div className="proc-consequence">
                  <span className="proc-consequence-label">If skipped</span>
                  {s.skip_consequence}
                </div>
                {(s.requires || []).length > 0 && (
                  <div className="proc-consequence">
                    <span className="proc-consequence-label">
                      If done before {s.requires.map((r) => byId[r]?.title || r).join(', ')}
                    </span>
                    {s.wrong_order_consequence}
                  </div>
                )}
              </div>
            </li>
          )
        })}
      </ol>

      <div className="doc-section-title">Success criteria</div>
      <p className="md-p">{procedure.success_criteria}</p>

      {(procedure.common_mistakes || []).length > 0 && (
        <>
          <div className="doc-section-title">Common mistakes</div>
          <ul className="doc-parts">
            {procedure.common_mistakes.map((m, i) => <li key={i}>{m}</li>)}
          </ul>
        </>
      )}
    </div>
  )
}
