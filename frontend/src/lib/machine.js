/**
 * machine.js — client-side knowledge of the machine-twin domains (the live
 * physics runtime: gas turbine, wire-EDM, rail, hospital, EV, defence). Used to
 * decide when a panel should render the machine-twin surfaces instead of the
 * HVAC/CFP feed.
 */
export const MACHINE_DOMAINS = ['turbine-engine', 'edm-machine',
  'railway-metro', 'railway-trainset', 'hospital-campus', 'ev-charging-network', 'ev-battery-pack',
  'defence-base', 'defence-warship']

export const isMachineDomain = (domain) => MACHINE_DOMAINS.includes(domain)

/** Domains that expose a live network/spatial payload via GET /twins/{tenant}/network. */
export const NETWORK_DOMAINS = ['railway-metro', 'hospital-campus',
  'ev-charging-network', 'ev-battery-pack', 'defence-base', 'defence-warship']

export const isNetworkDomain = (domain) => NETWORK_DOMAINS.includes(domain)

/** Status → CSS colour var, matching the sensor-card status classes. */
export const statusColor = (status) => ({
  ok: 'var(--ok)', healthy: 'var(--ok)',
  warning: 'var(--accent-amber)', warn: 'var(--accent-amber)',
  critical: 'var(--accent-red)', crit: 'var(--accent-red)',
  unknown: 'var(--muted)',
}[status] || 'var(--muted)')

/** Health 0..1 → {label, color}. */
export const healthBand = (h) => {
  if (h == null) return { label: '—', color: 'var(--muted)' }
  if (h >= 0.72) return { label: 'Healthy', color: 'var(--ok)' }
  if (h >= 0.4) return { label: 'Degraded', color: 'var(--accent-amber)' }
  return { label: 'Critical', color: 'var(--accent-red)' }
}

/** Health 0..1 → hex colour (for rings/sparklines that need a raw colour). */
export const hColor = (h) =>
  h == null ? '#9aa1ad' : h > 0.7 ? '#16a34a' : h > 0.4 ? '#d97706' : '#e11d48'

/** Risk = inverse of health, as a 0..100 score with a band. */
export const riskFromHealth = (h) => {
  if (h == null) return { score: null, label: '—', color: 'var(--muted)' }
  const score = Math.round((1 - h) * 100)
  if (score >= 60) return { score, label: 'HIGH', color: 'var(--accent-red)' }
  if (score >= 30) return { score, label: 'ELEVATED', color: 'var(--accent-amber)' }
  return { score, label: 'LOW', color: 'var(--accent-green)' }
}

/**
 * Display metadata per domain (icon, accent, tag, blurb, control label). Merged
 * with the backend template's label/description in the Twins library + dashboard.
 * Covers the 3 machine domains and the facility templates.
 */
export const DOMAIN_META = {
  'turbine-engine': { label: 'Gas Turbine', tag: 'Aerospace · Power', icon: 'ti-engine',
    accent: '#e11d48', control: 'Throttle', machine: true, signals: 8,
    blurb: 'A live gas-turbine twin — EGT, shaft speeds, fuel, vibration, EPR and oil, with subsystem health and RUL.' },
  'edm-machine': { label: 'Wire EDM', tag: 'Precision Machining', icon: 'ti-square-rotated-forbid-2',
    accent: '#2563eb', control: 'Intensity', machine: true, signals: 18,
    blurb: 'A wire electrical-discharge-machining twin — discharge, dielectric, wire-transport and axis signals.' },
  'railway-metro': { label: 'Metro Rail Network', tag: 'Rail · Transit', icon: 'ti-train',
    accent: '#0ea5e9', control: 'Service level', machine: true, network: true, signals: 25,
    blurb: 'A full MRT / metro twin — lines, stations, permanent way, third-rail traction power, CBTC signalling and station services, with a live network map, per-station KPIs and a depot board.' },
  'railway-trainset': { label: 'Rolling Stock', tag: 'Rail · Vehicle', icon: 'ti-container',
    accent: '#6366f1', control: 'Throttle', machine: true, signals: 14,
    blurb: 'A rolling-stock twin — one train set at the vehicle level: traction, bogies, braking, doors and auxiliaries, with health and remaining-useful-life.' },
  'hospital-campus': { label: 'Hospital Campus', tag: 'Healthcare · Estates', icon: 'ti-building-hospital',
    accent: '#0891b2', control: 'Patient load', machine: true, network: true, signals: 22,
    blurb: 'A full hospital-campus twin — theatres, ICU, ED, pharmacy, wards, medical gas, water safety, power resilience, sterilisation, infection control and patient flow, with a bed board, OR calendar, patient-flow funnel, infection map and medical-gas schematic.' },
  'ev-charging-network': { label: 'EV Charging Network', tag: 'E-Mobility · Grid', icon: 'ti-charging-pile',
    accent: '#16a34a', control: 'Demand level', machine: true, network: true, signals: 20,
    blurb: 'An EV charging-network twin — stations, chargers, grid connection, transformer, solar and V2G, with a charging geo map, a grid load curve and a V2G arbitrage view.' },
  'ev-battery-pack': { label: 'EV Battery Pack', tag: 'E-Mobility · Battery', icon: 'ti-battery-charging',
    accent: '#65a30d', control: 'Charge rate', machine: true, network: true, signals: 9,
    blurb: 'A battery-pack twin at cell level — Thevenin ECM, thermal coupling and degradation — with a cell-health heatmap and imbalance / thermal-runaway / SoH monitoring.' },
  'defence-base': { label: 'Military Base (C4ISR)', tag: 'Defence · C4ISR', icon: 'ti-building-fortress',
    accent: '#475569', control: 'Readiness', machine: true, network: true, signals: 18,
    blurb: 'A military-base twin — perimeter, C4ISR, radar, hangars, fuel and ammunition storage and NBC, with a NATO APP-6 tactical map and a mission board.' },
  'defence-warship': { label: 'Warship', tag: 'Defence · Naval', icon: 'ti-ship',
    accent: '#334155', control: 'Speed demand', machine: true, network: true, signals: 14,
    blurb: 'A warship twin — gas-turbine propulsion, stability under progressive flooding and hull structural fatigue, with a damage-control compartment diagram.' },
  hvac: { label: 'HVAC Facility', tag: 'Buildings', icon: 'ti-air-conditioning',
    accent: '#7c3aed', signals: 1,
    blurb: 'A site with a server room cooled by an air handler — the live temperature feed and Tier A/B/C rules.' },
  'generic-facility': { label: 'Generic Facility', tag: 'Buildings', icon: 'ti-building-cog',
    accent: '#7c3aed', signals: 8,
    blurb: 'A 3-floor building with HVAC, power, fire, security, water and network systems.' },
  'scanned-object': { label: 'Scanned Object', tag: 'Reconstruction', icon: 'ti-cube-3d-sphere',
    accent: '#0891b2', blurb: 'A 3-D object reconstructed from a photo with TRELLIS (RunPod).' },
  blank: { label: 'Blank Twin', tag: 'Custom', icon: 'ti-square-plus', accent: '#6b7280',
    blurb: 'An empty twin with just a root site — build it by hand via Add Asset.' },
}

