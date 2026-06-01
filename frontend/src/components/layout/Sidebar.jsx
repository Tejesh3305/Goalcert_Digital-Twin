import { NavLink } from 'react-router-dom'
import { NAV } from '../../nav'
import { usePolling } from '../../hooks/useApi'
import api from '../../api/client'
import { useTwin } from '../../context/TwinContext'

/** Left navigation. Badges show live counts for the active twin where it
 *  makes sense (findings, incidents). */
export default function Sidebar() {
  const { activeTenant } = useTwin()
  const { data: stats } = usePolling(
    () => api.stats(activeTenant), 5000, [activeTenant], { skip: !activeTenant },
  )

  const findings = stats?.total_findings || 0
  const incidents = stats?.entity_counts?.Incident || 0

  const badgeFor = (id) => {
    if (id === 'live' && findings) return { cls: 'badge-red', text: findings }
    if (id === 'agents') return { cls: 'badge-blue', text: 4 }
    if (id === 'compliance') return { cls: 'badge-amber', text: 4 }
    if (id === 'predict' && incidents) return { cls: 'badge-amber', text: incidents }
    return null
  }

  return (
    <div className="sidebar">
      {NAV.map((item, i) => {
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
  )
}
