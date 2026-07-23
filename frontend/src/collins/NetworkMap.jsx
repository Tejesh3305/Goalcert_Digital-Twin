// NetworkMap.jsx — the live schematic network map for fleet twins.
//
// Renders the twin's network geometry (routes / stops / depots / substations)
// as an SVG transit map and animates every vehicle from the live physics
// (`GET /api/twins/{tenant}/network`, polled while the twin runs). Blocked or
// degraded routes glow red/amber; clicking a tram or a stop opens an inspector.
import React, { useEffect, useMemo, useRef, useState } from 'react'
import api from './api.js'
import { Icon } from './lib.jsx'

// Interpolate a point at fraction t (0..1) along a polyline.
function pointAt(points, t) {
  if (!points || points.length < 2) return [0, 0]
  const segs = []
  let total = 0
  for (let i = 0; i < points.length - 1; i++) {
    const dx = points[i + 1][0] - points[i][0], dy = points[i + 1][1] - points[i][1]
    const len = Math.hypot(dx, dy) || 1e-6
    segs.push(len); total += len
  }
  let d = Math.max(0, Math.min(1, t)) * total
  for (let i = 0; i < segs.length; i++) {
    if (d <= segs[i]) {
      const k = d / segs[i]
      return [points[i][0] + (points[i + 1][0] - points[i][0]) * k,
              points[i][1] + (points[i + 1][1] - points[i][1]) * k]
    }
    d -= segs[i]
  }
  return points[points.length - 1]
}

// Small deterministic per-route offset so routes sharing a corridor render as
// parallel strands (transit-map style) instead of a single overdrawn line.
function routeOffset(idx) {
  const k = (idx % 5) - 2
  return [k * 0.28, k * 0.28]
}

const STATUS_COLOR = { ok: null, degraded: '#d97706', blocked: '#e11d48' }

