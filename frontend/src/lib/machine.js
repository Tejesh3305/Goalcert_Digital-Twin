/**
 * machine.js — client-side knowledge of the machine-twin domains (the live
 * physics runtime: gas turbine, wire-EDM, tram fleet). Used to decide when a
 * panel should render the machine-twin surfaces instead of the HVAC/CFP feed.
 */
export const MACHINE_DOMAINS = ['turbine-engine', 'edm-machine', 'tram-network']

export const isMachineDomain = (domain) => MACHINE_DOMAINS.includes(domain)

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
