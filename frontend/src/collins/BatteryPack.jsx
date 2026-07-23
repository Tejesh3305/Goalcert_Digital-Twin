// BatteryPack.jsx — the cell-level energy twin for the EV site. Two toggleable views:
//   • Battery cells — an isometric 3-D battery module of cylindrical cells that glow
//     by temperature/health; every cell is SELECTABLE — click one to inspect its
//     temp / voltage / SoH / internal-resistance. GoalCert's predictive AI singles
//     out the failing hot-spot cell ("Cell 17 · fails in 42 days").
//   • Solar array — a top-down view of the site's PV field: rows of panels with a
//     sun-glint sweep, per-panel output, and the AI flagging an under-performing /
//     soiled panel. Both react live to the EV telemetry frame.
import React, { useMemo, useState } from 'react'
import './collins.css'
import { Icon } from './lib.jsx'

// battery module geometry
const COLS = 12, ROWS = 6, N = COLS * ROWS
const HOTSPOT = 17            // the cell the AI singles out

// solar array geometry
const P_COLS = 8, P_ROWS = 4, PN = P_COLS * P_ROWS
const FAULT_PANEL = 13        // the panel the AI flags

const clamp01 = (x) => Math.max(0, Math.min(1, x))
// deterministic per-index pseudo-noise (stable across renders — no flicker)
const rnd = (i, s = 1) => { const x = Math.sin(i * 12.9898 + s * 4.13) * 43758.5453; return x - Math.floor(x) }
const panelName = (p) => `${String.fromCharCode(65 + p.r)}${p.c + 1}`

// temp → colour (blue → teal → amber → red)
function tempColor(t) {
  const k = clamp01((t - 24) / 40)   // t in ~20..70 °C
  if (k < 0.35) return { c: '#38bdf8', glow: '#0ea5e9' }
  if (k < 0.6) return { c: '#22d3ee', glow: '#06b6d4' }
  if (k < 0.8) return { c: '#fbbf24', glow: '#f59e0b' }
  return { c: '#fb7185', glow: '#ef4444' }
}

function buildCells(cellTempMax, risk, soh) {
  const arr = []
  for (let i = 0; i < N; i++) {
    const r = Math.floor(i / COLS), c = i % COLS
    const jitter = rnd(i) * 6 - 3
    const centreBias = 6 * Math.exp(-(((c - COLS * 0.62) ** 2) / 30 + ((r - ROWS * 0.5) ** 2) / 8))
    let temp = 24 + (cellTempMax - 24) * 0.55 + centreBias + jitter
    let failing = false
    if (i === HOTSPOT) { temp = cellTempMax + risk * 0.35; failing = risk > 12 || temp > 46 }
    const volt = +(4.02 - rnd(i, 2) * 0.05 - (failing ? 0.30 : 0) - clamp01((temp - 40) / 30) * 0.08).toFixed(3)
    const cellSoh = Math.round(clamp01(soh / 100 - rnd(i, 3) * 0.05 - (failing ? 0.15 : 0)) * 100)
    const esr = Math.round(16 + rnd(i, 4) * 6 + (failing ? 15 : 0) + clamp01((temp - 40) / 20) * 8)
    arr.push({ i, r, c, temp, failing, volt, soh: cellSoh, esr })
  }
  return arr
}

function buildPanels(totalKw) {
  const base = Math.max(0, totalKw) / PN
  const arr = []
  for (let i = 0; i < PN; i++) {
    const r = Math.floor(i / P_COLS), c = i % P_COLS
    let factor = 0.92 + rnd(i, 5) * 0.13, status = 'ok'
    if (i === FAULT_PANEL) { factor = 0.34; status = 'crit' }
    else if (rnd(i, 6) > 0.88) { factor = 0.70; status = 'warn' }
    arr.push({
      i, r, c, factor, status,
      kw: +(base * factor).toFixed(2),
      irr: Math.round(760 * clamp01(factor)),
      temp: Math.round(38 + rnd(i, 7) * 10 + (status === 'crit' ? 9 : 0)),
    })
  }
  return arr
}

