/**
 * WorkBits.jsx — the small pieces the dispatch panels share.
 *
 * These live together because a severity chip that means one thing on the
 * supervisor's queue and something else on the operator's card is worse than no
 * chip at all — the colour is the whole message, and it has to be the same
 * message everywhere.
 */

/** Severity → colour. Matches the twin's Finding severities exactly. */
const SEVERITY = {
  critical: { cls: 'badge-red', label: 'Critical' },
  serious: { cls: 'badge-amber', label: 'Serious' },
  warning: { cls: 'badge-amber', label: 'Warning' },
  info: { cls: 'badge-blue', label: 'Info' },
}

/** Status → colour. `open` is red because an unowned fault is the urgent state. */
const STATUS = {
  open: { cls: 'badge-red', label: 'Unassigned' },
  assigned: { cls: 'badge-blue', label: 'Assigned' },
  in_progress: { cls: 'badge-amber', label: 'In progress' },
  blocked: { cls: 'badge-amber', label: 'Blocked' },
  resolved: { cls: 'badge-green', label: 'Fixed' },
  closed: { cls: 'badge-muted', label: 'Closed' },
}

export function SeverityChip({ severity }) {
  const s = SEVERITY[severity] || SEVERITY.info
  return <span className={`nav-badge ${s.cls}`}>{s.label}</span>
}

export function StatusChip({ status }) {
  const s = STATUS[status] || { cls: 'badge-muted', label: status }
  return <span className={`nav-badge ${s.cls}`}>{s.label}</span>
}

/**
 * The level bar.
 *
 * `into`/`span` come from the SERVER (work/xp.py `progress`), not from arithmetic
 * here. Computing the bar in the browser is how a bar ends up disagreeing with
 * the level badge printed next to it.
 */
export function XpBar({ xp, compact = false }) {
  if (!xp) return null
  return (
    <div className={`xp-bar ${compact ? 'xp-bar-compact' : ''}`}>
      <div className="xp-head">
        <span className="xp-level">
          <i className="ti ti-award" aria-hidden="true" /> L{xp.level} · {xp.title}
        </span>
        <span className="xp-total">{xp.total_xp} XP</span>
      </div>
      <div className="xp-track">
        <div className="xp-fill" style={{ width: `${Math.min(100, xp.pct)}%` }} />
      </div>
      {!compact && (
        <div className="xp-foot">
          {xp.to_next > 0
            ? `${xp.to_next} XP to level ${xp.level + 1}`
            : 'Next level reached'}
        </div>
      )}
    </div>
  )
}

/** A task card, used by both consoles so a fault reads the same to both roles. */
export function TaskCard({ task, children, onClick, active = false }) {
  return (
    <div
      className={`task-card ${active ? 'task-card-active' : ''} ${onClick ? 'task-card-click' : ''}`}
      onClick={onClick}
      role={onClick ? 'button' : undefined}
      tabIndex={onClick ? 0 : undefined}
      onKeyDown={onClick ? (e) => { if (e.key === 'Enter') onClick(e) } : undefined}
    >
      <div className="task-card-top">
        <span className="task-code">{task.code}</span>
        <SeverityChip severity={task.severity} />
        <StatusChip status={task.status} />
      </div>
      <div className="task-title">{task.title}</div>
      <div className="task-meta">
        {task.asset_name && (
          <span><i className="ti ti-box" aria-hidden="true" /> {task.asset_name}</span>
        )}
        {task.behavior_id && (
          <span><i className="ti ti-activity" aria-hidden="true" /> {task.behavior_id}</span>
        )}
        {task.xp_awarded > 0 && (
          <span className="task-xp"><i className="ti ti-award" aria-hidden="true" /> +{task.xp_awarded} XP</span>
        )}
      </div>
      {children}
    </div>
  )
}

/** Relative time, short. Absolute dates are noise on a shift-length timeline. */
export function ago(iso) {
  if (!iso) return ''
  const secs = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000)
  if (secs < 60) return 'just now'
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`
  return `${Math.floor(secs / 86400)}d ago`
}

/**
 * The panel shown when there is no persona to render for.
 *
 * TWO CAUSES, AND THEY NEED DIFFERENT SENTENCES. Both arrive here as "no
 * persona", and telling a developer their account lacks a role when the real
 * answer is "the server is running with authentication off, so nobody is signed
 * in" sends them to fix the wrong thing. It is reached by running the launcher
 * with `-NoAuth`, which sets NXR_DEV_MODE=1 and leaves the API open.
 */
export function NoWorkspace({ what = 'this page', openBackend = false, signedIn = false }) {
  if (openBackend && !signedIn) {
    return (
      <div className="empty" style={{ padding: '48px 24px' }}>
        <i className="ti ti-lock-open" style={{ fontSize: 28, display: 'block', marginBottom: 10 }} />
        <div style={{ fontWeight: 600, marginBottom: 6 }}>Nobody is signed in</div>
        <div style={{ color: 'var(--muted)', maxWidth: 500, margin: '0 auto', lineHeight: 1.6 }}>
          The server is running with authentication disabled
          (<code>NXR_DEV_MODE=1</code>), so there is no account and therefore no
          supervisor or operator role to work as.
          <br /><br />
          Restart with <code>./start.ps1</code> (authentication is on by
          default) and sign in at <b>/login</b> — dispatch is per-account, so
          it needs a real session.
        </div>
      </div>
    )
  }
  return (
    <div className="empty" style={{ padding: '48px 24px' }}>
      <i className="ti ti-id-badge-2" style={{ fontSize: 28, display: 'block', marginBottom: 10 }} />
      <div style={{ fontWeight: 600, marginBottom: 6 }}>No operational role on this account</div>
      <div style={{ color: 'var(--muted)', maxWidth: 460, margin: '0 auto', lineHeight: 1.55 }}>
        {what} needs a persona — supervisor or operator. Your account has a data
        role but no job assigned, so there is no work to show. Ask an
        administrator to set one.
      </div>
    </div>
  )
}
