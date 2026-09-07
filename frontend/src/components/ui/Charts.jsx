/**
 * Charts.jsx — the dashboard's SVG chart vocabulary.
 *
 * Separate from `Viz.jsx` on purpose: that module holds the per-machine gauges
 * (a health ring, a signal sparkline) that sit inside a telemetry card. These
 * are the shapes a *work* dashboard is built from — how much got done, how it
 * broke down, how a score moved. Different job, different consumers.
 *
 * HAND-ROLLED, AND WHY. The project ships no charting library. Adding one for
 * these four shapes would put ~500KB into a bundle that federates into the hub,
 * to draw marks that are forty lines of path arithmetic. If a fifth chart ever
 * needs a real scale library, that is the moment to reconsider — not this one.
 *
 * THE RULES EVERY MARK HERE FOLLOWS, so a later addition matches:
 *   · one measure per axis — never two y-scales in one frame
 *   · 2px strokes, 4px rounded data-ends anchored to the baseline, a surface
 *     gap between adjacent fills, hit targets larger than the mark
 *   · grid and axes recessive; the data is the darkest thing in the frame
 *   · no value is carried by hue alone — direct labels or a hover readout.
 *     Severity especially: it is a STATUS palette (reserved red/amber/blue)
 *     and a status colour always travels with its word.
 *   · text wears text tokens (--text / --muted), never the series colour
 *
 * ON THE SEVERITY BREAKDOWN BEING BARS RATHER THAN A RING. Four warm status
 * hues in one donut is precisely where red and amber stop being separable, and
 * a ring cannot carry its counts without leader lines. Labelled bars state the
 * name and the number on every row, so the colour only reinforces.
 */

import { useState } from 'react'
import '../../styles/charts.css'

/** A rounded-top bar: square on the baseline, 4px radius at the data end. */
function barPath(x, y, w, h, r = 4) {
  const rad = Math.max(0, Math.min(r, w / 2, h))
  if (h <= 0) return ''
  return `M${x},${y + h} L${x},${y + rad} Q${x},${y} ${x + rad},${y} `
       + `L${x + w - rad},${y} Q${x + w},${y} ${x + w},${y + rad} `
       + `L${x + w},${y + h} Z`
}

function shortDay(iso) {
  const d = new Date(`${iso}T00:00:00`)
  return Number.isNaN(d.getTime())
    ? iso : d.toLocaleDateString(undefined, { day: 'numeric', month: 'short' })
}

function longDay(iso) {
  const d = new Date(`${iso}T00:00:00`)
  return Number.isNaN(d.getTime())
    ? iso : d.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' })
}

/**
 * DayBars — one column per day.
 *
 * A single sequential hue rather than a colour per bar: the days are not
 * different *kinds* of thing, and colouring them as if they were would imply a
 * category the data does not have.
 */
export function DayBars({ series = [], height = 96, color = 'var(--brand)',
                          label = 'closed' }) {
  const [hover, setHover] = useState(null)
  if (!series.length) return <div style={{ height }} />

  const slot = 100 / series.length
  const gap = Math.min(1.2, slot * 0.18)
  const barW = Math.max(0.5, slot - gap)
  const max = Math.max(1, ...series.map((d) => d.closed))
  const peak = series.reduce((best, d) => (d.closed > best.closed ? d : best), series[0])

  return (
    <div className="viz-wrap">
      <svg viewBox={`0 0 100 ${height}`} width="100%" height={height}
           preserveAspectRatio="none" className="viz-svg" role="img"
           aria-label={`Jobs ${label} per day over the last ${series.length} days`}>
        <line x1="0" y1={height - 0.5} x2="100" y2={height - 0.5}
              stroke="var(--border2)" strokeWidth="1" vectorEffect="non-scaling-stroke" />
        {series.map((d, i) => {
          const h = d.closed === 0 ? 0 : Math.max(3, (d.closed / max) * (height - 14))
          const x = i * slot + gap / 2
          return (
            <g key={d.date} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
              {/* Full-height transparent target: an empty day must still be
                  hoverable, and a 3px bar is not a hit target. */}
              <rect x={i * slot} y="0" width={slot} height={height} fill="transparent" />
              {h > 0 ? (
                <path d={barPath(x, height - h, barW, h)} fill={color}
                      opacity={hover === null || hover === i ? 1 : 0.4} />
              ) : (
                <line x1={x} y1={height - 1} x2={x + barW} y2={height - 1}
                      stroke="var(--border2)" strokeWidth="2" />
              )}
            </g>
          )
        })}
      </svg>

      {/* Selective direct labels: the window's ends and the peak. A number on
          every column would out-shout the shape the chart exists to show. */}
      <div className="viz-axis">
        <span>{shortDay(series[0].date)}</span>
        {peak.closed > 0 && <span className="viz-axis-peak">peak {peak.closed}</span>}
        <span>{shortDay(series[series.length - 1].date)}</span>
      </div>

      {hover !== null && (
        <div className="viz-tip" style={{ left: `${(hover + 0.5) * slot}%` }}>
          <b>{series[hover].closed}</b> {label}
          {series[hover].xp > 0 && <span className="viz-tip-sub"> · {series[hover].xp} XP</span>}
          <div className="viz-tip-date">{longDay(series[hover].date)}</div>
        </div>
      )}
    </div>
  )
}

