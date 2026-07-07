/**
 * NetworkMap — the fleet-domain live spatial map. Renders the tram network
 * geometry (routes, stops, depots, substations) with live vehicle positions
 * from GET /twins/{tenant}/network. Blocked routes render red/dashed and their
 * held vehicles pulse. Pure render of a `net` payload; polling lives upstream.
 */
export default function NetworkMap({ net }) {
  if (!net || !net.routes) return null
  const routes = net.routes || []
  const nodes = net.nodes || []
  const subs = net.substations || []
  const depots = net.depots || []
  const vehicles = net.vehicles || []
  const routeColor = Object.fromEntries(routes.map((r) => [r.id, r.color]))

  return (
    <div style={{ position: 'relative' }}>
      <svg viewBox="0 0 100 100" style={{ width: '100%', height: 'auto', display: 'block',
        background: 'radial-gradient(circle at 50% 42%, var(--surface), var(--surface2))',
        borderRadius: 12, border: '1px solid var(--border)' }}>
        {/* routes */}
        {routes.map((r) => {
          const pts = (r.points || []).map((p) => p.join(',')).join(' ')
          const blocked = r.status === 'blocked'
          return (
            <polyline key={r.id} points={pts} fill="none"
              stroke={blocked ? 'var(--accent-red)' : r.color}
              strokeWidth={blocked ? 1.1 : 1.6}
              strokeOpacity={blocked ? 0.9 : 0.85}
              strokeDasharray={blocked ? '2 1.6' : undefined}
              strokeLinecap="round" strokeLinejoin="round" />
          )
        })}
        {/* substations */}
        {subs.map((s) => (
          <g key={s.id}>
            <rect x={s.x - 1.4} y={s.y - 1.4} width="2.8" height="2.8" rx="0.5"
              fill="var(--surface)" stroke="var(--accent-blue)" strokeWidth="0.6" />
          </g>
        ))}
        {/* depots */}
        {depots.map((d) => (
          <rect key={d.id} x={d.x - 1.8} y={d.y - 1.8} width="3.6" height="3.6" rx="0.6"
            transform={`rotate(45 ${d.x} ${d.y})`}
            fill="var(--surface)" stroke="var(--muted)" strokeWidth="0.6" />
        ))}
        {/* stops */}
        {nodes.map((n) => (
          <g key={n.id}>
            <circle cx={n.x} cy={n.y} r="1.2" fill="var(--surface)"
              stroke="var(--text)" strokeWidth="0.5" />
            <text x={n.x + 1.8} y={n.y + 0.6} fontSize="2.1" fill="var(--muted)"
              style={{ fontFamily: 'var(--font)' }}>{n.name}</text>
          </g>
        ))}
        {/* live vehicles */}
        {vehicles.map((v) => {
          const held = v.status === 'held'
          return (
            <circle key={v.id} cx={v.x} cy={v.y} r={held ? 1.5 : 1.3}
              fill={held ? 'var(--accent-red)' : (routeColor[v.route] || 'var(--brand)')}
              stroke="#fff" strokeWidth="0.4">
              {held && <animate attributeName="opacity" values="1;0.35;1" dur="1.1s" repeatCount="indefinite" />}
            </circle>
          )
        })}
      </svg>
      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginTop: 8, fontSize: 11.5 }}>
        {routes.map((r) => (
          <span key={r.id} style={{ display: 'inline-flex', alignItems: 'center', gap: 5,
            color: r.status === 'blocked' ? 'var(--accent-red)' : 'var(--muted)' }}>
            <span style={{ width: 9, height: 9, borderRadius: 2,
              background: r.status === 'blocked' ? 'var(--accent-red)' : r.color }} />
            {r.name} {r.status === 'blocked' && <b>· BLOCKED</b>}
          </span>
        ))}
        <span className="muted" style={{ marginLeft: 'auto' }}>
          {net.fleet_size} trams · {net.route_km} route-km
        </span>
      </div>
    </div>
  )
}
