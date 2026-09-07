/**
 * WorkOrder.jsx — "what am I actually fixing, and how", for one job.
 *
 * This is the twin's own answer, not a rewrite of it. The page shows three
 * things, in the order an operator needs them:
 *
 *   1  THE ORDER      the maintenance work order the twin's agents wrote from
 *                     the live diagnosis — parts, priority, safety, steps
 *   2  ON THE ASSET   the guided steps grounded in the findings open on that
 *                     node RIGHT NOW (the same AR overlay the twin serves)
 *   3  THE REPORT     what the operator did, once they have done it
 *
 * NEITHER IS STORED ON THE TASK. Both are derived from current state rather
 * than frozen when the supervisor clicked assign — a procedure describing the
 * plant as it was hours ago is, on a developing fault, precisely the document
 * you must not follow.
 *
 * THE TWO HALVES COST DIFFERENT AMOUNTS, AND ARE TREATED DIFFERENTLY.
 * (2) reads the graph and is free, so it loads with the page. (1) runs an LLM
 * agent and bills the account per call, so it happens ONLY when somebody presses
 * the button. This page originally generated on open, which quietly made merely
 * LOOKING at a job an API charge — the twin's own co-pilot strip had already
 * settled that question ("a trigger, not a background monitor") and this now
 * follows it.
 *
 * It degrades rather than failing: with no key, or no credit on it, the agents
 * return a deterministic template and the page says so plainly instead of
 * passing it off as reasoning. An operator with the guided procedure and no
 * generated order can still do the job.
 */

import { useCallback, useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import api from '../api/client'
import { Card } from '../components/ui/Card'
import { ErrorBox, Loading } from '../components/ui/States'
import { SeverityChip, StatusChip, ago } from '../components/work/WorkBits'
import { useWork } from '../context/WorkContext'
import '../styles/work.css'

/**
 * The agent-written order, rendered against the ACTUAL `WorkOrder` model in
 * copilot/agents.py — `{wo_number, ata_chapter, priority, compliance_ref,
 * fault_description, root_cause, steps[{step, action, criteria, safety}],
 * estimated_hours, parts_required, sign_off}`.
 *
 * Written to that schema rather than to a guess at it. An earlier version tried
 * `s.instruction || s.title` and would have silently dropped every acceptance
 * criterion and safety warning on the page a technician is meant to work from —
 * which is the half of a work order that makes it a work order.
 */
function OrderBody({ wo }) {
  if (!wo) {
    return (
      <div className="empty" style={{ padding: 22 }}>
        <i className="ti ti-file-off" style={{ fontSize: 22, display: 'block', marginBottom: 6 }} />
        No generated work order available.
        <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 6, lineHeight: 1.55 }}>
          The twin's agents could not be reached. The guided procedure still
          works — it does not depend on them.
        </div>
      </div>
    )
  }

  const facts = [
    ['Order', wo.wo_number],
    ['Priority', wo.priority],
    ['Estimated', wo.estimated_hours ? `${wo.estimated_hours} h` : null],
    ['System', wo.ata_chapter],
    ['Compliance', wo.compliance_ref],
    ['Sign-off', wo.sign_off],
  ].filter(([, v]) => v)

  return (
    <>
      {facts.length > 0 && (
        <div className="wo-facts">
          {facts.map(([k, v]) => (
            <div key={k} className="wo-fact">
              <span className="wo-fact-k">{k}</span>
              <span className="wo-fact-v">{String(v)}</span>
            </div>
          ))}
        </div>
      )}

      {wo.fault_description && (
        <>
          <div className="wo-sub">Fault</div>
          <p className="wo-desc">{wo.fault_description}</p>
        </>
      )}

      {wo.root_cause && (
        <>
          <div className="wo-sub">Suspected root cause</div>
          <p className="wo-desc">{wo.root_cause}</p>
        </>
      )}

      {(wo.steps || []).length > 0 && (
        <>
          <div className="wo-sub">Steps</div>
          <ol className="wo-steps">
            {wo.steps.map((s) => (
              <li key={s.step}>
                <b>{s.action}</b>
                {s.criteria && (
                  <div className="wo-step-body">
                    <span className="wo-crit">Accept when:</span> {s.criteria}
                  </div>
                )}
                {s.safety && (
                  <div className="wo-step-safety">
                    <i className="ti ti-alert-triangle" aria-hidden="true" /> {s.safety}
                  </div>
                )}
              </li>
            ))}
          </ol>
        </>
      )}

      {(wo.parts_required || []).length > 0 && (
        <div className="wo-parts">
          <div className="wo-sub">Parts required</div>
          {wo.parts_required.map((p, i) => (
            <div key={i} className="wo-part">{p}</div>
          ))}
        </div>
      )}
    </>
  )
}

