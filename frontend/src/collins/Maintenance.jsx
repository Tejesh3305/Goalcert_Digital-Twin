// Maintenance.jsx — the "AI Maintenance Director": a full-screen, cinematic
// autonomous-maintenance takeover. The AI is no longer in a chat box — it takes
// control of the digital twin, flies the camera to the fault, explains the root
// cause, and walks the repair step-by-step while telemetry recovers in real time.
//
// Design note (per brief): no flying-bot gimmick. The AI presence is a fixed
// holographic "core", and everything is screen-space animation, component
// highlights, and method panels beside the machine — all GPU-cheap and smooth.
import React, { useEffect, useLayoutEffect, useCallback, useMemo, useRef, useState } from 'react'
import { createViewer } from './scene/engine.js'
import ModelViewer from './ModelViewer.jsx'
import EVWorld from './EVWorld.jsx'
import { SIG, sevClass, fmt } from './lib.jsx'
import './Maintenance.css'

const I = ({ n }) => <i className={`ti ${n}`} />

// ── per-domain subsystem metadata ─────────────────────────────────────
const DOMAIN_SUBS = {
  'ev-network': {
    meta: {
      'SITE':    { label: 'GoalCert Energy Site',     icon: 'ti-charging-pile' },
      'CHARGER': { label: 'Charging Network (OCPP)',  icon: 'ti-plug-connected' },
      'BATTERY': { label: 'Battery Health',           icon: 'ti-battery-3' },
      'GRID':    { label: 'Grid & EMS',               icon: 'ti-building-factory' },
      'ENERGY':  { label: 'Solar & BESS',             icon: 'ti-sun' },
      'THERMAL': { label: 'Thermal Management',       icon: 'ti-temperature' },
    },
    order: ['CHARGER', 'BATTERY', 'GRID', 'ENERGY', 'THERMAL'],
    signalMap(sig) {
      if (/uptime|utilization|sessions|faulted|ocpp|queue|chargingPower/i.test(sig)) return 'CHARGER'
      if (/stateOfCharge|stateOfHealth|cellImbalance/i.test(sig)) return 'BATTERY'
      if (/gridLoad|loadHeadroom|peakDemand|transformer/i.test(sig)) return 'GRID'
      if (/bess|solar|selfConsumption|v2g/i.test(sig)) return 'ENERGY'
      if (/cellTemp|coolant|insulation|runaway/i.test(sig)) return 'THERMAL'
      return 'SITE'
    },
    degraded: {
      'ev:cellTempMax': 52, 'ev:thermalRunawayRisk': 44, 'ev:cellImbalance': 58,
      'ev:coolantTemp': 44, 'ev:insulationResistance': 90, 'ev:stateOfHealth': 72,
      'ev:stateOfCharge': 46, 'ev:gridLoad': 96, 'ev:transformerTemp': 98,
      'ev:loadHeadroom': 4, 'ev:peakDemand': 540, 'ev:chargerUptime': 88,
      'ev:faultedChargers': 6, 'ev:ocppLatency': 1400, 'ev:utilization': 21,
      'ev:bessSoc': 8, 'ev:solarOutput': 60,
    },
  },
  'edm-machine': {
    meta: {
      'EDM-1':   { label: 'Wire EDM Machine',    icon: 'ti-grill' },
      'GEN-1':   { label: 'Discharge Generator', icon: 'ti-bolt' },
      'DIE-1':   { label: 'Dielectric & Flushing', icon: 'ti-droplet' },
      'WIRE-1':  { label: 'Wire Transport',      icon: 'ti-line-dashed' },
      'GUIDE-1': { label: 'Guides & Axes',       icon: 'ti-square' },
    },
    order: ['GEN-1', 'DIE-1', 'WIRE-1', 'GUIDE-1'],
    signalMap(sig) {
      if (/dielectric|Flow|Pressure/i.test(sig)) return 'DIE-1'
      if (/wireBreak|wireTension|wireFeed/i.test(sig)) return 'WIRE-1'
      if (/short|gapVoltage|peakCurrent|Energy|pulse|spark(?!Gap)/i.test(sig)) return 'GEN-1'
      if (/wireWear|surfaceRough|sparkGap/i.test(sig)) return 'GUIDE-1'
      return 'EDM-1'
    },
    degraded: {
      'edm:dielectricTemperature': 33, 'edm:dielectricConductivity': 24, 'edm:dielectricFlow': 2.4,
      'edm:dielectricPressure': 2.6, 'edm:shortCircuitRate': 22, 'edm:cuttingSpeed': 96,
      'edm:gapVoltage': 23, 'edm:peakCurrent': 27, 'edm:wireBreakRisk': 76, 'edm:wireTension': 5.4,
      'edm:wireFeedRate': 5, 'edm:wireWear': 88, 'edm:surfaceRoughnessRa': 3.4,
    },
  },
  'turbine-engine': {
    meta: {
      'TURBINE':    { label: 'Gas Turbine Engine',  icon: 'ti-engine' },
      'COMPRESSOR': { label: 'Compressor Module',   icon: 'ti-rotate-clockwise' },
      'COMBUSTOR':  { label: 'Combustor Module',    icon: 'ti-flame' },
      'BEARING':    { label: 'Bearing & Lube',      icon: 'ti-settings' },
    },
    order: ['COMPRESSOR', 'COMBUSTOR', 'BEARING'],
    signalMap(sig) {
      if (/compressor|N1/i.test(sig)) return 'COMPRESSOR'
      if (/fuel|EGT|combustor/i.test(sig)) return 'COMBUSTOR'
      if (/oil|vibration|bearing/i.test(sig)) return 'BEARING'
      return 'TURBINE'
    },
    degraded: {
      'aero:exhaustGasTemp': 760, 'aero:shaftSpeedN1': 5460, 'aero:vibrationG': 1.8,
      'aero:oilTemperature': 82, 'aero:oilPressure': 42, 'aero:fuelFlow': 0.48,
    },
  },
  'datacenter': {
    meta: {
      'RACK-B2': { label: 'Server Rack B2',  icon: 'ti-server' },
      'CRAC-1':  { label: 'CRAC Cooling',    icon: 'ti-snowflake' },
      'UPS-1':   { label: 'UPS Power',       icon: 'ti-battery-charging' },
    },
    order: ['RACK-B2', 'CRAC-1', 'UPS-1'],
    signalMap(sig) {
      if (/rack|inlet|Load/i.test(sig)) return 'RACK-B2'
      if (/cooling|COP/i.test(sig)) return 'CRAC-1'
      if (/ups|Charge|pue/i.test(sig)) return 'UPS-1'
      return 'RACK-B2'
    },
    degraded: {
      'dc:rackLoad': 96, 'dc:inletTemp': 33, 'dc:coolingCOP': 2.4,
      'dc:upsCharge': 38, 'dc:pue': 1.92,
    },
  },
  'hospital': {
    meta: {
      'OR-LAF':  { label: 'OR Laminar Flow', icon: 'ti-wind' },
      'OR-GAS':  { label: 'Medical Gas',     icon: 'ti-vaccine' },
      'PHARM':   { label: 'Pharmacy Fridge',  icon: 'ti-temperature-snow' },
      'ED-HVAC': { label: 'ED HVAC',         icon: 'ti-air-conditioning' },
    },
    order: ['OR-LAF', 'OR-GAS', 'PHARM', 'ED-HVAC'],
    signalMap(sig) {
      if (/orPressure|isoPressure|laminar|autoclaveF0/i.test(sig)) return 'OR-LAF'
      if (/medGas|o2|n2o/i.test(sig)) return 'OR-GAS'
      if (/bloodBank|fridge|coldchain/i.test(sig)) return 'PHARM'
      if (/airChanges|hvac|ups|generator|criticalPower|edWait|bedOcc|infection/i.test(sig)) return 'ED-HVAC'
      return 'OR-LAF'
    },
    degraded: {
      'hsp:orPressure': 4, 'hsp:airChanges': 8, 'hsp:bloodBankTemp': 9.2,
      'hsp:medGasO2Pressure': 305, 'hsp:infectionRisk': 7.5, 'hsp:upsRuntime': 9,
    },
  },
  'manufacturing': {
    meta: {
      'CNC-7':   { label: 'CNC Machine 7',  icon: 'ti-settings' },
      'ROBOT-3': { label: 'Robot Arm 3',     icon: 'ti-robot' },
      'CONV-A':  { label: 'Conveyor A',      icon: 'ti-arrows-right' },
      'COMP-1':  { label: 'Compressor',      icon: 'ti-engine' },
    },
    order: ['CNC-7', 'ROBOT-3', 'CONV-A', 'COMP-1'],
    signalMap(sig) {
      if (/spindle|Vib/i.test(sig)) return 'CNC-7'
      if (/motor|robot/i.test(sig)) return 'ROBOT-3'
      if (/throughput|conveyor/i.test(sig)) return 'CONV-A'
      if (/oee|compressor|cycleTime/i.test(sig)) return 'COMP-1'
      return 'CNC-7'
    },
    degraded: {
      'mfg:spindleVib': 7.5, 'mfg:motorTemp': 88, 'mfg:oee': 55,
      'mfg:throughput': 62, 'mfg:cycleTime': 58,
    },
  },
}

// backward compat: legacy code references these flat objects
const SUB_META = DOMAIN_SUBS['edm-machine'].meta
const SUB_ORDER = DOMAIN_SUBS['edm-machine'].order
function subForSignal(sig = '', domain = 'edm-machine') {
  return (DOMAIN_SUBS[domain] || DOMAIN_SUBS['edm-machine']).signalMap(sig)
}
const DEGRADED = DOMAIN_SUBS['edm-machine'].degraded