/** TrendLine — one series over time. No legend: the card title names it. */
export function TrendLine({ points = [], height = 88, color = 'var(--brand)',
                            suffix = '', empty = 'Not enough runs to plot a trend yet.' }) {
  const [hover, setHover] = useState(null)
  if (points.length < 2) return <div className="viz-empty">{empty}</div>

  const min = Math.min(...points.map((p) => p.value))
  const max = Math.max(...points.map((p) => p.value))
  const span = max - min || 1
  const pad = 10
  const xy = points.map((p, i) => ({
    ...p,
    x: (i / (points.length - 1)) * 100,
    y: pad + (1 - (p.value - min) / span) * (height - pad * 2),
  }))
  const d = xy.map((p, i) => `${i ? 'L' : 'M'}${p.x.toFixed(2)},${p.y.toFixed(2)}`).join(' ')

  return (
    <div className="viz-wrap">
      <svg viewBox={`0 0 100 ${height}`} width="100%" height={height}
           preserveAspectRatio="none" className="viz-svg" role="img" aria-label="Score trend">
        <path d={d} fill="none" stroke={color} strokeWidth="2"
              strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
        {xy.map((p, i) => (
          <g key={p.key ?? i} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
            <rect x={p.x - 50 / points.length} y="0"
                  width={100 / points.length} height={height} fill="transparent" />
            {/* A 2px surface ring keeps the marker readable where the line
                doubles back underneath it. */}
            <circle cx={p.x} cy={p.y} r={hover === i ? 4 : 2.6} fill={color}
                    stroke="var(--surface)" strokeWidth="2" vectorEffect="non-scaling-stroke" />
          </g>
        ))}
      </svg>
      {hover !== null && (
        <div className="viz-tip" style={{ left: `${xy[hover].x}%` }}>
          <b>{Math.round(xy[hover].value)}{suffix}</b>
          {xy[hover].label && <div className="viz-tip-date">{xy[hover].label}</div>}
        </div>
      )}
    </div>
  )
}

/** LabelledBars — a named, counted bar per class. See the module note. */
export function LabelledBars({ rows = [], empty = 'Nothing assigned.' }) {
  const shown = rows.filter((r) => r.value > 0)
  if (!shown.length) return <div className="viz-empty">{empty}</div>
  const max = Math.max(...shown.map((r) => r.value))
  return (
    <div className="viz-rows">
      {shown.map((r) => (
        <div className="viz-row" key={r.key}>
          <span className="viz-row-label">{r.label}</span>
          <span className="viz-row-track">
            <span className="viz-row-fill"
                  style={{ width: `${Math.max(4, (r.value / max) * 100)}%`, background: r.color }} />
          </span>
          <span className="viz-row-value">{r.value}</span>
        </div>
      ))}
    </div>
  )
}

/** StatTile — the KPI: a number, what it means, and optionally why it moved. */
export function StatTile({ label, value, hint, tone, icon, onClick }) {
  const Tag = onClick ? 'button' : 'div'
  return (
    <Tag type={onClick ? 'button' : undefined} onClick={onClick}
         className={`card kpi stat-tile${tone ? ` stat-${tone}` : ''}`
                    + `${onClick ? ' stat-clickable' : ''}`}>
      <div className="card-label">
        {icon && <i className={`ti ${icon}`} aria-hidden="true" />} {label}
      </div>
      <div className="card-value">{value}</div>
      {hint && <div className="card-change">{hint}</div>}
    </Tag>
  )
}

/** The reserved status palette, validated for CVD separation against the
 *  light surface. Shared so every severity mark in the product agrees. */
export const SEVERITY_COLOR = {
  critical: '#e11d48',
  serious: '#d97706',
  warning: '#d97706',
  info: '#2563eb',
}
