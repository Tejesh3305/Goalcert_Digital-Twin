/**
 * nav.js — sidebar navigation config.
 *
 * Trimmed to the core twin workflow. Live Ops, BIM Studio, Simulation and the
 * standalone Twin Health / Copilot pages were removed — their content now lives
 * inside the unified Dashboard (3-D scene, telemetry, co-pilot, findings,
 * maintenance and health, all for the active twin).
 *
 * `backing`: 'live' fully wired to the backend · 'partial' partly mocked ·
 * 'mock' UI placeholder only.
 */
export const NAV = [
  { section: 'Overview' },
  { id: 'twins',       path: '/twins',       label: 'Twins',        icon: 'ti-stack-2',          backing: 'live' },
  { id: 'dashboard',   path: '/',            label: 'Dashboard',    icon: 'ti-layout-dashboard', backing: 'live' },
  { id: 'concierge',   path: '/build',       label: 'Build a Twin', icon: 'ti-sparkles',         backing: 'live' },

  { section: 'Intelligence' },
  { id: 'predict',     path: '/predict',     label: 'Prediction',   icon: 'ti-chart-histogram',  backing: 'live' },
  { id: 'agents',      path: '/agents',      label: 'Twin Intelligence', icon: 'ti-robot',       backing: 'partial' },

  { section: 'Governance' },
  { id: 'changelog',   path: '/changelog',   label: 'Change Log',   icon: 'ti-history',          backing: 'live' },

  { section: 'Platform' },
  { id: 'bundle',      path: '/bundle-author', label: 'Bundle Author', icon: 'ti-wand',           backing: 'live' },
  { id: 'marketplace', path: '/marketplace', label: 'Marketplace',  icon: 'ti-layout-grid',      backing: 'mock' },
]