// Per-subsystem repair plans (root cause + telemetry targets + ordered steps).
const PLANS = {
  'DIE-1': {
    title: 'Dielectric & Flushing — Overheat & Flushing Loss',
    rootCause: [
      { icon: 'ti-temperature', text: '<b>Dielectric temperature high</b> — chiller under-performing' },
      { icon: 'ti-droplet', text: '<b>Flushing efficiency falls</b> — debris not cleared from the gap' },
      { icon: 'ti-plug-connected-x', text: '<b>Discharge turns unstable</b> — short-circuit rate climbs' },
      { icon: 'ti-alert-triangle', text: '<b>Wire-break risk & poor surface finish</b>' },
      { icon: 'ti-player-stop', text: '<b>Cut aborts / wire snap</b> if left untreated' },
    ],
    signals: [['edm:dielectricTemperature', 24], ['edm:dielectricConductivity', 10],
      ['edm:dielectricFlow', 6], ['edm:shortCircuitRate', 5], ['edm:cuttingSpeed', 150]],
    steps: [
      { t: 'Diagnose dielectric loop', d: 'Read chiller, conductivity and flow sensors to localise the loss.', f: 'DIE-1', tool: 'Sensor suite', time: 18, diff: 'Low' },
      { t: 'Lockout / isolate machine power', d: 'Apply LOTO before opening the dielectric housing.', f: 'EDM-1', tool: 'LOTO kit', time: 30, diff: 'Low', safety: true },
      { t: 'Inspect chiller & filter housing', d: 'Check pump pressure and the filter differential.', f: 'DIE-1', tool: 'Inspection', time: 40, diff: 'Low' },
      { t: 'Replace dielectric filter cartridge', d: 'Swap the clogged cartridge; reseat the O-ring seal.', f: 'DIE-1', tool: '8 mm hex · filter wrench', time: 90, diff: 'Medium' },
      { t: 'Recharge fluid & de-ioniser resin', d: 'Top up dielectric and restore resin to drop conductivity.', f: 'DIE-1', tool: 'Resin · fluid', time: 70, diff: 'Medium' },
      { t: 'Re-prime flushing & set pressure', d: 'Purge air and set upper/lower flush to spec.', f: 'EDM-1', tool: 'Pressure gauge', time: 45, diff: 'Low' },
      { t: 'Test cut & verify stability', d: 'Run a coupon; confirm temp, flow and gap are nominal.', f: 'EDM-1', tool: 'Test coupon', time: 60, diff: 'Low' },
    ],
  },
  'GEN-1': {
    title: 'Discharge Generator — Short-Circuiting',
    rootCause: [
      { icon: 'ti-plug-connected-x', text: '<b>Short-circuit rate rising</b> — contaminated power feed' },
      { icon: 'ti-bolt-off', text: '<b>Gap voltage collapses</b> — discharge cannot ionise cleanly' },
      { icon: 'ti-slice', text: '<b>Material-removal rate drops</b> — cutting speed falls' },
      { icon: 'ti-alert-triangle', text: '<b>Heat & wire-break risk climb</b>' },
      { icon: 'ti-player-stop', text: '<b>Generator fault / wire snap</b> if untreated' },
    ],
    signals: [['edm:shortCircuitRate', 4], ['edm:gapVoltage', 52], ['edm:peakCurrent', 18],
      ['edm:wireBreakRisk', 15], ['edm:cuttingSpeed', 150]],
    steps: [
      { t: 'Diagnose discharge circuit', d: 'Inspect short-circuit telemetry and gap voltage trace.', f: 'GEN-1', tool: 'Sensor suite', time: 18, diff: 'Low' },
      { t: 'Lockout / isolate machine power', d: 'Apply LOTO before touching the power feed.', f: 'EDM-1', tool: 'LOTO kit', time: 30, diff: 'Low', safety: true },
      { t: 'Inspect power-feed contacts', d: 'Check the feed contacts and bus connections for burn.', f: 'GEN-1', tool: 'Inspection', time: 40, diff: 'Medium' },
      { t: 'Clean / replace feed contacts', d: 'Dress or swap pitted contacts; clean the gap path.', f: 'GEN-1', tool: 'Contact kit', time: 80, diff: 'Medium' },
      { t: 'Re-tune pulse parameters', d: 'Reset Ton/Toff and peak current to a stable regime.', f: 'GEN-1', tool: 'CNC pendant', time: 60, diff: 'Medium' },
      { t: 'Verify gap stability', d: 'Confirm voltage and short-circuit rate are nominal.', f: 'EDM-1', tool: 'Scope', time: 45, diff: 'Low' },
      { t: 'Test cut & sign off', d: 'Run a coupon and confirm cutting speed recovered.', f: 'EDM-1', tool: 'Test coupon', time: 60, diff: 'Low' },
    ],
  },
  'WIRE-1': {
    title: 'Wire Transport — Tension Loss & Break Risk',
    rootCause: [
      { icon: 'ti-line-dashed', text: '<b>Wire tension below spec</b> — servo / brake slipping' },
      { icon: 'ti-wave-sine', text: '<b>Wire vibrates in the gap</b> — geometry error grows' },
      { icon: 'ti-plug-connected-x', text: '<b>Short-circuits increase</b> — unstable contact' },
      { icon: 'ti-alert-triangle', text: '<b>Wire-break risk climbs sharply</b>' },
      { icon: 'ti-player-stop', text: '<b>Wire snap & re-thread</b> if untreated' },
    ],
    signals: [['edm:wireTension', 15], ['edm:wireBreakRisk', 12], ['edm:wireFeedRate', 9],
      ['edm:shortCircuitRate', 5], ['edm:cuttingSpeed', 150]],
    steps: [
      { t: 'Diagnose wire path', d: 'Read tension, feed-rate and break-risk telemetry.', f: 'WIRE-1', tool: 'Sensor suite', time: 18, diff: 'Low' },
      { t: 'Lockout / isolate machine power', d: 'Apply LOTO before opening the wire path.', f: 'EDM-1', tool: 'LOTO kit', time: 30, diff: 'Low', safety: true },
      { t: 'Inspect spool & tension servo', d: 'Check brake, spool drag and the tension roller.', f: 'WIRE-1', tool: 'Inspection', time: 40, diff: 'Medium' },
      { t: 'Re-thread / replace wire', d: 'Load fresh brass wire and re-thread the guides.', f: 'WIRE-1', tool: 'Threader', time: 85, diff: 'Medium' },
      { t: 'Calibrate tension & feed', d: 'Set tension to spec and verify feed-rate tracking.', f: 'WIRE-1', tool: 'Tension meter', time: 60, diff: 'Medium' },
      { t: 'Verify gap stability', d: 'Confirm break-risk and short-circuit rate dropped.', f: 'EDM-1', tool: 'Scope', time: 45, diff: 'Low' },
      { t: 'Test cut & sign off', d: 'Run a coupon; confirm a clean, stable cut.', f: 'EDM-1', tool: 'Test coupon', time: 60, diff: 'Low' },
    ],
  },
  'GUIDE-1': {
    title: 'Guides & Axes — Guide Wear',
    rootCause: [
      { icon: 'ti-circle-dashed', text: '<b>Diamond guide wear high</b> — wire positioning drifts' },
      { icon: 'ti-wave-saw-tool', text: '<b>Surface roughness rises</b> — finish out of tolerance' },
      { icon: 'ti-ruler-measure', text: '<b>Geometry error grows</b> on the cut profile' },
      { icon: 'ti-alert-triangle', text: '<b>Scrap risk & rework</b> increase' },
      { icon: 'ti-player-stop', text: '<b>Part rejection</b> if untreated' },
    ],
    signals: [['edm:wireWear', 5], ['edm:surfaceRoughnessRa', 1.4], ['edm:cuttingSpeed', 150]],
    steps: [
      { t: 'Diagnose guides & axes', d: 'Read wire-wear and surface-roughness telemetry.', f: 'GUIDE-1', tool: 'Sensor suite', time: 18, diff: 'Low' },
      { t: 'Lockout / isolate machine power', d: 'Apply LOTO before accessing the guide head.', f: 'EDM-1', tool: 'LOTO kit', time: 30, diff: 'Low', safety: true },
      { t: 'Inspect diamond guides', d: 'Check upper/lower guides for grooving and wear.', f: 'GUIDE-1', tool: 'Loupe', time: 40, diff: 'Medium' },
      { t: 'Replace worn guide inserts', d: 'Swap the worn diamond inserts; clean the seats.', f: 'GUIDE-1', tool: 'Guide kit', time: 80, diff: 'Medium' },
      { t: 'Re-align U/V axes', d: 'Square the wire and re-reference the axes.', f: 'GUIDE-1', tool: 'Alignment jig', time: 65, diff: 'High' },
      { t: 'Verify squareness', d: 'Confirm geometry and roughness are in tolerance.', f: 'EDM-1', tool: 'Gauge', time: 45, diff: 'Low' },
      { t: 'Test cut & sign off', d: 'Run a coupon; confirm finish recovered.', f: 'EDM-1', tool: 'Test coupon', time: 60, diff: 'Low' },
    ],
  },
}

