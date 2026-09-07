/**
 * RepairPanel.jsx — "teach me how to fix THIS", as a component.
 *
 * This used to be the body of `panels/RepairWithAI.jsx` and nothing else could
 * reach it. Three places want it now — the assigned-fault page, the training
 * library, and the twin's own asset view — and the alternative to extracting it
 * was three copies of the build-and-render logic that would drift apart the
 * first time one of them fixed a bug.
 *
 * TWO SUBJECTS, ONE COMPONENT.
 *   a TASK       a real dispatched fault. Carries an asset, a severity, a
 *                behaviour id, and a graded drill to follow.
 *   a PROCEDURE  a library item being practised. No fault, nobody assigned it,
 *                and nothing closes when it is finished.
 * `subject` normalises the two so the render path below does not branch on
 * which one it got — only on whether a drill exists to offer at the end.
 *
 * THE GENERATION IS EXPLICIT, AND SLOW. The procedure agent is the heaviest in
 * the product (~70s) and it bills the account per call, so nothing here runs on
 * mount. The operator presses the button. That is the same rule the work order
 * follows, for the same reason — and it is what lets this component be mounted
 * on a dashboard without every page load costing money.
 *
 * LEARN, THEN BE TESTED. The graded drill stays a separate step. Reading a
 * procedure and being scored on it are different activities, and collapsing
 * them would mean the score measured reading comprehension of a page that was
 * still on screen.
 */

import { useCallback, useEffect, useState } from 'react'
import api from '../../api/client'
import { ProcedureView } from '../copilot/Structured'
import { Card } from '../ui/Card'
import { ErrorBox } from '../ui/States'
import { SeverityChip, ago } from './WorkBits'

/**
 * Normalise a task or a procedure into the one shape the agent call needs.
 * Exported because the pages build their own titles from the same fields.
 */
export function repairSubject({ task, procedure, twin }) {
  if (task) {
    return {
      kind: 'task',
      title: task.title,
      machine: task.asset_name || twin?.name || 'asset',
      domain: twin?.domain || '',
      fault: task.behavior_id || task.title,
      context: task.detail || '',
      severity: task.severity,
      code: task.code,
      assetName: task.asset_name,
      scenarioId: task.scenario_id,
      taskId: task.task_id,
      when: task.assigned_at || task.created_at,
      assignedBy: task.assigned_by_name,
    }
  }
  if (procedure) {
    return {
      kind: 'procedure',
      title: procedure.title,
      machine: twin?.name || procedure.domain || 'asset',
      domain: procedure.domain || twin?.domain || '',
      fault: procedure.id,
      context: procedure.summary || '',
      severity: '',
      code: '',
      assetName: '',
      scenarioId: procedure.id,
      taskId: '',
      when: '',
      assignedBy: '',
    }
  }
  return null
}

