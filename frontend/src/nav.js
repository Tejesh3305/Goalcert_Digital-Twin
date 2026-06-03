/**
 * nav.js — sidebar navigation config + per-route "backing" status.
 *
 * `backing` documents, for YOUR reference and the in-UI demo tag, how real each
 * panel is against the current core:
 *   'live'  — fully wired to the platform backend (real graph data)
 *   'partial' — partly real, partly mocked (a placeholder fills the gap)
 *   'mock'  — UI placeholder only; no core support yet (see PLACEHOLDERS.md)
 */
export const NAV = [
  { section: 'Overview' },
  { id: 'dashboard',   path: '/',            label: 'Dashboard',   icon: 'ti-layout-dashboard', backing: 'live' },
  { id: 'concierge',   path: '/build',       label: 'Build a Twin', icon: 'ti-sparkles',        backing: 'live' },
  { id: 'twins',       path: '/twins',       label: 'Twins',       icon: 'ti-stack-2',          backing: 'live' },
  { id: 'assets',      path: '/assets',      label: 'Asset Graph', icon: 'ti-sitemap',          backing: 'live' },
  { id: 'live',        path: '/live',        label: 'Live Ops',    icon: 'ti-activity',         backing: 'live' },

  { section: 'Intelligence' },
  { id: 'bundle',      path: '/bundle-author', label: 'Bundle Author', icon: 'ti-wand',         backing: 'live' },
  { id: 'agents',      path: '/agents',      label: 'AI Agents',   icon: 'ti-robot',            backing: 'partial' },
  { id: 'predict',     path: '/predict',     label: 'Predict',     icon: 'ti-trending-up',      backing: 'mock' },
  { id: 'copilot',     path: '/copilot',     label: 'Copilot',     icon: 'ti-message-chatbot',  backing: 'mock' },

  { section: 'Governance' },
  { id: 'changelog',   path: '/changelog',   label: 'Change Log',  icon: 'ti-history',          backing: 'live' },
  { id: 'compliance',  path: '/compliance',  label: 'Compliance',  icon: 'ti-shield-check',     backing: 'mock' },

  { section: 'Platform' },
  { id: 'plugin',      path: '/plugin',      label: 'Plugin SDK',  icon: 'ti-plug',             backing: 'live' },
  { id: 'accelerator', path: '/accelerator', label: 'Accelerators', icon: 'ti-package',         backing: 'live' },
  { id: 'simulation',  path: '/simulation',  label: 'Simulation',  icon: 'ti-urgent',           backing: 'mock' },
  { id: 'health',      path: '/health',      label: 'Twin Health', icon: 'ti-heart-rate-monitor', backing: 'partial' },
  { id: 'marketplace', path: '/marketplace', label: 'Marketplace', icon: 'ti-layout-grid',      backing: 'mock' },
]
