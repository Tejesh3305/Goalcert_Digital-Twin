/**
 * Dispatch.jsx — the supervisor's console.
 *
 * One screen answering one question: which faults does nobody own, and who
 * should own them? Everything else on this page is in service of that.
 *
 *     the twin detects  ->  QUEUE (here)  ->  assign  ->  the operator's phone
 *
 * The roster comes from the server's `assignable_user_ids`, the SAME function
 * the assign endpoint enforces with. That is deliberate: a dialog built from a
 * different list would eventually offer somebody the API then refuses, and a
 * button that 403s reads as a broken product rather than as a boundary.
 *
 * The board polls rather than streaming. Dispatch is a minutes-scale activity —
 * a supervisor is not watching for sub-second changes — and a poll cannot leave
 * the page silently stale after a dropped SSE connection.
 */

import { useCallback, useMemo, useState } from 'react'
import api from '../api/client'
import { Card, PanelHeader } from '../components/ui/Card'
import { ErrorBox, Loading } from '../components/ui/States'
import { NoWorkspace, StatusChip, TaskCard, ago } from '../components/work/WorkBits'
import { useTwin } from '../context/TwinContext'
import { useWork } from '../context/WorkContext'
import { usePolling } from '../hooks/useApi'
import '../styles/work.css'
import '../styles/work-dashboard.css'

/** A job raised by hand starts empty and unassigned; the form resets to this. */
const BLANK_JOB = {
  title: '', detail: '', severity: 'warning', asset_name: '', assignee_id: '',
}

