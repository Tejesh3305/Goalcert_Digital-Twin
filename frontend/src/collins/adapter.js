// adapter.js — bridges a live v3 machine-twin (its domain key + telemetry, which
// use the v3 signal vocabulary, e.g. `turbine:egt`) onto the Collins UI vocabulary
// (`aero:exhaustGasTemp`, domain key `turbine-engine`/`ev-network`/…). This is
// what lets the self-contained Collins components (Maintenance / EVWorld / …) run
// verbatim against real v3 twins.

// v3 domain key → Collins domain key.
export const V3_TO_COLLINS_DOMAIN = {
  'turbine-engine': 'turbine-engine',
  'edm-machine': 'edm-machine',
  'ev-charging-network': 'ev-network',
  'ev-battery-pack': 'ev-network',
  'hospital-campus': 'hospital',
  'hospital-imaging': 'hospital-imaging',
  'tram-network': 'tram-network',
  'railway-metro': 'mrt-line',
  'railway-trainset': 'mrt-line',
  'defence-base': 'defence-base',
  'defence-warship': 'defence-base',
  // sim-only Collins domains keep their own keys
  datacenter: 'datacenter',
  manufacturing: 'manufacturing',
}

export const toCollinsDomain = (v3domain) =>
  V3_TO_COLLINS_DOMAIN[v3domain] || v3domain

// Per-Collins-domain signal remap: v3 CURIE → Collins CURIE. Only the divergent
// keys are listed; anything not in the table passes through unchanged (edm / hsp /
// fleet largely already share names with the Collins vocabulary).
const CURIE_MAP = {
  'turbine-engine': {
    'turbine:egt': 'aero:exhaustGasTemp',
    'turbine:n1': 'aero:shaftSpeedN1',
    'turbine:n2': 'aero:shaftSpeedN2',
    'turbine:fuelFlow': 'aero:fuelFlow',
    'turbine:vibration': 'aero:vibrationG',
    'turbine:epr': 'aero:enginePressureRatio',
    'turbine:oilTemp': 'aero:oilTemperature',
    'turbine:oilPressure': 'aero:oilPressure',
  },
  'edm-machine': {
    'edm:dielectricTemp': 'edm:dielectricTemperature',
    'edm:surfaceFinishRa': 'edm:surfaceRoughnessRa',
  },
  'ev-network': {
    'ev:networkLoad': 'ev:gridLoad',
    'ev:activeSessions': 'ev:sessionsActive',
    'ev:availableChargers': 'ev:chargerUptime',
    'ev:avgChargePower': 'ev:chargingPower',
    'ev:transformerHotspot': 'ev:transformerTemp',
    'ev:chargerFaults': 'ev:faultedChargers',
    'ev:energyDelivered': 'ev:energyToday',
    'ev:v2gExport': 'ev:v2gCapacity',
    'ev:v2gRevenue': 'ev:revenueToday',
    'ev:spotPrice': 'ev:tariffRate',
    'ev:connectorTemp': 'ev:cellTempMax',
    // ev:stateOfCharge / ev:stateOfHealth / ev:solarOutput / ev:selfConsumption
    // already share names — pass through.
  },
  'defence-base': {
    'def:perimeterBreaches': 'def:perimeterAlerts',
    'def:fuelLevel': 'def:fuelReserve',
    'def:ammoTemperature': 'def:ammoTemp',
    'def:threatLevel': 'def:uasThreatLevel',
    'def:missionReadiness': 'def:forceReadiness',
  },
}

/** Remap a v3 telemetry frame into the Collins signal vocabulary for a domain. */
export function toCollinsLatest(v3domain, latest = {}) {
  const map = CURIE_MAP[toCollinsDomain(v3domain)] || {}
  const out = {}
  for (const [k, v] of Object.entries(latest || {})) out[map[k] || k] = v
  return out
}

/** Build the Collins-shaped `twin` the Maintenance overlay consumes. */
export function toCollinsTwin(v3domain, state) {
  return {
    latest: toCollinsLatest(v3domain, state?.latest),
    health: state?.health,
    findings: state?.findings || [],
  }
}

// Collins domains the cinematic Repair-with-AI (Maintenance) has repair plans for.
const MAINT_COLLINS_DOMAINS = new Set([
  'turbine-engine', 'edm-machine', 'ev-network', 'hospital', 'hospital-imaging',
  'datacenter', 'manufacturing',
])

/** Does the cinematic Maintenance Director support this v3 domain? */
export const maintSupported = (v3domain) =>
  MAINT_COLLINS_DOMAINS.has(toCollinsDomain(v3domain))
