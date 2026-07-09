/**
 * RailwayNetworkMap — the metro (railway-metro) live spatial map.
 *
 * Renders the line topology (P1-021), live train positions (P1-022, polled at
 * 2 s upstream), a click-through station KPI side-panel (P1-023) and an optional
 * passenger-density heatmap overlay (P1-024) from GET /twins/{tenant}/network.
 * A line-selector filters the whole view to one line. Pure render of a `net`
 * payload; polling lives in the parent.
 */
import { useState } from 'react'

const losColor = (i) =>
  i == null ? 'var(--muted)' : i <= 2 ? 'var(--ok)' : i <= 4 ? 'var(--accent-amber)'
    : i === 5 ? '#f97316' : 'var(--accent-red)'

const statusColor = (s) => ({
  ok: 'var(--ok)', warning: 'var(--accent-amber)', critical: 'var(--accent-red)',
}[s] || 'var(--muted)')

const lineStroke = (status, color) =>
  status === 'blocked' ? 'var(--accent-red)' : status === 'degraded' ? 'var(--accent-amber)' : color

export default function RailwayNetworkMap({ net }) {
  const [selLine, setSelLine] = useState(null)
  const [heat, setHeat] = useState(false)
  const [selStation, setSelStation] = useState(null)

  if (!net || !net.lines) return null
  const lines = net.lines || []
  const stations = net.stations || []
  const trains = net.trains || []
  const subs = net.substations || []
  const depots = net.depots || []
  const lineColor = Object.fromEntries(lines.map((l) => [l.id, l.color]))
  const maxPax = Math.max(1, ...stations.map((s) => s.pax || 0))
  const dimLine = (id) => selLine && id !== selLine
  const dimStation = (s) => selLine && !s.lines.includes(selLine)
  const station = stations.find((s) => s.id === selStation)

  return (
    <div style={{ position: 'relative' }}>
      {/* Controls: line selector + heatmap toggle */}
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center', marginBottom: 10 }}>
        <button className="quick-chip" onClick={() => setSelLine(null)}
          style={{ borderColor: !selLine ? 'var(--brand)' : 'var(--border)', fontWeight: !selLine ? 700 : 500 }}>
          All lines
        </button>
        {lines.map((l) => (
          <button key={l.id} className="quick-chip" onClick={() => setSelLine(l.id === selLine ? null : l.id)}
            style={{ borderColor: l.id === selLine ? l.color : 'var(--border)',
              color: l.id === selLine ? l.color : 'var(--text)', fontWeight: l.id === selLine ? 700 : 500 }}>
            <span style={{ width: 9, height: 9, borderRadius: 2, background: lineStroke(l.status, l.color),
              display: 'inline-block', marginRight: 5 }} />
            {l.name.split('·')[0].trim()}
            {l.status === 'blocked' && <b style={{ color: 'var(--accent-red)' }}> · BLOCKED</b>}
          </button>
        ))}
        <button className="quick-chip" onClick={() => setHeat((h) => !h)}
          style={{ marginLeft: 'auto', borderColor: heat ? 'var(--accent-amber)' : 'var(--border)',
            color: heat ? 'var(--accent-amber)' : 'var(--text)', fontWeight: heat ? 700 : 500 }}>
          <i className="ti ti-flame" /> Passenger heatmap
        </button>
      </div>

      <svg viewBox="0 0 100 100" style={{ width: '100%', height: 'auto', display: 'block',
        background: 'radial-gradient(circle at 50% 45%, var(--surface), var(--surface2))',
        borderRadius: 12, border: '1px solid var(--border)' }}>
        {/* heatmap halos (drawn under everything) */}
        {heat && stations.map((s) => {
          const r = 2.5 + (s.pax / maxPax) * 6.5
          return <circle key={'h' + s.id} cx={s.x} cy={s.y} r={r} fill={losColor(s.los_index)}
            opacity={dimStation(s) ? 0.05 : 0.28} />
        })}

        {/* lines */}
        {lines.map((l) => {
          const pts = (l.points || []).map((p) => p.join(',')).join(' ')
          const blocked = l.status === 'blocked'
          return (
            <polyline key={l.id} points={pts} fill="none"
              stroke={lineStroke(l.status, l.color)}
              strokeWidth={blocked ? 1.2 : 1.8}
              strokeOpacity={dimLine(l.id) ? 0.15 : 0.9}
              strokeDasharray={blocked ? '2.2 1.6' : undefined}
              strokeLinecap="round" strokeLinejoin="round" />
          )
        })}

        {/* traction substations */}
        {subs.map((s) => (
          <rect key={s.id} x={s.x - 1.1} y={s.y - 1.1} width="2.2" height="2.2" rx="0.4"
            fill="var(--surface)" stroke="var(--accent-blue)" strokeWidth="0.5" opacity={selLine ? 0.4 : 0.9}>
            <title>{s.name}</title>
          </rect>
        ))}

        {/* depots */}
        {depots.map((d) => (
          <g key={d.id}>
            <rect x={d.x - 1.9} y={d.y - 1.9} width="3.8" height="3.8" rx="0.6"
              transform={`rotate(45 ${d.x} ${d.y})`}
              fill="var(--surface)" stroke="var(--muted)" strokeWidth="0.6" />
            <title>{d.name} — {d.available}/{d.berth_count} available</title>
          </g>
        ))}

        {/* stations */}
        {stations.map((s) => {
          const dim = dimStation(s)
          const ring = statusColor(s.status)
          const r = s.interchange ? 1.7 : 1.25
          return (
            <g key={s.id} style={{ cursor: 'pointer', opacity: dim ? 0.25 : 1 }}
              onClick={() => setSelStation(s.id === selStation ? null : s.id)}>
              {s.interchange && <circle cx={s.x} cy={s.y} r={r + 0.9} fill="none"
                stroke="var(--text)" strokeWidth="0.4" opacity="0.5" />}
              <circle cx={s.x} cy={s.y} r={r}
                fill={s.status === 'ok' ? 'var(--surface)' : ring}
                stroke={s.id === selStation ? 'var(--brand)' : ring}
                strokeWidth={s.id === selStation ? 0.9 : 0.55} />
              {!heat && !selLine && (
                <text x={s.x + (s.x > 78 ? -2.2 : 2.1)} y={s.y + 0.6} fontSize="2"
                  textAnchor={s.x > 78 ? 'end' : 'start'} fill="var(--muted)"
                  style={{ fontFamily: 'var(--font)' }}>{s.name}</text>
              )}
            </g>
          )
        })}

        {/* live trains */}
        {trains.filter((t) => !selLine || t.line === selLine).map((t) => {
          const held = t.status === 'held'
          const r = 1.0 + (t.load / 120) * 0.9
          return (
            <circle key={t.id} cx={t.x} cy={t.y} r={held ? 1.6 : r}
              fill={held ? 'var(--accent-red)' : (lineColor[t.line] || 'var(--brand)')}
              stroke="#fff" strokeWidth="0.4">
              <title>{t.id} · {t.load}% load · {t.status}</title>
              {held && <animate attributeName="opacity" values="1;0.35;1" dur="1.1s" repeatCount="indefinite" />}
            </circle>
          )
        })}
      </svg>

      {/* Legend / summary */}
      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginTop: 8, fontSize: 11.5, alignItems: 'center' }}>
        {heat ? (
          ['A–B', 'C–D', 'E', 'F'].map((lab, i) => (
            <span key={lab} style={{ display: 'inline-flex', alignItems: 'center', gap: 5, color: 'var(--muted)' }}>
              <span style={{ width: 9, height: 9, borderRadius: 999, background: losColor([2, 4, 5, 6][i]) }} />
              LOS {lab}
            </span>
          ))
        ) : (
          lines.map((l) => (
            <span key={l.id} style={{ display: 'inline-flex', alignItems: 'center', gap: 5,
              color: l.status === 'blocked' ? 'var(--accent-red)' : 'var(--muted)' }}>
              <span style={{ width: 9, height: 9, borderRadius: 2, background: lineStroke(l.status, l.color) }} />
              {l.name.split('·')[1]?.trim() || l.name}
            </span>
          ))
        )}
        <span className="muted" style={{ marginLeft: 'auto' }}>
          {net.fleet_size} trains · {stations.length} stations · {net.route_km} route-km
        </span>
      </div>

      {/* Station KPI side panel (P1-023) */}
      {station && (
        <div style={{ position: 'absolute', top: 44, right: 8, width: 210, background: 'var(--surface)',
          border: '1px solid var(--border)', borderRadius: 12, padding: '12px 14px',
          boxShadow: '0 8px 30px rgba(0,0,0,.18)', zIndex: 3 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 8 }}>
            <div>
              <div style={{ fontWeight: 700, fontFamily: 'var(--display)' }}>{station.name}</div>
              <div className="muted" style={{ fontSize: 10.5 }}>
                {station.interchange ? 'Interchange · ' : ''}{station.lines.join(' / ')}
              </div>
            </div>
            <span onClick={() => setSelStation(null)} style={{ cursor: 'pointer', color: 'var(--hint)' }}>
              <i className="ti ti-x" />
            </span>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginBottom: 10 }}>
            <Kpi label="Passengers" value={station.pax} />
            <Kpi label="Platform LOS" value={station.los} color={losColor(station.los_index)} />
          </div>
          {[['Platform-screen doors', station.psd], ['Escalators', station.escalator],
            ['ACMV / HVAC', station.hvac], ['Track circuits', station.track_circuit]].map(([lab, st]) => (
            <div key={lab} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11.5, padding: '3px 0' }}>
              <span className="muted">{lab}</span>
              <span style={{ color: statusColor(st), fontWeight: 600, textTransform: 'capitalize' }}>
                <i className={`ti ${st === 'ok' ? 'ti-circle-check' : 'ti-alert-triangle'}`} style={{ fontSize: 12 }} /> {st}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function Kpi({ label, value, color }) {
  return (
    <div style={{ background: 'var(--surface2)', borderRadius: 8, padding: '6px 8px' }}>
      <div className="card-label" style={{ fontSize: 9.5 }}>{label}</div>
      <div style={{ fontWeight: 700, fontFamily: 'var(--mono)', color: color || 'var(--text)' }}>{value}</div>
    </div>
  )
}