// ── Turbine-specific repair plans ──
const TURBINE_PLANS = {
  'COMBUSTOR': {
    title: 'Hot-Section Degradation — EGT Exceedance',
    rootCause: [
      { icon: 'ti-flame', text: '<b>EGT deviation above baseline</b> — hot-section efficiency loss' },
      { icon: 'ti-blade', text: '<b>Blade erosion or nozzle coking</b> — reduced turbine area' },
      { icon: 'ti-trending-up', text: '<b>Fuel flow compensates</b> — accelerates degradation' },
      { icon: 'ti-alert-octagon', text: '<b>EGT redline risk</b> if unchecked' },
      { icon: 'ti-player-stop', text: '<b>In-flight shutdown / AOG</b>' },
    ],
    signals: [['aero:exhaustGasTemp', 640], ['aero:fuelFlow', 0.35], ['aero:shaftSpeedN1', 5200]],
    steps: [
      { t: 'Review EGT trend data', d: 'Analyse the EGT deviation pattern over last 50 cycles.', f: 'COMBUSTOR', tool: 'ECAM / recorder', time: 15, diff: 'Low' },
      { t: 'Apply LOTO to test rig', d: 'Isolate fuel, ignition, and starter before access.', f: 'TURBINE', tool: 'LOTO kit', time: 30, diff: 'Low', safety: true },
      { t: 'Borescope hot section', d: 'Inspect HP turbine blades and nozzle guide vanes per CMM 72-00-00.', f: 'COMBUSTOR', tool: 'Borescope', time: 45, diff: 'Medium' },
      { t: 'Chemical clean combustor nozzles', d: 'Remove carbon deposits from fuel nozzles; verify spray pattern.', f: 'COMBUSTOR', tool: 'Nozzle cleaning kit', time: 60, diff: 'Medium' },
      { t: 'Replace eroded blades (if needed)', d: 'Swap HP blade set if tip loss exceeds 0.5mm limit.', f: 'COMBUSTOR', tool: 'Blade set · torque wrench', time: 120, diff: 'High' },
      { t: 'Ground run & EGT verification', d: 'Run engine at 85% N1 for 10 min; confirm EGT within ±15C of baseline.', f: 'TURBINE', tool: 'Test cell', time: 90, diff: 'Medium' },
      { t: 'Sign off & return to service', d: 'Level II inspector sign-off per EASA Part 145.', f: 'TURBINE', tool: '—', time: 20, diff: 'Low' },
    ],
  },
  'BEARING': {
    title: 'Bearing & Lubrication — Vibration / Oil Anomaly',
    rootCause: [
      { icon: 'ti-activity', text: '<b>Vibration elevated</b> — bearing surface degradation' },
      { icon: 'ti-droplet', text: '<b>Oil temperature / pressure drift</b> — lubrication inadequate' },
      { icon: 'ti-rotate-clockwise', text: '<b>Shaft alignment shifts</b> — secondary imbalance' },
      { icon: 'ti-alert-triangle', text: '<b>Bearing seizure risk</b>' },
      { icon: 'ti-player-stop', text: '<b>Catastrophic failure</b> if untreated' },
    ],
    signals: [['aero:vibrationG', 0.8], ['aero:oilTemperature', 65], ['aero:oilPressure', 55]],
    steps: [
      { t: 'Analyse vibration spectrum', d: 'FFT the vibration data to identify bearing defect frequencies.', f: 'BEARING', tool: 'Spectrum analyser', time: 20, diff: 'Medium' },
      { t: 'Lockout & drain oil', d: 'LOTO the rig and drain the oil system.', f: 'TURBINE', tool: 'LOTO kit · drain pan', time: 40, diff: 'Low', safety: true },
      { t: 'Inspect bearing race', d: 'Remove and inspect the main shaft bearings for spalling.', f: 'BEARING', tool: 'Puller · loupe', time: 60, diff: 'High' },
      { t: 'Replace bearing & seals', d: 'Fit new bearing set and oil seals per CMM spec.', f: 'BEARING', tool: 'Bearing kit · press', time: 90, diff: 'High' },
      { t: 'Refill oil & prime', d: 'Refill oil system, prime the pump, verify pressure.', f: 'BEARING', tool: 'Oil · gauge', time: 40, diff: 'Low' },
      { t: 'Vibration acceptance run', d: 'Confirm vibration below 1.0g at stabilised N1.', f: 'TURBINE', tool: 'Test cell', time: 60, diff: 'Medium' },
      { t: 'Sign off', d: 'Inspector sign-off; update maintenance log.', f: 'TURBINE', tool: '—', time: 15, diff: 'Low' },
    ],
  },
  'COMPRESSOR': {
    title: 'Compressor Fouling — Performance Loss',
    rootCause: [
      { icon: 'ti-rotate-clockwise', text: '<b>N1 droop at constant throttle</b> — airflow restricted' },
      { icon: 'ti-flame', text: '<b>EGT rises to compensate</b> — fuel controller adds fuel' },
      { icon: 'ti-trending-down', text: '<b>EPR margin shrinks</b> — reduced thrust available' },
      { icon: 'ti-alert-triangle', text: '<b>Compressor surge risk</b> at high power' },
      { icon: 'ti-player-stop', text: '<b>Engine rollback / flameout</b>' },
    ],
    signals: [['aero:shaftSpeedN1', 5200], ['aero:exhaustGasTemp', 640], ['aero:enginePressureRatio', 1.35]],
    steps: [
      { t: 'Diagnose compressor health', d: 'Compare N1/EPR trend against baseline.', f: 'COMPRESSOR', tool: 'Trend data', time: 15, diff: 'Low' },
      { t: 'Lockout test rig', d: 'Isolate fuel, starter, and electrical before wash.', f: 'TURBINE', tool: 'LOTO kit', time: 30, diff: 'Low', safety: true },
      { t: 'Compressor water wash', d: 'Run desalination wash cycle per AMM procedure.', f: 'COMPRESSOR', tool: 'Wash rig · fluid', time: 45, diff: 'Medium' },
      { t: 'Borescope compressor stages', d: 'Check blade leading edges for FOD and erosion.', f: 'COMPRESSOR', tool: 'Borescope', time: 40, diff: 'Medium' },
      { t: 'Blend minor blade damage', d: 'Dress small nicks per blend limits in CMM.', f: 'COMPRESSOR', tool: 'Blend tools', time: 50, diff: 'High' },
      { t: 'Post-wash performance run', d: 'Verify N1/EPR recovery and EGT margin restored.', f: 'TURBINE', tool: 'Test cell', time: 60, diff: 'Medium' },
      { t: 'Sign off', d: 'Inspector approval; log wash cycle count.', f: 'TURBINE', tool: '—', time: 15, diff: 'Low' },
    ],
  },
}

// ── Datacenter repair plans ──
const DATACENTER_PLANS = {
  'CRAC-1': {
    title: 'CRAC Cooling Failure — Thermal Runaway Risk',
    rootCause: [
      { icon: 'ti-snowflake', text: '<b>CRAC supply temp rising</b> — compressor or fan failure' },
      { icon: 'ti-temperature', text: '<b>Inlet temps climb</b> across server racks' },
      { icon: 'ti-server', text: '<b>Thermal throttling begins</b> — compute capacity drops' },
      { icon: 'ti-alert-octagon', text: '<b>Thermal shutdown risk</b> on dense racks' },
      { icon: 'ti-player-stop', text: '<b>Service outage</b>' },
    ],
    signals: [['dc:inletTemp', 22], ['dc:coolingCOP', 3.8], ['dc:pue', 1.4]],
    steps: [
      { t: 'Confirm CRAC fault', d: 'Check CRAC display for fault codes; read supply/return delta.', f: 'CRAC-1', tool: 'BMS', time: 10, diff: 'Low' },
      { t: 'Isolate CRAC electrically', d: 'Lock out CRAC breaker; verify zero energy.', f: 'CRAC-1', tool: 'LOTO kit', time: 20, diff: 'Low', safety: true },
      { t: 'Inspect filters & coils', d: 'Check air filters for clogging; inspect evaporator coil.', f: 'CRAC-1', tool: 'Inspection', time: 30, diff: 'Low' },
      { t: 'Replace filters / clean coils', d: 'Swap clogged filters; chemical clean the coil if fouled.', f: 'CRAC-1', tool: 'Filter set · coil cleaner', time: 45, diff: 'Medium' },
      { t: 'Check refrigerant charge', d: 'Verify suction/discharge pressure and superheat.', f: 'CRAC-1', tool: 'Manifold gauge', time: 30, diff: 'Medium' },
      { t: 'Restart & verify cooling', d: 'Power on CRAC; confirm supply temp and COP recover.', f: 'CRAC-1', tool: 'BMS', time: 40, diff: 'Low' },
      { t: 'Monitor & sign off', d: 'Watch rack inlet temps return to target over 15 min.', f: 'RACK-B2', tool: '—', time: 30, diff: 'Low' },
    ],
  },
  'UPS-1': {
    title: 'UPS Battery Depletion — Power Continuity Risk',
    rootCause: [
      { icon: 'ti-battery-charging', text: '<b>UPS charge below threshold</b> — battery degradation' },
      { icon: 'ti-bolt', text: '<b>Runtime insufficient</b> for generator switchover' },
      { icon: 'ti-plug-connected-x', text: '<b>Load transfer risk</b> during power event' },
      { icon: 'ti-alert-triangle', text: '<b>Data loss risk</b> on hard shutdown' },
      { icon: 'ti-player-stop', text: '<b>Full hall outage</b>' },
    ],
    signals: [['dc:upsCharge', 96], ['dc:pue', 1.4]],
    steps: [
      { t: 'Read UPS battery status', d: 'Check individual cell voltages and impedance.', f: 'UPS-1', tool: 'Battery analyser', time: 20, diff: 'Low' },
      { t: 'Transfer load to bypass', d: 'Switch UPS to static bypass; verify load on mains.', f: 'UPS-1', tool: 'UPS panel', time: 15, diff: 'Medium', safety: true },
      { t: 'Replace weak battery strings', d: 'Swap degraded strings; verify polarity and torque.', f: 'UPS-1', tool: 'Battery set · torque wrench', time: 90, diff: 'High' },
      { t: 'Equalise charge', d: 'Run equalisation charge cycle per manufacturer spec.', f: 'UPS-1', tool: 'UPS controller', time: 60, diff: 'Low' },
      { t: 'Load bank test', d: 'Run at rated load for 10 min; verify runtime target.', f: 'UPS-1', tool: 'Load bank', time: 45, diff: 'Medium' },
      { t: 'Return to online mode', d: 'Transfer load back to UPS from bypass.', f: 'UPS-1', tool: 'UPS panel', time: 10, diff: 'Low' },
      { t: 'Sign off', d: 'Update battery replacement log and PM schedule.', f: 'UPS-1', tool: '—', time: 10, diff: 'Low' },
    ],
  },
}

