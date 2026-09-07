/**
 * RoleDashboard.jsx — "how is MY work going", per role. The landing page.
 *
 * This is a different question from the one the twin's own monitoring page
 * answers, which is why they are two pages rather than one:
 *
 *     /            this — the work I have been given     (role dashboard)
 *     /twin        how is the PLANT — 3-D, telemetry    (twin monitoring)
 *     /twin-library which twins exist                   (the catalogue)
 *
 * THE OPERATOR'S DASHBOARD ABSORBED THE PROGRESS PAGE.
 * There used to be a separate `/progress` holding the XP ledger and the run
 * history. It was a page you had to remember to visit, which meant the numbers
 * that are supposed to motivate the work were never on screen while the work
 * was being chosen. Standing and workload are now one screen: the jobs, the
 * pace, the score trend and the ledger that explains them. The nav entry is
 * gone and the old path redirects here.
 *
 * THE MACHINE IS ON THE RIGHT, AND IT IS CLICKABLE.
 * Selecting a job puts its twin in the side panel — the actual 3-D asset, its
 * health, its open findings. Clicking the model switches to that job's plant and
 * opens the full twin view. An operator told "the fault is in the turbine" can
 * click the turbine, which is the whole point.
 */

import { useCallback, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import api from '../api/client'
import { Card, PanelHeader } from '../components/ui/Card'
import { DayBars, LabelledBars, SEVERITY_COLOR, StatTile, TrendLine } from '../components/ui/Charts'
import { ErrorBox, Loading } from '../components/ui/States'
import TaskTwinCard from '../components/work/TaskTwinCard'
import { NoWorkspace, SeverityChip, StatusChip, XpBar, ago } from '../components/work/WorkBits'
import { useTwin } from '../context/TwinContext'
import { useWork } from '../context/WorkContext'
import { usePolling } from '../hooks/useApi'
import '../styles/work.css'
import '../styles/work-dashboard.css'

/* ────────────────────────────────────────────────────────────────────────
 * Supervisor
 * ──────────────────────────────────────────────────────────────────────── */

/** A twin-health strip the supervisor gets, so the plant is never off-screen. */
function TwinStrip() {
  const { activeTenant, activeTwin } = useTwin()
  const navigate = useNavigate()
  const { data: stats } = usePolling(
    () => api.stats(activeTenant), 8000, [activeTenant], { skip: !activeTenant })

  if (!activeTenant) {
    return (
      <Card title="The twin">
        <div className="empty" style={{ padding: 20 }}>
          No twin selected.
          <div style={{ marginTop: 10 }}>
            <button className="btn btn-primary btn-sm" onClick={() => navigate('/twin-library')}>
              Open the twin library
            </button>
          </div>
        </div>
      </Card>
    )
  }

  const findings = stats?.total_findings || 0
  return (
    <Card
      title={<><i className="ti ti-box" aria-hidden="true" /> {activeTwin?.name || activeTenant}</>}
      action={<button className="btn btn-ghost btn-sm" onClick={() => navigate('/twin')}>
        Open the twin
      </button>}
    >
      <div className="twin-stats" style={{ marginTop: 0, paddingTop: 0, borderTop: 'none' }}>
        <div className="twin-stat">
          <span className="twin-stat-k">Entities</span>
          <span className="twin-stat-v">{stats?.total_entities ?? '—'}</span>
        </div>
        <div className="twin-stat">
          <span className="twin-stat-k">Open findings</span>
          <span className="twin-stat-v" style={{ color: findings ? 'var(--accent-red)' : undefined }}>
            {findings}
          </span>
        </div>
        <div className="twin-stat">
          <span className="twin-stat-k">Incidents</span>
          <span className="twin-stat-v">{stats?.entity_counts?.Incident ?? 0}</span>
        </div>
      </div>
    </Card>
  )
}

/**
 * The supervisor's: what is unowned, what is running, and — the addition — the
 * whole team's load rather than only the operators they may dispatch to.
 *
 * `scope=all` is a VIEW widening: rows carry `assignable`, and the assign
 * endpoint still refuses anyone outside the supervisor's teams. Showing the
 * unassignable ones greyed rather than hiding them is what lets a supervisor
 * see that a colleague's operator is buried, which is the information that
 * makes them ask rather than pile on.
 */
function SupervisorDashboard() {
  const navigate = useNavigate()
  const { data: board } = usePolling(() => api.work.board(), 10000, [])
  const { data: teamData } = usePolling(() => api.work.teamStats(), 20000, [])

  const tasks = board?.tasks || []
  const unassigned = tasks.filter((t) => t.status === 'open')
  const running = tasks.filter((t) => ['assigned', 'in_progress', 'blocked'].includes(t.status))
  const awaiting = tasks.filter((t) => t.status === 'resolved')
  const operators = teamData?.operators || []
  const mine = operators.filter((o) => o.assignable)

  const busiest = Math.max(1, ...operators.map((o) => o.open_tasks))

  return (
    <div className="panel">
      <PanelHeader title="Dashboard" subtitle="Your shift, at a glance">
        <button className="btn btn-primary" onClick={() => navigate('/dispatch')}>
          <i className="ti ti-clipboard-list" aria-hidden="true" /> Go to dispatch
        </button>
      </PanelHeader>

      <div className="kpi-row">
        <StatTile label="Unassigned" value={unassigned.length}
                  hint="waiting for a decision" icon="ti-inbox"
                  tone={unassigned.length ? 'critical' : undefined}
                  onClick={() => navigate('/dispatch')} />
        <StatTile label="In the field" value={running.length}
                  hint="assigned or being fixed" icon="ti-run" />
        <StatTile label="Awaiting sign-off" value={awaiting.length}
                  hint="operator says fixed" icon="ti-checkbox"
                  tone={awaiting.length ? 'warning' : undefined}
                  onClick={() => navigate('/dispatch')} />
        <StatTile label="Your operators" value={mine.length}
                  hint={operators.length > mine.length
                    ? `${operators.length} in the organisation` : 'on your teams'}
                  icon="ti-users" />
      </div>

      <div className="dispatch-grid">
        <div className="dispatch-col">
          <Card title={`Needs a decision (${unassigned.length})`}
                action={unassigned.length > 0 && (
                  <button className="btn btn-ghost btn-sm" onClick={() => navigate('/dispatch')}>
                    Assign
                  </button>)}>
            {unassigned.length === 0 ? (
              <div className="empty" style={{ padding: 22 }}>
                Every raised fault has an owner.
              </div>
            ) : unassigned.slice(0, 5).map((t) => (
              <div key={t.task_id} className="task-card">
                <div className="task-card-top">
                  <span className="task-code">{t.code}</span>
                  <SeverityChip severity={t.severity} />
                </div>
                <div className="task-title">{t.title}</div>
                <div className="task-meta">
                  <span><i className="ti ti-clock" aria-hidden="true" /> raised {ago(t.created_at)}</span>
                </div>
              </div>
            ))}
          </Card>

          <Card title="Team load"
                action={<span className="card-note">last {teamData?.window_days ?? 14} days</span>}>
            {operators.length === 0 ? (
              <div className="empty" style={{ padding: 22 }}>No operators yet.</div>
            ) : (
              <div className="team-table">
                {operators.map((o) => (
                  <div key={o.user_id}
                       className={`team-row${o.assignable ? '' : ' team-row-readonly'}`}>
                    <div className="team-who">
                      <span className="team-name">{o.name}</span>
                      <span className="nav-badge badge-blue">L{o.xp.level} {o.xp.title}</span>
                      {!o.assignable && (
                        <span className="team-scope" title="Not on a team you supervise">
                          <i className="ti ti-lock" aria-hidden="true" /> other team
                        </span>
                      )}
                    </div>
                    <div className="team-load">
                      <span className="team-load-track">
                        <span className="team-load-fill"
                              style={{
                                width: `${Math.max(4, (o.open_tasks / busiest) * 100)}%`,
                                background: o.open_tasks >= busiest && busiest > 1
                                  ? 'var(--accent-amber)' : 'var(--brand)',
                              }} />
                      </span>
                      <span className="team-load-n">{o.open_tasks} open</span>
                    </div>
                    <div className="team-through">
                      <span className="team-through-n">{o.closed_window}</span>
                      <span className="team-through-k">closed</span>
                    </div>
                    <div className="team-score">
                      {o.avg_score === null || o.avg_score === undefined
                        ? <span className="team-score-none">—</span>
                        : <span className="team-score-n">{Math.round(o.avg_score)}%</span>}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </Card>
        </div>

        <div className="dispatch-col">
          <TwinStrip />
          <Card title={`In the field (${running.length})`}>
            {running.length === 0
              ? <div className="empty" style={{ padding: 22 }}>Nothing in progress.</div>
              : running.slice(0, 6).map((t) => (
                <div key={t.task_id} className="task-card">
                  <div className="task-card-top">
                    <span className="task-code">{t.code}</span>
                    <StatusChip status={t.status} />
                  </div>
                  <div className="task-title">{t.title}</div>
                </div>
              ))}
          </Card>
        </div>
      </div>
    </div>
  )
}

/* ────────────────────────────────────────────────────────────────────────
 * Operator
 * ──────────────────────────────────────────────────────────────────────── */

const SEVERITY_ORDER = ['critical', 'serious', 'warning', 'info']

function OperatorDashboard() {
  const navigate = useNavigate()
  const [busy, setBusy] = useState(false)
  const [actionError, setActionError] = useState(null)
  const [blocking, setBlocking] = useState(null)
  const [reason, setReason] = useState('')
  const [selectedId, setSelectedId] = useState(null)

  const { can } = useWork()
  const { data: inbox, refetch } = usePolling(() => api.work.inbox(), 10000, [])
  const { data: stats, refetch: refetchStats } = usePolling(() => api.work.myStats(), 20000, [])
  const { data: xpData } = usePolling(() => api.work.xp(), 30000, [])
  const { data: runsData } = usePolling(() => api.scenario.runs(25), 30000, [])

  const today = useMemo(() => inbox?.today || [], [inbox])
  const earlier = useMemo(() => inbox?.earlier || [], [inbox])
  const awaiting = inbox?.awaiting_signoff || []
  const openJobs = useMemo(() => [...today, ...earlier], [today, earlier])

  // The side panel follows the selected job, defaulting to the first open one.
  // Falling back rather than showing nothing matters: an operator with work
  // should never land on an empty machine panel.
  const selected = useMemo(
    () => openJobs.find((t) => t.task_id === selectedId) || openJobs[0] || null,
    [openJobs, selectedId])

  const runs = runsData?.runs || []
  const ledger = xpData?.ledger || []
  const totals = stats?.totals || {}

  const severityRows = useMemo(() => SEVERITY_ORDER.map((key) => ({
    key,
    label: key,
    value: stats?.severity_mix?.[key] || 0,
    color: SEVERITY_COLOR[key] || 'var(--brand)',
  })), [stats])

  // Score history reads oldest -> newest so the line runs the way time does.
  const scorePoints = useMemo(() => runs
    .filter((r) => r.status === 'completed' && r.score !== null && r.score !== undefined)
    .slice(0, 12)
    .reverse()
    .map((r) => ({ key: r.run_id, value: r.score, label: r.scenario_id })), [runs])

  const block = useCallback(async (taskId) => {
    if (!reason.trim()) return
    setBusy(true)
    setActionError(null)
    try {
      await api.work.block(taskId, reason.trim())
      setBlocking(null)
      setReason('')
      await Promise.all([refetch(), refetchStats()])
    } catch (e) {
      setActionError(e)
    } finally {
      setBusy(false)
    }
  }, [reason, refetch, refetchStats])

  const JobCard = ({ task }) => {
    const active = selected?.task_id === task.task_id
    return (
      <div className={`job-card${active ? ' job-card-active' : ''}`}
           onClick={() => setSelectedId(task.task_id)}
           role="button" tabIndex={0}
           onKeyDown={(e) => { if (e.key === 'Enter') setSelectedId(task.task_id) }}>
        <div className="job-card-head">
          <span className="task-code">{task.code}</span>
          <SeverityChip severity={task.severity} />
          <StatusChip status={task.status} />
          {active && <span className="job-card-showing">
            <i className="ti ti-eye" aria-hidden="true" /> showing
          </span>}
        </div>
        <div className="job-card-title">{task.title}</div>
        <div className="job-card-meta">
          {task.asset_name && (
            <span><i className="ti ti-box" aria-hidden="true" /> {task.asset_name}</span>
          )}
          {task.assigned_by_name && (
            <span><i className="ti ti-user-check" aria-hidden="true" /> {task.assigned_by_name}</span>
          )}
          <span><i className="ti ti-clock" aria-hidden="true" /> {ago(task.assigned_at || task.created_at)}</span>
        </div>
        {task.detail && <div className="job-card-detail">{task.detail}</div>}

        <div className="job-card-actions" onClick={(e) => e.stopPropagation()}>
          <button className="btn btn-ghost btn-sm"
                  onClick={() => navigate(`/order/${encodeURIComponent(task.task_id)}`)}>
            <i className="ti ti-clipboard-text" aria-hidden="true" /> Work order
          </button>
          {task.scenario_id && (
            <button className="btn btn-ghost btn-sm"
                    onClick={() => navigate(`/repair/${encodeURIComponent(task.task_id)}`)}>
              <i className="ti ti-sparkles" aria-hidden="true" /> Repair with AI
            </button>
          )}
          {can('scenario.run') && (task.scenario_id ? (
            <button className="btn btn-primary"
                    onClick={() => navigate(`/fix/${encodeURIComponent(task.task_id)}`)}>
              <i className="ti ti-tool" aria-hidden="true" /> Fix the fault
            </button>
          ) : (
            <span className="job-noproc">No procedure yet</span>
          ))}
          {can('task.block') && task.status !== 'blocked' && (
            <button className="btn btn-ghost btn-sm"
                    onClick={() => { setBlocking(blocking === task.task_id ? null : task.task_id); setReason('') }}>
              I&apos;m blocked
            </button>
          )}
        </div>

        {blocking === task.task_id && (
          <div className="block-box" onClick={(e) => e.stopPropagation()}>
            <input className="assign-note" placeholder="What is stopping you?"
                   value={reason} onChange={(e) => setReason(e.target.value)} />
            <button className="btn btn-ghost btn-sm" disabled={busy || !reason.trim()}
                    onClick={() => block(task.task_id)}>
              Report it
            </button>
          </div>
        )}
      </div>
    )
  }

  return (
    <div className="panel">
      <PanelHeader title="Today" subtitle="What you are on shift to fix">
        <button className="btn btn-ghost" onClick={() => { refetch(); refetchStats() }} disabled={busy}>
          <i className="ti ti-refresh" aria-hidden="true" /> Refresh
        </button>
      </PanelHeader>

      {actionError && <ErrorBox error={actionError} />}

      <div className="shift-strip">
        <div className="shift-count">
          <div className="shift-n">{openJobs.length}</div>
          <div className="shift-label">open job{openJobs.length === 1 ? '' : 's'}</div>
        </div>
        <XpBar xp={inbox?.xp || stats?.xp} compact />
        {stats?.streak_days > 0 && (
          <div className="shift-streak">
            <i className="ti ti-flame" aria-hidden="true" />
            <b>{stats.streak_days}</b> day streak
          </div>
        )}
      </div>

      <div className="kpi-row">
        <StatTile label="Assigned" value={totals.open ?? 0} icon="ti-clipboard"
                  hint="waiting on you" tone={totals.open ? 'brand' : undefined} />
        <StatTile label="In progress" value={totals.in_progress ?? 0} icon="ti-run"
                  hint="started, not finished" />
        <StatTile label="Blocked" value={totals.blocked ?? 0} icon="ti-hand-stop"
                  hint="waiting on someone else"
                  tone={totals.blocked ? 'critical' : undefined} />
        <StatTile label="Awaiting sign-off" value={totals.awaiting_signoff ?? 0}
                  icon="ti-checkbox" hint="your supervisor has it" />
        <StatTile label="Average score" icon="ti-target-arrow"
                  value={stats?.score?.avg === null || stats?.score?.avg === undefined
                    ? '—' : `${Math.round(stats.score.avg)}%`}
                  hint={stats?.score?.n ? `over ${stats.score.n} graded job${stats.score.n === 1 ? '' : 's'}` : 'no graded jobs yet'}
                  tone="good" />
      </div>

      <div className="dispatch-grid">
        <div className="dispatch-col">
          <Card title={`Assigned today (${today.length})`}>
            {today.length === 0 ? (
              <div className="empty" style={{ padding: 26 }}>
                <i className="ti ti-coffee" style={{ fontSize: 24, display: 'block', marginBottom: 8 }} />
                Nothing new today.
              </div>
            ) : today.map((t) => <JobCard key={t.task_id} task={t} />)}
          </Card>

          {earlier.length > 0 && (
            <Card title={`Carried over (${earlier.length})`}>
              {earlier.map((t) => <JobCard key={t.task_id} task={t} />)}
            </Card>
          )}

          {awaiting.length > 0 && (
            <Card title={`Done — waiting on your supervisor (${awaiting.length})`}>
              {awaiting.map((t) => (
                <div key={t.task_id} className="job-card job-card-done">
                  <div className="job-card-head">
                    <span className="task-code">{t.code}</span>
                    <StatusChip status={t.status} />
                    {t.score !== null && t.score !== undefined && (
                      <span className="task-score">
                        <i className="ti ti-target-arrow" aria-hidden="true" /> {Math.round(t.score)}%
                      </span>
                    )}
                    {t.xp_awarded > 0 && (
                      <span className="task-xp">
                        <i className="ti ti-award" aria-hidden="true" /> +{t.xp_awarded} XP
                      </span>
                    )}
                  </div>
                  <div className="job-card-title">{t.title}</div>
                </div>
              ))}
            </Card>
          )}

          {/* ── The former Progress page, folded in ────────────────────── */}
          <div className="chart-duo">
            <Card title="Jobs closed"
                  action={<span className="card-note">last {stats?.window_days ?? 14} days</span>}>
              <DayBars series={stats?.series || []} label="closed" />
            </Card>
            <Card title="Your open work by severity">
              <LabelledBars rows={severityRows}
                            empty="Nothing assigned to you right now." />
            </Card>
          </div>

          <Card title="Score trend"
                action={<span className="card-note">last {scorePoints.length} graded runs</span>}>
            <TrendLine points={scorePoints} suffix="%"
                       empty="Finish two graded drills and your trend appears here." />
          </Card>

          <Card title={`XP ledger (${ledger.length})`}>
            {ledger.length === 0 ? (
              <div className="empty" style={{ padding: 24 }}>
                <i className="ti ti-award" style={{ fontSize: 22, display: 'block', marginBottom: 8 }} />
                No XP yet. Fix your first fault to get on the board.
              </div>
            ) : (
              <div className="history-list">
                {ledger.slice(0, 8).map((e) => (
                  <div key={e.entry_id} className="ledger-row">
                    <span className="ledger-points">+{e.points}</span>
                    <div className="ledger-body">
                      <div className="ledger-reason">
                        {e.reason === 'task:passed' ? 'Fault fixed' : 'Attempt recorded'}
                      </div>
                      {e.detail && <div className="ledger-detail">{e.detail}</div>}
                    </div>
                    <span className="history-when">{ago(e.at)}</span>
                  </div>
                ))}
              </div>
            )}
          </Card>

          <Card title={`Practice history (${runs.length})`}
                action={<button className="btn btn-ghost btn-sm" onClick={() => navigate('/training')}>
                  Training
                </button>}>
            {runs.length === 0 ? (
              <div className="empty" style={{ padding: 24 }}>No procedure runs yet.</div>
            ) : (
              <div className="history-list">
                {runs.slice(0, 8).map((r) => (
                  <div key={r.run_id} className="run-row">
                    <i className={`ti ${r.passed ? 'ti-circle-check' : r.status === 'completed' ? 'ti-circle-x' : 'ti-clock'}`}
                       style={{ color: r.passed ? 'var(--accent-green)' : r.status === 'completed' ? 'var(--accent-red)' : 'inherit' }}
                       aria-hidden="true" />
                    <span className="run-name">{r.scenario_id}</span>
                    <span className="run-score">
                      {r.score === null || r.score === undefined ? 'in progress' : `${Math.round(r.score)}%`}
                    </span>
                    <span className="run-detail">{r.wrong_steps} wrong · {r.hints_used} hints</span>
                    <span className="history-when">{ago(r.completed_at || r.started_at)}</span>
                  </div>
                ))}
              </div>
            )}
          </Card>
        </div>

        <div className="dispatch-col">
          <TaskTwinCard
            task={selected}
            onWorkOrder={selected ? () => navigate(`/order/${encodeURIComponent(selected.task_id)}`) : null}
            onRepair={selected ? () => navigate(`/repair/${encodeURIComponent(selected.task_id)}`) : null}
            onFix={selected && can('scenario.run')
              ? () => navigate(`/fix/${encodeURIComponent(selected.task_id)}`) : null}
          />
        </div>
      </div>
    </div>
  )
}

export default function RoleDashboard() {
  const { persona, hasWorkspace, loading, openBackend, signedIn } = useWork()

  if (loading) return <Loading label="Loading your dashboard…" />

  // No persona (dev mode, or an account with no membership) falls through to the
  // twin's own operations overview, which is what `/` meant before roles existed.
  if (!hasWorkspace) {
    return <NoWorkspace what="The dashboard" openBackend={openBackend} signedIn={signedIn} />
  }
  if (persona === 'supervisor') return <SupervisorDashboard />
  return <OperatorDashboard />
}