export default function RepairPanel({
  subject,
  order = null,
  onDrill = null,
  showFacts = true,
  title = 'The repair procedure',
}) {
  const [procedure, setProcedure] = useState(null)
  const [building, setBuilding] = useState(false)
  const [elapsed, setElapsed] = useState(0)
  const [checked, setChecked] = useState(new Set())
  const [error, setError] = useState(null)

  // Switching subject must not leave the previous fault's steps on screen — the
  // operator would tick off a procedure for a machine they are not standing at.
  useEffect(() => {
    setProcedure(null)
    setChecked(new Set())
    setError(null)
  }, [subject?.taskId, subject?.scenarioId, subject?.fault])

  // A visible timer, because a 70-second silence reads as a hang.
  useEffect(() => {
    if (!building) return undefined
    const started = Date.now()
    const id = setInterval(() => setElapsed((Date.now() - started) / 1000), 250)
    return () => clearInterval(id)
  }, [building])

  const build = useCallback(async () => {
    if (!subject) return
    setBuilding(true)
    setElapsed(0)
    setChecked(new Set())
    setError(null)
    try {
      const res = await api.copilot.procedure({
        machine: subject.machine,
        domain: subject.domain,
        fault: subject.fault,
        title: subject.title,
        context: subject.context,
      })
      setProcedure(res.procedure)
    } catch (e) {
      setError(e)
    } finally {
      setBuilding(false)
    }
  }, [subject])

  const toggle = (id) => setChecked((prev) => {
    const next = new Set(prev)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    return next
  })

  if (!subject) return null

  return (
    <>
      {showFacts && (
        <Card title={subject.title}>
          <div className="wo-facts">
            <div className="wo-fact">
              <span className="wo-fact-k">Fault</span>
              <span className="wo-fact-v">{subject.fault || '—'}</span>
            </div>
            {subject.severity && (
              <div className="wo-fact">
                <span className="wo-fact-k">Severity</span>
                <span className="wo-fact-v">{subject.severity}</span>
              </div>
            )}
            {subject.assignedBy && (
              <div className="wo-fact">
                <span className="wo-fact-k">Assigned by</span>
                <span className="wo-fact-v">{subject.assignedBy}</span>
              </div>
            )}
            {subject.when && (
              <div className="wo-fact">
                <span className="wo-fact-k">When</span>
                <span className="wo-fact-v">{ago(subject.when)}</span>
              </div>
            )}
          </div>
          {subject.context && <p className="wo-desc">{subject.context}</p>}
        </Card>
      )}

      {error && <ErrorBox error={error} />}

      <Card title={title}>
        {!procedure && !building && (
          <div className="empty" style={{ padding: 24 }}>
            <i className="ti ti-school" style={{ fontSize: 24, display: 'block', marginBottom: 9 }} />
            <div style={{ marginBottom: 9, fontWeight: 600 }}>
              Build the repair procedure for this fault
            </div>
            <div style={{ fontSize: 12, color: 'var(--muted)', maxWidth: 420,
                          margin: '0 auto 15px', lineHeight: 1.6 }}>
              The AI writes the correctly-ordered steps for <b>{subject.fault || 'this fault'}</b>,
              each with what goes wrong if you skip it and what goes wrong if you
              do it too early. It runs an agent and bills the account, and it is
              the slowest one — around a minute.
            </div>
            <button className="btn btn-primary" onClick={build}>
              <i className="ti ti-sparkles" aria-hidden="true" /> Build the procedure
            </button>
          </div>
        )}

        {building && (
          <div className="empty" style={{ padding: 30 }}>
            <span className="spinner" />
            <div style={{ marginTop: 12, fontWeight: 600 }}>
              Writing the procedure… {elapsed.toFixed(0)}s
            </div>
            <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 6 }}>
              The trainer is the slowest agent — around 70 seconds.
            </div>
          </div>
        )}

        {procedure && !building && (
          <>
            <div className="wo-sub-note">
              Tick steps off in order. A step whose prerequisites are unmet shows
              as blocked — that ordering is the lesson.
            </div>
            <ProcedureView procedure={procedure} checked={checked} onToggle={toggle} />
            <div className="fix-actions">
              <button className="btn btn-ghost btn-sm" onClick={build}>
                <i className="ti ti-refresh" aria-hidden="true" /> Rebuild
              </button>
            </div>
          </>
        )}
      </Card>

      {/* Learn here, be scored next door. See the module note. */}
      {onDrill && subject.scenarioId && (
        <Card title="Now prove it">
          <div className="wo-sub-note">
            {subject.kind === 'task'
              ? 'The graded drill runs the same fault as a scored decision exercise. '
                + 'That score is what earns your XP and what your supervisor signs off.'
              : 'Practising this drill scores you on the same decisions, but nothing '
                + 'is dispatched and no job closes — it is rehearsal, not a shift.'}
          </div>
          <button className="btn btn-primary" onClick={onDrill}>
            <i className="ti ti-tool" aria-hidden="true" />
            {subject.kind === 'task' ? ' Take the graded drill' : ' Practise the drill'}
          </button>
        </Card>
      )}
    </>
  )
}

/** The header strip both callers render above the panel. */
export function RepairHeader({ subject, onBack, backLabel = 'Back' }) {
  return (
    <div className="fix-head">
      <button className="btn btn-ghost btn-sm" onClick={onBack}>
        <i className="ti ti-arrow-left" aria-hidden="true" /> {backLabel}
      </button>
      <div className="fix-head-main">
        <div className="fix-title">Repair with AI</div>
        <div className="fix-sub">
          {subject?.code && <span className="task-code">{subject.code}</span>}
          {subject?.severity && <SeverityChip severity={subject.severity} />}
          {subject?.assetName && <span className="fix-asset">{subject.assetName}</span>}
        </div>
      </div>
    </div>
  )
}