// ── Hospital repair plans ──
const HOSPITAL_PLANS = {
  'OR-LAF': {
    title: 'OR Laminar Flow Loss — Surgical Infection Risk',
    rootCause: [
      { icon: 'ti-wind', text: '<b>Laminar flow velocity dropped</b> — fan or filter failure' },
      { icon: 'ti-temperature', text: '<b>OR pressure differential lost</b> — contamination path opens' },
      { icon: 'ti-virus', text: '<b>Particulate count rises</b> — sterile field compromised' },
      { icon: 'ti-alert-octagon', text: '<b>Surgical site infection risk</b>' },
      { icon: 'ti-player-stop', text: '<b>OR must close</b> until restored' },
    ],
    signals: [['hsp:orPressure', 15], ['hsp:airChanges', 22]],
    steps: [
      { t: 'Confirm pressure/velocity loss', d: 'Read OR differential pressure and air velocity at the diffuser.', f: 'OR-LAF', tool: 'Manometer · anemometer', time: 10, diff: 'Low' },
      { t: 'Close OR to procedures', d: 'Notify charge nurse; divert scheduled cases.', f: 'OR-LAF', tool: 'Protocol', time: 5, diff: 'Low', safety: true },
      { t: 'Inspect HEPA filters', d: 'Check filter DP; visually inspect for damage or bypass.', f: 'OR-LAF', tool: 'Inspection', time: 25, diff: 'Low' },
      { t: 'Replace HEPA bank', d: 'Swap the terminal HEPA filter bank; gel-seal test.', f: 'OR-LAF', tool: 'HEPA filter · gel frame', time: 60, diff: 'High' },
      { t: 'Check fan belt / motor', d: 'Inspect AHU fan belt tension and motor current.', f: 'ED-HVAC', tool: 'Belt gauge · clamp meter', time: 30, diff: 'Medium' },
      { t: 'Particle count validation', d: 'Run 0.5µm particle count at rest and in-operation.', f: 'OR-LAF', tool: 'Particle counter', time: 40, diff: 'Medium' },
      { t: 'Release OR for use', d: 'Infection control sign-off; update compliance log.', f: 'OR-LAF', tool: '—', time: 10, diff: 'Low' },
    ],
  },
  'PHARM': {
    title: 'Pharmacy Cold-Chain Excursion — Medication Risk',
    rootCause: [
      { icon: 'ti-temperature-snow', text: '<b>Fridge temperature rising</b> — compressor or door seal issue' },
      { icon: 'ti-pill', text: '<b>Medication efficacy at risk</b> — biologics, vaccines, insulin' },
      { icon: 'ti-clock', text: '<b>Excursion clock running</b> — limited recovery window' },
      { icon: 'ti-alert-triangle', text: '<b>Mandatory reporting</b> if threshold exceeded' },
      { icon: 'ti-trash', text: '<b>Stock destruction</b> — financial and patient impact' },
    ],
    signals: [['hsp:bloodBankTemp', 4.0]],
    steps: [
      { t: 'Confirm excursion', d: 'Read fridge probe vs backup thermometer; check duration.', f: 'PHARM', tool: 'Thermometer', time: 5, diff: 'Low' },
      { t: 'Relocate critical stock', d: 'Move biologics/vaccines to backup cold storage immediately.', f: 'PHARM', tool: 'Cool box', time: 15, diff: 'Low', safety: true },
      { t: 'Inspect compressor & seals', d: 'Check compressor run, condenser fan, and door gasket.', f: 'PHARM', tool: 'Inspection', time: 20, diff: 'Low' },
      { t: 'Repair / replace component', d: 'Fix door seal, clean condenser, or swap compressor.', f: 'PHARM', tool: 'Seal kit / compressor', time: 60, diff: 'Medium' },
      { t: 'Cool-down verification', d: 'Confirm temp recovers to 2-8°C within 30 min.', f: 'PHARM', tool: 'Logger', time: 45, diff: 'Low' },
      { t: 'Assess affected stock', d: 'Pharmacist reviews excursion data vs stability profiles.', f: 'PHARM', tool: 'Stability data', time: 30, diff: 'Medium' },
      { t: 'Report & sign off', d: 'File cold-chain incident report; update CMMS.', f: 'PHARM', tool: '—', time: 15, diff: 'Low' },
    ],
  },
}

// ── Manufacturing repair plans ──
const MANUFACTURING_PLANS = {
  'CNC-7': {
    title: 'CNC Spindle Bearing Wear — Vibration & Quality',
    rootCause: [
      { icon: 'ti-activity', text: '<b>Spindle vibration elevated</b> — bearing degradation' },
      { icon: 'ti-temperature', text: '<b>Motor temp climbing</b> — increased friction' },
      { icon: 'ti-ruler-measure', text: '<b>Surface finish deteriorating</b> — tolerance drift' },
      { icon: 'ti-alert-triangle', text: '<b>Scrap rate increasing</b>' },
      { icon: 'ti-player-stop', text: '<b>Spindle seizure</b> risk' },
    ],
    signals: [['mfg:spindleVib', 2.0], ['mfg:motorTemp', 55], ['mfg:oee', 85]],
    steps: [
      { t: 'Capture vibration spectrum', d: 'Run FFT at multiple speeds to identify bearing defect frequency.', f: 'CNC-7', tool: 'Accelerometer', time: 20, diff: 'Medium' },
      { t: 'Lockout CNC', d: 'E-stop and LOTO the machine; verify spindle stopped.', f: 'CNC-7', tool: 'LOTO kit', time: 15, diff: 'Low', safety: true },
      { t: 'Remove spindle cartridge', d: 'Disconnect drawbar, coolant, and sensor leads.', f: 'CNC-7', tool: 'Spindle puller', time: 60, diff: 'High' },
      { t: 'Replace bearing set', d: 'Press-fit new angular contact bearings; set preload.', f: 'CNC-7', tool: 'Bearing press · preload gauge', time: 90, diff: 'High' },
      { t: 'Reinstall & align', d: 'Mount cartridge; verify runout < 0.003mm.', f: 'CNC-7', tool: 'Dial indicator', time: 45, diff: 'High' },
      { t: 'Test cut', d: 'Run a test coupon; measure surface finish and dimensions.', f: 'CNC-7', tool: 'Profilometer', time: 30, diff: 'Medium' },
      { t: 'Return to production', d: 'Update maintenance log; reset vibration baseline.', f: 'CNC-7', tool: '—', time: 10, diff: 'Low' },
    ],
  },
  'ROBOT-3': {
    title: 'Robot Joint Overload — Motor Thermal',
    rootCause: [
      { icon: 'ti-robot', text: '<b>Joint motor temperature high</b> — overloaded or stiff' },
      { icon: 'ti-gauge', text: '<b>OEE declining</b> — cycle time extending' },
      { icon: 'ti-settings', text: '<b>Gear reducer wear</b> — backlash increasing' },
      { icon: 'ti-alert-triangle', text: '<b>Motor protection trip</b> imminent' },
      { icon: 'ti-player-stop', text: '<b>Line stoppage</b>' },
    ],
    signals: [['mfg:motorTemp', 55], ['mfg:oee', 85], ['mfg:cycleTime', 42]],
    steps: [
      { t: 'Read motor thermals', d: 'Check J2-J4 motor temperatures and current draw.', f: 'ROBOT-3', tool: 'Teach pendant', time: 15, diff: 'Low' },
      { t: 'Power down robot', d: 'Safe-stop; engage mechanical brake; LOTO cabinet.', f: 'ROBOT-3', tool: 'LOTO kit', time: 20, diff: 'Low', safety: true },
      { t: 'Inspect gear reducer', d: 'Check backlash on overloaded axis; oil sample.', f: 'ROBOT-3', tool: 'Dial gauge · sample kit', time: 40, diff: 'Medium' },
      { t: 'Grease / replace reducer', d: 'Re-grease or swap gear reducer on affected joint.', f: 'ROBOT-3', tool: 'Grease gun / reducer kit', time: 80, diff: 'High' },
      { t: 'Recalibrate axis', d: 'Re-master the axis encoder; update tool center point.', f: 'ROBOT-3', tool: 'Mastering jig', time: 35, diff: 'Medium' },
      { t: 'Cycle test', d: 'Run 50 production cycles; verify temp and cycle time.', f: 'ROBOT-3', tool: 'Production mode', time: 45, diff: 'Low' },
      { t: 'Return to production', d: 'Update PM schedule; log reducer replacement.', f: 'ROBOT-3', tool: '—', time: 10, diff: 'Low' },
    ],
  },
}

// ── EV / GoalCert energy-site repair plans ──
// Healthy baseline for the 3-D energy-site world. The Repair flow interpolates
// each plan's `degrade` overrides on top of this by a "crisis" fraction, so the
// world visibly slides into a fault during scan/diagnose and recovers step-by-step.
const EV_NOMINAL = {
  'ev:transformerTemp': 62, 'ev:gridLoad': 64, 'ev:loadHeadroom': 34, 'ev:peakDemand': 410,
  'ev:bessSoc': 72, 'ev:bessPower': -60, 'ev:solarOutput': 210, 'ev:sessionsActive': 16,
  'ev:faultedChargers': 0, 'ev:chargingPower': 148, 'ev:thermalRunawayRisk': 2, 'ev:cellTempMax': 33,
}

