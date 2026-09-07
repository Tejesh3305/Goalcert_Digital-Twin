/**
 * RepairWithAI.jsx — the page around `components/work/RepairPanel`.
 *
 * The teaching itself moved into that component so Training and the twin can
 * mount it too. What is left here is what a PAGE owns and a component must not:
 * resolving the subject from the URL, the back button, and the twin alongside.
 *
 * TWO ENTRY POINTS, because there are two reasons to be here:
 *
 *   /repair/:taskId                 a fault you have been DISPATCHED. Carries a
 *                                   severity, an asset and a graded drill that
 *                                   closes the job.
 *   /repair/procedure/:scenarioId   a library procedure you chose to LEARN.
 *                                   Nothing is assigned and nothing closes.
 *
 * The second is new. Training's required drills could always reach the trainer,
 * because a required drill has a task behind it; library items could not, and
 * "learn this properly" was therefore available only for work somebody had
 * already given you. That is backwards for a training library.
 */

import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import api from '../api/client'
import RepairPanel, { RepairHeader, repairSubject } from '../components/work/RepairPanel'
import TwinPanel from '../components/work/TwinPanel'
import { ErrorBox, Loading } from '../components/ui/States'
import { useTwin } from '../context/TwinContext'
import { useWork } from '../context/WorkContext'
import '../styles/work.css'

export default function RepairWithAI() {
  const { taskId, scenarioId } = useParams()
  const navigate = useNavigate()
  const { can } = useWork()
  const { activeTwin } = useTwin()

  const [task, setTask] = useState(null)
  const [procedure, setProcedure] = useState(null)
  const [order, setOrder] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    ;(async () => {
      try {
        if (scenarioId) {
          const p = await api.scenario.procedure(scenarioId)
          if (!cancelled) { setProcedure(p); setTask(null); setError(null) }
        } else {
          const t = await api.work.task(taskId)
          if (cancelled) return
          setTask(t)
          setProcedure(null)
          setError(null)
          try {
            const o = await api.work.workorder(taskId, false)
            if (!cancelled) setOrder(o)
          } catch { /* the twin panel renders its own empty state */ }
        }
      } catch (e) {
        if (!cancelled) setError(e)
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => { cancelled = true }
  }, [taskId, scenarioId])

  const subject = repairSubject({ task, procedure, twin: activeTwin })

  if (loading) return <Loading label="Loading the fault…" />
  if (error && !subject) {
    return (
      <div className="panel">
        <ErrorBox error={error} hint="This job may not be assigned to you." />
        <button className="btn btn-ghost" onClick={() => navigate('/training')}>
          <i className="ti ti-arrow-left" aria-hidden="true" /> Back to training
        </button>
      </div>
    )
  }
  if (!subject) return null

  // A drill is offered when there is one to run and the operator may run it.
  // For a dispatched fault that drill closes the job; for a library procedure
  // it is practice. `RepairPanel` words the difference.
  const drillable = can('scenario.run') && subject.scenarioId
  const startDrill = async () => {
    if (subject.kind === 'task') {
      navigate(`/fix/${encodeURIComponent(subject.taskId)}`)
      return
    }
    try {
      const started = await api.scenario.startPractice(subject.scenarioId)
      navigate(`/drill/${encodeURIComponent(started.run_id)}`)
    } catch (e) {
      setError(e)
    }
  }

  return (
    <div className="panel run-layout">
      <div className="run-main">
        <RepairHeader subject={subject} backLabel="Training"
                      onBack={() => navigate('/training')} />
        <RepairPanel subject={subject} order={order}
                     onDrill={drillable ? startDrill : null} />
      </div>

      <TwinPanel task={task} order={order} canGenerate={false} />
    </div>
  )
}
