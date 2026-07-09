/**
 * DefenceDamageControl — naval damage-control diagram (P4-019). An SVG ship
 * cross-section with compartments colour-coded by flooding status from the warship
 * stability twin, tilted by the computed list, with manual override controls.
 * Pure render of `net.compartments` / `net.ship`.
 */
import { useState } from 'react'

const statusColor = (s) => ({ ok: '#1e3a5f', warning: 'var(--accent-amber)', critical: 'var(--accent-red)' }[s] || '#1e3a5f')

export default function DefenceDamageControl({ net }) {
  const [overrides, setOverrides] = useState({})
  if (!net || !net.compartments) return null
  const comps = net.compartments
  const ship = net.ship || {}
  const list = ship.list_deg || 0
  const flooded = comps.filter((c) => c.flooding > 15)

  return (
    <div>
      <div style={{ display: 'flex', gap: 12, marginBottom: 12, flexWrap: 'wrap' }}>
        {[['List', `${list.toFixed(1)}°`, Math.abs(list) > 15 ? 'var(--accent-red)' : 'var(--text)'],
          ['Righting GZ', `${ship.gz_m} m`, ship.gz_m < 0.2 ? 'var(--accent-red)' : 'var(--ok)'],
          ['Draft', `${ship.draft_m} m`, 'var(--text)'],
          ['Freeboard', `${ship.freeboard_m} m`, ship.freeboard_m < 2 ? 'var(--accent-amber)' : 'var(--text)']].map(([l, v, c]) => (
          <div key={l} style={{ background: 'var(--surface2)', borderRadius: 8, padding: '6px 12px' }}>
            <div className="card-label" style={{ fontSize: 9.5 }}>{l}</div>
            <div style={{ fontWeight: 700, fontFamily: 'var(--mono)', color: c }}>{v}</div>
          </div>
        ))}
        {ship.capsize_risk && <span className="pill pill-red" style={{ alignSelf: 'center' }}><i className="ti ti-alert-triangle" /> CAPSIZE RISK</span>}
      </div>

      <svg viewBox="0 0 100 46" style={{ width: '100%', height: 'auto', display: 'block',
        background: 'linear-gradient(180deg,#0b1220 60%, #0c2233 60%)', borderRadius: 12, border: '1px solid #1e293b' }}>
        <g transform={`rotate(${Math.max(-18, Math.min(18, list * 0.6))} 50 30)`}>
          {/* hull outline */}
          <path d="M4,12 L88,12 Q96,12 92,24 L90,33 Q88,36 82,36 L12,36 Q6,36 5,30 Z"
            fill="#0f1e30" stroke="#3b5573" strokeWidth="0.6" />
          {/* superstructure */}
          <rect x="40" y="5" width="20" height="7" rx="1" fill="#122740" stroke="#3b5573" strokeWidth="0.4" />
          {/* waterline */}
          <line x1="0" y1="30" x2="100" y2="30" stroke="#2b6cb0" strokeWidth="0.5" strokeDasharray="2 1.5" opacity="0.7" />
          {/* compartments */}
          {comps.map((c) => {
            const fillH = (c.flooding / 100) * c.h
            const ov = overrides[c.id]
            return (
              <g key={c.id}>
                <rect x={c.x} y={c.y} width={c.w} height={c.h} rx="0.6" fill="#0a1626"
                  stroke={ov ? 'var(--ok)' : statusColor(c.status)} strokeWidth={c.status === 'critical' ? 0.9 : 0.4}
                  style={{ cursor: 'pointer' }} onClick={() => setOverrides((o) => ({ ...o, [c.id]: !o[c.id] }))}>
                  <title>{c.name}: {c.flooding}% flooded{ov ? ' · counter-flood ON' : ''}</title>
                </rect>
                {/* flood water */}
                {fillH > 0 && <rect x={c.x} y={c.y + c.h - fillH} width={c.w} height={fillH} rx="0.4"
                  fill={ov ? 'rgba(34,197,94,.35)' : 'rgba(43,108,176,.6)'} />}
                <text x={c.x + c.w / 2} y={c.y + c.h / 2 + 0.8} fontSize="1.7" textAnchor="middle" fill="#8ba0bd"
                  style={{ fontFamily: 'var(--font)', pointerEvents: 'none' }}>{c.name}</text>
              </g>
            )
          })}
        </g>
      </svg>

      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginTop: 8, fontSize: 11, alignItems: 'center' }}>
        {[['dry', '#1e3a5f'], ['making water', 'var(--accent-amber)'], ['flooded', 'var(--accent-red)'], ['counter-flood', 'var(--ok)']].map(([l, c]) => (
          <span key={l} style={{ display: 'inline-flex', alignItems: 'center', gap: 5, color: 'var(--muted)' }}>
            <span style={{ width: 10, height: 6, background: c, borderRadius: 1 }} />{l}
          </span>
        ))}
        <span style={{ marginLeft: 'auto', color: flooded.length ? 'var(--accent-red)' : 'var(--muted)', fontWeight: 600 }}>
          {flooded.length ? `${flooded.length} compartment(s) making water — click to counter-flood` : 'Hull integrity nominal'}
        </span>
      </div>
    </div>
  )
}