const EV_PLANS = {
  'THERMAL': {
    title: 'Battery Thermal Event — Runaway Precursor',
    // 3-D cascade: numbered bubbles pop over each asset the fault reaches, the
    // camera sweeps to follow, then the bubbles recede as the AI works the fix.
    world: {
      focus: 'BESS-A',
      cascade: [
        { anchor: 'bess', label: 'Battery over-temp' },
        { anchor: 'transformer', label: 'BESS derated · grid covers' },
        { anchor: 'dcfc', label: 'Fast-charging throttled' },
      ],
      degrade: { 'ev:cellTempMax': 54, 'ev:thermalRunawayRisk': 46, 'ev:bessSoc': 12, 'ev:bessPower': 45,
        'ev:transformerTemp': 86, 'ev:gridLoad': 90, 'ev:chargingPower': 70 },
    },
    rootCause: [
      { icon: 'ti-temperature', text: '<b>Cell temperature climbing</b> — cooling losing the pack' },
      { icon: 'ti-arrows-diff', text: '<b>Cell imbalance rising</b> — dendrite / hot-spot precursor' },
      { icon: 'ti-flame', text: '<b>Thermal-runaway risk high</b> — off-gassing possible' },
      { icon: 'ti-shield-bolt', text: '<b>HV insulation degrading</b> — isolation-fault risk' },
      { icon: 'ti-player-stop', text: '<b>Pack venting / fire</b> if untreated' },
    ],
    signals: [['ev:cellTempMax', 33], ['ev:thermalRunawayRisk', 2], ['ev:coolantTemp', 29], ['ev:cellImbalance', 14]],
    steps: [
      { t: 'Diagnose battery thermal state', d: 'Read cell temp, imbalance, coolant and runaway-risk telemetry.', f: 'THERMAL', tool: 'BMS suite', time: 15, diff: 'Low' },
      { t: 'Derate charging & isolate pack', d: 'Cut charge current to the affected string; arm suppression.', f: 'BATTERY', tool: 'EMS · LOTO', time: 20, diff: 'Low', safety: true },
      { t: 'Boost coolant flow', d: 'Step up the liquid-cooling loop and verify inlet temperature drops.', f: 'THERMAL', tool: 'Coolant pump', time: 25, diff: 'Medium' },
      { t: 'Inspect module & balance cells', d: 'Locate the outlier cells; run an active-balancing cycle.', f: 'BATTERY', tool: 'Cell balancer', time: 60, diff: 'Medium' },
      { t: 'Replace degraded module', d: 'Swap the module if imbalance / SoH is past limit; torque HV links.', f: 'BATTERY', tool: 'Module kit · torque wrench', time: 90, diff: 'High' },
      { t: 'Verify thermal stability', d: 'Confirm cell temp, imbalance and runaway risk are nominal.', f: 'THERMAL', tool: 'BMS suite', time: 45, diff: 'Low' },
      { t: 'Return pack to service', d: 'Clear isolation, log the event and update the PM schedule.', f: 'BATTERY', tool: '—', time: 20, diff: 'Low' },
    ],
  },
  'GRID': {
    title: 'Grid Overload — Transformer Over-Temperature',
    world: {
      focus: 'TX-1',
      cascade: [
        { anchor: 'transformer', label: 'Transformer over-temp' },
        { anchor: 'ems', label: 'Grid headroom exhausted' },
        { anchor: 'dcfc', label: 'Sessions curtailed' },
      ],
      degrade: { 'ev:transformerTemp': 98, 'ev:gridLoad': 97, 'ev:loadHeadroom': 4, 'ev:peakDemand': 540,
        'ev:faultedChargers': 3, 'ev:chargingPower': 90 },
    },
    rootCause: [
      { icon: 'ti-building-factory', text: '<b>Site draw near the transformer limit</b> — headroom collapsing' },
      { icon: 'ti-temperature', text: '<b>Transformer hot-spot rising</b> — insulation aging accelerates' },
      { icon: 'ti-arrow-bar-to-up', text: '<b>Grid headroom exhausted</b> — no capacity for new sessions' },
      { icon: 'ti-alert-octagon', text: '<b>Utility penalties / trip risk</b>' },
      { icon: 'ti-player-stop', text: '<b>Site-wide outage</b> if untreated' },
    ],
    signals: [['ev:gridLoad', 68], ['ev:transformerTemp', 66], ['ev:loadHeadroom', 32], ['ev:peakDemand', 420]],
    steps: [
      { t: 'Diagnose EMS load balance', d: 'Read grid load, headroom, transformer temp and site demand.', f: 'GRID', tool: 'EMS console', time: 12, diff: 'Low' },
      { t: 'Curtail charging & dispatch BESS', d: 'Fair-share bays down and peak-shave from the on-site BESS.', f: 'GRID', tool: 'EMS controller', time: 15, diff: 'Low', safety: true },
      { t: 'Inspect transformer cooling', d: 'Check fans / radiators and oil temperature for the hot-spot.', f: 'GRID', tool: 'Inspection · IR camera', time: 40, diff: 'Medium' },
      { t: 'Rebalance load & set limits', d: 'Reset the site limit and per-bay caps to restore headroom.', f: 'ENERGY', tool: 'EMS controller', time: 45, diff: 'Medium' },
      { t: 'Verify draw under limit', d: 'Confirm grid load, headroom and transformer temp are nominal.', f: 'GRID', tool: 'EMS console', time: 40, diff: 'Low' },
      { t: 'Sign off', d: 'Log the peak event; no transformer upgrade required.', f: 'GRID', tool: '—', time: 15, diff: 'Low' },
    ],
  },
  'CHARGER': {
    title: 'Charger Fault — OCPP Heartbeat Loss',
    world: {
      focus: 'EMS-1',
      cascade: [
        { anchor: 'dcfc', label: 'Chargers faulted' },
        { anchor: 'ems', label: 'OCPP heartbeat lost' },
        { anchor: 'building', label: 'Revenue & SLA at risk' },
      ],
      degrade: { 'ev:faultedChargers': 6, 'ev:chargingPower': 60, 'ev:sessionsActive': 6, 'ev:gridLoad': 58 },
    },
    rootCause: [
      { icon: 'ti-plug-connected-x', text: '<b>Chargers faulted</b> — sessions dropping off the network' },
      { icon: 'ti-heartbeat', text: '<b>OCPP heartbeat latency high</b> — comms degraded' },
      { icon: 'ti-chart-arcs', text: '<b>Utilisation falling</b> — queue wait climbing' },
      { icon: 'ti-alert-triangle', text: '<b>Revenue & SLA at risk</b>' },
      { icon: 'ti-player-stop', text: '<b>Truck-roll / bay offline</b> if untreated' },
    ],
    signals: [['ev:chargerUptime', 99], ['ev:faultedChargers', 0], ['ev:ocppLatency', 240], ['ev:utilization', 41]],
    steps: [
      { t: 'Diagnose OCPP telemetry', d: 'Read uptime, faulted count, heartbeat latency per EVSE.', f: 'CHARGER', tool: 'OCPP monitor', time: 12, diff: 'Low' },
      { t: 'Remote self-healing reset', d: 'Issue OCPP soft-reset to the faulted charge points.', f: 'CHARGER', tool: 'CSMS', time: 10, diff: 'Low' },
      { t: 'Isolate & inspect EVSE', d: 'LOTO the affected bay; inspect contactor, connector and comms.', f: 'CHARGER', tool: 'LOTO · meter', time: 40, diff: 'Medium', safety: true },
      { t: 'Replace module / contactor', d: 'Swap the failed power module or contactor; reseat connector.', f: 'CHARGER', tool: 'EVSE spares', time: 80, diff: 'High' },
      { t: 'Verify sessions restored', d: 'Confirm uptime, latency and utilisation are nominal.', f: 'CHARGER', tool: 'OCPP monitor', time: 30, diff: 'Low' },
      { t: 'Sign off', d: 'Close the work order; update the reliability log.', f: 'CHARGER', tool: '—', time: 10, diff: 'Low' },
    ],
  },
  'BATTERY': {
    title: 'Fleet Battery Degradation — SoH Below Limit',
    world: {
      focus: 'BESS-A',
      cascade: [
        { anchor: 'bess', label: 'Pack SoH below limit' },
        { anchor: 'dcfc', label: 'Charge rate limited' },
      ],
      degrade: { 'ev:bessSoc': 30, 'ev:cellTempMax': 44, 'ev:thermalRunawayRisk': 30, 'ev:chargingPower': 95 },
    },
    rootCause: [
      { icon: 'ti-battery-3', text: '<b>Fleet SoH falling</b> — approaching end-of-life' },
      { icon: 'ti-arrows-diff', text: '<b>Cell imbalance rising</b> — capacity loss' },
      { icon: 'ti-trending-down', text: '<b>Usable range shrinking</b> — charge cycles up' },
      { icon: 'ti-alert-triangle', text: '<b>Higher thermal risk</b> on degraded packs' },
      { icon: 'ti-player-stop', text: '<b>Pack retirement</b> if untreated' },
    ],
    signals: [['ev:stateOfHealth', 93], ['ev:cellImbalance', 14], ['ev:stateOfCharge', 64]],
    steps: [
      { t: 'Diagnose pack health', d: 'Read SoH, imbalance and cycle history for the fleet packs.', f: 'BATTERY', tool: 'BMS suite', time: 15, diff: 'Low' },
      { t: 'Run balancing cycle', d: 'Active-balance the pack to recover usable capacity.', f: 'BATTERY', tool: 'Cell balancer', time: 60, diff: 'Medium' },
      { t: 'Assess second-life eligibility', d: 'Grade the pack against first-life vs second-life thresholds.', f: 'BATTERY', tool: 'Grading rig', time: 45, diff: 'Medium' },
      { t: 'Swap end-of-life module', d: 'Replace modules below the SoH floor; torque HV links.', f: 'BATTERY', tool: 'Module kit · torque wrench', time: 90, diff: 'High' },
      { t: 'Verify pack recovered', d: 'Confirm SoH, imbalance and SoC are back in band.', f: 'BATTERY', tool: 'BMS suite', time: 40, diff: 'Low' },
      { t: 'Sign off', d: 'Update the battery register and refurbishment plan.', f: 'BATTERY', tool: '—', time: 15, diff: 'Low' },
    ],
  },
}

// all domain plans merged into one lookup
const ALL_PLANS = { ...PLANS, ...TURBINE_PLANS, ...DATACENTER_PLANS, ...HOSPITAL_PLANS, ...MANUFACTURING_PLANS, ...EV_PLANS }

