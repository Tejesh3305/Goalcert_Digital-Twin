// Scene3D.jsx — React wrapper that mounts the procedural Three.js engine
// (scene/engine.js), wires the inspector's "Ask AI" to the orchestrator, and
// streams real telemetry onto all domain subsystems (not just EDM).
import React, { useEffect, useRef } from 'react'
import { createViewer } from './scene/engine.js'
import { SIG, sevClass, fmt } from './lib.jsx'
import api from './api'

// ── signal → metric helper ──────────────────────────────────────────
function metric(sig, live) { const m = SIG[sig]; return [m?.label || sig, m?.unit || '', fmt(live[sig])] }
function worst(sigs, live) {
  let s = 'ok'
  for (const sig of sigs) { const c = sevClass(sig, live[sig]); if (c === 'crit') return 'crit'; if (c === 'warn') s = 'warn' }
  return s
}

// ── EDM domain ──────────────────────────────────────────────────────
function edmUpdates(live) {
  if (!live || live['edm:cuttingSpeed'] == null) return null
  return {
    'EDM-1': { status: worst(['edm:shortCircuitRate', 'edm:wireBreakRisk', 'edm:dielectricTemperature'], live),
      metrics: [metric('edm:cuttingSpeed', live), metric('edm:gapVoltage', live), metric('edm:shortCircuitRate', live)] },
    'GEN-1': { status: worst(['edm:shortCircuitRate', 'edm:gapVoltage'], live),
      metrics: [metric('edm:peakCurrent', live), metric('edm:shortCircuitRate', live), metric('edm:gapVoltage', live)] },
    'DIE-1': { status: worst(['edm:dielectricConductivity', 'edm:dielectricTemperature', 'edm:dielectricPressure', 'edm:dielectricFlow'], live),
      metrics: [metric('edm:dielectricConductivity', live), metric('edm:dielectricTemperature', live), metric('edm:dielectricFlow', live)] },
    'WIRE-1': { status: worst(['edm:wireBreakRisk', 'edm:wireTension'], live),
      metrics: [metric('edm:wireTension', live), metric('edm:wireBreakRisk', live), metric('edm:wireFeedRate', live)] },
    'GUIDE-1': { status: worst(['edm:wireWear', 'edm:surfaceRoughnessRa'], live),
      metrics: [metric('edm:wireWear', live), metric('edm:surfaceRoughnessRa', live)] },
  }
}

// ── Datacenter domain ───────────────────────────────────────────────
function datacenterUpdates(live) {
  if (!live || live['dc:rackLoad'] == null) return null
  const rackStatus = (id, loadKey) => ({
    status: worst(['dc:rackLoad', 'dc:inletTemp'], live),
    metrics: [metric('dc:rackLoad', live), metric('dc:inletTemp', live)]
  })
  return {
    'RACK-A1': rackStatus(),
    'RACK-A4': rackStatus(),
    'RACK-B2': rackStatus(),
    'CRAC-1': { status: worst(['dc:coolingCOP'], live),
      metrics: [metric('dc:coolingCOP', live), metric('dc:inletTemp', live)] },
    'UPS-1': { status: worst(['dc:upsCharge'], live),
      metrics: [metric('dc:upsCharge', live), metric('dc:pue', live)] },
    'NET-CORE': { status: 'ok',
      metrics: [metric('dc:pue', live), metric('dc:rackLoad', live)] },
  }
}

// ── Hospital domain ─────────────────────────────────────────────────
function hospitalUpdates(live) {
  if (!live || live['hsp:orPressure'] == null) return null
  return {
    'MRI-1': { status: worst(['hsp:criticalPowerLoad'], live),
      metrics: [metric('hsp:criticalPowerLoad', live), ['Field', 'T', '3.0']] },
    'ED-HVAC': { status: worst(['hsp:airChanges'], live),
      metrics: [metric('hsp:airChanges', live), metric('hsp:edWaitTime', live)] },
    'PHARM': { status: worst(['hsp:bloodBankTemp'], live),
      metrics: [metric('hsp:bloodBankTemp', live)] },
    'OR-GAS': { status: worst(['hsp:medGasO2Pressure'], live),
      metrics: [metric('hsp:medGasO2Pressure', live), metric('hsp:medGasReserve', live)] },
    'OR-LAF': { status: worst(['hsp:orPressure', 'hsp:airChanges'], live),
      metrics: [metric('hsp:orPressure', live), metric('hsp:airChanges', live)] },
    'ICU-NC': { status: worst(['hsp:infectionRisk', 'hsp:upsRuntime'], live),
      metrics: [metric('hsp:infectionRisk', live), metric('hsp:upsRuntime', live)] },
  }
}

// ── Manufacturing domain ────────────────────────────────────────────
function manufacturingUpdates(live) {
  if (!live || live['mfg:oee'] == null) return null
  return {
    'PRESS-1': { status: worst(['mfg:cycleTime'], live),
      metrics: [metric('mfg:cycleTime', live), metric('mfg:spindleVib', live)] },
    'ROBOT-3': { status: worst(['mfg:motorTemp'], live),
      metrics: [metric('mfg:motorTemp', live), metric('mfg:oee', live)] },
    'CONV-A': { status: worst(['mfg:throughput'], live),
      metrics: [metric('mfg:throughput', live)] },
    'CNC-7': { status: worst(['mfg:spindleVib', 'mfg:motorTemp'], live),
      metrics: [metric('mfg:spindleVib', live), metric('mfg:motorTemp', live)] },
    'WELD-2': { status: 'ok', metrics: [metric('mfg:oee', live)] },
    'COMP-1': { status: worst(['mfg:oee'], live),
      metrics: [metric('mfg:oee', live), metric('mfg:cycleTime', live)] },
  }
}

// ── Turbine domain (uses TurbineModel.jsx for GLB, but if Scene3D is used) ──
function turbineUpdates(live) {
  if (!live || live['aero:exhaustGasTemp'] == null) return null
  return {}  // turbine uses TurbineModel.jsx, not Scene3D
}

// ── Domain dispatcher ───────────────────────────────────────────────
function domainUpdates(domain, live) {
  switch (domain) {
    case 'edm-machine': return edmUpdates(live)
    case 'datacenter': return datacenterUpdates(live)
    case 'hospital': return hospitalUpdates(live)
    case 'manufacturing': return manufacturingUpdates(live)
    case 'turbine-engine': return turbineUpdates(live)
    default: return null
  }
}

export default function Scene3D({ domain, machine, live, height = 380 }) {
  const hostRef = useRef(null)
  const viewerRef = useRef(null)

  useEffect(() => {
    if (!hostRef.current) return
    const onAskAI = async (asset) => {
      const r = await api.assetStatus({ machine: machine || domain, domain, asset })
      return r?.status || 'No AI status returned.'
    }
    let viewer
    try { viewer = createViewer(hostRef.current, { domain, machine, onAskAI }) } catch (e) { console.error('3D scene failed', e) }
    viewerRef.current = viewer
    return () => { try { viewer && viewer.dispose() } catch {} viewerRef.current = null }
  }, [domain, machine])

  // Stream live telemetry onto ALL domain subsystems
  useEffect(() => {
    const v = viewerRef.current
    if (!v || !v.updateAssets) return
    const updates = domainUpdates(domain, live)
    if (updates) v.updateAssets(updates)
  }, [live, domain])

  return <div ref={hostRef} className="hero3d scene3d-host" style={{ height, position: 'relative', padding: 0, overflow: 'hidden' }} />
}
