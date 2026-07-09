/**
 * EVGridLoadCurve — grid load-curve chart (P2-022). A 24-hour time-series of
 * total charging demand vs solar generation vs transformer capacity, with
 * demand-response event windows overlaid. Self-contained SVG. Pure render of
 * `net.load_curve`.
 */
export default function EVGridLoadCurve({ curve }) {
  if (!curve || !curve.length) return null
  const W = 640, H = 220, padL = 40, padB = 20, padT = 10, padR = 10
  const cap = curve[0].capacity_kw || 1
  const maxY = cap * 1.15
  const x = (h) => padL + (h / 23) * (W - padL - padR)
  const y = (v) => padT + (1 - v / maxY) * (H - padT - padB)
  const line = (key) => curve.map((p) => `${x(p.hour)},${y(p[key])}`).join(' ')
  const area = (key) => `${x(0)},${y(0)} ${line(key)} ${x(23)},${y(0)}`

  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', height: 'auto', display: 'block' }}>
        {/* DR event bands */}
        {curve.map((p) => p.dr_event && (
          <rect key={'dr' + p.hour} x={x(p.hour) - 6} y={padT} width="13" height={H - padT - padB}
            fill="var(--accent-amber)" opacity="0.12" />
        ))}
        {/* capacity line */}
        <line x1={padL} y1={y(cap)} x2={W - padR} y2={y(cap)} stroke="var(--accent-red)"
          strokeWidth="1.4" strokeDasharray="5 4" />
        <text x={W - padR} y={y(cap) - 3} fontSize="10" textAnchor="end" fill="var(--accent-red)">
          transformer capacity {cap} kW
        </text>
        {/* solar area */}
        <polygon points={area('solar_kw')} fill="rgba(217,119,6,.16)" stroke="none" />
        <polyline points={line('solar_kw')} fill="none" stroke="var(--accent-amber)" strokeWidth="1.6" />
        {/* demand line */}
        <polyline points={line('demand_kw')} fill="none" stroke="var(--brand)" strokeWidth="2.2"
          strokeLinejoin="round" />
        {/* grid import line */}
        <polyline points={line('grid_kw')} fill="none" stroke="var(--accent-blue)" strokeWidth="1.6"
          strokeDasharray="3 2" />
        {/* axes */}
        <line x1={padL} y1={H - padB} x2={W - padR} y2={H - padB} stroke="var(--border)" />
        {[0, 6, 12, 18, 23].map((h) => (
          <text key={h} x={x(h)} y={H - padB + 12} fontSize="10" textAnchor="middle" fill="var(--muted)"
            fontFamily="var(--mono)">{String(h).padStart(2, '0')}:00</text>
        ))}
        <text x={padL} y={y(cap) + 12} fontSize="9" fill="var(--muted)" fontFamily="var(--mono)">kW</text>
      </svg>
      <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', marginTop: 6, fontSize: 11.5 }}>
        {[['Charging demand', 'var(--brand)'], ['Solar', 'var(--accent-amber)'],
          ['Grid import', 'var(--accent-blue)'], ['DR window', 'rgba(217,119,6,.4)']].map(([l, c]) => (
          <span key={l} style={{ display: 'inline-flex', alignItems: 'center', gap: 5, color: 'var(--muted)' }}>
            <span style={{ width: 12, height: 4, borderRadius: 2, background: c }} />{l}
          </span>
        ))}
      </div>
    </div>
  )
}