function healthyTarget(key) {
  const m = SIG[key] || {}
  if (m.warn != null) return +(m.warn * 0.62).toFixed(2)
  if (m.warnLow != null) return +(m.warnLow * 1.5).toFixed(2)
  if (m.crit != null) return +(m.crit * 0.5).toFixed(2)
  if (m.critLow != null) return +(m.critLow * 1.8).toFixed(2)
  return 0
}

// Build the active plan from the domain + live findings.
function buildPlan(domain, twin) {
  const ds = DOMAIN_SUBS[domain] || DOMAIN_SUBS['edm-machine']
  const findings = (twin?.findings || []).slice()
    .sort((a, b) => (b.severity === 'critical') - (a.severity === 'critical'))
  const worst = findings[0]
  const faultSig = worst?.signal

  // try to find a domain-specific plan
  const sub = ds.signalMap(faultSig || '')
  if (ALL_PLANS[sub]) {
    return { ...ALL_PLANS[sub], sub, focusSub: sub }
  }

  // try any plan that matches a subsystem in this domain
  for (const key of ds.order) {
    if (ALL_PLANS[key]) return { ...ALL_PLANS[key], sub: key, focusSub: key }
  }

  // fallback: generic plan
  const sigs = findings.slice(0, 5).map(f => f.signal).filter(Boolean)
  const signals = (sigs.length ? sigs : Object.keys(twin?.latest || {}).slice(0, 4))
    .map(k => [k, healthyTarget(k)])
  return {
    sub: ds.order[0] || 'EDM-1', focusSub: null,
    title: worst?.displayName || 'Restore machine to nominal',
    rootCause: [
      { icon: 'ti-alert-triangle', text: `<b>${worst?.displayName || 'Out-of-band signal'}</b>` },
      { icon: 'ti-activity', text: 'Degradation spreads to coupled subsystems' },
      { icon: 'ti-trending-down', text: 'Overall health falls below target' },
      { icon: 'ti-player-stop', text: '<b>Unplanned downtime</b> if untreated' },
    ],
    signals,
    steps: [
      { t: 'Diagnose the fault', d: 'Read the affected telemetry and localise the cause.', f: null, tool: 'Sensor suite', time: 20, diff: 'Low' },
      { t: 'Isolate / make safe', d: 'Apply lockout before any intervention.', f: null, tool: 'LOTO kit', time: 30, diff: 'Low', safety: true },
      { t: 'Inspect the component', d: 'Confirm the failure mode on the affected asset.', f: null, tool: 'Inspection', time: 45, diff: 'Medium' },
      { t: 'Repair / replace', d: 'Restore the failed part to serviceable condition.', f: null, tool: 'Tooling', time: 90, diff: 'Medium' },
      { t: 'Recalibrate', d: 'Bring the subsystem back to its set-point.', f: null, tool: 'Calibration', time: 60, diff: 'Medium' },
      { t: 'Functional test', d: 'Run the asset and confirm signals are nominal.', f: null, tool: 'Test', time: 50, diff: 'Low' },
      { t: 'Verify & sign off', d: 'Confirm health is restored and close the work order.', f: null, tool: '—', time: 30, diff: 'Low' },
    ],
  }
}

const mmss = (s) => `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, '0')}`
const lerp = (a, b, t) => a + (b - a) * t

