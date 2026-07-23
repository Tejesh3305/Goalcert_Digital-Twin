/**
 * simTwins.js — the frontend-simulated twin domains (no backend physics pack).
 *
 * Helix Data Center and Forge Plant 7 stream a client-side simulation
 * (collins/lib.jsx `simTwin`), exactly as in the Collins demo. They are opened
 * from the Twins library and become the active twin via a synthetic tenant id
 * of the form `sim:<domain>`, which the Dashboard detects to render the
 * SimMachineDashboard instead of polling the backend.
 */
export const SIM_PREFIX = 'sim:'

// The sim twin cards shown in the Twins library. `domain` is the Collins domain
// key (matches collins/lib.jsx DOMAINS), which is what simTwin() / Scene3D expect.
export const SIM_TWINS = [
  {
    domain: 'datacenter',
    label: 'Helix Data Center',
    tag: 'Data Center',
    icon: 'ti-server-2',
    accent: '#0ea5e9',
    blurb: 'Server halls, CRAC cooling and UPS power with rack-level telemetry — streaming live signals with fault injection and AI repair.',
  },
  {
    domain: 'manufacturing',
    label: 'Forge Plant 7',
    tag: 'Manufacturing',
    icon: 'ti-building-factory-2',
    accent: '#f59e0b',
    blurb: 'Production lines, robotics and utilities with predictive-maintenance signals — streaming live OEE, vibration and thermal telemetry.',
  },
]

export const isSimTenant = (tenant) => typeof tenant === 'string' && tenant.startsWith(SIM_PREFIX)

/** The Collins domain of a sim tenant, or null if this isn't a sim tenant. */
export const simDomainOf = (tenant) => (isSimTenant(tenant) ? tenant.slice(SIM_PREFIX.length) : null)

/** The synthetic tenant id for a sim domain. */
export const simTenantFor = (domain) => `${SIM_PREFIX}${domain}`
