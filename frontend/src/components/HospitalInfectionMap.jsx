/**
 * HospitalInfectionMap — floor-plan infection map (P3-026). Zones shaded by the
 * Wells-Riley airborne infection probability, with pressure-cascade status and
 * ward-closure recommendations. Pure render of `net.zones`.
 */
const riskColor = (p) =>
  p == null ? 'var(--muted)' : p >= 5 ? 'var(--accent-red)' : p >= 3 ? '#f97316'
    : p >= 1.5 ? 'var(--accent-amber)' : 'var(--ok)'

const statusStroke = (s) => ({
  ok: 'var(--border)', warning: 'var(--accent-amber)', critical: 'var(--accent-red)',
}[s] || 'var(--border)')

export default function HospitalInfectionMap({ zones }) {
  if (!zones || !zones.length) return null
  const closures = zones.filter((z) => z.recommend_closure)

  return (
    <div>
      <svg viewBox="0 0 100 100" style={{ width: '100%', height: 'auto', display: 'block',
        background: 'var(--surface2)', borderRadius: 12, border: '1px solid var(--border)' }}>
        {/* department blocks */}
        {zones.map((z) => {
          const w = 15, h = 13
          return (
            <g key={z.id}>
              <rect x={z.x - w / 2} y={z.y - h / 2} width={w} height={h} rx="1.4"
                fill={riskColor(z.infection_prob)} fillOpacity="0.22"
                stroke={statusStroke(z.status)} strokeWidth={z.status === 'critical' ? 1.1 : 0.5} />
              <text x={z.x} y={z.y - 1.5} fontSize="2.5" textAnchor="middle" fill="var(--text)"
                style={{ fontFamily: 'var(--font)', fontWeight: 600 }}>{z.name}</text>
              <text x={z.x} y={z.y + 2.4} fontSize="2.7" textAnchor="middle"
                fill={riskColor(z.infection_prob)} style={{ fontFamily: 'var(--mono)', fontWeight: 700 }}>
                {z.infection_prob}%
              </text>
              {(z.kind === 'or' || z.kind === 'iso') && (
                <text x={z.x} y={z.y + 5.2} fontSize="1.9" textAnchor="middle" fill="var(--muted)"
                  style={{ fontFamily: 'var(--mono)' }}>{z.pressure > 0 ? '+' : ''}{z.pressure} Pa</text>
              )}
              {z.recommend_closure && (
                <text x={z.x} y={z.y - h / 2 - 0.8} fontSize="2.2" textAnchor="middle"
                  fill="var(--accent-red)" style={{ fontWeight: 700 }}>⚠ close</text>
              )}
            </g>
          )
        })}
      </svg>
      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginTop: 8, fontSize: 11, alignItems: 'center' }}>
        {[['A/B safe', 'var(--ok)'], ['elevated', 'var(--accent-amber)'], ['high', '#f97316'], ['outbreak', 'var(--accent-red)']].map(([l, c]) => (
          <span key={l} style={{ display: 'inline-flex', alignItems: 'center', gap: 5, color: 'var(--muted)' }}>
            <span style={{ width: 9, height: 9, borderRadius: 2, background: c }} />{l}
          </span>
        ))}
        <span style={{ marginLeft: 'auto', color: closures.length ? 'var(--accent-red)' : 'var(--muted)', fontWeight: 600 }}>
          {closures.length
            ? `${closures.length} zone(s) recommended for closure: ${closures.map((z) => z.name).join(', ')}`
            : 'No closures recommended'}
        </span>
      </div>
    </div>
  )
}
