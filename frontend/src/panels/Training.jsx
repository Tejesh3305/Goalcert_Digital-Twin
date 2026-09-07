/**
 * Training.jsx — the simulation library: what to practise, and what is required.
 *
 * Two audiences in one page, and the ordering is what separates them:
 *
 *   REQUIRED   a procedure one of your OPEN jobs will make you run. These sort
 *              first and carry the job code, because the drill you need for
 *              tonight's fault outranks the one you might need someday.
 *   PRACTICE   everything else in the library, with the ones you have not
 *              passed ahead of the ones you have.
 *
 * The server does that sort (`GET /scenario/procedures`), so the page cannot
 * disagree with it about what you owe.
 *
 * EVERY CARD STATES ITS STANDING.
 * A library that shows only "passed / not passed" hides the two states an
 * operator actually acts on: a run they abandoned half-finished, and a
 * procedure they have failed twice. Both now show, and a half-finished run
 * offers Resume rather than making them start again — restarting silently was
 * how people lost a drill they were most of the way through.
 *
 * REPAIR WITH AI IS AVAILABLE FOR EVERY PROCEDURE, not only required ones.
 * It used to need a task behind it, which meant "teach me this properly" was
 * offered only for work somebody had already assigned you. That is backwards
 * for a training library, so a library card opens the trainer against the
 * procedure itself (`/repair/procedure/:id`).
 *
 * PRACTICE vs THE REAL THING. Running a drill here is not the same as fixing a
 * fault: there is no task, nothing is dispatched, and nothing closes. XP is
 * awarded once per procedure on the first clean pass — enough that drilling is
 * worth doing, not enough that repeating the shortest one is a strategy.
 */

