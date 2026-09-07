/**
 * AppShell.jsx — the platform's chrome, with the navigation the persona owns.
 *
 * This IS the twin's original shell (`.app-root > Topbar + (.body > .sidebar +
 * .content)`), unchanged in structure and styling. The only difference from
 * before personas existed is that the left rail renders `workspace.nav` instead
 * of one hardcoded list.
 *
 * Keeping the structure identical is the point. Every rule in app.css hangs off
 * `.app-root` / `.body` / `.content` / `.sidebar` / `.nav-item`, so a shell that
 * invented its own markup would have to restate the entire design system — and
 * would drift from it the first time either side changed. A supervisor and an
 * operator should feel like they are using the same product, because they are.
 */

import { NavLink, useNavigate } from 'react-router-dom'
import api from '../api/client'
import CommandPalette from '../components/CommandPalette'
import Topbar from '../components/layout/Topbar'
import { useTwin } from '../context/TwinContext'
import { useWork } from '../context/WorkContext'
import { usePolling } from '../hooks/useApi'
import { hasBackendState } from '../lib/simTwins'
import '../styles/persona.css'

/**
 * The left rail, driven by the persona's nav list.
 *
 * Badges stay live for the entries where a count means something. The dispatch
 * badge is the load-bearing one for a supervisor — an unassigned fault is a
 * decision nobody has made yet, and it should be visible from every page — and
 * the operator's job count does the same for them.
 */
function PersonaSidebar({ workspace }) {
  const { activeTenant } = useTwin()
  const { can, xp, persona } = useWork()
  const navigate = useNavigate()

  // Gate each poll on the CAPABILITY, not on the nav entry. The no-persona
  // fallback (dev mode with auth off) lists both work pages but holds no
  // capabilities, so keying off the nav would poll endpoints that 403 on a loop.
  const mayReadQueue = can('queue.read')
  const mayReadOwn = can('task.read_own')

  const { data: stats } = usePolling(
    () => api.stats(activeTenant), 5000, [activeTenant],
    { skip: !hasBackendState(activeTenant) })
  const { data: queue } = usePolling(
    () => api.work.queue(), 15000, [], { skip: !mayReadQueue })
  const { data: inbox } = usePolling(
    () => api.work.inbox(), 20000, [], { skip: !mayReadOwn })

  const findings = stats?.total_findings || 0
  const incidents = stats?.entity_counts?.Incident || 0
  const unassigned = mayReadQueue ? (queue?.count || 0) : 0
  const myOpen = mayReadOwn
    ? (inbox?.today?.length || 0) + (inbox?.earlier?.length || 0) : 0

  const badgeFor = (id) => {
    if (id === 'dispatch' && unassigned) return { cls: 'badge-red', text: unassigned }
    if (id === 'dashboard' && myOpen) return { cls: 'badge-red', text: myOpen }
    // Findings are the PLANT's problem, so the badge sits on Twin — the role
    // dashboard counts work, not faults.
    if (id === 'twin' && findings) return { cls: 'badge-red', text: findings }
    if (id === 'predict' && incidents) return { cls: 'badge-amber', text: incidents }
    return null
  }

  return (
    <div className="sidebar">
      <div className="sidebar-nav">
        {workspace.nav.map((item, i) => {
          if (item.section) return <div key={`s${i}`} className="sidebar-section">{item.section}</div>
          const badge = badgeFor(item.id)
          return (
            <NavLink
              key={item.id}
              to={item.path}
              end={item.path === '/'}
              className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
            >
              <i className={`ti ${item.icon}`} aria-hidden="true" />
              {item.label}
              {badge && <span className={`nav-badge ${badge.cls}`}>{badge.text}</span>}
            </NavLink>
          )
        })}
      </div>

      <div className="sidebar-foot">
        {/* The operator's standing, where the "Build a twin" shortcut sits for a
            supervisor. Both are the thing that persona most wants one click from. */}
        {persona === 'frontline' && xp && (
          <div className="rail-xp">
            <div className="rail-xp-head">
              <i className="ti ti-award" aria-hidden="true" />
              <span>L{xp.level} · {xp.title}</span>
            </div>
            <div className="xp-track">
              <div className="xp-fill" style={{ width: `${Math.min(100, xp.pct)}%` }} />
            </div>
            <div className="rail-xp-foot">
              {xp.to_next > 0 ? `${xp.to_next} XP to level ${xp.level + 1}` : 'Next level reached'}
            </div>
          </div>
        )}

        {workspace.nav.some((i) => i.id === 'concierge') && (
          <div className="sidebar-help" onClick={() => navigate('/build')}>
            <i className="ti ti-sparkles" aria-hidden="true" />
            Build a twin
          </div>
        )}

        <div className="sidebar-ver">Goalcert · {workspace.label}</div>
      </div>
    </div>
  )
}

export default function AppShell({ workspace, children }) {
  return (
    <div className="app-root" data-persona={workspace.accent}>
      <Topbar />
      <div className="body">
        <PersonaSidebar workspace={workspace} />
        <div className="content">{children}</div>
      </div>
      <CommandPalette />
    </div>
  )
}