export const domainMeta = (key) => ({
  label: key, tag: 'Domain', icon: 'ti-cube', accent: '#7c3aed', blurb: '',
  ...(DOMAIN_META[key] || {}),
})

/** Local name of a signal key ('turbine:egt' → 'egt'). */
const _loc = (s) => (s || '').split('#').pop().split(':').pop()

/**
 * Zero-token co-pilot: a deterministic auto-observation from a state snapshot.
 * Mirrors the collins-demo stub so the dashboard co-pilot works with no backend
 * LLM call. `st` is the /state payload (health, latest, findings, name).
 */
export function stubNarration(st) {
  if (!st || !st.latest) return null
  const h = st.health
  const findings = st.findings || []
  const name = st.name || 'the machine'
  if (findings.length) {
    const crit = findings.find((f) => f.severity === 'critical') || findings[0]
    return `Watching ${name}: ${findings.length} active finding${findings.length > 1 ? 's' : ''}. ` +
      `Most pressing — ${crit.message}`
  }
  if (h != null && h < 0.7) return `${name} health is ${Math.round(h * 100)}% and trending down — worth a look before it breaches a limit.`
  return `${name} is running within limits (health ${h == null ? '—' : Math.round(h * 100) + '%'}). No active findings.`
}

/** Zero-token co-pilot reply to a user question, grounded in the snapshot. */
export function stubReply(msg, st) {
  const q = (msg || '').toLowerCase()
  const h = st?.health
  const findings = st?.findings || []
  const name = st?.name || 'the machine'
  const latest = st?.latest || {}
  const worst = findings.find((f) => f.severity === 'critical') || findings[0]
  if (/health|how.*(doing|is it)|status|overall/.test(q)) {
    return `${name} is at ${h == null ? '—' : Math.round(h * 100) + '%'} health` +
      (findings.length ? `, with ${findings.length} active finding${findings.length > 1 ? 's' : ''}. ${worst ? 'Top issue: ' + worst.message : ''}`
        : ' and no active findings — within limits.')
  }
  if (/concern|worst|worry|problem|issue|wrong/.test(q)) {
    return worst ? `The most concerning right now is: ${worst.message}` : `Nothing concerning — ${name} is within limits on every signal.`
  }
  if (/check|next|do|action|recommend|fix|maintain/.test(q)) {
    return worst ? `I'd inspect the subsystem behind "${worst.message.split('—')[0].trim()}". Open the maintenance work order below for the guided procedure.`
      : `No action needed. Keep monitoring; I'll flag anything that drifts toward a limit.`
  }
  // signal lookup: does the question name a signal?
  const hit = Object.keys(latest).find((s) => q.includes(_loc(s).toLowerCase()))
  if (hit) return `${_loc(hit)} is currently ${latest[hit]}.`
  return `I'm observing ${name} live. Ask about its health, the most concerning signal, or what to check next.`
}