import { useCallback, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import api from '../api/client'
import { Card, PanelHeader } from '../components/ui/Card'
import { StatTile } from '../components/ui/Charts'
import { ErrorBox, Loading } from '../components/ui/States'
import { NoWorkspace } from '../components/work/WorkBits'
import { useWork } from '../context/WorkContext'
import { useApi } from '../hooks/useApi'
import '../styles/work.css'
import '../styles/work-dashboard.css'

const DIFFICULTY = {
  easy: 'badge-green', medium: 'badge-blue', hard: 'badge-amber', expert: 'badge-red',
}

/**
 * The standing chip. Four states, in the order they matter to the operator:
 * something left open, something passed, something tried and not passed, and
 * something never touched.
 */
function StatusChipFor({ p }) {
  if (p.in_progress_run_id) {
    return (
      <span className="sim-status sim-status-open">
        <i className="ti ti-player-pause" aria-hidden="true" /> in progress
      </span>
    )
  }
  if (p.passed) {
    return (
      <span className="sim-status sim-status-passed">
        <i className="ti ti-circle-check" aria-hidden="true" /> passed
        {p.best_score != null && ` · ${Math.round(p.best_score)}%`}
      </span>
    )
  }
  if (p.attempts > 0) {
    return (
      <span className="sim-status sim-status-tried">
        <i className="ti ti-refresh" aria-hidden="true" />
        {` ${p.attempts} attempt${p.attempts === 1 ? '' : 's'}`}
        {p.last_score != null && ` · last ${Math.round(p.last_score)}%`}
      </span>
    )
  }
  return (
    <span className="sim-status sim-status-new">
      <i className="ti ti-circle-dashed" aria-hidden="true" /> not started
    </span>
  )
}

function ProcedureCard({ p, onRun, onResume, onReview, onRepair, busy }) {
  return (
    <div className={`sim-card ${p.required_by ? 'sim-card-required' : ''}`}>
      <div className="sim-card-head">
        <span className={`nav-badge ${DIFFICULTY[p.difficulty] || 'badge-muted'}`}>
          {p.difficulty}
        </span>
        {p.required_by && (
          <span className="nav-badge badge-red">required · {p.required_by}</span>
        )}
        <StatusChipFor p={p} />
      </div>

      <div className="sim-title">{p.title}</div>
      <div className="sim-summary">{p.summary}</div>

      <div className="sim-meta">
        <span><i className="ti ti-list-numbers" aria-hidden="true" /> {p.total_steps} steps</span>
        <span><i className="ti ti-clock" aria-hidden="true" /> ~{p.est_minutes} min</span>
        <span><i className="ti ti-category" aria-hidden="true" /> {p.domain}</span>
      </div>

      {p.safety && (
        <div className="sim-safety">
          <i className="ti ti-alert-triangle" aria-hidden="true" /> {p.safety}
        </div>
      )}

      <div className="sim-actions">
        {/* Learn, then be tested. Repair with AI leads on every card: it writes
            the ordered steps for this exact fault, which is what makes the
            drill afterwards a test rather than a guess. */}
        <button className="btn btn-primary btn-sm" onClick={() => onRepair(p)}>
          <i className="ti ti-sparkles" aria-hidden="true" /> Repair with AI
        </button>

        {/* A half-finished run is resumed, never silently restarted. */}
        {p.in_progress_run_id ? (
          <button className="btn btn-ghost btn-sm" onClick={() => onResume(p)}>
            <i className="ti ti-player-play" aria-hidden="true" /> Resume
          </button>
        ) : (
          <button className="btn btn-ghost btn-sm" disabled={busy} onClick={() => onRun(p)}>
            <i className="ti ti-player-play" aria-hidden="true" />
            {p.passed ? ' Practise again' : ' Practise'}
          </button>
        )}

        <button className="btn btn-ghost btn-sm" onClick={() => onReview(p)}>
          Read it first
        </button>
      </div>
    </div>
  )
}

export default function Training() {
  const { can, hasWorkspace, loading: personaLoading, openBackend, signedIn } = useWork()
  const navigate = useNavigate()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [reviewing, setReviewing] = useState(null)

  const mayRun = can('scenario.run')
  const { data, loading } = useApi(() => api.scenario.procedures(), [], { skip: !mayRun })

  const run = useCallback(async (p) => {
    setBusy(true)
    setError(null)
    try {
      const started = await api.scenario.startPractice(p.id)
      navigate(`/drill/${encodeURIComponent(started.run_id)}`)
    } catch (e) {
      setError(e)
      setBusy(false)
    }
  }, [navigate])

  const resume = useCallback((p) => {
    navigate(`/drill/${encodeURIComponent(p.in_progress_run_id)}`)
  }, [navigate])

  /**
   * A required drill opens the trainer against the JOB — it has an asset, a
   * severity and a supervisor behind it, and losing that context would make the
   * procedure generic. A library item opens it against the procedure.
   */
  const repair = useCallback((p) => {
    if (p.required_task_id) {
      navigate(`/repair/${encodeURIComponent(p.required_task_id)}`)
    } else {
      navigate(`/repair/procedure/${encodeURIComponent(p.id)}`)
    }
  }, [navigate])

  const review = useCallback(async (p) => {
    setError(null)
    try {
      setReviewing(await api.scenario.procedure(p.id))
    } catch (e) { setError(e) }
  }, [])

  if (personaLoading) return <Loading label="Loading…" />
  if (!hasWorkspace) {
    return <NoWorkspace what="Training" openBackend={openBackend} signedIn={signedIn} />
  }
  if (!mayRun) {
    return (
      <div className="empty" style={{ padding: '48px 24px' }}>
        <i className="ti ti-lock" style={{ fontSize: 28, display: 'block', marginBottom: 10 }} />
        <div style={{ fontWeight: 600 }}>Training is for frontline operators</div>
      </div>
    )
  }
  if (loading) return <Loading label="Loading the library…" />

  const procedures = data?.procedures || []
  const required = procedures.filter((p) => p.required_by)
  const rest = procedures.filter((p) => !p.required_by)
  const inProgress = procedures.filter((p) => p.in_progress_run_id)

  return (
    <div className="panel">
      <PanelHeader title="Training" subtitle="Drills you owe, and the library to practise on" />
      {error && <ErrorBox error={error} />}

      <div className="kpi-row">
        <StatTile label="Required for your jobs" value={required.length}
                  icon="ti-alert-circle" hint="a job of yours needs these"
                  tone={required.length ? 'critical' : undefined} />
        <StatTile label="Left in progress" value={inProgress.length}
                  icon="ti-player-pause" hint="resume where you stopped"
                  tone={inProgress.length ? 'warning' : undefined} />
        <StatTile label="Procedures passed" icon="ti-circle-check" tone="good"
                  value={<>{data?.passed_count ?? 0}<span className="kpi-of"> / {procedures.length}</span></>}
                  hint="across the whole library" />
        <StatTile label="Attempted" value={data?.attempted_count ?? 0}
                  icon="ti-history" hint="tried at least once" />
      </div>

      {required.length > 0 && (
        <Card title={`Required for work you have been given (${required.length})`}>
          <div className="wo-sub-note">
            One of your open jobs needs this. <b>Repair with AI</b> writes the
            correctly-ordered steps for that exact fault — what to do, what goes
            wrong if you skip it, and what goes wrong if you do it too early.
          </div>
          <div className="sim-grid">
            {required.map((p) => (
              <ProcedureCard key={p.id} p={p} onRun={run} onResume={resume}
                             onReview={review} onRepair={repair} busy={busy} />
            ))}
          </div>
        </Card>
      )}

      <Card title={`Library (${rest.length})`}>
        <div className="sim-grid">
          {rest.map((p) => (
            <ProcedureCard key={p.id} p={p} onRun={run} onResume={resume}
                           onReview={review} onRepair={repair} busy={busy} />
          ))}
        </div>
      </Card>

      {/* Read-only runbook. Deliberately WITHOUT the answers — reviewing a
          procedure must not hand you the key to the drill you take next. */}
      {reviewing && (
        <div className="sim-modal" onClick={() => setReviewing(null)}>
          <div className="sim-modal-body" onClick={(e) => e.stopPropagation()}>
            <div className="sim-modal-head">
              <div>
                <div className="fix-title">{reviewing.title}</div>
                <div className="sim-summary">{reviewing.summary}</div>
              </div>
              <button className="btn btn-ghost btn-sm" onClick={() => setReviewing(null)}>
                <i className="ti ti-x" aria-hidden="true" /> Close
              </button>
            </div>
            {reviewing.safety && (
              <div className="fix-safety">
                <i className="ti ti-alert-triangle" aria-hidden="true" /> {reviewing.safety}
              </div>
            )}
            <ol className="wo-steps">
              {(reviewing.steps || []).map((s) => (
                <li key={s.id}>
                  <b>{s.title}</b> <span className="sim-phase">{s.phase}</span>
                  <div className="wo-step-body">{s.instruction}</div>
                </li>
              ))}
            </ol>
            <div className="wo-sub-note">
              The correct actions are not shown here — you will be scored on them
              in the drill.
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
