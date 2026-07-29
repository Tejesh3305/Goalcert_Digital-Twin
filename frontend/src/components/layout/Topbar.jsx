import { usePolling } from '../../hooks/useApi'
import api from '../../api/client'
import { useTwin } from '../../context/TwinContext'
import TwinSwitcher from './TwinSwitcher'
import UserMenu from './UserMenu'
import Logo from '../ui/Logo'

/** Top bar: brand, twin switcher, live platform stats, health dot. */
export default function Topbar() {
  const { activeTenant, activeTwin } = useTwin()

  const { data: health, error: healthError } = usePolling(() => api.health(), 6000, [])
  const { data: stats } = usePolling(
    () => api.stats(activeTenant), 4000, [activeTenant], { skip: !activeTenant },
  )

  // Three states: server unreachable (healthError) > degraded (DB down) > healthy.
  const serverDown = !!healthError
  const status = serverDown ? 'down' : (health?.status || 'connecting')
  const bus = health?.bus?.backend
  const assets = stats?.entity_counts?.PhysicalAsset || 0
  const total = stats?.total_entities || 0
  const events = stats?.changelog_events || 0

  const dot = { healthy: '', degraded: 'amber', down: 'red', connecting: 'amber' }[status]
  const dotTitle = {
    healthy: `Neo4j connected · bus: ${bus}`,
    degraded: 'Database offline (Docker not running) — UI works; live graph data is paused',
    down: 'Backend server unreachable',
    connecting: 'Connecting…',
  }[status]

  return (
    <div className="topbar">
      <Logo size={30} />
      <TwinSwitcher />
      <div className="topbar-breadcrumb">
        {activeTwin
          ? <><b>{activeTwin.name}</b> · {activeTwin.domain} twin</>
          : <span className="muted">No twin selected</span>}
      </div>
      {status === 'degraded' && (
        <div className="topbar-stat" style={{ color: 'var(--accent-amber)', borderColor: 'rgba(224,150,47,.35)' }}
             title={dotTitle}>
          <i className="ti ti-database-off" /> DB offline
        </div>
      )}
      {status === 'down' && (
        <div className="topbar-stat" style={{ color: 'var(--accent-red)', borderColor: 'rgba(226,86,78,.35)' }}
             title={dotTitle}>
          <i className="ti ti-plug-connected-x" /> server down
        </div>
      )}
      <div className="topbar-stat"><b>{total}</b> entities</div>
      <div className="topbar-stat"><b>{assets}</b> assets</div>
      <div className="topbar-stat"><b>{events}</b> log events</div>
      {bus && <div className="topbar-stat">bus: <b>{bus}</b></div>}
      <div className={`status-dot ${dot}`} title={dotTitle} />
      {/* Who you are and which organisation you're acting for. Last, so it sits
          at the right edge where an account control is expected. */}
      <UserMenu />
    </div>
  )
}
