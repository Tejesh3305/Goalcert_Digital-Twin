/**
 * DefenceTacticalMap — dark tactical map with NATO APP-6 symbology (P4-017).
 * Renders land / sea / air / installation assets by affiliation (friend / hostile
 * / neutral / unknown) with per-class layer toggles, from the base twin graph.
 * Self-contained SVG. Pure render of `net.assets` / `net.sectors`.
 */
import { useState } from 'react'

// APP-6 affiliation frame colours + shapes
const AFFIL = {
  friend: { color: '#3b82f6', fill: 'rgba(59,130,246,.18)' },
  hostile: { color: '#ef4444', fill: 'rgba(239,68,68,.2)' },
  neutral: { color: '#22c55e', fill: 'rgba(34,197,94,.18)' },
  unknown: { color: '#eab308', fill: 'rgba(234,179,8,.18)' },
}
const CAT_ICON = { air: 'ti-plane', land: 'ti-tank', sea: 'ti-ship', installation: 'ti-building-fortress' }
const CATS = ['air', 'land', 'sea', 'installation']

// APP-6 frame: friend = rectangle, hostile = diamond, neutral = square, unknown = quatrefoil (~circle)
function Frame({ affil, x, y, r }) {
  const c = AFFIL[affil] || AFFIL.unknown
  const p = { fill: c.fill, stroke: c.color, strokeWidth: 0.7 }
  if (affil === 'hostile') return <polygon points={`${x},${y - r} ${x + r},${y} ${x},${y + r} ${x - r},${y}`} {...p} />
  if (affil === 'neutral') return <rect x={x - r} y={y - r} width={r * 2} height={r * 2} {...p} />
  if (affil === 'unknown') return <circle cx={x} cy={y} r={r} {...p} />
  return <rect x={x - r * 1.15} y={y - r * 0.8} width={r * 2.3} height={r * 1.6} rx="0.4" {...p} />  // friend
}

export default function DefenceTacticalMap({ net }) {
  const [layers, setLayers] = useState(() => Object.fromEntries(CATS.map((c) => [c, true])))
  if (!net || !net.assets) return null
  const assets = net.assets.filter((a) => layers[a.category])

  return (
    <div>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center', marginBottom: 10 }}>
        {CATS.map((c) => (
          <button key={c} className="quick-chip" onClick={() => setLayers((l) => ({ ...l, [c]: !l[c] }))}
            style={{ borderColor: layers[c] ? 'var(--brand)' : 'var(--border)', opacity: layers[c] ? 1 : 0.5 }}>
            <i className={`ti ${CAT_ICON[c]}`} /> {c}
          </button>
        ))}
        <span style={{ marginLeft: 'auto', fontSize: 11.5, color: 'var(--accent-red)', fontWeight: 600 }}>
          THREAT {net.threat_level} · FPCON {net.force_protection}
        </span>
      </div>

      <svg viewBox="0 0 100 100" style={{ width: '100%', height: 'auto', display: 'block',
        background: 'radial-gradient(circle at 50% 50%, #0b1220, #060a12)',
        borderRadius: 12, border: '1px solid #1e293b' }}>
        {/* range rings + bearing lines */}
        {[15, 30, 45].map((r) => (
          <circle key={r} cx="50" cy="50" r={r} fill="none" stroke="#1e3a5f" strokeWidth="0.3" strokeDasharray="1 1.5" />
        ))}
        {[0, 45, 90, 135].map((a) => {
          const rad = (a * Math.PI) / 180
          return <line key={a} x1={50 - 46 * Math.cos(rad)} y1={50 - 46 * Math.sin(rad)}
            x2={50 + 46 * Math.cos(rad)} y2={50 + 46 * Math.sin(rad)} stroke="#12233b" strokeWidth="0.3" />
        })}
        {/* assets */}
        {assets.map((a) => {
          const c = AFFIL[a.affiliation] || AFFIL.unknown
          const r = a.affiliation === 'friend' ? 2.4 : 2.6
          return (
            <g key={a.id}>
              <Frame affil={a.affiliation} x={a.x} y={a.y} r={r} />
              <text x={a.x} y={a.y + 0.9} fontSize="2.6" textAnchor="middle" fill={c.color}
                style={{ fontFamily: 'var(--font)' }}>
                {a.category === 'air' ? '✈' : a.category === 'sea' ? '⚓' : a.category === 'land' ? '▲' : '⬢'}
              </text>
              <text x={a.x} y={a.y + r + 2.4} fontSize="1.9" textAnchor="middle" fill="#7b8ba3"
                style={{ fontFamily: 'var(--mono)' }}>{a.name}</text>
              {a.status === 'critical' && <circle cx={a.x} cy={a.y} r={r + 1.4} fill="none"
                stroke="#ef4444" strokeWidth="0.5"><animate attributeName="opacity" values="1;0.2;1" dur="1s" repeatCount="indefinite" /></circle>}
            </g>
          )
        })}
      </svg>

      <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', marginTop: 8, fontSize: 11 }}>
        {Object.entries(AFFIL).map(([k, v]) => (
          <span key={k} style={{ display: 'inline-flex', alignItems: 'center', gap: 5, color: 'var(--muted)' }}>
            <span style={{ width: 10, height: 10, background: v.fill, border: `1px solid ${v.color}`,
              borderRadius: k === 'unknown' ? 999 : k === 'hostile' ? 0 : 2,
              transform: k === 'hostile' ? 'rotate(45deg)' : 'none' }} />{k}
          </span>
        ))}
        <span className="muted" style={{ marginLeft: 'auto' }}>NATO APP-6 · {assets.length} tracks</span>
      </div>
    </div>
  )
}
