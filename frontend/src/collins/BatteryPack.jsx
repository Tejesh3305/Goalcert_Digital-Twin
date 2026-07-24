// BatteryPack.jsx — the cell-level energy twin for the EV site. Two toggleable views:
//   • Battery cells — an isometric 3-D battery module of cylindrical cells that glow
//     by temperature/health; every cell is SELECTABLE — click one to inspect its
//     telemetry in the side panel. The predictive AI singles out the failing cell.
//   • Solar array — a top-down view of the PV field: click any array (panel) to see
//     its telemetry on the side; the side panel always shows the overall + live
//     telemetry. When the twin has no solar physics (the battery-pack twin), the
//     array streams a built-in simulation so nothing is ever empty.
import React, { useEffect, useMemo, useRef, useState } from 'react'
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

function buildPanels(totalKw, irr0) {
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
      irr: Math.round((irr0 || 760) * clamp01(factor)),
      temp: Math.round(38 + rnd(i, 7) * 10 + (status === 'crit' ? 9 : 0)),
    })
  }
  return arr
}

// ── Built-in solar simulation (used when the twin has no solar physics) ──────
// A gentle day-curve so array output, irradiance, self-consumption and ambient
// vary live instead of sitting empty.
function solarFrame(phase) {
  const dayN = clamp01(0.55 + 0.4 * Math.sin(phase) + (Math.random() - 0.5) * 0.05)
  return {
    output: Math.round(120 + 180 * dayN),   // kW  (~120–300)
    irr: Math.round(280 + 640 * dayN),       // W/m²
    selfUse: Math.round(50 + 24 * dayN),     // %
    ambient: Math.round(26 + 12 * dayN),     // °C
    dayN,
  }
}

function useSolarSim() {
  const [frame, setFrame] = useState(() => solarFrame(0.6))
  const phase = useRef(0.6)
  useEffect(() => {
    const t = setInterval(() => { phase.current += 0.05; setFrame(solarFrame(phase.current)) }, 2000)
    return () => clearInterval(t)
  }, [])
  return frame
}

// A labelled telemetry row for the side panel.
function Tele({ label, value, unit, tone }) {
  return (
    <div className="bp-tele-row">
      <span>{label}</span>
      <b style={tone ? { color: tone } : undefined}>{value}{unit ? <span className="bp-tele-unit"> {unit}</span> : null}</b>
    </div>
  )
}

