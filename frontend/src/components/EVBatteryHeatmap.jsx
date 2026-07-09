/**
 * EVBatteryHeatmap — cell-level battery health heatmap (P2-021). A module×cell
 * grid coloured by the selected metric (SoC / SoH / voltage / temperature) with a
 * click-to-inspect detail panel. Self-contained SVG/CSS grid (no D3). Pure render
 * of the battery-pack twin's `net.cells`.
 */
import { useState } from 'react'

const METRICS = {
  temp: { label: 'Temperature', unit: '°C', lo: 20, hi: 55, fmt: (v) => v.toFixed(1) },
  voltage: { label: 'Voltage', unit: 'V', lo: 3.4, hi: 4.15, fmt: (v) => v.toFixed(3) },
  soc: { label: 'SoC', unit: '%', lo: 20, hi: 100, fmt: (v) => v.toFixed(0) },
  soh: { label: 'SoH', unit: '%', lo: 78, hi: 100, fmt: (v) => v.toFixed(1) },
}

// blue (cool/low) → green → amber → red (hot/high); temp & voltage spread use this,
// soc/soh use green(high)→red(low)
function heat(metric, v) {
  const m = METRICS[metric]
  let t = (v - m.lo) / (m.hi - m.lo)
  t = Math.max(0, Math.min(1, t))
  if (metric === 'soc' || metric === 'soh') t = 1 - t   // low is bad
  const stops = [[37, 99, 235], [22, 163, 74], [217, 119, 6], [225, 29, 72]]
  const seg = Math.min(2, Math.floor(t * 3))
  const f = t * 3 - seg
  const a = stops[seg], b = stops[seg + 1]
  const c = a.map((x, i) => Math.round(x + (b[i] - x) * f))
  return `rgb(${c[0]},${c[1]},${c[2]})`
}

export default function EVBatteryHeatmap({ net }) {
  const [metric, setMetric] = useState('temp')
  const [sel, setSel] = useState(null)
  if (!net || !net.cells) return null
  const { rows, cols, cells, pack } = net
  const cell = cells.find((c) => c.id === sel)
  const m = METRICS[metric]

  return (
    <div>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center', marginBottom: 12 }}>
        {Object.entries(METRICS).map(([k, v]) => (
          <button key={k} className="quick-chip" onClick={() => setMetric(k)}
            style={{ borderColor: metric === k ? 'var(--brand)' : 'var(--border)', fontWeight: metric === k ? 700 : 500 }}>
            {v.label}
          </button>
        ))}
        <span className="muted" style={{ marginLeft: 'auto', fontSize: 11.5 }}>
          {pack?.series}S · SoC {pack?.soc}% · SoH {pack?.soh}%
        </span>
      </div>

      <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', alignItems: 'flex-start' }}>
        <div style={{ flex: '1 1 320px', display: 'grid', gap: 3,
          gridTemplateColumns: `repeat(${cols}, 1fr)` }}>
          {cells.map((c) => (
            <div key={c.id} title={`${c.id}: ${m.fmt(c[metric])}${m.unit}`}
              onClick={() => setSel(c.id === sel ? null : c.id)}
              style={{ aspectRatio: '1', borderRadius: 4, cursor: 'pointer',
                background: heat(metric, c[metric]),
                outline: c.id === sel ? '2px solid var(--text)' : c.status === 'critical' ? '2px solid var(--accent-red)' : 'none',
                outlineOffset: -1 }} />
          ))}
        </div>

        <div style={{ width: 176, flexShrink: 0 }}>
          {/* colour scale */}
          <div style={{ height: 10, borderRadius: 6, marginBottom: 4,
            background: metric === 'soc' || metric === 'soh'
              ? 'linear-gradient(90deg, rgb(225,29,72), rgb(217,119,6), rgb(22,163,74), rgb(37,99,235))'
              : 'linear-gradient(90deg, rgb(37,99,235), rgb(22,163,74), rgb(217,119,6), rgb(225,29,72))' }} />
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 9.5, color: 'var(--muted)', marginBottom: 12 }}>
            <span>{m.lo}{m.unit}</span><span>{m.label}</span><span>{m.hi}{m.unit}</span>
          </div>
          {cell ? (
            <div style={{ background: 'var(--surface2)', borderRadius: 10, padding: '10px 12px' }}>
              <div style={{ fontWeight: 700, fontFamily: 'var(--mono)', marginBottom: 6 }}>{cell.id}</div>
              {[['Voltage', cell.voltage.toFixed(3) + ' V'], ['Temp', cell.temp + ' °C'],
                ['SoC', cell.soc + ' %'], ['SoH', cell.soh + ' %']].map(([l, v]) => (
                <div key={l} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, padding: '2px 0' }}>
                  <span className="muted">{l}</span><b>{v}</b>
                </div>
              ))}
              <div style={{ marginTop: 6, fontSize: 11, color: cell.status === 'critical' ? 'var(--accent-red)' : cell.status === 'warning' ? 'var(--accent-amber)' : 'var(--ok)', fontWeight: 600, textTransform: 'capitalize' }}>
                <i className={`ti ${cell.status === 'ok' ? 'ti-circle-check' : 'ti-alert-triangle'}`} /> {cell.status}
              </div>
            </div>
          ) : (
            <div className="muted" style={{ fontSize: 11.5, padding: '8px 4px' }}>
              {rows} modules × {cols} cells. Click a cell to inspect.
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
