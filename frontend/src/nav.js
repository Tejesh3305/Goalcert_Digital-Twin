/**
 * nav.js — sidebar navigation config.
 *
 * Trimmed to the core twin workflow. Live Ops, BIM Studio, Simulation and the
 * standalone Twin Health page were removed — their content now lives inside the
 * unified Dashboard (3-D scene, telemetry, findings, maintenance and health,
 * all for the active twin).
 *
 * Twin Copilot is back as its own page, and is no longer the old rule-based
 * mock: it is the embedded agent layer (nextxr-ontology/copilot/) — diagnosis,
 * work orders, procurement, incident reports, repair training, and the two
 * chat agents, all running in-process against the live twin.
 *
 * `backing`: 'live' fully wired to the backend · 'partial' partly mocked ·
 * 'mock' UI placeholder only.
 */
export const NAV = [
  { section: 'Overview' },
  { id: 'twin',        path: '/twin',        label: 'Twin',         icon: 'ti-box',              backing: 'live' },
  { id: 'twin-library', path: '/twin-library', label: 'Twin Library', icon: 'ti-stack-2',        backing: 'live' },
  { id: 'dashboard',   path: '/',            label: 'Dashboard',    icon: 'ti-layout-dashboard', backing: 'live' },
  { id: 'concierge',   path: '/build',       label: 'Build a Twin', icon: 'ti-sparkles',         backing: 'live' },

  { section: 'Intelligence' },
  { id: 'copilot',     path: '/copilot',     label: 'Twin Copilot', icon: 'ti-sparkles',         backing: 'live' },
  { id: 'predict',     path: '/predict',     label: 'Prediction',   icon: 'ti-chart-histogram',  backing: 'live' },

  { section: 'Work' },
  // Both are listed for everyone and each page refuses politely if the persona
  // is wrong. Filtering the nav by persona would need the persona BEFORE the
  // first paint, which means either blocking the sidebar on a fetch or flashing
  // the wrong menu — and the pages themselves already explain the boundary.
  { id: 'dispatch',    path: '/dispatch',    label: 'Dispatch',     icon: 'ti-clipboard-list',   backing: 'live' },
  { id: 'mywork',      path: '/my-work',     label: 'My Work',      icon: 'ti-tool',             backing: 'live' },

  { section: 'Governance' },
  { id: 'changelog',   path: '/changelog',   label: 'Change Log',   icon: 'ti-history',          backing: 'live' },

  { section: 'Platform' },
  { id: 'marketplace', path: '/marketplace', label: 'Marketplace',  icon: 'ti-layout-grid',      backing: 'mock' },
  { id: 'hospital3d',  path: '/hospital-floorplan', label: 'Hospital 3D Plan', icon: 'ti-building-hospital', backing: 'live' },
]
