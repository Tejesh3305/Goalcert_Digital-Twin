/**
 * Scene3D — mounts the procedural three.js machine engine (collinsEngine.js,
 * ported from the collins-demo) and streams this backend's live telemetry onto
 * its subsystems. Used for the Wire EDM twin (and any other procedural domain the
 * engine supports). No external assets — fully offline.
 */
import { useEffect, useRef } from 'react'
import { createViewer } from '../three/collinsEngine'

// Signal metadata for the EDM readouts (label/unit + thresholds for status).
const SIG = {
  'edm:cuttingSpeed': { label: 'Cut Speed', unit: 'mm²/min' },
  'edm:gapVoltage': { label: 'Gap Voltage', unit: 'V', warnLow: 31, critLow: 25 },
  'edm:shortCircuitRate': { label: 'Short-Circuit', unit: '%', warn: 15, crit: 25 },
  'edm:peakCurrent': { label: 'Peak Current', unit: 'A' },
  'edm:dielectricConductivity': { label: 'Conductivity', unit: 'µS/cm', warn: 20, crit: 25 },
  'edm:dielectricTemp': { label: 'Dielectric Temp', unit: '°C', warn: 29, crit: 32 },
  'edm:dielectricFlow': { label: 'Flush Flow', unit: 'L/min' },
  'edm:wireTension': { label: 'Wire Tension', unit: 'N', warnLow: 9, critLow: 8 },
  'edm:wireBreakRisk': { label: 'Wire-Break Risk', unit: '%', warn: 45, crit: 60 },
  'edm:wireFeedRate': { label: 'Wire Feed', unit: 'm/min' },
  'edm:wireWear': { label: 'Wire Wear', unit: '%', warn: 60, crit: 85 },
  'edm:surfaceFinishRa': { label: 'Surface Ra', unit: 'µm' },
}
const fmt = (v) => (v == null ? '—' : Math.abs(v) >= 100 ? Math.round(v).toLocaleString() : Number(v).toFixed(Math.abs(v) < 10 ? 2 : 1))
function sevClass(sig, v) {
  const m = SIG[sig]; if (!m || v == null) return 'ok'
  if (m.crit != null && v >= m.crit) return 'crit'
  if (m.critLow != null && v <= m.critLow) return 'crit'
  if (m.warn != null && v >= m.warn) return 'warn'
  if (m.warnLow != null && v <= m.warnLow) return 'warn'
  return 'ok'
}
const metric = (sig, live) => [SIG[sig]?.label || sig, SIG[sig]?.unit || '', fmt(live[sig])]
function worst(sigs, live) {
  let s = 'ok'
  for (const sig of sigs) { const c = sevClass(sig, live[sig]); if (c === 'crit') return 'crit'; if (c === 'warn') s = 'warn' }
  return s
}

function edmUpdates(live) {
  if (!live || live['edm:cuttingSpeed'] == null) return null
  return {
    'EDM-1': { status: worst(['edm:shortCircuitRate', 'edm:wireBreakRisk', 'edm:dielectricTemp'], live),
      metrics: [metric('edm:cuttingSpeed', live), metric('edm:gapVoltage', live), metric('edm:shortCircuitRate', live)] },
    'GEN-1': { status: worst(['edm:shortCircuitRate', 'edm:gapVoltage'], live),
      metrics: [metric('edm:peakCurrent', live), metric('edm:shortCircuitRate', live), metric('edm:gapVoltage', live)] },
    'DIE-1': { status: worst(['edm:dielectricConductivity', 'edm:dielectricTemp', 'edm:dielectricFlow'], live),
      metrics: [metric('edm:dielectricConductivity', live), metric('edm:dielectricTemp', live), metric('edm:dielectricFlow', live)] },
    'WIRE-1': { status: worst(['edm:wireBreakRisk', 'edm:wireTension'], live),
      metrics: [metric('edm:wireTension', live), metric('edm:wireBreakRisk', live), metric('edm:wireFeedRate', live)] },
    'GUIDE-1': { status: worst(['edm:wireWear', 'edm:surfaceFinishRa'], live),
      metrics: [metric('edm:wireWear', live), metric('edm:surfaceFinishRa', live)] },
  }
}

function domainUpdates(domain, live) {
  if (domain === 'edm-machine') return edmUpdates(live)
  return null
}

export default function Scene3D({ domain, machine, live = {}, height = 320 }) {
  const hostRef = useRef(null)
  const viewerRef = useRef(null)

  useEffect(() => {
    if (!hostRef.current) return
    const onAskAI = async (asset) => {
      const u = domainUpdates(domain, live)?.[asset]
      const st = u?.status || 'ok'
      return `${asset}: ${st === 'crit' ? 'critical — inspect immediately.' : st === 'warn' ? 'drifting out of band — monitor.' : 'operating within limits.'}`
    }
    let viewer
    try { viewer = createViewer(hostRef.current, { domain, machine, onAskAI }) }
    catch (e) { console.error('3D scene failed', e) }
    viewerRef.current = viewer
    return () => { try { viewer && viewer.dispose() } catch { /* */ } viewerRef.current = null }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [domain, machine])

  useEffect(() => {
    const v = viewerRef.current
    if (!v || !v.updateAssets) return
    const updates = domainUpdates(domain, live)
    if (updates) v.updateAssets(updates)
  }, [live, domain])

  return <div ref={hostRef} style={{ height, position: 'relative', borderRadius: 12, overflow: 'hidden', border: '1px solid var(--border)' }} />
}
