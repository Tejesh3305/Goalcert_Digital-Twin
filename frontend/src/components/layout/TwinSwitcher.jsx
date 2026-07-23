import { useTwin } from '../../context/TwinContext'
import { SIM_TWINS, simTenantFor, isSimTenant } from '../../lib/simTwins'

/** Dropdown to switch the active twin. Sits in the top bar. */
export default function TwinSwitcher() {
  const { twins, activeTenant, setActiveTenant, loading } = useTwin()

  if (loading && !twins.length && !isSimTenant(activeTenant)) {
    return <div className="topbar-stat">loading twins…</div>
  }
  if (!twins.length && !isSimTenant(activeTenant)) {
    return <div className="topbar-stat">no twins yet</div>
  }

  return (
    <select
      className="select"
      style={{ width: 'auto', padding: '5px 9px', fontSize: 12 }}
      value={activeTenant || ''}
      onChange={(e) => setActiveTenant(e.target.value)}
    >
      {twins.map((t) => (
        <option key={t.tenant_id} value={t.tenant_id}>
          {t.name} ({t.domain})
        </option>
      ))}
      {SIM_TWINS.map((s) => (
        <option key={s.domain} value={simTenantFor(s.domain)}>
          {s.label}
        </option>
      ))}
    </select>
  )
}