export default function NetworkMap({ tenant, height = 460, running = true }) {
  const [net, setNet] = useState(null)
  const [sel, setSel] = useState(null)          // {kind:'vehicle'|'stop'|'route', ...}
  const [focusRoute, setFocusRoute] = useState(null)
  const geomRef = useRef(null)                  // static geometry, cached once

  useEffect(() => {
    let alive = true
    async function poll() {
      try {
        const n = await api.twinNetwork(tenant)
        if (!alive || !n || !n.vehicles) return
        if (n.geometry) geomRef.current = n.geometry
        setNet(n)
      } catch { /* twin may still be seeding */ }
    }
    poll()
    const iv = running ? setInterval(poll, 2000) : null
    return () => { alive = false; if (iv) clearInterval(iv) }
  }, [tenant, running])

  const geom = geomRef.current
  const routes = geom?.routes || []
  const nodes = geom?.nodes || {}
  const routeStatus = net?.route_status || {}
  const vehicles = net?.vehicles || []
  const routeById = useMemo(() => Object.fromEntries(routes.map(r => [r.id, r])), [routes])

  const inService = vehicles.filter(v => v.status === 'ok' || v.status === 'warn').length
  const stopped = vehicles.filter(v => v.status === 'stopped').length
  const disrupted = Object.entries(routeStatus).filter(([, s]) => s !== 'ok')

  if (!geom) {
    return <div className="hero3d" style={{ height }}>
      <div className="lbl"><div className="big">⬡ Network</div>Loading the live network map…</div>
    </div>
  }

  return (
    <div className="netmap" style={{ height, position: 'relative' }}>
      <svg viewBox="-2 -2 104 90" style={{ width: '100%', height: '100%', display: 'block' }}
        onClick={() => { setSel(null); setFocusRoute(null) }}>
        {/* free-tram-zone style CBD tint */}
        <rect x={28} y={30} width={26} height={20} rx={2.5} fill="rgba(120,190,32,.07)"
          stroke="rgba(120,190,32,.25)" strokeWidth={0.2} strokeDasharray="1 .7" />

        {/* route polylines */}
        {routes.map((r, i) => {
          const [ox, oy] = routeOffset(i)
          const pts = r.points.map(([x, y]) => `${x + ox},${y + oy}`).join(' ')
          const st = routeStatus[r.id] || 'ok'
          const dim = focusRoute && focusRoute !== r.id
          return (
            <g key={r.id} style={{ cursor: 'pointer' }}
              onClick={e => { e.stopPropagation(); setFocusRoute(focusRoute === r.id ? null : r.id); setSel({ kind: 'route', route: r }) }}>
              <polyline points={pts} fill="none" stroke={r.color}
                strokeWidth={focusRoute === r.id ? 1.15 : 0.65} strokeLinecap="round" strokeLinejoin="round"
                opacity={dim ? 0.12 : 0.9} />
              {st !== 'ok' && !dim && (
                <polyline points={pts} fill="none" stroke={STATUS_COLOR[st]}
                  strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round"
                  opacity={0.45} strokeDasharray="1.6 1.6" className="netmap-flash" />
              )}
            </g>
          )
        })}

        {/* substations + depots */}
        {(geom.substations || []).map(s => (
          <g key={s.id} opacity={0.85}>
            <rect x={s.x - 0.7} y={s.y - 0.7} width={1.4} height={1.4} rx={0.25}
              fill="var(--netmap-infra, #f5f0e6)" stroke="#d97706" strokeWidth={0.18} />
            <text x={s.x} y={s.y + 0.42} textAnchor="middle" fontSize={1.05} fill="#d97706">⚡</text>
            <title>{s.name} · {s.capacity_mw} MW</title>
          </g>
        ))}
        {(geom.depots || []).map(d => (
          <g key={d.id} opacity={0.85}>
            <rect x={d.x - 0.8} y={d.y - 0.6} width={1.6} height={1.2} rx={0.2}
              fill="var(--netmap-infra, #eef1f6)" stroke="#64748b" strokeWidth={0.18} />
            <title>{d.name} · capacity {d.capacity}</title>
          </g>
        ))}

        {/* stops (interchanges + termini get the white "station" ring) */}
        {Object.entries(nodes).map(([id, n]) => {
          const major = n.kind === 'interchange' || n.kind === 'terminus' || n.kind === 'junction'
          return (
            <g key={id} style={{ cursor: 'pointer' }}
              onClick={e => { e.stopPropagation(); setSel({ kind: 'stop', id, node: n }) }}>
              <circle cx={n.x} cy={n.y} r={major ? 0.62 : 0.3}
                fill={major ? '#fff' : '#8b93a3'} stroke="#3c4250"
                strokeWidth={major ? 0.22 : 0.1} />
              <title>{n.name}</title>
            </g>
          )
        })}

        {/* live vehicles */}
        {vehicles.map(v => {
          const r = routeById[v.route]
          if (!r) return null
          const i = routes.indexOf(r)
          const [ox, oy] = routeOffset(i)
          const [x, y] = pointAt(r.points, v.progress)
          if (v.status === 'idle') return null
          const halo = v.status === 'stopped' ? '#e11d48' : v.status === 'warn' ? '#d97706' : null
          const dim = focusRoute && focusRoute !== v.route
          return (
            <g key={v.id} style={{ cursor: 'pointer', transition: 'transform .9s linear' }}
              transform={`translate(${x + ox},${y + oy})`} opacity={dim ? 0.15 : 1}
              onClick={e => { e.stopPropagation(); setSel({ kind: 'vehicle', v, route: r }) }}>
              {halo && <circle r={1.05} fill="none" stroke={halo} strokeWidth={0.3} className="netmap-pulse" />}
              <circle r={0.58} fill={r.color} stroke="#fff" strokeWidth={0.2} />
              <title>{v.id} · Route {v.route} · {v.cls}-class · {v.speed} km/h · {v.status}</title>
            </g>
          )
        })}
      </svg>

      {/* header overlay */}
      <div className="netmap-head">
        <span className="v-chip"><Icon n="ti-train" /> <b>{net?.name || 'Network'}</b></span>
        <span className="netmap-stat"><b>{inService}</b> in service</span>
        {stopped > 0 && <span className="netmap-stat" style={{ color: 'var(--accent-red)' }}><b>{stopped}</b> stopped</span>}
        {disrupted.length > 0 && (
          <span className="netmap-stat" style={{ color: 'var(--accent-red)' }}>
            <Icon n="ti-alert-triangle" /> Route {disrupted.map(([id]) => id).join(', ')} disrupted</span>
        )}
      </div>

      {/* route chip rail */}
      <div className="netmap-rail">
        {routes.map(r => {
          const st = routeStatus[r.id] || 'ok'
          return (
            <button key={r.id} className={`netmap-chip ${focusRoute === r.id ? 'on' : ''}`}
              style={{ '--c': r.color }} title={`${r.name} · via ${r.via}`}
              onClick={() => { setFocusRoute(focusRoute === r.id ? null : r.id); setSel({ kind: 'route', route: r }) }}>
              <i style={{ background: r.color }} />{r.id}
              {st !== 'ok' && <span className="dot" style={{ background: STATUS_COLOR[st] }} />}
            </button>
          )
        })}
      </div>

      {/* inspector */}
      {sel && (
        <div className="netmap-inspect" onClick={e => e.stopPropagation()}>
          {sel.kind === 'vehicle' && <>
            <div className="t"><i style={{ background: sel.route.color }} /> {sel.v.id} · Route {sel.v.route}</div>
            <div className="r"><span>Class</span><b>{sel.v.cls}</b></div>
            <div className="r"><span>Speed</span><b>{sel.v.speed} km/h</b></div>
            <div className="r"><span>Status</span><b style={{ color: sel.v.status === 'stopped' ? 'var(--accent-red)' : sel.v.status === 'warn' ? 'var(--accent-amber)' : 'var(--accent-green)' }}>{sel.v.status}</b></div>
            <div className="r"><span>Line</span><b>{sel.route.name}</b></div>
          </>}
          {sel.kind === 'stop' && <>
            <div className="t"><Icon n="ti-map-pin" /> {sel.node.name}</div>
            <div className="r"><span>Kind</span><b>{sel.node.kind}</b></div>
            <div className="r"><span>Routes</span><b>{routes.filter(r => r.path.includes(sel.id)).map(r => r.id).join(', ') || '—'}</b></div>
          </>}
          {sel.kind === 'route' && <>
            <div className="t"><i style={{ background: sel.route.color }} /> Route {sel.route.id}</div>
            <div className="r"><span>Name</span><b>{sel.route.name}</b></div>
            <div className="r"><span>Via</span><b>{sel.route.via}</b></div>
            <div className="r"><span>Length</span><b>{sel.route.length_km} km</b></div>
            <div className="r"><span>Status</span><b style={{ color: (routeStatus[sel.route.id] || 'ok') === 'ok' ? 'var(--accent-green)' : 'var(--accent-red)' }}>{routeStatus[sel.route.id] || 'ok'}</b></div>
            <div className="r"><span>Trams</span><b>{vehicles.filter(v => v.route === sel.route.id && v.status !== 'idle').length}</b></div>
          </>}
          <button className="x" onClick={() => setSel(null)}>×</button>
        </div>
      )}
    </div>
  )
}
