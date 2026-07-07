/** Viz — tiny SVG primitives reused across the machine dashboards. */
import { hColor } from '../../lib/machine'

/** HealthRing — a circular 0..1 gauge coloured by health band. */
export function HealthRing({ value, size = 56, stroke = 5 }) {
  const r = (size - stroke) / 2
  const c = 2 * Math.PI * r
  const v = value == null ? 0 : Math.max(0, Math.min(1, value))
  const color = hColor(value)
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} style={{ flexShrink: 0 }}>
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--surface2)" strokeWidth={stroke} />
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={color} strokeWidth={stroke}
        strokeLinecap="round" strokeDasharray={c} strokeDashoffset={c * (1 - v)}
        transform={`rotate(-90 ${size / 2} ${size / 2})`} style={{ transition: 'stroke-dashoffset .6s ease' }} />
      <text x="50%" y="52%" textAnchor="middle" dominantBaseline="middle"
        style={{ fontFamily: 'var(--mono)', fontSize: size * 0.26, fontWeight: 700, fill: color }}>
        {value == null ? '—' : Math.round(v * 100)}
      </text>
    </svg>
  )
}

/** Sparkline — a compact trend line for a rolling signal buffer. */
export function Sparkline({ data, color = '#7c3aed', width = 120, height = 30 }) {
  if (!data || data.length < 2) return <div style={{ height }} />
  const min = Math.min(...data)
  const max = Math.max(...data)
  const span = max - min || 1
  const pts = data.map((v, i) => {
    const x = (i / (data.length - 1)) * width
    const y = height - ((v - min) / span) * (height - 4) - 2
    return `${x.toFixed(1)},${y.toFixed(1)}`
  }).join(' ')
  return (
    <svg width="100%" height={height} viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none"
      style={{ display: 'block', marginTop: 6 }}>
      <polyline points={pts} fill="none" stroke={color} strokeWidth="1.5"
        strokeLinejoin="round" strokeLinecap="round" opacity="0.9" />
    </svg>
  )
}
