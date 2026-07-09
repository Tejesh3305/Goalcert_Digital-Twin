/**
 * HospitalMedicalGasSchematic — live P&ID of the medical-gas distribution system
 * (P3-027). Manifolds feed pipeline pressure zones, colour-coded by pressure
 * status with fault indicators. Pure render of `net.medical_gas`.
 */
const statusColor = (s) => ({
  ok: 'var(--ok)', warning: 'var(--accent-amber)', critical: 'var(--accent-red)',
}[s] || 'var(--muted)')

export default function HospitalMedicalGasSchematic({ gas }) {
  if (!gas || !gas.manifolds) return null
  const manifolds = gas.manifolds
  const zones = gas.zones || []
  const zoneY = (i) => 12 + i * (76 / Math.max(1, zones.length))
  const manY = (i) => 20 + i * (60 / Math.max(1, manifolds.length))
  // O2 manifold is the header feeding the O2 zones
  const header = manifolds.find((m) => m.gas === 'O2') || manifolds[0]

  return (
    <div>
      <svg viewBox="0 0 100 100" style={{ width: '100%', height: 'auto', display: 'block',
        background: 'var(--surface2)', borderRadius: 12, border: '1px solid var(--border)' }}>
        {/* header pipe */}
        <line x1="26" y1={manY(manifolds.indexOf(header))} x2="26" y2={zoneY(zones.length - 1)}
          stroke="var(--border)" strokeWidth="0.8" />
        {/* manifold -> header + header -> zones */}
        {zones.map((z, i) => (
          <line key={'l' + z.id} x1="26" y1={zoneY(i)} x2="60" y2={zoneY(i)}
            stroke={statusColor(z.status)} strokeWidth="0.9" strokeDasharray={z.status === 'critical' ? '2 1.4' : undefined} />
        ))}

        {/* manifolds (sources) */}
        {manifolds.map((m, i) => (
          <g key={m.id}>
            <line x1="14" y1={manY(i)} x2="26" y2={manY(i)} stroke="var(--border)" strokeWidth="0.8" />
            <circle cx="10" cy={manY(i)} r="4" fill="var(--surface)" stroke={statusColor(m.status)} strokeWidth="0.8" />
            <text x="10" y={manY(i) + 0.9} fontSize="2.4" textAnchor="middle" fill="var(--text)"
              style={{ fontFamily: 'var(--mono)', fontWeight: 700 }}>{m.gas}</text>
            <text x="10" y={manY(i) + 6} fontSize="2.1" textAnchor="middle" fill={statusColor(m.status)}
              style={{ fontFamily: 'var(--mono)' }}>{m.pressure_kpa}kPa</text>
            {m.status !== 'ok' && <text x="15.5" y={manY(i) - 3.2} fontSize="3" fill="var(--accent-red)">⚠</text>}
          </g>
        ))}

        {/* zones (loads) */}
        {zones.map((z, i) => (
          <g key={z.id}>
            <rect x="60" y={zoneY(i) - 3.4} width="34" height="6.8" rx="1.2"
              fill="var(--surface)" stroke={statusColor(z.status)} strokeWidth={z.status === 'critical' ? 1 : 0.5} />
            <text x="62.5" y={zoneY(i) + 0.9} fontSize="2.5" fill="var(--text)"
              style={{ fontFamily: 'var(--font)' }}>{z.name}</text>
            <text x="91.5" y={zoneY(i) + 0.9} fontSize="2.4" textAnchor="end" fill={statusColor(z.status)}
              style={{ fontFamily: 'var(--mono)', fontWeight: 700 }}>{z.pressure_kpa}</text>
          </g>
        ))}
      </svg>
      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginTop: 8, fontSize: 11, alignItems: 'center' }}>
        {[['nominal', 'var(--ok)'], ['low', 'var(--accent-amber)'], ['below setpoint', 'var(--accent-red)']].map(([l, c]) => (
          <span key={l} style={{ display: 'inline-flex', alignItems: 'center', gap: 5, color: 'var(--muted)' }}>
            <span style={{ width: 9, height: 9, borderRadius: 2, background: c }} />{l}
          </span>
        ))}
        <span className="muted" style={{ marginLeft: 'auto' }}>O2 setpoint ≥ 350 kPa (HTM 02-01)</span>
      </div>
    </div>
  )
}