export default function Maintenance({ domain = 'edm-machine', machineName = 'Wire EDM Machine', twin, modelUrl, claudeOn, onExit }) {
  const hostRef = useRef(null)
  const viewerRef = useRef(null)
  const calloutRef = useRef(null)
  const startRef = useRef(Date.now())
  const eventsRef = useRef([])
  const flowRef = useRef(null)
  const nodeRefs = useRef({})
  // Always show the twin the user is actually on — never a stale/default model.
  //  • ev-network → the live EV energy-site world (same visual as the dashboard)
  //  • any twin with a reconstructed GLB → that GLB
  //  • otherwise → the procedural domain scene
  const useEVWorld = domain === 'ev-network'
  const useGlb = !useEVWorld && !!modelUrl
  const useCustomVisual = useEVWorld || useGlb   // not the procedural createViewer scene

  const plan = useMemo(() => buildPlan(domain, twin), [domain, twin?.findings?.length])
  const steps = plan.steps
  const total = steps.length

  const [stage, setStage] = useState('intro')          // intro | scan | diagnose | repair | complete
  const [introOut, setIntroOut] = useState(false)
  const [rcLit, setRcLit] = useState(0)                 // root-cause nodes lit
  const [step, setStep] = useState(0)                   // current repair step (0-based)
  const [playing, setPlaying] = useState(false)         // manual by default — user clicks "Next step"
  const [viewStep, setViewStep] = useState(null)        // step index the user clicked to inspect (floating detail)
  const [anchor, setAnchor] = useState(null)            // {top,left} for the floating step-detail window
  const [voice, setVoice] = useState(false)
  const [focusSub, setFocusSub] = useState(plan.focusSub)
  const [subtitle, setSubtitle] = useState('')
  const [typed, setTyped] = useState(0)
  const [scan, setScan] = useState(false)

  // before/after telemetry per signal
  const tele = useMemo(() => plan.signals.map(([key, after]) => {
    const domDegraded = (DOMAIN_SUBS[domain] || DOMAIN_SUBS['edm-machine']).degraded
    const before = twin?.latest?.[key] ?? domDegraded[key] ?? after
    return { key, after, before, meta: SIG[key] || { label: key, unit: '' } }
  }), [plan, twin])

  // recovery fraction (drives telemetry + health)
  const frac = stage === 'complete' ? 1 : stage === 'repair' ? Math.min(1, (step + 1) / total) : 0
  const startHealth = twin?.health != null ? twin.health : 0.23
  const health = stage === 'complete' ? 1 : lerp(startHealth, 0.99, frac)

  // ── EV energy-site cinematic: drive the 3-D world through the fault cascade ──
  // crisis 0→1 slides the world into the fault (scan/diagnose) then back out as
  // the repair progresses; numbered stage bubbles pop over the affected assets and
  // the camera sweeps to follow, then the bubbles recede step-by-step as it heals.
  const evWorld = plan.world
  const crisis = stage === 'complete' ? 0
    : stage === 'repair' ? Math.max(0, 1 - frac)
    : (stage === 'diagnose' || stage === 'scan') ? 1 : 0
  const worldLive = useMemo(() => {
    if (!useEVWorld) return twin?.latest
    const out = { ...EV_NOMINAL }
    const deg = evWorld?.degrade || {}
    for (const k of Object.keys(out)) if (deg[k] != null) out[k] = lerp(out[k], deg[k], crisis)
    out['ev:faultedChargers'] = Math.round(out['ev:faultedChargers'])
    return out
  }, [useEVWorld, evWorld, crisis, twin])
  const worldStages = useMemo(() => {
    if (!useEVWorld || !evWorld) return []
    const casc = (evWorld.cascade || []).map((c, i) => ({ n: i + 1, anchor: c.anchor, label: c.label }))
    if (stage === 'diagnose') return casc.slice(0, Math.min(casc.length, rcLit))
    if (stage === 'repair') return casc.slice(Math.round(casc.length * frac))   // root clears first, stable numbering
    return []
  }, [useEVWorld, evWorld, stage, rcLit, frac])
  const worldFocus = (useEVWorld && (stage === 'diagnose' || stage === 'repair')) ? (evWorld?.focus || null) : null

  // ── mount the cinematic 3-D viewer (scene loads behind the intro) ──
  // Only for procedural domains; when a reconstructed GLB exists we render
  // <ModelViewer> (the actual twin model) instead.
  useEffect(() => {
    if (useCustomVisual || !hostRef.current) return
    let v
    try { v = createViewer(hostRef.current, { domain, machine: machineName, cinematic: true }) }
    catch (e) { /* graceful: HUD still works without the 3-D */ }
    viewerRef.current = v
    return () => { try { v && v.dispose() } catch {} viewerRef.current = null }
  }, [domain, machineName, useCustomVisual])

  // keep the glued callout pinned to the focused subsystem every frame
  // (procedural scene only — the GLB has no named subsystems to project to)
  useEffect(() => {
    if (useCustomVisual) return
    let raf
    const tick = () => {
      raf = requestAnimationFrame(tick)
      const node = calloutRef.current, v = viewerRef.current
      if (!node || !v || !focusSub || !(stage === 'diagnose' || stage === 'repair')) {
        if (node) node.style.opacity = '0'; return
      }
      const p = v.worldToScreen(focusSub)
      if (!p || !p.visible) { node.style.opacity = '0'; return }
      node.style.transform = `translate(${p.x}px, ${p.y}px) translate(-50%,-100%)`
      node.style.opacity = '1'
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [focusSub, stage, useCustomVisual])

  const focus = (id) => { setFocusSub(id); viewerRef.current && viewerRef.current.focusAsset(id) }
  const colorSubsystems = (allGood) => {
    const v = viewerRef.current; if (!v) return
    const up = {}
    const domOrder = (DOMAIN_SUBS[domain] || DOMAIN_SUBS['edm-machine']).order
    domOrder.forEach(id => { up[id] = { status: allGood ? 'ok' : (id === plan.focusSub ? 'crit' : 'ok') } })
    up['EDM-1'] = { status: allGood ? 'ok' : (plan.focusSub ? 'warn' : 'crit') }
    v.updateAssets(up)
  }

  const say = (txt) => { setSubtitle(txt); eventsRef.current.push({ t: (Date.now() - startRef.current) / 1000, label: txt, stage, step }) }

  // ── stage choreography ──
  useEffect(() => {
    const timers = []
    const at = (ms, fn) => timers.push(setTimeout(fn, ms))
    if (stage === 'intro') {
      say('Maintenance mode activated. Taking control of the digital twin.')
      at(2300, () => setIntroOut(true))
      at(3100, () => setStage('scan'))
    } else if (stage === 'scan') {
      setScan(true)
      say(`Scanning ${machineName}. Generating a live health map across all subsystems.`)
      viewerRef.current && viewerRef.current.resetView(1.4)
      at(1500, () => colorSubsystems(false))
      at(3000, () => { setScan(false); setStage('diagnose') })
    } else if (stage === 'diagnose') {
      if (plan.focusSub) focus(plan.focusSub)
      const domMeta = (DOMAIN_SUBS[domain] || DOMAIN_SUBS['edm-machine']).meta
      const subLabel = domMeta[plan.focusSub]?.label || 'the affected component'
      say(`Fault isolated to ${subLabel}. Projecting the root-cause chain.`)
      plan.rootCause.forEach((_, i) => at(700 + i * 650, () => setRcLit(i + 1)))
      at(900 + plan.rootCause.length * 650 + 900, () => { setStep(0); setStage('repair') })
    } else if (stage === 'repair') {
      // (per-step effect below drives camera + narration)
    } else if (stage === 'complete') {
      colorSubsystems(true)
      viewerRef.current && viewerRef.current.resetView(1.8)
      say('Diagnostic re-scan passed. All subsystems nominal. Maintenance complete.')
    }
    return () => timers.forEach(clearTimeout)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stage])

  // per-step choreography while repairing
  useEffect(() => {
    if (stage !== 'repair') return
    setViewStep(null)   // active step changed — collapse any manually-opened detail
    const s = steps[step]; if (!s) return
    focus(s.f || plan.focusSub || 'EDM-1')
    say(`Step ${step + 1} of ${total}: ${s.t}. ${s.d}`)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stage, step])

  // Which step's detail is shown in the floating window: an explicitly-clicked
  // step wins, else the active repair step.
  const detailStep = viewStep != null ? viewStep : (stage === 'repair' ? step : null)

  // Anchor the floating detail window beside its flow node (re-measured on
  // step change, list scroll and resize).
  const measureAnchor = useCallback(() => {
    const node = detailStep != null ? nodeRefs.current[detailStep] : null
    if (!node) { setAnchor(null); return }
    const r = node.getBoundingClientRect()
    setAnchor({ top: Math.round(r.top + r.height / 2), left: Math.round(r.right + 14) })
  }, [detailStep])
  useLayoutEffect(measureAnchor, [measureAnchor, stage, step, rcLit])
  useEffect(() => {
    const fl = flowRef.current
    window.addEventListener('resize', measureAnchor)
    fl && fl.addEventListener('scroll', measureAnchor)
    return () => { window.removeEventListener('resize', measureAnchor); fl && fl.removeEventListener('scroll', measureAnchor) }
  }, [measureAnchor])

  // auto-advance the repair when playing
  useEffect(() => {
    if (stage !== 'repair' || !playing) return
    const s = steps[step]; if (!s) return
    const dur = Math.max(8000, Math.min(18000, s.time * 180))
    const t = setTimeout(() => {
      if (step + 1 >= total) setStage('complete')
      else setStep(step + 1)
    }, dur)
    return () => clearTimeout(t)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stage, step, playing])

  // typewriter for the subtitle
  useEffect(() => {
    setTyped(0)
    if (!subtitle) return
    let i = 0
    const id = setInterval(() => { i += 2; setTyped(i); if (i >= subtitle.length) clearInterval(id) }, 16)
    return () => clearInterval(id)
  }, [subtitle])

  // optional voice (opt-in; guarded)
  useEffect(() => {
    if (!voice || !subtitle || !('speechSynthesis' in window)) return
    try {
      window.speechSynthesis.cancel()
      const u = new SpeechSynthesisUtterance(subtitle)
      u.rate = 1.04; u.pitch = 1.0
      window.speechSynthesis.speak(u)
    } catch {}
    return () => { try { window.speechSynthesis.cancel() } catch {} }
  }, [subtitle, voice])

  // escape to exit
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onExit && onExit() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onExit])

  // remaining ETA
  const remaining = stage === 'complete' ? 0 : steps.slice(stage === 'repair' ? step : 0).reduce((a, s) => a + s.time, 0)
  const speaking = stage !== 'complete'

  const goStep = (i) => { setStage('repair'); setPlaying(false); setStep(Math.max(0, Math.min(total - 1, i))) }
  const domMeta = (DOMAIN_SUBS[domain] || DOMAIN_SUBS['edm-machine']).meta
  const domOrder = (DOMAIN_SUBS[domain] || DOMAIN_SUBS['edm-machine']).order
  const subsysRows = domOrder.map(id => ({
    id, label: (domMeta[id] || {}).label || id,
    status: stage === 'complete' ? 'ok' : (id === plan.focusSub ? (stage === 'repair' || stage === 'diagnose' ? 'crit' : 'crit') : 'ok'),
  }))
  const statusColor = (s) => s === 'crit' ? 'var(--mx-red)' : s === 'warn' ? 'var(--mx-amber)' : 'var(--mx-green)'

  // focused subsystem live metrics (for the glued callout)
  const focusTele = tele.filter(x => subForSignal(x.key, domain) === focusSub).slice(0, 2)
  const cur = (x) => lerp(x.before, x.after, frac)

  return (
    <div className="mx-root" style={{ '--mx-dim-level': (stage === 'diagnose' || stage === 'repair') ? 0.9 : 0.25 }}>
      <div className="mx-veil" />

      {/* ── intro takeover ── */}
      {stage === 'intro' && (
        <div className={`mx-intro ${introOut ? 'out' : ''}`}>
          <AICore size={150} speaking />
          <div>
            <div className="mx-intro-title">AI Maintenance Director</div>
          </div>
          <div className="mx-intro-sub">Taking control of digital twin</div>
          <div className="mx-intro-bar"><i /></div>
        </div>
      )}

      {/* ── main stage ── */}
      <div className="mx-stage">
        {/* top bar */}
        <div className="mx-top">
          <div className="mx-badge"><AICore size={32} speaking={speaking} />
            <div><b>AI Maintenance Director</b><br /><small>Autonomous mode</small></div>
          </div>
          <div className="mx-top-spacer" />
          <div className="mx-chip"><span className="dot" style={{ background: 'var(--mx-cyan)' }} />{machineName}</div>
          <div className="mx-chip">Agent <b>{claudeOn ? 'Claude' : 'on-board'}</b></div>
          <div className="mx-exit" onClick={onExit}><I n="ti-x" /> Exit</div>
        </div>

        {/* LEFT rail — flow / root cause */}
        <div className="mx-left">
          <div className="mx-card">
            <div className="mx-h"><I n="ti-affiliate" /> Root cause<span className="tag">{plan.sub}</span></div>
            <div className="mx-rc">
              {plan.rootCause.map((n, i) => (
                <React.Fragment key={i}>
                  {i > 0 && <div className={`mx-rcarrow ${rcLit > i ? 'lit' : ''}`} />}
                  <div className={`mx-rcn ${rcLit > i ? 'lit' : ''}`}>
                    <div className="ic"><I n={n.icon} /></div>
                    <div className="tx" dangerouslySetInnerHTML={{ __html: n.text }} />
                  </div>
                </React.Fragment>
              ))}
            </div>
          </div>

          <div className="mx-card flush">
            <div className="mx-h"><I n="ti-route" /> Repair plan<span className="tag">{total} steps</span></div>
            <div className="mx-flow" ref={flowRef}>
              {steps.map((s, i) => {
                const st = stage === 'complete' || i < step ? 'done' : (stage === 'repair' && i === step) ? 'active' : ''
                const isFocused = detailStep === i
                return (
                  <div key={i} ref={el => { nodeRefs.current[i] = el }}
                    className={`mx-fnode ${st} ${isFocused ? 'viewing' : ''}`}
                    onClick={() => { if (st === 'active') { setViewStep(null); return } setViewStep(v => v === i ? null : i); setPlaying(false) }}>
                    <div className="rail">
                      <div className="bead">{st === 'done' ? '✓' : s.safety ? '!' : i + 1}</div>
                      <div className="wire" />
                    </div>
                    <div className="body">
                      <div className="ftitle">
                        {s.t}
                        {s.safety && <span className="safety">SAFETY</span>}
                        <span className="chev">{isFocused ? '▾' : '›'}</span>
                      </div>
                      <div className="fsub">{s.d}</div>
                    </div>
                  </div>
                )
              })}
            </div>
          </div>
        </div>

        {/* CENTER — the 3-D twin. Shows the actual reconstructed GLB when the
            twin has one, else the procedural domain scene. */}
        <div className="mx-center">
          {useEVWorld
            ? <div className="mx-canvas" style={{ position: 'absolute', inset: 0 }}>
                <EVWorld live={{ ...(worldLive || {}), __stages: worldStages }} machine={machineName}
                  focusId={worldFocus} height="100%" />
              </div>
            : useGlb
            ? <div className="mx-canvas" style={{ position: 'absolute', inset: 0 }}>
                <ModelViewer url={modelUrl} height="100%" autoRotate={stage !== 'repair'}
                  badge={<><I n={domMeta[focusSub]?.icon || 'ti-cube'} /> <b>{machineName}</b>
                    {focusSub && domMeta[focusSub] ? ` · ${domMeta[focusSub].label}` : ''}</>} />
              </div>
            : <div ref={hostRef} className="mx-canvas hero3d scene3d-host" />}
          <div className={`mx-scan ${scan ? 'on' : ''}`}><i /><b /></div>
          {/* glued component callout (procedural scene only) */}
          {!useCustomVisual && (
            <div ref={calloutRef} className={`mx-callout ${stage === 'repair' || stage === 'complete' ? 'repair' : ''}`} style={{ opacity: 0 }}>
              <div className="lab">
                <div className="nm"><I n={domMeta[focusSub]?.icon || 'ti-cube'} /> {domMeta[focusSub]?.label || ''}</div>
                {focusTele.length > 0 && <div className="mm">
                  {focusTele.map(x => <span key={x.key}>{x.meta.label}<b>{fmt(cur(x))}{x.meta.unit ? ' ' + x.meta.unit : ''}</b></span>)}
                </div>}
              </div>
              <div className="stem" /><div className="ring" />
            </div>
          )}
        </div>

        {/* RIGHT rail — health + telemetry */}
        <div className="mx-right">
          <div className="mx-card">
            <div className="mx-h"><I n="ti-heartbeat" /> Twin health</div>
            <HealthRing value={health} />
            <div className="mx-sub-list">
              {subsysRows.map(r => (
                <div key={r.id} className="mx-subrow">
                  <span className="sd" style={{ background: statusColor(r.status), boxShadow: `0 0 10px ${statusColor(r.status)}` }} />
                  <span className="nm">{r.label}</span>
                  <span className="st" style={{ color: statusColor(r.status) }}>{r.status === 'crit' ? 'FAULT' : r.status === 'warn' ? 'WATCH' : 'OK'}</span>
                </div>
              ))}
            </div>
          </div>

          <div className="mx-card flush">
            <div className="mx-h"><I n="ti-activity" /> Live telemetry<span className="tag">recovering</span></div>
            <div className="mx-tel">
              {tele.map(x => {
                const v = cur(x); const sev = sevClass(x.key, v)
                const col = sev === 'crit' ? 'var(--mx-red)' : sev === 'warn' ? 'var(--mx-amber)' : 'var(--mx-green)'
                const m = x.meta
                const fill = m.crit != null ? Math.min(100, (v / (m.crit * 1.15)) * 100)
                  : m.critLow != null ? Math.min(100, Math.max(6, (v / ((m.warnLow || m.critLow) * 2)) * 100))
                  : Math.max(8, Math.min(100, frac * 100))
                return (
                  <div key={x.key} className="mx-trow">
                    <div className="tt"><span className="nm">{m.label}</span>
                      <span className="vv" style={{ color: col }}>{fmt(v)}<u>{m.unit}</u></span></div>
                    <div className="mx-bar"><i style={{ width: `${fill}%`, background: col }} /></div>
                  </div>
                )
              })}
            </div>
          </div>
        </div>

        {/* BOTTOM — AI voice / task / controls */}
        <div className="mx-bottom">
          <div className="mx-bar-wrap">
            <div className="mx-voice">
              <AICore size={46} speaking={speaking} />
              <div className={`mx-wave ${speaking ? '' : 'idle'}`}>{Array.from({ length: 7 }).map((_, i) => <i key={i} />)}</div>
              <div className="mx-said">
                <div className="who">AI Director {voice ? '· speaking' : ''}</div>
                <div className="txt">{subtitle.slice(0, typed)}{typed < subtitle.length && <span className="car">▍</span>}</div>
              </div>
            </div>

            <div className="mx-task">
              <div className="lbl">Current task</div>
              <div className="now">{stage === 'complete' ? 'Maintenance complete' : stage === 'repair' ? steps[step]?.t : stage === 'diagnose' ? 'Root-cause analysis' : stage === 'scan' ? 'Health scan' : 'Initialising'}</div>
              <div className="eta">{stage === 'complete' ? 'Done' : `~${mmss(remaining)} remaining`}</div>
              <div className="mx-prog"><i style={{ width: `${Math.round(frac * 100)}%` }} /></div>
            </div>

            <div className="mx-ctrls">
              <div className={`mx-btn ${voice ? 'on' : ''}`} title="Voice narration" onClick={() => setVoice(v => !v)}><I n={voice ? 'ti-volume' : 'ti-volume-off'} /></div>
              <div className="mx-btn" title="Previous step" onClick={() => goStep(step - 1)} disabled={stage !== 'repair' || step === 0}><I n="ti-player-track-prev" /></div>
              <div className="mx-btn" title={playing ? 'Pause auto-play' : 'Auto-play steps'} onClick={() => { if (stage === 'complete') return; if (stage !== 'repair') setStage('repair'); setPlaying(p => !p) }}>
                <I n={playing && stage === 'repair' ? 'ti-player-pause' : 'ti-player-play'} /></div>
              {/* Primary manual action — the user advances at their own pace */}
              <button className="mx-next" disabled={stage === 'complete'}
                onClick={() => {
                  if (stage === 'complete') return
                  if (stage === 'intro' || stage === 'scan' || stage === 'diagnose') { setStage('repair'); setStep(0); return }
                  if (step + 1 >= total) setStage('complete'); else goStep(step + 1)
                }}>
                {stage === 'complete' ? 'Done'
                  : stage !== 'repair' ? <>Start repair <I n="ti-player-track-next" /></>
                  : step + 1 >= total ? <>Finish <I n="ti-check" /></>
                  : <>Next step <I n="ti-player-track-next" /></>}
              </button>
            </div>
          </div>
        </div>
      </div>

      {/* ── floating step-detail window (anchored beside its flow node) ── */}
      {detailStep != null && steps[detailStep] && anchor && (() => {
        const s = steps[detailStep]
        const diffColor = s.diff === 'High' ? 'var(--mx-red)' : s.diff === 'Medium' ? 'var(--mx-amber)' : 'var(--mx-green)'
        const compLabel = domMeta[s.f || plan.focusSub || domOrder[0]]?.label || '—'
        const isActive = stage === 'repair' && detailStep === step
        return (
          <div className="mx-stepdetail" style={{ top: anchor.top, left: anchor.left }}>
            <div className="sd-head">
              <span className="sd-idx">{isActive ? 'NOW' : `STEP ${detailStep + 1}`}</span>
              <span className="sd-title">{s.t}</span>
              {viewStep != null && <span className="sd-x" onClick={() => setViewStep(null)}><I n="ti-x" /></span>}
            </div>
            {s.safety && (
              <div className="sd-safety"><I n="ti-alert-triangle" /> SAFETY HOLD — apply Lockout/Tagout first</div>
            )}
            <div className="sd-desc">{s.d}</div>
            <div className="sd-meta">
              <div><span>Tool required</span><b style={{ color: 'var(--mx-cyan)' }}>{s.tool}</b></div>
              <div><span>Est. duration</span><b>{mmss(s.time)}</b></div>
              <div><span>Difficulty</span><b style={{ color: diffColor }}>{s.diff}</b></div>
              <div><span>Component</span><b style={{ color: 'var(--mx-violet)' }}>{compLabel}</b></div>
            </div>
            {!isActive && (
              <button className="sd-jump" onClick={() => { setViewStep(null); goStep(detailStep) }}>
                <I n="ti-player-play" /> Jump to this step
              </button>
            )}
          </div>
        )
      })()}

      {/* ── completion ── */}
      {stage === 'complete' && (
        <div className="mx-complete">
          <Confetti />
          <div className="ok"><I n="ti-check" /></div>
          <div className="big">Maintenance Complete</div>
          <div className="sub">{machineName} restored to nominal · health {Math.round(health * 100)}% · {total} steps verified</div>
          <div className="mx-play">
            <div className="mx-h" style={{ justifyContent: 'center', marginTop: 10 }}><I n="ti-history" /> Session playback</div>
            {steps.map((s, i) => (
              <div key={i} className="row" onClick={() => goStep(i)}>
                <span className="t">{mmss(steps.slice(0, i).reduce((a, x) => a + x.time, 0))}</span>
                <span className="l">{s.t}</span>
                <I n="ti-player-play" />
              </div>
            ))}
          </div>
          <div className="acts">
            <div className="mx-exit" style={{ background: 'rgba(124,150,255,.12)', borderColor: 'var(--mx-line2)', color: 'var(--mx-text)' }} onClick={() => { startRef.current = Date.now(); eventsRef.current = []; setRcLit(0); setStep(0); setPlaying(true); setStage('scan') }}>
              <I n="ti-refresh" /> Replay
            </div>
            <div className="mx-exit" onClick={onExit}><I n="ti-check" /> Done — exit</div>
          </div>
        </div>
      )}
    </div>
  )
}

