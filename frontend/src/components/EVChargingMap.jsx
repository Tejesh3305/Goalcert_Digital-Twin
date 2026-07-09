/**
 * EVChargingMap — the charging-network map (P2-020). Station markers coloured by
 * availability with a click popover showing live charger status, plus grid /
 * solar summary. An SVG geo-style map (self-contained, no external tiles). Pure
 * render of `net.stations` / `net.grid`.
 */
import { useState } from 'react'

const statusColor = (s) => ({
  ok: 'var(--ok)', warning: 'var(--accent-amber)', critical: 'var(--accent-red)',
}[s] || 'var(--muted)')

export default function EVChargingMap({ net }) {
  const [sel, setSel] = useState(null)
  if (!net || !net.stations) return null
  const stations = net.stations
  const grid = net.grid || {}
  const station = stations.find((s) => s.id === sel)
  const loadPct = grid.transformer_rated_kw ? Math.round(100 * (grid.load_kw || 0) / grid.transformer_rated_kw) : 0

  return (
    <div style={{ position: 'relative' }}>
      <svg viewBox="0 0 100 100" style={{ width: '100%', height: 'auto', display: 'block',
        background: 'radial-gradient(circle at 50% 45%, var(--surface), var(--surface2))',
        borderRadius: 12, border: '1px solid var(--border)' }}>
        {/* faux road grid */}
        {[20, 40, 60, 80].map((v) => (
          <g key={v} stroke="var(--border)" strokeWidth="0.25" opacity="0.5">
            <line x1={v} y1="4" x2={v} y2="96" /><line x1="4" y1={v} x2="96" y2={v} />
          </g>
        ))}
        {/* grid connection node (centre) */}
        <g>
          <rect x="47" y="45" width="6" height="6" rx="1" fill="var(--surface)"
            stroke="var(--accent-blue)" strokeWidth="0.6" />
          <text x="50" y="43.5" fontSize="2.2" textAnchor="middle" fill="var(--muted)">Grid PCC</text>
        </g>
        {/* feeders */}
        {stations.map((s) => (
          <line key={'f' + s.id} x1="50" y1="48" x2={s.x} y2={s.y}
            stroke="var(--border)" strokeWidth="0.4" opacity="0.6" />
        ))}
        {/* stations */}
        {stations.map((s) => {
          const r = 2.4 + s.chargers_total * 0.18
          return (
            <g key={s.id} style={{ cursor: 'pointer' }} onClick={() => setSel(s.id === sel ? null : s.id)}>
              <circle cx={s.x} cy={s.y} r={r} fill={statusColor(s.status)} fillOpacity="0.22"
                stroke={s.id === sel ? 'var(--brand)' : statusColor(s.status)}
                strokeWidth={s.id === sel ? 1 : 0.6} />
              <text x={s.x} y={s.y + 0.8} fontSize="2.4" textAnchor="middle"
                fill={statusColor(s.status)} style={{ fontWeight: 700, fontFamily: 'var(--mono)' }}>
                {s.chargers_available}
              </text>
              <text x={s.x} y={s.y + r + 2.4} fontSize="2.1" textAnchor="middle" fill="var(--muted)">{s.name}</text>
            </g>
          )
        })}
      </svg>

      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginTop: 8, fontSize: 11.5, alignItems: 'center' }}>
        {[['available', 'var(--ok)'], ['busy', 'var(--accent-amber)'], ['fault', 'var(--accent-red)']].map(([l, c]) => (
          <span key={l} style={{ display: 'inline-flex', alignItems: 'center', gap: 5, color: 'var(--muted)' }}>
            <span style={{ width: 9, height: 9, borderRadius: 999, background: c }} />{l}
          </span>
        ))}
        <span className="muted" style={{ marginLeft: 'auto' }}>
          Transformer {loadPct}% · {Math.round(grid.load_kw || 0)} / {grid.transformer_rated_kw || 0} kW
        </span>
      </div>

      {station && (
        <div style={{ position: 'absolute', top: 10, right: 10, width: 190, background: 'var(--surface)',
          border: '1px solid var(--border)', borderRadius: 12, padding: '12px 14px',
          boxShadow: '0 8px 30px rgba(0,0,0,.18)', zIndex: 3 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
            <b style={{ fontFamily: 'var(--display)' }}>{station.name}</b>
            <span onClick={() => setSel(null)} style={{ cursor: 'pointer', color: 'var(--hint)' }}><i className="ti ti-x" /></span>
          </div>
          {[['Available', station.chargers_available, 'var(--ok)'],
            ['In use', station.chargers_active, 'var(--accent-amber)'],
            ['Faulted', station.chargers_faulted, 'var(--accent-red)']].map(([l, v, c]) => (
            <div key={l} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, padding: '2px 0' }}>
              <span className="muted">{l}</span><b style={{ color: c }}>{v}</b>
            </div>
          ))}
          <div style={{ borderTop: '1px solid var(--border)', marginTop: 6, paddingTop: 6, fontSize: 11.5 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}><span className="muted">Load</span><b>{station.load_kw} kW</b></div>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}><span className="muted">Max/point</span><b>{station.max_kw} kW</b></div>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}><span className="muted">Utilisation</span><b>{station.utilisation}%</b></div>
          </div>
        </div>
      )}
    </div>
  )
}
