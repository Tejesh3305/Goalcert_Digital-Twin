/**
 * persona/nav.js — a different application per role, expressed as data.
 *
 * Signing in as a supervisor and signing in as an operator now load genuinely
 * different products, not the same product with two extra menu items. This file
 * is where that difference is declared, because the alternative — `persona ===
 * 'supervisor' && <NavItem/>` sprinkled through the shell — puts the same
 * decision in a dozen places and guarantees they drift.
 *
 * WHAT EACH ROLE ACTUALLY NEEDS
 * -----------------------------
 * A supervisor is at a desk deciding who fixes what. They need the twin's own
 * views — the dashboard, prediction, the change log — because that is how you
 * understand a fault well enough to dispatch it, and they need the whole twin
 * switcher because they cover a site rather than a machine.
 *
 * An operator is standing in front of the machine, usually on a phone. They need
 * the job in front of them, the work order that says what it is, and the
 * procedure that fixes it. A twin switcher, an entity count and a "Build a
 * Twin" wizard are not merely unnecessary there — they are noise between the
 * operator and the only screens that matter on a shift.
 *
 * So the operator's list is deliberately SHORT. Resisting the urge to add "just
 * one more useful page" to it is most of the design.
 *
 * `home` is where that persona lands on sign-in and where a redirect sends them
 * when they reach a page belonging to the other role.
 */

/** Nav entries shared by anyone who is allowed to look at the twin itself. */
const TWIN_VIEWS = [
  // `Twin` is the MONITORING page — the 3-D model, the telemetry, the live
  // findings. `Twin Library` is the catalogue of twins to switch between. The
  // role's own dashboard is separate and lives at `/`.
  { id: 'twin', path: '/twin', label: 'Twin', icon: 'ti-box' },
  { id: 'twin-library', path: '/twin-library', label: 'Twin Library', icon: 'ti-stack-2' },
  { id: 'predict', path: '/predict', label: 'Prediction', icon: 'ti-chart-histogram' },
  { id: 'changelog', path: '/changelog', label: 'Change Log', icon: 'ti-history' },
]

export const PERSONA_NAV = {
  supervisor: {
    shell: 'command',
    home: '/',
    label: 'Command',
    tagline: 'Dispatch faults to your team',
    accent: 'supervisor',
    nav: [
      { section: 'My shift' },
      { id: 'dashboard', path: '/', label: 'Dashboard', icon: 'ti-layout-dashboard' },
      { id: 'dispatch', path: '/dispatch', label: 'Dispatch', icon: 'ti-clipboard-list' },

      { section: 'The twin' },
      ...TWIN_VIEWS,
      { id: 'copilot', path: '/copilot', label: 'Twin Copilot', icon: 'ti-sparkles' },

      { section: 'Platform' },
      { id: 'concierge', path: '/build', label: 'Build a Twin', icon: 'ti-sparkles' },
      { id: 'marketplace', path: '/marketplace', label: 'Marketplace', icon: 'ti-layout-grid' },
    ],
  },

  frontline: {
    shell: 'field',
    home: '/',
    label: 'Field',
    tagline: 'Your jobs, and how to fix them',
    accent: 'frontline',
    // Three destinations, and no more: today's work, the drills, and the
    // record of what has been closed. Everything else an operator needs —
    // the work order, the procedure, the write-up — is reached FROM a job
    // card, because each is about one specific fault and belongs behind it
    // rather than beside it in a tab bar.
    nav: [
      { section: 'My shift' },
      // ONE page for the day's work. Dashboard and a separate "Today" listed the
      // same jobs twice and made the operator choose between two doors to the
      // same room; the dashboard IS today's assignment now.
      // Progress used to be a third entry here. It held the XP ledger and the
      // run history — the numbers that are supposed to motivate the work — on a
      // page you had to remember to open, so they were never on screen while
      // the work was being chosen. The dashboard shows standing and workload
      // together now, and `/progress` redirects to it.
      { id: 'dashboard', path: '/', label: 'Dashboard', icon: 'ti-clipboard-check' },
      { id: 'training', path: '/training', label: 'Training', icon: 'ti-school' },

      // The operator needs the twin too. Every procedure they run is a
      // rehearsal on a real asset, and the drill page shows that asset's live
      // state — so being able to open the twin properly, and switch between
      // twins, is part of the job rather than a supervisor's privilege.
      { section: 'The twin' },
      { id: 'twin', path: '/twin', label: 'Twin', icon: 'ti-box' },
      { id: 'twin-library', path: '/twin-library', label: 'Twin Library', icon: 'ti-stack-2' },
    ],
  },
}

/**
 * The full nav, for a session with NO persona.
 *
 * This is the dev-mode and no-membership path (`./start.ps1` without -Secure
 * leaves the API open and nobody signed in). It keeps the twin exactly as it was
 * before personas existed, so running locally with auth off is unchanged — a
 * developer must not have to provision an account to look at a dashboard.
 */
export const FULL_NAV = {
  shell: 'command',
  home: '/twin',
  label: 'NextXR',
  tagline: 'Universal modular digital twin',
  accent: 'default',
  nav: [
    { section: 'Overview' },
    { id: 'twin', path: '/twin', label: 'Twin', icon: 'ti-box' },
    { id: 'twin-library', path: '/twin-library', label: 'Twin Library', icon: 'ti-stack-2' },
    { id: 'concierge', path: '/build', label: 'Build a Twin', icon: 'ti-sparkles' },

    { section: 'Intelligence' },
    { id: 'copilot', path: '/copilot', label: 'Twin Copilot', icon: 'ti-sparkles' },
    { id: 'predict', path: '/predict', label: 'Prediction', icon: 'ti-chart-histogram' },

    { section: 'Work' },
    { id: 'dispatch', path: '/dispatch', label: 'Dispatch', icon: 'ti-clipboard-list' },
    { id: 'mywork', path: '/my-work', label: 'My Work', icon: 'ti-tool' },

    { section: 'Governance' },
    { id: 'changelog', path: '/changelog', label: 'Change Log', icon: 'ti-history' },

    { section: 'Platform' },
    { id: 'marketplace', path: '/marketplace', label: 'Marketplace', icon: 'ti-layout-grid' },
    { id: 'hospital3d', path: '/hospital-floorplan', label: 'Hospital 3D Plan', icon: 'ti-building-hospital' },
  ],
}

/** The workspace config for a persona. Unknown or absent falls back to FULL_NAV. */
export function workspaceFor(persona) {
  return PERSONA_NAV[persona] || FULL_NAV
}

/**
 * Paths that belong to exactly one persona, and who owns them.
 *
 * Used by the route guard to redirect rather than to 403. A supervisor who opens
 * an operator's deep link should land somewhere useful, not on a refusal — the
 * refusal panels still exist underneath as defence, but a role-based UI should
 * rarely reach them.
 */
export const OWNED_PATHS = [
  { prefix: '/dispatch', persona: 'supervisor' },
  { prefix: '/my-work', persona: 'frontline' },
  { prefix: '/repair/', persona: 'frontline' },
  { prefix: '/fix/', persona: 'frontline' },
  { prefix: '/progress', persona: 'frontline' },
  { prefix: '/training', persona: 'frontline' },
  { prefix: '/drill/', persona: 'frontline' },
  // '/order/' is NOT owned: a supervisor reads the same work order when
  // reviewing what an operator did, and owning it here would redirect them away
  // from a page they are entitled to.
]

export function ownerOf(pathname) {
  const match = OWNED_PATHS.find((p) => pathname.startsWith(p.prefix))
  return match ? match.persona : null
}