export default function WorkOrder() {
  const { taskId } = useParams()
  const navigate = useNavigate()
  const { can } = useWork()

  const [task, setTask] = useState(null)
  const [order, setOrder] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [generating, setGenerating] = useState(false)
  const [note, setNote] = useState('')
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      setLoading(true)
      try {
        // The task first: if this one is not the operator's, nothing else should
        // be fetched or shown.
        const t = await api.work.task(taskId)
        if (cancelled) return
        setTask(t)
        setError(null)
        // Free half only: the on-asset steps come from the graph. The written
        // work order is an LLM call and is NOT made until asked — see the note
        // on the endpoint.
        try {
          const o = await api.work.workorder(taskId, false)
          if (!cancelled) setOrder(o)
        } catch { /* rendered as "not generated yet" */ }
      } catch (e) {
        if (!cancelled) setError(e)
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => { cancelled = true }
  }, [taskId])

  /** The one paid action on this page, behind an explicit click. */
  const generate = useCallback(async () => {
    setGenerating(true)
    try {
      setOrder(await api.work.workorder(taskId, true))
    } catch (e) {
      setError(e)
    } finally {
      setGenerating(false)
    }
  }, [taskId])

  const report = useCallback(async () => {
    if (!note.trim()) return
    setSaving(true)
    try {
      await api.work.comment(taskId, note.trim())
      setNote('')
      setSaved(true)
      const t = await api.work.task(taskId)
      setTask(t)
      setTimeout(() => setSaved(false), 4000)
    } catch (e) {
      setError(e)
    } finally {
      setSaving(false)
    }
  }, [note, taskId])

  if (loading) return <Loading label="Loading the work order…" />
  if (error && !task) {
    return (
      <div className="panel">
        <ErrorBox error={error} hint="This job may not be assigned to you." />
        <button className="btn btn-ghost" onClick={() => navigate('/my-work')}>
          <i className="ti ti-arrow-left" aria-hidden="true" /> Back to my work
        </button>
      </div>
    )
  }
  if (!task) return null

  const arSteps = order?.ar_steps || []
  const events = task.events || []
  const done = ['resolved', 'closed'].includes(task.status)

  return (
    <div className="panel fix-panel">
      <div className="fix-head">
        <button className="btn btn-ghost btn-sm" onClick={() => navigate('/my-work')}>
          <i className="ti ti-arrow-left" aria-hidden="true" /> My work
        </button>
        <div className="fix-head-main">
          <div className="fix-title">{task.title}</div>
          <div className="fix-sub">
            <span className="task-code">{task.code}</span>
            <SeverityChip severity={task.severity} />
            <StatusChip status={task.status} />
            {task.asset_name && <span className="fix-asset">{task.asset_name}</span>}
          </div>
        </div>
      </div>

      {error && <ErrorBox error={error} />}

      {task.assigned_by_name && (
        <div className="wo-assigned">
          <i className="ti ti-user-check" aria-hidden="true" />
          Assigned by <b>{task.assigned_by_name}</b> {ago(task.assigned_at)}
        </div>
      )}

      <Card title="Work order">
        {order?.work_order ? (
          <>
            {order.source === 'stub' && (
              <div className="wo-stub-note">
                <i className="ti ti-robot-off" aria-hidden="true" />
                Written from the built-in template — the AI backend was not
                reachable, so this is the deterministic fallback.
              </div>
            )}
            <OrderBody wo={order.work_order} />
          </>
        ) : (
          <div className="empty" style={{ padding: 22 }}>
            <i className="ti ti-sparkles" style={{ fontSize: 22, display: 'block', marginBottom: 8 }} />
            <div style={{ marginBottom: 10 }}>No work order written yet.</div>
            <div style={{ fontSize: 12, color: 'var(--muted)', maxWidth: 380,
                          margin: '0 auto 14px', lineHeight: 1.55 }}>
              Generating one runs an AI agent and bills the account, so it is not
              done automatically. The steps below and the guided procedure do not
              need it.
            </div>
            <button className="btn btn-primary btn-sm" disabled={generating}
                    onClick={generate}>
              {generating ? 'Writing…' : 'Generate work order'}
            </button>
          </div>
        )}
      </Card>

      {arSteps.length > 0 && (
        <Card title="On the asset">
          <div className="wo-sub-note">
            Generated from the findings open on this asset right now.
          </div>
          <ol className="wo-steps">
            {arSteps.map((s) => (
              <li key={s.n}>
                <b>{s.title}</b>
                {s.severity && <span className={`nav-badge ${s.severity === 'critical' ? 'badge-red' : 'badge-amber'}`}>{s.severity}</span>}
                <div className="wo-step-body">{s.instruction}</div>
              </li>
            ))}
          </ol>
        </Card>
      )}

      {can('scenario.run') && task.scenario_id && !done && (
        <Card title="How to fix it">
          <div className="wo-sub-note">
            Walk the procedure step by step. It scores how much help you needed,
            and that score is what earns your XP.
          </div>
          <button className="btn btn-primary"
                  onClick={() => navigate(`/fix/${encodeURIComponent(task.task_id)}`)}>
            <i className="ti ti-tool" aria-hidden="true" /> Fix the fault
          </button>
        </Card>
      )}

      <Card title="Report">
        {done && (
          <div className="wo-done">
            <i className="ti ti-circle-check" aria-hidden="true" />
            {task.status === 'closed'
              ? 'Closed and signed off by your supervisor.'
              : 'Reported fixed — waiting on your supervisor to sign it off.'}
            {task.score !== null && task.score !== undefined &&
              <span> Scored {Math.round(task.score)}%.</span>}
          </div>
        )}

        {can('task.comment') && (
          <div className="wo-report">
            <textarea
              className="assign-note wo-textarea"
              rows={3}
              placeholder="What did you find, and what did you do? This goes on the record for this fault."
              value={note}
              onChange={(e) => setNote(e.target.value)}
            />
            <button className="btn btn-ghost btn-sm" disabled={saving || !note.trim()}
                    onClick={report}>
              {saving ? 'Saving…' : 'Add to the record'}
            </button>
            {saved && <span className="wo-saved"><i className="ti ti-check" aria-hidden="true" /> saved</span>}
          </div>
        )}

        {events.length > 0 && (
          <div className="wo-timeline">
            {events.map((e) => (
              <div key={e.event_id} className="wo-event">
                <span className={`wo-event-kind kind-${e.kind}`}>{e.kind.replace('_', ' ')}</span>
                <span className="wo-event-summary">{e.summary}</span>
                <span className="wo-event-who">{e.actor_name || 'system'} · {ago(e.at)}</span>
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  )
}
