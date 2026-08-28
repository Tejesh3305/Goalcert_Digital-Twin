/**
 * assetPlacements.js — furniture & equipment layout for the hospital scene.
 *
 * Every `key` is an EXISTING prop from frontend/src/three/catalog.js, built by
 * buildProp() and scaled by the global PROP_SCALE (0.32) — no per-instance
 * rescaling, so all equipment keeps its calibrated real-world dimensions
 * (hospital bed 0.93×2.24 m, MRI Ø2.11×3.20 m, wheelchair 0.70×0.77 m…).
 *
 * pos: [x, z] plan metres (see floorPlan.js). rotY: radians. y: lift for
 * wall-mounted items. Orientation conventions of the prop library:
 * chairs/sofas/desks/cabinets have their BACK at local -Z, so
 * rotY 0 backs onto a north wall, π onto south, +π/2 west, -π/2 east.
 *
 * Placement rules observed: nothing inside a door-swing arc, ≥0.9 m of
 * working clearance around couches/equipment, corridors kept ≥1.5 m clear.
 */

const HPI = Math.PI / 2

export const PLACEMENTS = [
  // ── Consultant 1 ─────────────────────────────────────────────────────────
  { room: 'c1', key: 'desk',      pos: [2.4, 1.6],  rotY: Math.PI },
  { room: 'c1', key: 'chair',     pos: [2.4, 1.0],  rotY: 0 },
  { room: 'c1', key: 'chair',     pos: [3.6, 2.8],  rotY: -2.2 },
  { room: 'c1', key: 'chair',     pos: [4.3, 2.2],  rotY: -1.8 },
  { room: 'c1', key: 'examtable', pos: [0.85, 4.5], rotY: 0 },
  { room: 'c1', key: 'sink',      pos: [3.3, 6.1],  rotY: Math.PI },
  { room: 'c1', key: 'cabinet',   pos: [5.05, 3.2], rotY: -HPI },
  { room: 'c1', key: 'splitac',   pos: [2.6, 0.32], rotY: 0, y: 2.45, eq: 'AC-C1' },

  // ── Consultant 2 ─────────────────────────────────────────────────────────
  { room: 'c2', key: 'desk',      pos: [7.9, 1.6],  rotY: Math.PI },
  { room: 'c2', key: 'chair',     pos: [7.9, 1.0],  rotY: 0 },
  { room: 'c2', key: 'chair',     pos: [9.1, 2.8],  rotY: -2.2 },
  { room: 'c2', key: 'chair',     pos: [9.7, 2.2],  rotY: -1.8 },
  { room: 'c2', key: 'examtable', pos: [6.25, 4.5], rotY: 0 },
  { room: 'c2', key: 'sink',      pos: [8.6, 6.1],  rotY: Math.PI },
  { room: 'c2', key: 'cabinet',   pos: [10.25, 3.2], rotY: -HPI },
  { room: 'c2', key: 'splitac',   pos: [8.0, 0.32], rotY: 0, y: 2.45, eq: 'AC-C2' },

  // ── Consultant 3 ─────────────────────────────────────────────────────────
  { room: 'c3', key: 'desk',      pos: [13.0, 1.6],  rotY: Math.PI },
  { room: 'c3', key: 'chair',     pos: [13.0, 1.0],  rotY: 0 },
  { room: 'c3', key: 'chair',     pos: [14.2, 2.8],  rotY: -2.2 },
  { room: 'c3', key: 'chair',     pos: [14.8, 2.2],  rotY: -1.8 },
  { room: 'c3', key: 'examtable', pos: [11.45, 4.5], rotY: 0 },
  { room: 'c3', key: 'sink',      pos: [13.6, 6.1],  rotY: Math.PI },
  { room: 'c3', key: 'cabinet',   pos: [15.35, 3.2], rotY: -HPI },
  { room: 'c3', key: 'splitac',   pos: [13.1, 0.32], rotY: 0, y: 2.45, eq: 'AC-C3' },

  // ── Plant (north) ────────────────────────────────────────────────────────
  { room: 'plantN', key: 'ahu',    pos: [16.85, 1.3], rotY: 0, eq: 'AHU-N1' },
  { room: 'plantN', key: 'ahu',    pos: [16.85, 2.9], rotY: 0, eq: 'AHU-N2' },
  { room: 'plantN', key: 'pump',   pos: [16.5, 4.3],  rotY: 0, eq: 'PUMP-1' },
  { room: 'plantN', key: 'boiler', pos: [17.3, 4.8],  rotY: -HPI, eq: 'BLR-1' },

  // ── North corridor ───────────────────────────────────────────────────────
  { room: 'vcorrN', key: 'whiteboard',   pos: [18.11, 3.6], rotY: HPI },
  { room: 'vcorrN', key: 'extinguisher', pos: [21.25, 5.9], rotY: -HPI },
  { room: 'vcorrN', key: 'exitsign',     pos: [19.7, 0.35], rotY: 0, y: 0.9 },

  // ── MRI 1 Equip ──────────────────────────────────────────────────────────
  { room: 'mriEquip', key: 'ahu',        pos: [23.2, 0.9],  rotY: 0, eq: 'AHU-EQ' },
  { room: 'mriEquip', key: 'switchgear', pos: [25.3, 0.65], rotY: 0, eq: 'SWG-EQ' },
  { room: 'mriEquip', key: 'ups',        pos: [26.4, 1.0],  rotY: -HPI, eq: 'UPS-EQ' },

  // ── WC (north) ───────────────────────────────────────────────────────────
  { room: 'wcN', key: 'toilet', pos: [22.9, 6.0], rotY: Math.PI },
  { room: 'wcN', key: 'sink',   pos: [22.5, 3.7], rotY: 0 },

  // ── Cannulation ──────────────────────────────────────────────────────────
  { room: 'cann', key: 'examtable',      pos: [24.35, 4.85], rotY: 0 },
  { room: 'cann', key: 'ivstand',        pos: [25.15, 4.0],  rotY: 0 },
  { room: 'cann', key: 'patientmonitor', pos: [25.9, 3.85],  rotY: Math.PI, eq: 'MON-CANN' },
  { room: 'cann', key: 'medcart',        pos: [26.5, 4.6],   rotY: -HPI },
  { room: 'cann', key: 'sink',           pos: [26.5, 5.95],  rotY: Math.PI },

  // ── MRI 1 (magnet room) ──────────────────────────────────────────────────
  { room: 'mri1', key: 'mri',     pos: [31.9, 3.1],  rotY: -HPI, eq: 'MRI-1' },  // table toward the door
  { room: 'mri1', key: 'cabinet', pos: [33.9, 0.55], rotY: 0 },
  { room: 'mri1', key: 'cabinet', pos: [34.9, 0.55], rotY: 0 },
  { room: 'mri1', key: 'gas',     pos: [35.6, 3.4],  rotY: -HPI, eq: 'GAS-MRI' },

  // ── MRI Control ──────────────────────────────────────────────────────────
  { room: 'mriCtrl', key: 'desk',    pos: [31.9, 6.9],  rotY: 0 },   // operators face the window
  { room: 'mriCtrl', key: 'desk',    pos: [33.3, 6.9],  rotY: 0 },
  { room: 'mriCtrl', key: 'chair',   pos: [31.9, 7.75], rotY: Math.PI },
  { room: 'mriCtrl', key: 'chair',   pos: [33.3, 7.75], rotY: Math.PI },
  { room: 'mriCtrl', key: 'cabinet', pos: [35.5, 8.35], rotY: Math.PI },
  { room: 'mriCtrl', key: 'printer', pos: [34.9, 6.85], rotY: 0 },

  // ── Store ────────────────────────────────────────────────────────────────
  { room: 'store', key: 'bookshelf', pos: [0.8, 6.65],  rotY: 0 },
  { room: 'store', key: 'locker',    pos: [1.1, 8.55],  rotY: Math.PI },
  { room: 'store', key: 'cabinet',   pos: [2.1, 6.75],  rotY: -HPI },
  { room: 'store', key: 'box',       pos: [0.5, 8.0],   rotY: 0.4 },

  // ── Main corridor ────────────────────────────────────────────────────────
  { room: 'corr', key: 'chair',        pos: [11.6, 6.72], rotY: 0 },
  { room: 'corr', key: 'chair',        pos: [12.3, 6.72], rotY: 0 },
  { room: 'corr', key: 'plant',        pos: [17.7, 6.78], rotY: 0 },
  { room: 'corr', key: 'whiteboard',   pos: [11.9, 8.72], rotY: Math.PI },
  { room: 'corr', key: 'extinguisher', pos: [2.55, 6.6],  rotY: HPI },
  { room: 'corr', key: 'exitsign',     pos: [4.4, 8.72],  rotY: Math.PI, y: 0.9 },
  { room: 'corr', key: 'exitsign',     pos: [23.9, 7.6],  rotY: HPI, y: 0.9 },

  // ── MRI Lobby ────────────────────────────────────────────────────────────
  { room: 'lobby', key: 'locker', pos: [28.9, 8.45], rotY: Math.PI },  // patient belongings
  { room: 'lobby', key: 'chair',  pos: [24.6, 8.4],  rotY: Math.PI },
  { room: 'lobby', key: 'chair',  pos: [25.3, 8.4],  rotY: Math.PI },
  { room: 'lobby', key: 'plant',  pos: [24.4, 6.75], rotY: 0 },

  // ── Change rooms + Acc. Change ───────────────────────────────────────────
  { room: 'change1', key: 'locker', pos: [26.6, 9.6],  rotY: -HPI },
  { room: 'change1', key: 'stool',  pos: [25.1, 9.6],  rotY: 0 },
  { room: 'change2', key: 'locker', pos: [26.6, 11.2], rotY: -HPI },
  { room: 'change2', key: 'stool',  pos: [25.1, 11.2], rotY: 0 },
  { room: 'accCh',   key: 'locker', pos: [22.5, 17.3], rotY: Math.PI },
  { room: 'accCh',   key: 'stool',  pos: [21.6, 16.2], rotY: 0 },
  { room: 'accCh',   key: 'sink',   pos: [23.55, 15.3], rotY: 0 },

  // ── CT waiting corridor ──────────────────────────────────────────────────
  { room: 'vcorrS', key: 'chair',      pos: [21.3, 13.3],  rotY: HPI },
  { room: 'vcorrS', key: 'chair',      pos: [21.3, 13.95], rotY: HPI },
  { room: 'vcorrS', key: 'chair',      pos: [23.7, 13.5],  rotY: -HPI },
  { room: 'vcorrS', key: 'wheelchair', pos: [21.4, 9.4],   rotY: -HPI },
  { room: 'vcorrS', key: 'stretcher',  pos: [21.5, 10.9],  rotY: 0 },
  { room: 'vcorrS', key: 'wheelchair', pos: [26.3, 17.0],  rotY: HPI },
  { room: 'vcorrS', key: 'plant',      pos: [24.5, 12.5],  rotY: 0 },

  // ── CT room ──────────────────────────────────────────────────────────────
  { room: 'ct', key: 'ctscanner',      pos: [31.9, 11.2], rotY: -HPI, eq: 'CT-1' },  // couch toward the door
  { room: 'ct', key: 'crashcart',      pos: [35.4, 9.3],  rotY: -HPI },
  { room: 'ct', key: 'cabinet',        pos: [28.2, 9.15], rotY: 0 },
  { room: 'ct', key: 'ivstand',        pos: [29.4, 9.4],  rotY: 0 },
  { room: 'ct', key: 'patientmonitor', pos: [34.5, 12.9], rotY: -HPI, eq: 'MON-CT' },
  { room: 'ct', key: 'gas',            pos: [35.6, 11.2], rotY: -HPI, eq: 'GAS-CT' },

  // ── CT Control ───────────────────────────────────────────────────────────
  { room: 'ctCtrl', key: 'desk',    pos: [29.3, 14.3],  rotY: 0 },   // under the control window
  { room: 'ctCtrl', key: 'desk',    pos: [30.6, 14.3],  rotY: 0 },
  { room: 'ctCtrl', key: 'chair',   pos: [29.3, 15.0],  rotY: Math.PI },
  { room: 'ctCtrl', key: 'chair',   pos: [30.6, 15.0],  rotY: Math.PI },
  { room: 'ctCtrl', key: 'cabinet', pos: [27.5, 17.25], rotY: Math.PI },
  { room: 'ctCtrl', key: 'printer', pos: [31.5, 16.9],  rotY: Math.PI },
  { room: 'ctCtrl', key: 'plant',   pos: [27.2, 15.8],  rotY: 0 },

  // ── Plant (south) ────────────────────────────────────────────────────────
  { room: 'plantS', key: 'ahu',        pos: [32.9, 15.2], rotY: 0, eq: 'AHU-S1' },
  { room: 'plantS', key: 'ahu',        pos: [34.8, 15.2], rotY: 0, eq: 'AHU-S2' },
  { room: 'plantS', key: 'switchgear', pos: [35.5, 16.5], rotY: -HPI, eq: 'SWG-S' },
  { room: 'plantS', key: 'ups',        pos: [32.6, 17.1], rotY: Math.PI, eq: 'UPS-S' },

  // ── Office ───────────────────────────────────────────────────────────────
  { room: 'office', key: 'desk',      pos: [8.0, 9.8],   rotY: 0 },
  { room: 'office', key: 'chair',     pos: [8.0, 10.45], rotY: Math.PI },
  { room: 'office', key: 'desk',      pos: [8.0, 11.9],  rotY: Math.PI },
  { room: 'office', key: 'chair',     pos: [8.0, 11.25], rotY: 0 },
  { room: 'office', key: 'cabinet',   pos: [8.7, 12.5],  rotY: Math.PI },
  { room: 'office', key: 'bookshelf', pos: [6.75, 12.55], rotY: Math.PI },
  { room: 'office', key: 'printer',   pos: [6.6, 11.6],  rotY: HPI },

  // ── Accessible WC ────────────────────────────────────────────────────────
  { room: 'accWC', key: 'toilet', pos: [10.55, 10.55], rotY: Math.PI },
  { room: 'accWC', key: 'sink',   pos: [9.5, 10.3],    rotY: HPI },

  // ── Staff Room ───────────────────────────────────────────────────────────
  { room: 'staff', key: 'kitchen',     pos: [9.63, 12.9],  rotY: HPI },
  { room: 'staff', key: 'fridge',      pos: [9.6, 11.35],  rotY: HPI },
  { room: 'staff', key: 'table',       pos: [11.2, 13.0],  rotY: 0 },
  { room: 'staff', key: 'chair',       pos: [11.2, 12.35], rotY: 0 },
  { room: 'staff', key: 'chair',       pos: [11.2, 13.65], rotY: Math.PI },
  { room: 'staff', key: 'chair',       pos: [10.15, 13.0], rotY: HPI },
  { room: 'staff', key: 'watercooler', pos: [12.15, 12.1], rotY: -HPI },

  // ── Central Waiting ──────────────────────────────────────────────────────
  { room: 'waitC', key: 'sofa',        pos: [13.9, 10.6],  rotY: Math.PI },
  { room: 'waitC', key: 'chair',       pos: [16.55, 9.5],  rotY: -HPI },
  { room: 'waitC', key: 'chair',       pos: [16.55, 10.2], rotY: -HPI },
  { room: 'waitC', key: 'coffeetable', pos: [13.9, 9.8],   rotY: 0 },
  { room: 'waitC', key: 'plant',       pos: [11.35, 9.15], rotY: 0 },
  { room: 'waitC', key: 'tv',          pos: [11.11, 9.9],  rotY: HPI, y: 0.5 },

  // ── Utility ──────────────────────────────────────────────────────────────
  { room: 'utility', key: 'sink',    pos: [19.6, 9.1],   rotY: 0 },
  { room: 'utility', key: 'cabinet', pos: [20.7, 9.6],   rotY: -HPI },
  { room: 'utility', key: 'locker',  pos: [19.6, 12.45], rotY: Math.PI },
  { room: 'utility', key: 'box',     pos: [20.5, 11.4],  rotY: 0.2 },
  { room: 'utility', key: 'box',     pos: [18.75, 11.9], rotY: -0.3 },

  // ── Consultant 4 ─────────────────────────────────────────────────────────
  { room: 'c4', key: 'desk',      pos: [19.5, 13.9],  rotY: Math.PI },
  { room: 'c4', key: 'chair',     pos: [19.5, 13.35], rotY: 0 },
  { room: 'c4', key: 'chair',     pos: [18.2, 14.8],  rotY: 1.9 },
  { room: 'c4', key: 'chair',     pos: [17.75, 15.5], rotY: 1.5 },
  { room: 'c4', key: 'examtable', pos: [20.45, 16.2], rotY: Math.PI },
  { room: 'c4', key: 'sink',      pos: [17.4, 17.25], rotY: Math.PI },
  { room: 'c4', key: 'splitac',   pos: [19.0, 17.28], rotY: Math.PI, y: 2.45, eq: 'AC-C4' },

  // ── Consultant 5 (L-shaped, exam alcove) ─────────────────────────────────
  { room: 'c5', key: 'desk',      pos: [14.3, 12.6],  rotY: Math.PI },
  { room: 'c5', key: 'chair',     pos: [14.3, 12.0],  rotY: 0 },
  { room: 'c5', key: 'chair',     pos: [15.9, 13.1],  rotY: -1.6 },
  { room: 'c5', key: 'chair',     pos: [16.3, 12.4],  rotY: -1.9 },
  { room: 'c5', key: 'examtable', pos: [10.6, 16.3],  rotY: HPI },
  { room: 'c5', key: 'sink',      pos: [12.0, 17.3],  rotY: Math.PI },
  { room: 'c5', key: 'cabinet',   pos: [16.55, 15.0], rotY: -HPI },
  { room: 'c5', key: 'plant',     pos: [12.75, 11.5], rotY: 0 },
  { room: 'c5', key: 'splitac',   pos: [14.0, 17.28], rotY: Math.PI, y: 2.45, eq: 'AC-C5' },

  // ── Reception / Waiting ──────────────────────────────────────────────────
  { room: 'recep', key: 'reception',   pos: [5.2, 12.2],  rotY: -HPI },  // counter faces the waiting area
  { room: 'recep', key: 'chair',       pos: [5.75, 11.9], rotY: -HPI },
  { room: 'recep', key: 'chair',       pos: [5.75, 12.6], rotY: -HPI },
  { room: 'recep', key: 'printer',     pos: [5.8, 13.7],  rotY: -HPI },
  { room: 'recep', key: 'sofa',        pos: [0.65, 9.9],  rotY: HPI },
  { room: 'recep', key: 'sofa',        pos: [0.65, 12.0], rotY: HPI },
  { room: 'recep', key: 'sofa',        pos: [0.65, 14.1], rotY: HPI },
  { room: 'recep', key: 'coffeetable', pos: [1.7, 10.4],  rotY: HPI },
  { room: 'recep', key: 'coffeetable', pos: [1.7, 13.4],  rotY: HPI },
  { room: 'recep', key: 'chair',       pos: [0.9, 16.9],  rotY: Math.PI },
  { room: 'recep', key: 'chair',       pos: [1.6, 16.9],  rotY: Math.PI },
  { room: 'recep', key: 'plant',       pos: [2.3, 9.1],   rotY: 0 },
  { room: 'recep', key: 'plant',       pos: [8.75, 17.2], rotY: 0 },
  { room: 'recep', key: 'watercooler', pos: [8.8, 13.05], rotY: 0 },
  { room: 'recep', key: 'tv',          pos: [7.7, 12.86], rotY: 0, y: 0.5 },
  { room: 'recep', key: 'wheelchair',  pos: [5.6, 16.8],  rotY: Math.PI },
  { room: 'recep', key: 'exitsign',    pos: [3.9, 17.25], rotY: Math.PI, y: 0.9 },
]

/** Props that never become collision obstacles (wall/ceiling mounted, signage). */
export const NO_COLLIDE = new Set([
  'ceilinglight', 'exitsign', 'whiteboard', 'tv', 'splitac', 'rug',
])

/**
 * Warm ceiling point lights (no shadows) — kept to ~16 for performance;
 * everything else is lit by ambient/hemisphere + emissive fixtures.
 */
export const POINT_LIGHTS = [
  [7.0, 7.6], [14.0, 7.6], [21.0, 7.6],          // main corridor
  [19.7, 2.6],                                    // north corridor
  [26.8, 7.6],                                    // MRI lobby
  [22.5, 12.6], [25.8, 15.6],                     // CT waiting corridor
  [3.2, 12.6], [4.6, 16.0],                       // reception / waiting
  [13.9, 9.9],                                    // central waiting
  [2.7, 3.2], [8.0, 3.2], [13.15, 3.2],           // consultants 1-3
  [14.6, 14.5], [18.9, 15.2],                     // consultants 4-5
  [31.5, 3.2], [31.4, 11.2],                      // MRI 1, CT
]
