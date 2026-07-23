// Heatmap.jsx — real-time signal anomaly heatmap. SVG grid showing all signals
// over the last 60 seconds, colored by severity. Anomaly patterns become
// immediately visible as colored bands spread across the time axis.
import React, { useEffect, useRef, useState, useMemo } from 'react'
import { SIG, sevClass } from './lib.jsx'

const COLS = 60 // 60 seconds of history
const CELL_W = 8
const CELL_H = 20
const LABEL_W = 90
const PAD_T = 20
const PAD_R = 8

const COLORS = {
  '': '#16a34a22',     // ok — faint green
  'ok': '#16a34a22',
  'warn': '#d9770666',  // amber
  'crit': '#e11d4888',  // red
}
const BORDER_COLORS = {
  '': 'transparent',
  'ok': 'transparent',
  'warn': '#d97706',
  'crit': '#e11d48',
}

export default function Heatmap({ signals, live, height }) {
  const bufferRef = useRef({})  // {signalKey: [values...]}
  const [tick, setTick] = useState(0)
  const [hover, setHover] = useState(null) // {row, col}

  // initialize buffer for each signal
  useEffect(() => {
    const buf = bufferRef.current
    signals.forEach(sig => {
      if (!buf[sig]) buf[sig] = new Array(COLS).fill(null)
    })
  }, [signals])

  // push new values on each live update
  useEffect(() => {
    if (!live || Object.keys(live).length === 0) return
    const buf = bufferRef.current
    signals.forEach(sig => {
      if (!buf[sig]) buf[sig] = new Array(COLS).fill(null)
      buf[sig].push(live[sig] ?? null)
      if (buf[sig].length > COLS) buf[sig].shift()
    })
    setTick(t => t + 1) // force re-render
  }, [live, signals])

  const rows = signals.length
  const W = LABEL_W + COLS * CELL_W + PAD_R
  const H = PAD_T + rows * CELL_H + 4
  const buf = bufferRef.current

  // time labels
  const timeLabels = useMemo(() => {
    const labels = []
    for (let i = 0; i < COLS; i += 15) {
      labels.push({ col: i, label: `-${COLS - i}s` })
    }
    labels.push({ col: COLS - 1, label: 'now' })
    return labels
  }, [])

  const hoverData = hover ? {
    signal: signals[hover.row],
    col: hover.col,
    value: buf[signals[hover.row]]?.[hover.col],
    sev: sevClass(signals[hover.row], buf[signals[hover.row]]?.[hover.col]),
    label: SIG[signals[hover.row]]?.label || signals[hover.row],
    unit: SIG[signals[hover.row]]?.unit || '',
    ago: COLS - hover.col,
  } : null

  return (
    <div style={{ position: 'relative' }}>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" style={{ display: 'block' }}
        onMouseLeave={() => setHover(null)}>
        {/* time axis labels */}
        {timeLabels.map(t => (
          <text key={t.col} x={LABEL_W + t.col * CELL_W + CELL_W / 2} y={14}
            textAnchor="middle" fill="#9aa1ad" fontSize="8" fontFamily="var(--mono)">
            {t.label}
          </text>
        ))}

        {/* rows */}
        {signals.map((sig, row) => {
          const vals = buf[sig] || []
          const meta = SIG[sig] || {}
          return (
            <g key={sig}>
              {/* signal label */}
              <text x={LABEL_W - 6} y={PAD_T + row * CELL_H + CELL_H / 2 + 3}
                textAnchor="end" fill="var(--muted)" fontSize="9" fontFamily="var(--mono)"
                fontWeight="600">
                {(meta.label || sig.split(':')[1] || sig).slice(0, 12)}
              </text>

              {/* cells */}
              {vals.map((val, col) => {
                const sev = val != null ? (sevClass(sig, val) || 'ok') : ''
                const isHover = hover?.row === row && hover?.col === col
                return (
                  <rect key={col}
                    x={LABEL_W + col * CELL_W} y={PAD_T + row * CELL_H}
                    width={CELL_W - 1} height={CELL_H - 2}
                    rx="2"
                    fill={val != null ? (COLORS[sev] || COLORS.ok) : '#1a1a2e08'}
                    stroke={isHover ? '#7c3aed' : (sev === 'crit' ? '#e11d4833' : 'transparent')}
                    strokeWidth={isHover ? 1.5 : 0.5}
                    style={{ cursor: 'crosshair', transition: 'fill .15s' }}
                    onMouseEnter={() => setHover({ row, col })}
                  />
                )
              })}

              {/* row separator */}
              <line x1={LABEL_W} x2={W - PAD_R}
                y1={PAD_T + (row + 1) * CELL_H - 1} y2={PAD_T + (row + 1) * CELL_H - 1}
                stroke="var(--border)" strokeWidth="0.5" opacity="0.3" />
            </g>
          )
        })}

        {/* crosshair column highlight */}
        {hover && (
          <rect x={LABEL_W + hover.col * CELL_W - 0.5} y={PAD_T - 2}
            width={CELL_W} height={rows * CELL_H + 4}
            fill="none" stroke="#7c3aed" strokeWidth="1" rx="2" opacity="0.4" />
        )}
      </svg>

      {/* Tooltip */}
      {hoverData && hoverData.value != null && (
        <div style={{ position: 'absolute',
          left: Math.min((LABEL_W + hover.col * CELL_W) / W * 100, 70) + '%',
          top: PAD_T + hover.row * CELL_H - 4,
          background: 'rgba(22,19,31,.92)', border: '1px solid rgba(124,58,237,.4)',
          borderRadius: 8, padding: '6px 10px', fontSize: 10, color: '#e9edff',
          fontFamily: 'var(--mono)', pointerEvents: 'none', zIndex: 5,
          backdropFilter: 'blur(8px)', whiteSpace: 'nowrap',
          transform: 'translateY(-100%)' }}>
          <div style={{ fontWeight: 700, marginBottom: 2 }}>{hoverData.label}</div>
          <div>
            <span style={{ color: BORDER_COLORS[hoverData.sev] || '#16a34a' }}>
              {typeof hoverData.value === 'number' ? hoverData.value.toFixed(1) : hoverData.value}
            </span>
            <span style={{ color: '#9aa1ad' }}> {hoverData.unit}</span>
            <span style={{ color: '#9aa1ad', marginLeft: 8 }}>{hoverData.ago}s ago</span>
          </div>
          <div style={{ marginTop: 2 }}>
            <span style={{ fontSize: 9, padding: '1px 5px', borderRadius: 4, fontWeight: 700,
              background: COLORS[hoverData.sev], color: BORDER_COLORS[hoverData.sev] || '#16a34a' }}>
              {hoverData.sev === 'crit' ? 'CRITICAL' : hoverData.sev === 'warn' ? 'WARNING' : 'NOMINAL'}
            </span>
          </div>
        </div>
      )}
    </div>
  )
}