// ── the stationary holographic AI core (no flying bot) ───────────────
function AICore({ size = 120, speaking = false }) {
  return (
    <div className={`mx-core ${speaking ? 'speaking' : ''}`} style={{ '--s': size + 'px' }}>
      <div className="ring r1" /><div className="ring r2" /><div className="ring r3" />
      <div className="pulse" /><div className="orb" />
    </div>
  )
}

// ── animated health ring ─────────────────────────────────────────────
function HealthRing({ value = 0 }) {
  const r = 40, c = 2 * Math.PI * r
  const off = c * (1 - Math.max(0, Math.min(1, value)))
  const col = value >= 0.8 ? 'var(--mx-green)' : value >= 0.55 ? 'var(--mx-cyan)' : value >= 0.4 ? 'var(--mx-amber)' : 'var(--mx-red)'
  return (
    <div className="mx-health">
      <svg className="mx-ring-svg" viewBox="0 0 96 96">
        <circle className="mx-ring-bg" cx="48" cy="48" r={r} />
        <circle className="mx-ring-fg" cx="48" cy="48" r={r} stroke={col}
          strokeDasharray={c} strokeDashoffset={off} />
      </svg>
      <div>
        <div className="mx-ring-num" style={{ color: col }}>{Math.round(value * 100)}%</div>
        <div className="mx-ring-lbl">Physics health</div>
      </div>
    </div>
  )
}

// ── holographic completion confetti (CSS-driven, GPU-cheap) ──────────
function Confetti() {
  const bits = useMemo(() => Array.from({ length: 64 }).map((_, i) => ({
    left: Math.random() * 100,
    delay: Math.random() * 0.8,
    dur: 2.4 + Math.random() * 1.8,
    col: ['#33e29b', '#36e3ff', '#9a7cff', '#5b8bff', '#ffc24b'][i % 5],
    rot: Math.random() * 360,
  })), [])
  return (
    <div className="mx-confetti">
      {bits.map((b, i) => (
        <i key={i} style={{ left: b.left + '%', background: b.col,
          transform: `rotate(${b.rot}deg)`, animationDuration: b.dur + 's', animationDelay: b.delay + 's' }} />
      ))}
    </div>
  )
}