export default function Dispatch() {
  const { can, hasWorkspace, loading: personaLoading, openBackend, signedIn } = useWork()
  const { activeTenant, activeTwin } = useTwin()
  const [selected, setSelected] = useState(null)
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [flash, setFlash] = useState(null)
  const [actionError, setActionError] = useState(null)
  const [raising, setRaising] = useState(false)
  const [form, setForm] = useState(BLANK_JOB)

  const mayDispatch = can('queue.read')

  const { data: board, error, refetch } = usePolling(
    () => api.work.board(), 8000, [], { skip: !mayDispatch })
  // `scope=all` so the supervisor SEES every operator in the organisation, not
  // only their own team. The rows carry `assignable`, and the assign endpoint is
  // unchanged — so this shows who is buried without letting anyone dispatch
  // outside their teams. Hiding the others entirely was the previous behaviour
  // and it made a loaded colleague invisible rather than out of scope.
  const { data: rosterData, refetch: refetchRoster } = usePolling(
    () => api.work.roster('all'), 15000, [], { skip: !mayDispatch })

  const tasks = board?.tasks || []
  const operators = rosterData?.operators || []
  const assignable = useMemo(() => operators.filter((o) => o.assignable), [operators])

  const unassigned = useMemo(
    () => tasks.filter((t) => t.status === 'open'), [tasks])
  const running = useMemo(
    () => tasks.filter((t) => ['assigned', 'in_progress', 'blocked'].includes(t.status)), [tasks])
  const awaiting = useMemo(
    () => tasks.filter((t) => t.status === 'resolved'), [tasks])

  const byId = useMemo(
    () => Object.fromEntries(operators.map((o) => [o.user_id, o])), [operators])

  const refreshAll = useCallback(async () => {
    await Promise.all([refetch(), refetchRoster()])
  }, [refetch, refetchRoster])

  const act = useCallback(async (fn, message) => {
    setBusy(true)
    setActionError(null)
    try {
      await fn()
      setFlash(message)
      setNote('')
      setSelected(null)
      await refreshAll()
      setTimeout(() => setFlash(null), 4000)
    } catch (e) {
      // Surfaced rather than swallowed: the refusals here ("not on a team you
      // supervise") are the RBAC explaining itself, and hiding them makes the
      // boundary feel like a bug.
      setActionError(e)
    } finally {
      setBusy(false)
    }
  }, [refreshAll])

  if (personaLoading) return <Loading label="Loading your role…" />
  if (!hasWorkspace) return <NoWorkspace what="Dispatch" openBackend={openBackend} signedIn={signedIn} />
  if (!mayDispatch) {
    return (
      <div className="empty" style={{ padding: '48px 24px' }}>
        <i className="ti ti-lock" style={{ fontSize: 28, display: 'block', marginBottom: 10 }} />
        <div style={{ fontWeight: 600, marginBottom: 6 }}>Dispatch is for supervisors</div>
        <div style={{ color: 'var(--muted)' }}>
          Your account is an operator. Your work is on <b>My Work</b>.
        </div>
      </div>
    )
  }

  return (
    <div className="panel">
      <PanelHeader
        title="Dispatch"
        subtitle="Faults the twin has raised, and who is fixing them"
      >
        <button className="btn btn-ghost" onClick={refreshAll} disabled={busy}>
          <i className="ti ti-refresh" aria-hidden="true" /> Refresh
        </button>
      </PanelHeader>

      {flash && (
        <div className="work-flash">
          <i className="ti ti-circle-check" aria-hidden="true" /> {flash}
        </div>
      )}
      {actionError && <ErrorBox error={actionError} />}
      {error && <ErrorBox error={error} hint="The dispatch queue could not be loaded." />}

      <div className="kpi-row">
        <div className="card kpi">
          <div className="card-label">Unassigned</div>
          <div className="card-value" style={{ color: unassigned.length ? 'var(--accent-red)' : undefined }}>
            {unassigned.length}
          </div>
          <div className="card-change">waiting for a decision</div>
        </div>
        <div className="card kpi">
          <div className="card-label">In the field</div>
          <div className="card-value">{running.length}</div>
          <div className="card-change">assigned or being fixed</div>
        </div>
        <div className="card kpi">
          <div className="card-label">Awaiting your sign-off</div>
          <div className="card-value">{awaiting.length}</div>
          <div className="card-change">operator says fixed</div>
        </div>
        <div className="card kpi">
          <div className="card-label">Your operators</div>
          <div className="card-value">{assignable.length}</div>
          <div className="card-change">
            {operators.length > assignable.length
              ? `${operators.length} in the organisation`
              : 'on your teams'}
          </div>
        </div>
      </div>

      <div className="dispatch-grid">
        <div className="dispatch-col">
          {/* Raising work by hand. `POST /work/tasks` has always accepted this —
              nothing in the UI called it, so a supervisor could only ever
              dispatch faults the twin happened to detect. Plenty of real jobs
              (a walk-round observation, a scheduled inspection, something a
              technician phoned in) never start as a detection. */}
          <Card
            title="Raise a job"
            action={
              <button className="btn btn-ghost btn-sm"
                      onClick={() => { setRaising(!raising); setActionError(null) }}>
                <i className={`ti ${raising ? 'ti-x' : 'ti-plus'}`} aria-hidden="true" />
                {raising ? ' Cancel' : ' New job'}
              </button>
            }
          >
            {!raising ? (
              <div className="wo-sub-note" style={{ margin: 0 }}>
                Not every job starts as a detection. Raise one by hand and assign
                it in the same step.
              </div>
            ) : (
              <div className="raise-form">
                <input className="assign-note" placeholder="What needs doing? (required)"
                       value={form.title} maxLength={200}
                       onChange={(e) => setForm({ ...form, title: e.target.value })} />
                <textarea className="assign-note raise-detail" rows={2}
                          placeholder="Any detail the operator needs"
                          value={form.detail}
                          onChange={(e) => setForm({ ...form, detail: e.target.value })} />
                <div className="raise-row">
                  <label className="raise-field">
                    <span>Severity</span>
                    <select value={form.severity}
                            onChange={(e) => setForm({ ...form, severity: e.target.value })}>
                      <option value="info">info</option>
                      <option value="warning">warning</option>
                      <option value="serious">serious</option>
                      <option value="critical">critical</option>
                    </select>
                  </label>
                  <label className="raise-field">
                    <span>Asset (optional)</span>
                    <input value={form.asset_name} maxLength={120}
                           placeholder="e.g. Pump P-101"
                           onChange={(e) => setForm({ ...form, asset_name: e.target.value })} />
                  </label>
                  <label className="raise-field">
                    <span>Assign to</span>
                    <select value={form.assignee_id}
                            onChange={(e) => setForm({ ...form, assignee_id: e.target.value })}>
                      {/* Leaving it unassigned is a real choice: it lands in the
                          queue below for a decision later. */}
                      <option value="">— leave in the queue —</option>
                      {assignable.map((o) => (
                        <option key={o.user_id} value={o.user_id}>
                          {o.name} ({o.open_tasks} open)
                        </option>
                      ))}
                    </select>
                  </label>
                </div>
                <div className="raise-actions">
                  <button className="btn btn-primary btn-sm"
                          disabled={busy || !form.title.trim()}
                          onClick={() => act(
                            () => api.work.createTask({
                              tenant_id: activeTenant || '',
                              title: form.title.trim(),
                              detail: form.detail.trim(),
                              severity: form.severity,
                              asset_name: form.asset_name.trim(),
                              assignee_id: form.assignee_id || null,
                            }).then(() => { setRaising(false); setForm(BLANK_JOB) }),
                            form.assignee_id ? 'Job raised and assigned' : 'Job raised into the queue')}>
                    <i className="ti ti-send" aria-hidden="true" /> Raise it
                  </button>
                  <span className="raise-hint">
                    {activeTenant
                      ? <>It will be filed against <b>{activeTwin?.name || activeTenant}</b>.</>
                      : 'No twin is open, so this job will carry no plant.'}
                  </span>
                </div>
              </div>
            )}
          </Card>

          <Card title={`Unassigned queue (${unassigned.length})`}>
            {unassigned.length === 0 ? (
              <div className="empty" style={{ padding: 24 }}>
                <i className="ti ti-circle-check" style={{ fontSize: 22, display: 'block', marginBottom: 6 }} />
                Nothing waiting. Every raised fault has an owner.
              </div>
            ) : unassigned.map((t) => (
              <TaskCard
                key={t.task_id}
                task={t}
                active={selected === t.task_id}
                onClick={() => { setSelected(selected === t.task_id ? null : t.task_id); setActionError(null) }}
              >
                <div className="task-foot">
                  <span className="task-age">raised {ago(t.created_at)}</span>
                  {t.scenario_id
                    ? <span className="task-proc"><i className="ti ti-book" aria-hidden="true" /> {t.scenario_id}</span>
                    : <span className="task-proc task-proc-none">no procedure published</span>}
                </div>

                {selected === t.task_id && (
                  <div className="assign-box" onClick={(e) => e.stopPropagation()}>
                    <div className="assign-label">Assign to</div>
                    {assignable.length === 0 ? (
                      <div className="assign-none">
                        You supervise no team members yet, so there is nobody to
                        assign to. Ask an administrator to add operators to your team.
                        {operators.length > 0 && (
                          <div style={{ marginTop: 6 }}>
                            {operators.length} operator{operators.length === 1 ? ' is' : 's are'} on
                            other teams — visible below, but not yours to dispatch.
                          </div>
                        )}
                      </div>
                    ) : (
                      <>
                        <input
                          className="assign-note"
                          placeholder="Note for the operator (optional)"
                          value={note}
                          onChange={(e) => setNote(e.target.value)}
                        />
                        <div className="assign-people">
                          {assignable.map((o) => (
                            <button
                              key={o.user_id}
                              className="assign-person"
                              disabled={busy}
                              onClick={() => act(
                                () => api.work.assign(t.task_id, o.user_id, note),
                                `${t.code} assigned to ${o.name}`)}
                            >
                              <span className="assign-person-name">{o.name}</span>
                              <span className="assign-person-meta">
                                L{o.xp.level} {o.xp.title} · {o.open_tasks} open
                              </span>
                            </button>
                          ))}
                        </div>
                      </>
                    )}
                  </div>
                )}
              </TaskCard>
            ))}
          </Card>
        </div>

        <div className="dispatch-col">
          <Card title={`In the field (${running.length})`}>
            {running.length === 0
              ? <div className="empty" style={{ padding: 24 }}>Nothing in progress.</div>
              : running.map((t) => (
                <TaskCard key={t.task_id} task={t}>
                  <div className="task-foot">
                    <span className="task-assignee">
                      <i className="ti ti-user" aria-hidden="true" />
                      {byId[t.assignee_id]?.name || 'assigned'}
                    </span>
                    <button
                      className="btn btn-ghost btn-sm"
                      disabled={busy}
                      onClick={() => act(
                        () => api.work.unassign(t.task_id, 'Returned to the queue'),
                        `${t.code} returned to the queue`)}
                    >
                      Return to queue
                    </button>
                  </div>
                </TaskCard>
              ))}
          </Card>

          <Card title={`Awaiting sign-off (${awaiting.length})`}>
            {awaiting.length === 0
              ? <div className="empty" style={{ padding: 24 }}>Nothing to sign off.</div>
              : awaiting.map((t) => (
                <TaskCard key={t.task_id} task={t}>
                  <div className="task-foot">
                    <span className="task-score">
                      <i className="ti ti-target-arrow" aria-hidden="true" />
                      scored {Math.round(t.score || 0)}%
                    </span>
                    {/* Closing is what resolves the Finding back in the twin —
                        see work/from_finding.resolve_finding. */}
                    <button
                      className="btn btn-primary btn-sm"
                      disabled={busy}
                      onClick={() => act(
                        () => api.work.close(t.task_id, 'Verified and accepted'),
                        `${t.code} closed — finding resolved in the twin`)}
                    >
                      Accept &amp; close
                    </button>
                  </div>
                </TaskCard>
              ))}
          </Card>

          <Card title={`Operators (${operators.length})`}
                action={assignable.length < operators.length && (
                  <span className="card-note">{assignable.length} yours to assign</span>)}>
            {operators.length === 0 ? (
              <div className="empty" style={{ padding: 24 }}>
                No operators in this organisation yet.
              </div>
            ) : (
              <div className="roster">
                {operators.map((o) => (
                  <div key={o.user_id}
                       className={`roster-row${o.assignable ? '' : ' team-row-readonly'}`}>
                    <div className="roster-name">
                      {o.name}
                      <span className="roster-email">{o.email}</span>
                    </div>
                    <div className="roster-xp">
                      {!o.assignable && (
                        <span className="team-scope" title="Not on a team you supervise">
                          <i className="ti ti-lock" aria-hidden="true" /> other team
                        </span>
                      )}
                      <span className="nav-badge badge-blue">L{o.xp.level} {o.xp.title}</span>
                      <span className="roster-open">{o.open_tasks} open</span>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </Card>
        </div>
      </div>
    </div>
  )
}