export default function BatteryPack({ live = {}, height = 340 }) {
  const [view, setView] = useState('battery')   // 'battery' | 'solar'
  const [sel, setSel] = useState(null)           // selected cell index
  const [selP, setSelP] = useState(null)         // selected panel index

  const cellTempMax = live['ev:cellTempMax'] ?? 33
  const imbalance = live['ev:cellImbalance'] ?? 14
  const soh = live['ev:stateOfHealth'] ?? 93
  const risk = live['ev:thermalRunawayRisk'] ?? 2

  // Solar: prefer the twin's real output if present, else the simulation.
  const sim = useSolarSim()
  const solarKw = live['ev:solarOutput'] ?? sim.output
  const irr = live['ev:solarIrradiance'] ?? sim.irr
  const selfUse = live['ev:selfConsumption'] ?? sim.selfUse

  const cells = useMemo(() => buildCells(cellTempMax, risk, soh), [cellTempMax, risk, soh])
  const panels = useMemo(() => buildPanels(solarKw, irr), [solarKw, irr])

  const days = Math.max(2, Math.round(180 - risk * 2.6 - (100 - soh) * 6 - Math.max(0, cellTempMax - 34) * 3))
  const packTone = risk >= 40 ? 'crit' : cellTempMax >= 42 ? 'warn' : 'ok'
  const selCell = sel != null ? cells[sel] : null

  const faultPanel = panels[FAULT_PANEL]
  const arrayKw = panels.reduce((a, p) => a + p.kw, 0)
  const avgYield = Math.round(panels.reduce((a, p) => a + p.factor, 0) / PN * 100)
  const faults = panels.filter((p) => p.status !== 'ok').length
  const worstPanel = panels.reduce((a, p) => (p.factor < a.factor ? p : a), panels[0])
  const selPanel = selP != null ? panels[selP] : null
  const failingCells = cells.filter((c) => c.failing).length

  const clsFor = (s) => (s === 'crit' ? 'var(--accent-red)' : s === 'warn' ? 'var(--accent-amber)' : 'var(--text)')

  return (
    <div className="card" style={{ overflow: 'hidden' }}>
      <div className="card-title">
        <Icon n={view === 'battery' ? 'ti-battery-4' : 'ti-solar-panel'} />
        {view === 'battery' ? 'Battery Module · Cell-Level Twin' : 'Solar Array · Panel-Level Twin'}
        <span className={`pill ${packTone === 'crit' ? 'pill-red' : packTone === 'warn' ? 'pill-amber' : 'pill-green'}`}>
          {view === 'battery' ? `${N} cells` : `${PN} arrays`}</span>
        <div className="bp-seg">
          <button className={view === 'battery' ? 'on' : ''} onClick={() => setView('battery')}>Battery cells</button>
          <button className={view === 'solar' ? 'on' : ''} onClick={() => setView('solar')}>Solar array</button>
        </div>
      </div>

      <div className="bp-body">
        {/* ── left: the visualisation ── */}
        {view === 'battery' ? (
          <div className="bp-stage" style={{ height }}>
            <div className="bp-module" style={{ gridTemplateColumns: `repeat(${COLS}, 1fr)` }}>
              {cells.map((cell) => {
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
            <div className="bp-hint"><Icon n="ti-hand-finger" /> Click a cell to inspect</div>
          </div>
        ) : (
          <div className="bp-stage solar" style={{ height }}>
            <div className="bp-sun" />
            <div className="bp-array" style={{ gridTemplateColumns: `repeat(${P_COLS}, 1fr)` }}>
              {panels.map((p) => (
                <button key={p.i}
                  className={`bp-panel ${p.status} ${selP === p.i ? 'sel' : ''}`}
                  title={`Array ${panelName(p)} · ${p.kw.toFixed(1)} kW`}
                  style={{ '--pf': p.factor.toFixed(2) }}
                  onClick={() => setSelP(selP === p.i ? null : p.i)} />
              ))}
            </div>
            <div className="bp-hint"><Icon n="ti-hand-finger" /> Click an array to inspect</div>
          </div>
        )}

        {/* ── right: the telemetry side panel ── */}
        <div className="bp-side">
          {view === 'battery' ? (
            <>
              <div className="bp-side-h"><Icon n="ti-activity" /> Overall telemetry</div>
              <div className="bp-tele">
                <Tele label="Pack SoH" value={soh.toFixed(1)} unit="%" />
                <Tele label="Cell max temp" value={cellTempMax.toFixed(1)} unit="°C" tone={cellTempMax >= 42 ? 'var(--accent-red)' : undefined} />
                <Tele label="Imbalance" value={Math.round(imbalance)} unit="mV" tone={imbalance >= 35 ? 'var(--accent-amber)' : undefined} />
                <Tele label="Runaway risk" value={Math.round(risk)} unit="%" tone={risk >= 15 ? 'var(--accent-red)' : undefined} />
                <Tele label="Cells" value={N} />
                <Tele label="Failing" value={failingCells} tone={failingCells ? 'var(--accent-red)' : 'var(--accent-green)'} />
              </div>

              <div className="bp-side-h">{selCell ? <><Icon n="ti-battery-4" /> Cell {selCell.i}</> : <><Icon n="ti-list" /> Live telemetry</>}</div>
              {selCell ? (
                <>
                  <div className="bp-tele">
                    <Tele label="Temp" value={selCell.temp.toFixed(1)} unit="°C" tone={selCell.temp > 44 ? 'var(--accent-red)' : undefined} />
                    <Tele label="Voltage" value={selCell.volt.toFixed(2)} unit="V" />
                    <Tele label="Cell SoH" value={selCell.soh} unit="%" />
                    <Tele label="Internal R" value={selCell.esr} unit="mΩ" />
                    <Tele label="Position" value={`R${selCell.r + 1}·C${selCell.c + 1}`} />
                    <Tele label="String" value={Math.floor(selCell.i / COLS) + 1} />
                  </div>
                  <div className={`bp-side-ai ${selCell.failing ? 'crit' : ''}`}>
                    <Icon n="ti-brain" /> {selCell.failing
                      ? <>Dendrite-growth precursor — projected failure in <b>{days} days</b>. Schedule module swap.</>
                      : 'Cell within nominal band — no action required.'}
                  </div>
                </>
              ) : (
                <>
                  <div className="bp-tele">
                    <Tele label="Hot-spot cell" value={`#${HOTSPOT}`} />
                    <Tele label="Failure ETA" value={days} unit="days" tone={days < 30 ? 'var(--accent-red)' : undefined} />
                    <Tele label="Coolant" value={(live['ev:coolantTemp'] ?? 29).toFixed?.(1) ?? live['ev:coolantTemp'] ?? 29} unit="°C" />
                    <Tele label="State of charge" value={Math.round(live['ev:stateOfCharge'] ?? 64)} unit="%" />
                  </div>
                  <div className="bp-side-ai"><Icon n="ti-brain" /> Predictive battery health — cell {HOTSPOT} projected failure in <b>{days} days</b> (dendrite-growth precursor).</div>
                </>
              )}
            </>
          ) : (
            <>
              <div className="bp-side-h"><Icon n="ti-activity" /> Overall telemetry</div>
              <div className="bp-tele">
                <Tele label="Array output" value={Math.round(arrayKw)} unit="kW" />
                <Tele label="Irradiance" value={irr} unit="W/m²" />
                <Tele label="Avg yield" value={avgYield} unit="%" tone={avgYield < 80 ? 'var(--accent-amber)' : undefined} />
                <Tele label="Arrays online" value={`${PN - faults}/${PN}`} tone={faults ? 'var(--accent-amber)' : 'var(--accent-green)'} />
                <Tele label="Self-consumption" value={selfUse} unit="%" />
                <Tele label="Worst array" value={panelName(worstPanel)} tone="var(--accent-red)" />
              </div>

              <div className="bp-side-h">{selPanel ? <><Icon n="ti-solar-panel" /> Array {panelName(selPanel)}</> : <><Icon n="ti-list" /> Live telemetry</>}</div>
              {selPanel ? (
                <>
                  <div className="bp-tele">
                    <Tele label="Output" value={selPanel.kw.toFixed(1)} unit="kW" tone={clsFor(selPanel.status)} />
                    <Tele label="Yield" value={Math.round(selPanel.factor * 100)} unit="%" tone={clsFor(selPanel.status)} />
                    <Tele label="Irradiance" value={selPanel.irr} unit="W/m²" />
                    <Tele label="Cell temp" value={selPanel.temp} unit="°C" />
                    <Tele label="Position" value={`R${selPanel.r + 1}·C${selPanel.c + 1}`} />
                    <Tele label="Status" value={selPanel.status === 'crit' ? 'FAULT' : selPanel.status === 'warn' ? 'REDUCED' : 'OPTIMAL'} tone={clsFor(selPanel.status)} />
                  </div>
                  <div className={`bp-side-ai ${selPanel.status === 'crit' ? 'crit' : ''}`}>
                    <Icon n="ti-brain" /> {selPanel.status === 'crit'
                      ? 'Soiling / shading suspected — clean & re-test; check the bypass diode.'
                      : selPanel.status === 'warn'
                        ? 'Below-band yield — schedule cleaning at the next site visit.'
                        : 'Array performing to spec.'}
                  </div>
                </>
              ) : (
                <>
                  <div className="bp-tele">
                    <Tele label="Peak array" value={panelName(panels.reduce((a, p) => (p.factor > a.factor ? p : a), panels[0]))} />
                    <Tele label="Ambient" value={sim.ambient} unit="°C" />
                    <Tele label="Faulted arrays" value={faults} tone={faults ? 'var(--accent-amber)' : 'var(--accent-green)'} />
                    <Tele label="Flagged" value={panelName(faultPanel)} tone="var(--accent-red)" />
                  </div>
                  <div className="bp-side-ai"><Icon n="ti-brain" /> Predictive solar health — array {panelName(faultPanel)} at <b>{Math.round(faultPanel.factor * 100)}% yield</b>; soiling / shading suspected, clean & inspect.</div>
                </>
              )}
            </>
          )}
        </div>
      </div>

      {/* legend */}
      <div className="bp-foot">
        {view === 'battery' ? (
          <div className="bp-legend">
            <span><i style={{ background: '#38bdf8', borderRadius: '50%' }} /> Healthy</span>
            <span><i style={{ background: '#fbbf24', borderRadius: '50%' }} /> Warm</span>
            <span><i style={{ background: '#fb7185', borderRadius: '50%' }} /> Failing</span>
          </div>
        ) : (
          <div className="bp-legend">
            <span><i style={{ background: '#2456c8' }} /> Optimal</span>
            <span><i style={{ background: '#f59e0b' }} /> Reduced</span>
            <span><i style={{ background: '#ef4444' }} /> Fault</span>
          </div>
        )}
      </div>
    </div>
  )
}