export default function BatteryPack({ live = {}, height = 340 }) {
  const [view, setView] = useState('battery')   // 'battery' | 'solar'
  const [sel, setSel] = useState(null)           // selected cell index
  const [selP, setSelP] = useState(null)         // selected panel index

  const cellTempMax = live['ev:cellTempMax'] ?? 33
  const imbalance = live['ev:cellImbalance'] ?? 14
  const soh = live['ev:stateOfHealth'] ?? 93
  const risk = live['ev:thermalRunawayRisk'] ?? 2
  const solarKw = live['ev:solarOutput'] ?? 210

  const cells = useMemo(() => buildCells(cellTempMax, risk, soh), [cellTempMax, risk, soh])
  const panels = useMemo(() => buildPanels(solarKw), [solarKw])

  // AI predicted failure horizon for the hot-spot cell
  const days = Math.max(2, Math.round(180 - risk * 2.6 - (100 - soh) * 6 - Math.max(0, cellTempMax - 34) * 3))
  const packTone = risk >= 40 ? 'crit' : cellTempMax >= 42 ? 'warn' : 'ok'
  const selCell = sel != null ? cells[sel] : null

  const faultPanel = panels[FAULT_PANEL]
  const arrayKw = panels.reduce((a, p) => a + p.kw, 0)
  const worstPanel = panels.reduce((a, p) => (p.factor < a.factor ? p : a), panels[0])
  const selPanel = selP != null ? panels[selP] : null

  return (
    <div className="card" style={{ overflow: 'hidden' }}>
      <div className="card-title">
        <Icon n={view === 'battery' ? 'ti-battery-4' : 'ti-solar-panel'} />
        {view === 'battery' ? 'Battery Module · Cell-Level Twin' : 'Solar Array · Panel-Level Twin'}
        <span className={`pill ${packTone === 'crit' ? 'pill-red' : packTone === 'warn' ? 'pill-amber' : 'pill-green'}`}>
          {view === 'battery' ? `${N} cells` : `${PN} panels`}</span>
        <div className="bp-seg">
          <button className={view === 'battery' ? 'on' : ''} onClick={() => setView('battery')}>Battery cells</button>
          <button className={view === 'solar' ? 'on' : ''} onClick={() => setView('solar')}>Solar array</button>
        </div>
      </div>

      {view === 'battery' ? (
        <>
          <div className="bp-stage" style={{ height }}>
            <div className="bp-module" style={{ gridTemplateColumns: `repeat(${COLS}, 1fr)` }}>
              {cells.map(cell => {
                const { c, glow } = tempColor(cell.temp)
                return (
                  <button key={cell.i}
                    className={`bp-cell ${cell.failing ? 'fail' : ''} ${sel === cell.i ? 'sel' : ''}`}
                    title={`Cell ${cell.i} · ${cell.temp.toFixed(1)}°C`}
                    style={{ '--cc': c, '--cg': glow }}
                    onClick={() => setSel(sel === cell.i ? null : cell.i)}>
                    <span className="cap" />
                  </button>
                )
              })}
            </div>

            {selCell ? (
              <div className="bp-insp">
                <div className="bp-insp-h">
                  <b>Cell {selCell.i}</b>
                  <span className={`pill ${selCell.failing ? 'pill-red' : selCell.temp > 44 ? 'pill-amber' : 'pill-green'}`}>
                    {selCell.failing ? 'FAILING' : selCell.temp > 44 ? 'WARM' : 'HEALTHY'}</span>
                  <span className="bp-insp-x" onClick={() => setSel(null)}>✕</span>
                </div>
                <div className="bp-insp-grid">
                  <div><span>Temp</span><b>{selCell.temp.toFixed(1)}°C</b></div>
                  <div><span>Voltage</span><b>{selCell.volt.toFixed(2)} V</b></div>
                  <div><span>Cell SoH</span><b>{selCell.soh}%</b></div>
                  <div><span>Internal R</span><b>{selCell.esr} mΩ</b></div>
                  <div><span>Position</span><b>R{selCell.r + 1}·C{selCell.c + 1}</b></div>
                  <div><span>String</span><b>{Math.floor(selCell.i / COLS) + 1}</b></div>
                </div>
                {selCell.failing &&
                  <div className="bp-insp-ai"><Icon n="ti-brain" /> Dendrite-growth precursor — projected failure in <b>{days} days</b>. Schedule module swap.</div>}
              </div>
            ) : (
              <div className="bp-callout">
                <div className="bp-callout-t"><Icon n="ti-brain" /> Predictive Battery Health</div>
                <div className="bp-callout-m">Cell {HOTSPOT} — projected failure in <b>{days} days</b></div>
                <div className="bp-callout-s">Dendrite-growth precursor · schedule module swap</div>
              </div>
            )}
            <div className="bp-hint"><Icon n="ti-hand-finger" /> Click a cell to inspect</div>
          </div>

          <div className="bp-foot">
            <div className="bp-legend">
              <span><i style={{ background: '#38bdf8', borderRadius: '50%' }} /> Healthy</span>
              <span><i style={{ background: '#fbbf24', borderRadius: '50%' }} /> Warm</span>
              <span><i style={{ background: '#fb7185', borderRadius: '50%' }} /> Failing</span>
            </div>
            <div className="bp-stats">
              <div><span>Cell max</span><b style={{ color: cellTempMax >= 42 ? 'var(--accent-red)' : 'var(--text)' }}>{cellTempMax.toFixed(1)}°C</b></div>
              <div><span>Imbalance</span><b style={{ color: imbalance >= 35 ? 'var(--accent-amber)' : 'var(--text)' }}>{Math.round(imbalance)} mV</b></div>
              <div><span>Pack SoH</span><b>{soh.toFixed(1)}%</b></div>
              <div><span>Runaway risk</span><b style={{ color: risk >= 15 ? 'var(--accent-red)' : 'var(--text)' }}>{Math.round(risk)}%</b></div>
            </div>
          </div>
        </>
      ) : (
        <>
          <div className="bp-stage solar" style={{ height }}>
            <div className="bp-sun" />
            <div className="bp-array" style={{ gridTemplateColumns: `repeat(${P_COLS}, 1fr)` }}>
              {panels.map(p => (
                <button key={p.i}
                  className={`bp-panel ${p.status} ${selP === p.i ? 'sel' : ''}`}
                  title={`Panel ${panelName(p)} · ${p.kw.toFixed(1)} kW`}
                  style={{ '--pf': p.factor.toFixed(2) }}
                  onClick={() => setSelP(selP === p.i ? null : p.i)} />
              ))}
            </div>

            {selPanel ? (
              <div className="bp-insp">
                <div className="bp-insp-h">
                  <b>Panel {panelName(selPanel)}</b>
                  <span className={`pill ${selPanel.status === 'crit' ? 'pill-red' : selPanel.status === 'warn' ? 'pill-amber' : 'pill-green'}`}>
                    {selPanel.status === 'crit' ? 'FAULT' : selPanel.status === 'warn' ? 'REDUCED' : 'OPTIMAL'}</span>
                  <span className="bp-insp-x" onClick={() => setSelP(null)}>✕</span>
                </div>
                <div className="bp-insp-grid">
                  <div><span>Output</span><b>{selPanel.kw.toFixed(1)} kW</b></div>
                  <div><span>Yield</span><b>{Math.round(selPanel.factor * 100)}%</b></div>
                  <div><span>Irradiance</span><b>{selPanel.irr} W/m²</b></div>
                  <div><span>Cell temp</span><b>{selPanel.temp}°C</b></div>
                  <div><span>Position</span><b>R{selPanel.r + 1}·C{selPanel.c + 1}</b></div>
                  <div><span>String</span><b>{selPanel.r + 1}</b></div>
                </div>
                {selPanel.status !== 'ok' &&
                  <div className="bp-insp-ai"><Icon n="ti-brain" /> {selPanel.status === 'crit'
                    ? 'Soiling / shading suspected — clean & re-test; check bypass diode.'
                    : 'Below-band yield — schedule cleaning at next site visit.'}</div>}
              </div>
            ) : (
              <div className="bp-callout">
                <div className="bp-callout-t"><Icon n="ti-brain" /> Predictive Solar Health</div>
                <div className="bp-callout-m">Panel {panelName(faultPanel)} — <b>{Math.round(faultPanel.factor * 100)}% yield</b></div>
                <div className="bp-callout-s">Soiling / shading suspected · clean & inspect</div>
              </div>
            )}
            <div className="bp-hint"><Icon n="ti-hand-finger" /> Click a panel to inspect</div>
          </div>

          <div className="bp-foot">
            <div className="bp-legend">
              <span><i style={{ background: '#2456c8' }} /> Optimal</span>
              <span><i style={{ background: '#f59e0b' }} /> Reduced</span>
              <span><i style={{ background: '#ef4444' }} /> Fault</span>
            </div>
            <div className="bp-stats">
              <div><span>Array output</span><b>{Math.round(arrayKw)} kW</b></div>
              <div><span>Panels</span><b>{PN}</b></div>
              <div><span>Worst</span><b style={{ color: 'var(--accent-red)' }}>{panelName(worstPanel)}</b></div>
              <div><span>Avg yield</span><b>{Math.round(panels.reduce((a, p) => a + p.factor, 0) / PN * 100)}%</b></div>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
