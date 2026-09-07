/**
 * FixIssue.jsx — running the procedure against a real, assigned fault.
 *
 * The run itself is `RunPlayer`, shared with the practice drill. What lives here
 * is the part that only applies to real work: the task it belongs to, and what
 * happens when the procedure is finished — the operator reports the fault fixed,
 * the server reads the score from the run, and the XP follows.
 *
 * Only the run's ID goes over the wire on completion. The score, the pass and
 * the hint count are read server-side from that run (`work.service`
 * `_verified_outcome`), so this page could not inflate them if it tried — which
 * is what makes the resulting XP worth anything.
 */

import { useCallback, useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import api from '../api/client'
import RunPlayer from '../components/work/RunPlayer'
import TwinPanel from '../components/work/TwinPanel'
import { ErrorBox, Loading } from '../components/ui/States'
import { SeverityChip, XpBar } from '../components/work/WorkBits'
import { useWork } from '../context/WorkContext'
import '../styles/work.css'

export default function FixIssue() {
  const { taskId } = useParams()
  const navigate = useNavigate()
  const { reload: reloadPersona } = useWork()

  const [task, setTask] = useState(null)
  const [run, setRun] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  const [outcome, setOutcome] = useState(null)
  const [order, setOrder] = useState(null)
  const [generating, setGenerating] = useState(false)

  // Start (or resume) the run on mount. Resumption is the server's default, so
  // reopening this page mid-procedure returns to the same step with the score
  // intact rather than silently restarting it.
  useEffect(() => {
    let cancelled = false
    ;(async () => {
      setLoading(true)
      try {
        const t = await api.work.task(taskId)
        const r = await api.scenario.startRun(taskId)
        if (cancelled) return
        setTask(t)
        setRun(r)
        setError(null)
        // Free half of the work order (on-asset steps from the graph). The
        // written order costs money and waits for a click — see TwinPanel.
        try {
          const o = await api.work.workorder(taskId, false)
          if (!cancelled) setOrder(o)
        } catch { /* the panel renders its own empty state */ }
      } catch (e) {
        if (!cancelled) setError(e)
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => { cancelled = true }
  }, [taskId])

  const generateOrder = useCallback(async () => {
    setGenerating(true)
    try {
      setOrder(await api.work.workorder(taskId, true))
    } catch (e) {
      setError(e)
    } finally {
      setGenerating(false)
    }
  }, [taskId])

  const finish = useCallback(async () => {
    if (!run) return
    setBusy(true)
    try {
      const res = await api.work.complete(taskId, { run_id: run.run_id })
      setOutcome(res)
      await reloadPersona()
    } catch (e) {
      setError(e)
    } finally {
      setBusy(false)
    }
  }, [run, taskId, reloadPersona])

  if (loading) return <Loading label="Opening the procedure…" />
  if (error && !run) {
    return (
      <div className="panel">
        <ErrorBox error={error}
                   hint="This job may not be assigned to you, or has no published procedure." />
        <button className="btn btn-ghost" onClick={() => navigate('/my-work')}>
          <i className="ti ti-arrow-left" aria-hidden="true" /> Back to my work
        </button>
      </div>
    )
  }
  if (!run) return null

  const passed = run.score >= run.pass_score

  const header = (
    <div className="fix-head">
      <button className="btn btn-ghost btn-sm" onClick={() => navigate('/my-work')}>
        <i className="ti ti-arrow-left" aria-hidden="true" /> My work
      </button>
      <div className="fix-head-main">
        <div className="fix-title">{run.procedure?.title}</div>
        <div className="fix-sub">
          {task && <><span className="task-code">{task.code}</span>
                     <SeverityChip severity={task.severity} /></>}
          {task?.asset_name && <span className="fix-asset">{task.asset_name}</span>}
        </div>
      </div>
      <div className="fix-score">
        <div className="fix-score-value">{Math.round(run.score)}%</div>
        <div className="fix-score-label">pass at {run.pass_score}%</div>
      </div>
    </div>
  )

  // Shown once the run is finished: report the fault fixed, then the award.
  const footer = outcome ? (
    <div className="fix-reward">
      <div className="reward-head">
        <i className="ti ti-award" aria-hidden="true" />
        <div>
          <div className="reward-xp">+{outcome.awarded} XP</div>
          <div className="reward-sub">
            {outcome.task.status === 'resolved'
              ? 'Fault reported fixed — your supervisor will sign it off.'
              : 'Attempt recorded. Run the procedure again when you are ready.'}
          </div>
        </div>
      </div>
      <ul className="reward-reasons">
        {(outcome.reasons || []).map((r, i) => <li key={i}>{r}</li>)}
      </ul>
      <XpBar xp={outcome.xp} />
      <div className="fix-actions">
        <button className="btn btn-primary" onClick={() => navigate('/my-work')}>
          Back to my work
        </button>
        <button className="btn btn-ghost"
                onClick={() => navigate(`/order/${encodeURIComponent(taskId)}`)}>
          Write up what you did
        </button>
      </div>
    </div>
  ) : (
    <>
      {error && <ErrorBox error={error} />}
      <button className="btn btn-primary" disabled={busy} onClick={finish}>
        {busy ? 'Recording…' : passed ? 'Report the fault fixed' : 'Record this attempt'}
      </button>
    </>
  )

  return (
    <RunPlayer
      run={run} setRun={setRun} header={header} footer={footer}
      aside={<TwinPanel task={task} order={order} onGenerate={generateOrder}
                        generating={generating} canGenerate />}
    />
  )
}
