/**
 * Drill.jsx — a practice run: the same procedure, with no fault behind it.
 *
 * Everything about the run itself is `RunPlayer`. What is different here is only
 * what finishing MEANS: there is no task to resolve, no supervisor waiting, and
 * the XP is awarded by the answer endpoint at the moment the last step is
 * answered — once per procedure, on the first clean pass.
 *
 * That "once" is the whole reason practice XP is safe to offer. Without it the
 * shortest two-step drill becomes an XP tap and every level stops meaning
 * anything; with it, drilling is worth doing exactly once per procedure and
 * repeating one is honestly presented as practice.
 */

import { useCallback, useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import api from '../api/client'
import RunPlayer from '../components/work/RunPlayer'
import TwinPanel from '../components/work/TwinPanel'
import { ErrorBox, Loading } from '../components/ui/States'
import { useWork } from '../context/WorkContext'
import '../styles/work.css'

export default function Drill() {
  const { runId } = useParams()
  const navigate = useNavigate()
  const { reload: reloadPersona } = useWork()

  const [run, setRun] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [award, setAward] = useState(null)

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const r = await api.scenario.run(runId)
        if (!cancelled) { setRun(r); setError(null) }
      } catch (e) {
        if (!cancelled) setError(e)
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => { cancelled = true }
  }, [runId])

  // The answer endpoint awards practice XP itself, so the standing in the header
  // is stale the moment a drill is passed. Refresh it rather than leaving the
  // operator to wonder whether the points landed.
  const onFinished = useCallback(async (res) => {
    setAward({ awarded: res.awarded || 0, reasons: res.reasons || [] })
    await reloadPersona()
  }, [reloadPersona])

  if (loading) return <Loading label="Opening the drill…" />
  if (error && !run) {
    return (
      <div className="panel">
        <ErrorBox error={error} hint="This drill may belong to someone else." />
        <button className="btn btn-ghost" onClick={() => navigate('/training')}>
          <i className="ti ti-arrow-left" aria-hidden="true" /> Back to training
        </button>
      </div>
    )
  }
  if (!run) return null

  const header = (
    <div className="fix-head">
      <button className="btn btn-ghost btn-sm" onClick={() => navigate('/training')}>
        <i className="ti ti-arrow-left" aria-hidden="true" /> Training
      </button>
      <div className="fix-head-main">
        <div className="fix-title">{run.procedure?.title}</div>
        <div className="fix-sub">
          <span className="nav-badge badge-blue">practice</span>
          <span className="fix-asset">{run.procedure?.domain}</span>
        </div>
      </div>
      <div className="fix-score">
        <div className="fix-score-value">{Math.round(run.score)}%</div>
        <div className="fix-score-label">pass at {run.pass_score}%</div>
      </div>
    </div>
  )

  const footer = (
    <>
      {award && (
        <div className="drill-award">
          {award.awarded > 0
            ? <><i className="ti ti-award" aria-hidden="true" /> <b>+{award.awarded} XP</b></>
            : <><i className="ti ti-repeat" aria-hidden="true" /> No XP this time</>}
          {award.reasons.map((r, i) => <div key={i} className="drill-award-why">{r}</div>)}
        </div>
      )}
      <div className="fix-actions">
        <button className="btn btn-primary" onClick={() => navigate('/training')}>
          Back to training
        </button>
        <button className="btn btn-ghost" onClick={() => navigate('/my-work')}>
          My work
        </button>
      </div>
    </>
  )

  // No task, so no work order — but the twin still runs beside the drill, which
  // is the difference between rehearsing on the plant and answering a quiz.
  return (
    <RunPlayer
      run={run} setRun={setRun} header={header}
      onFinished={onFinished} footer={footer}
      aside={<TwinPanel />}
    />
  )
}
