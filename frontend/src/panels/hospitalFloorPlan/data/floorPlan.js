/**
 * floorPlan.js — data-driven floor plan for the walkable hospital scene.
 *
 * Source of truth: the reference imaging-suite floor plan (Consultants 1-5,
 * Reception/Waiting, MRI 1 + Equip + Control, CT + Control, Cannulation,
 * Change rooms, Staff Room, Office, Store, Utility, Plant ×2, WCs).
 *
 * Units: 1 unit = 1 metre. Plan coordinates: x 0→36 left→right (west→east),
 * z 0→17.6 top→bottom of the plan (north→south). The scene renders the whole
 * plan inside a group offset by (-W/2, 0, -D/2) so the world origin is the
 * building centre. This file is pure data (no three.js) so it can be checked
 * by `node validatePlan.mjs`.
 *
 * Calibration: plan image is ~2:1; 36 m width puts every space at standard
 * healthcare sizes — corridors 2.4 m, single doors 0.9 m, patient doors 1.4 m,
 * MRI suite 9.0 × 6.4 m, consult rooms ~5.2 × 6.4 m.
 */

export const PLAN = {
  width: 36.0,          // overall building width (m), west→east
  depth: 17.6,          // overall building depth (m), north→south
  wallExt: 0.3,         // exterior wall thickness
  wallInt: 0.15,        // interior wall thickness
  ceil: 3.0,            // floor-to-ceiling height
  doorH: 2.1,           // structural door opening height
  openH: 2.4,           // open archway header height
  winSill: 1.0,         // exterior window sill height
  winHead: 2.2,         // exterior window head height
  glassSill: 0.9,       // internal control-window sill
  glassHead: 2.1,       // internal control-window head
  eyeHeight: 1.65,      // first-person camera height
}

// World offset: plan coords + OFFSET = world coords (building centred on origin).
export const OFFSET = { x: -PLAN.width / 2, z: -PLAN.depth / 2 }

// Room floor palette — follows the reference plan's colour legend.
export const ROOM_COLORS = {
  consult:  '#6fb5a5',  // teal — consultant/exam rooms
  reception:'#9cc8e0',  // light blue — reception & waiting
  imaging:  '#27436a',  // navy — MRI zone (RF-shielded suite)
  ct:       '#8db8d8',  // light blue — CT room
  control:  '#7dabcc',  // mid blue — CT control
  sanitary: '#e3c470',  // yellow — WC / change / cannulation
  plant:    '#a3c497',  // green — plant rooms
  support:  '#bdd5b6',  // pale green — office / staff / store / utility
  corridor: '#eed8bb',  // pale peach — circulation
  waiting:  '#f2e3c8',  // lighter peach — open waiting zones
}

/**
 * Rooms. Each room: { id, label, type, poly } with poly a simple polygon of
 * [x, z] plan points (m). Rooms tile the full 36 × 17.6 envelope exactly;
 * walls sit ON the shared boundary lines (centreline convention).
 */
export const ROOMS = [
  // ── North band ────────────────────────────────────────────────────────────
  { id: 'c1',       label: 'Consultant 1',  type: 'consult',
    poly: [[0, 0], [5.4, 0], [5.4, 6.4], [0, 6.4]] },
  { id: 'c2',       label: 'Consultant 2',  type: 'consult',
    poly: [[5.4, 0], [10.6, 0], [10.6, 6.4], [5.4, 6.4]] },
  { id: 'c3',       label: 'Consultant 3',  type: 'consult',
    poly: [[10.6, 0], [15.7, 0], [15.7, 6.4], [10.6, 6.4]] },
  { id: 'plantN',   label: 'Plant',         type: 'plant',
    poly: [[15.7, 0], [18.0, 0], [18.0, 6.4], [15.7, 6.4]] },
  { id: 'vcorrN',   label: 'Corridor',      type: 'corridor',
    poly: [[18.0, 0], [21.4, 0], [21.4, 6.4], [18.0, 6.4]] },
  { id: 'mriEquip', label: 'MRI 1 Equip',   type: 'imaging',
    poly: [[21.4, 0], [27.0, 0], [27.0, 3.4], [21.4, 3.4]] },
  { id: 'wcN',      label: 'WC',            type: 'sanitary',
    poly: [[21.4, 3.4], [23.4, 3.4], [23.4, 6.4], [21.4, 6.4]] },
  { id: 'cann',     label: 'Cannulation',   type: 'sanitary',
    poly: [[23.4, 3.4], [27.0, 3.4], [27.0, 6.4], [23.4, 6.4]] },
  // MRI 1 — SW corner chamfered toward the MRI lobby (as drawn on the plan).
  { id: 'mri1',     label: 'MRI 1',         type: 'imaging',
    poly: [[27.0, 0], [36.0, 0], [36.0, 6.4], [29.6, 6.4], [27.0, 3.8]] },

  // ── Corridor band ────────────────────────────────────────────────────────
  { id: 'store',    label: 'Store',         type: 'support',
    poly: [[0, 6.4], [2.4, 6.4], [2.4, 8.8], [0, 8.8]] },
  { id: 'corr',     label: 'Corridor',      type: 'corridor',
    poly: [[2.4, 6.4], [24.0, 6.4], [24.0, 8.8], [2.4, 8.8]] },
  // MRI lobby = rect + the chamfer triangle taken out of MRI 1.
  { id: 'lobby',    label: 'MRI Lobby',     type: 'waiting',
    poly: [[24.0, 8.8], [24.0, 6.4], [27.0, 6.4], [27.0, 3.8], [29.6, 6.4], [29.6, 8.8]] },
  { id: 'mriCtrl',  label: 'MRI Control',   type: 'imaging',
    poly: [[29.6, 6.4], [36.0, 6.4], [36.0, 8.8], [29.6, 8.8]] },

  // ── South band ───────────────────────────────────────────────────────────
  // Reception/Waiting is L-shaped: full-depth strip on the west + the wide
  // waiting area wrapping under the Office toward the entrance.
  { id: 'recep',    label: 'Reception / Waiting', type: 'reception',
    poly: [[0, 8.8], [6.2, 8.8], [6.2, 12.8], [9.2, 12.8], [9.2, 17.6], [0, 17.6]] },
  { id: 'office',   label: 'Office',        type: 'support',
    poly: [[6.2, 8.8], [9.2, 8.8], [9.2, 12.8], [6.2, 12.8]] },
  { id: 'accWC',    label: 'Acc. WC',       type: 'sanitary',
    poly: [[9.2, 8.8], [11.0, 8.8], [11.0, 11.0], [9.2, 11.0]] },
  { id: 'staff',    label: 'Staff Room',    type: 'support',
    poly: [[9.2, 11.0], [12.4, 11.0], [12.4, 14.4], [9.2, 14.4]] },
  { id: 'waitC',    label: 'Waiting',       type: 'waiting',
    poly: [[11.0, 8.8], [16.9, 8.8], [16.9, 11.0], [11.0, 11.0]] },
  // Consultant 5 is L-shaped: main room + exam alcove below the Staff Room.
  { id: 'c5',       label: 'Consultant 5',  type: 'consult',
    poly: [[12.4, 11.0], [16.9, 11.0], [16.9, 17.6], [9.2, 17.6], [9.2, 14.4], [12.4, 14.4]] },
  { id: 'passage',  label: '',              type: 'corridor',
    poly: [[16.9, 8.8], [18.2, 8.8], [18.2, 12.8], [16.9, 12.8]] },
  { id: 'utility',  label: 'Utility',       type: 'support',
    poly: [[18.2, 8.8], [21.0, 8.8], [21.0, 12.8], [18.2, 12.8]] },
  { id: 'c4',       label: 'Consultant 4',  type: 'consult',
    poly: [[16.9, 12.8], [21.0, 12.8], [21.0, 17.6], [16.9, 17.6]] },

  // ── South-east (CT wing) ─────────────────────────────────────────────────
  // CT patient corridor: L-shape past the change rooms with the small
  // Waiting zone, ending at Acc. Change / CT / CT Control doors.
  { id: 'vcorrS',   label: 'Waiting',       type: 'waiting',
    poly: [[21.0, 8.8], [24.0, 8.8], [24.0, 12.0], [26.9, 12.0], [26.9, 17.6], [24.0, 17.6], [24.0, 15.0], [21.0, 15.0]] },
  { id: 'change1',  label: 'Change',        type: 'sanitary',
    poly: [[24.0, 8.8], [26.9, 8.8], [26.9, 10.4], [24.0, 10.4]] },
  { id: 'change2',  label: 'Change',        type: 'sanitary',
    poly: [[24.0, 10.4], [26.9, 10.4], [26.9, 12.0], [24.0, 12.0]] },
  { id: 'accCh',    label: 'Acc. Change',   type: 'sanitary',
    poly: [[21.0, 15.0], [24.0, 15.0], [24.0, 17.6], [21.0, 17.6]] },
  { id: 'ct',       label: 'CT',            type: 'ct',
    poly: [[26.9, 8.8], [36.0, 8.8], [36.0, 13.6], [26.9, 13.6]] },
  { id: 'ctCtrl',   label: 'CT Control',    type: 'control',
    poly: [[26.9, 13.6], [31.9, 13.6], [31.9, 17.6], [26.9, 17.6]] },
  { id: 'plantS',   label: 'Plant',         type: 'plant',
    poly: [[31.9, 13.6], [36.0, 13.6], [36.0, 17.6], [31.9, 17.6]] },
]

/**
 * Walls. Each wall runs a→b along its centreline (plan coords, m).
 *   t: thickness · ext: exterior (full-height + windows allowed)
 *   openings: [{ at, w, kind, label?, hinge? }]
 *     at: distance from `a` to the opening CENTRE along the wall
 *     kind: 'door' 0.9 m leaf · 'wide' 1.4 m patient leaf · 'double' pair ·
 *           'open' archway (header only) · 'win' exterior window ·
 *           'glass' internal control window
 *     label: room the door serves (rendered as a wall sign).
 */
export const WALLS = [
  // ── Exterior ─────────────────────────────────────────────────────────────
  { id: 'extN', a: [0, 0], b: [36, 0], t: 0.3, ext: true, openings: [
    { at: 1.8,  w: 1.5, kind: 'win' }, { at: 3.9,  w: 1.5, kind: 'win' },
    { at: 7.0,  w: 1.5, kind: 'win' }, { at: 9.0,  w: 1.5, kind: 'win' },
    { at: 12.2, w: 1.5, kind: 'win' }, { at: 14.2, w: 1.5, kind: 'win' },
    { at: 19.7, w: 1.8, kind: 'double', label: 'North Entry' },
  ] },
  { id: 'extS', a: [0, 17.6], b: [36, 17.6], t: 0.3, ext: true, openings: [
    { at: 1.2,  w: 1.4, kind: 'win' },
    { at: 3.9,  w: 2.0, kind: 'double', label: 'Main Entrance' },
    { at: 6.2,  w: 1.4, kind: 'win' }, { at: 7.9,  w: 1.4, kind: 'win' },
    { at: 11.0, w: 1.5, kind: 'win' }, { at: 13.5, w: 1.5, kind: 'win' },
    { at: 15.8, w: 1.5, kind: 'win' },
    { at: 18.5, w: 1.4, kind: 'win' }, { at: 20.2, w: 1.4, kind: 'win' },
    { at: 25.7, w: 1.4, kind: 'win' },
    { at: 28.4, w: 1.5, kind: 'win' }, { at: 30.6, w: 1.5, kind: 'win' },
  ] },
  { id: 'extW', a: [0, 0], b: [0, 17.6], t: 0.3, ext: true, openings: [
    { at: 2.0,  w: 1.6, kind: 'win' }, { at: 4.4,  w: 1.6, kind: 'win' },
    { at: 10.5, w: 1.6, kind: 'win' }, { at: 12.5, w: 1.6, kind: 'win' },
    { at: 14.5, w: 1.6, kind: 'win' }, { at: 16.4, w: 1.6, kind: 'win' },
  ] },
  { id: 'extE', a: [36, 0], b: [36, 17.6], t: 0.3, ext: true, openings: [
    { at: 7.6, w: 1.4, kind: 'win' },
  ] },

  // ── North band interiors ────────────────────────────────────────────────
  { id: 'c1c2',    a: [5.4, 0],  b: [5.4, 6.4],  t: 0.15, openings: [{ at: 1.6, w: 0.9, kind: 'door' }] },
  { id: 'c2c3',    a: [10.6, 0], b: [10.6, 6.4], t: 0.15, openings: [{ at: 1.6, w: 0.9, kind: 'door' }] },
  { id: 'c3plant', a: [15.7, 0], b: [15.7, 6.4], t: 0.15, openings: [] },
  { id: 'plantV',  a: [18.0, 0], b: [18.0, 6.4], t: 0.15, openings: [] },
  { id: 'nbandS',  a: [0, 6.4],  b: [18.0, 6.4], t: 0.15, openings: [
    { at: 4.6,  w: 0.9, kind: 'door', label: 'Consultant 1' },
    { at: 9.8,  w: 0.9, kind: 'door', label: 'Consultant 2' },
    { at: 14.9, w: 0.9, kind: 'door', label: 'Consultant 3' },
    { at: 16.9, w: 0.9, kind: 'door', label: 'Plant' },
  ] },
  { id: 'vNe',     a: [21.4, 0], b: [21.4, 6.4], t: 0.15, openings: [
    { at: 1.7, w: 1.1, kind: 'wide', label: 'MRI 1 Equip' },
    { at: 5.0, w: 0.9, kind: 'door', label: 'WC' },
  ] },
  { id: 'equipS',  a: [21.4, 3.4], b: [27.0, 3.4], t: 0.15, openings: [] },
  { id: 'wcCann',  a: [23.4, 3.4], b: [23.4, 6.4], t: 0.15, openings: [] },
  { id: 'equipMri',a: [27.0, 0],  b: [27.0, 3.8], t: 0.15, openings: [] },
  // Chamfered MRI suite wall with the shielded door (length ≈ 3.68 m).
  { id: 'mriCham', a: [27.0, 3.8], b: [29.6, 6.4], t: 0.15, openings: [
    { at: 1.84, w: 1.25, kind: 'wide', label: 'MRI 1' },
  ] },
  { id: 'wcS',     a: [21.4, 6.4], b: [23.4, 6.4], t: 0.15, openings: [] },
  { id: 'cannS',   a: [23.4, 6.4], b: [27.0, 6.4], t: 0.15, openings: [
    { at: 2.1, w: 0.9, kind: 'door', label: 'Cannulation' },
  ] },
  { id: 'lobbyW',  a: [24.0, 6.4], b: [24.0, 8.8], t: 0.15, openings: [
    { at: 1.2, w: 1.4, kind: 'wide', label: 'MRI Lobby' },
  ] },
  { id: 'mriCtrlW',a: [29.6, 6.4], b: [29.6, 8.8], t: 0.15, openings: [
    { at: 1.2, w: 0.9, kind: 'door', label: 'MRI Control' },
  ] },
  { id: 'mriS',    a: [29.6, 6.4], b: [36.0, 6.4], t: 0.15, openings: [
    { at: 3.3, w: 2.2, kind: 'glass' },   // MRI control window
  ] },

  // ── Corridor band ────────────────────────────────────────────────────────
  { id: 'storeE',  a: [2.4, 6.4], b: [2.4, 8.8], t: 0.15, openings: [
    { at: 1.2, w: 0.9, kind: 'door', label: 'Store' },
  ] },
  { id: 'corrS',   a: [0, 8.8], b: [24.0, 8.8], t: 0.15, openings: [
    { at: 4.4,   w: 3.2, kind: 'open' },                          // → reception
    { at: 7.0,   w: 0.9, kind: 'door', label: 'Office' },
    { at: 10.1,  w: 0.9, kind: 'door', label: 'Acc. WC' },
    { at: 14.2,  w: 2.8, kind: 'open' },                          // → waiting
    { at: 17.55, w: 1.3, kind: 'open' },                          // → passage
    { at: 22.5,  w: 3.0, kind: 'open' },                          // → CT wing
  ] },
  { id: 'lobbyS',  a: [24.0, 8.8], b: [36.0, 8.8], t: 0.15, openings: [] },

  // ── South-west block ─────────────────────────────────────────────────────
  { id: 'offW',    a: [6.2, 8.8], b: [6.2, 12.8], t: 0.15, openings: [
    { at: 1.4, w: 0.9, kind: 'door', label: 'Office' },
  ] },
  { id: 'offS',    a: [6.2, 12.8], b: [9.2, 12.8], t: 0.15, openings: [] },
  { id: 'offE',    a: [9.2, 8.8],  b: [9.2, 11.0], t: 0.15, openings: [] },
  { id: 'westCol', a: [9.2, 11.0], b: [9.2, 17.6], t: 0.15, openings: [] },
  { id: 'staffN',  a: [9.2, 11.0], b: [12.4, 11.0], t: 0.15, openings: [
    { at: 2.4, w: 0.9, kind: 'door', label: 'Staff Room' },
  ] },
  { id: 'staffE',  a: [12.4, 11.0], b: [12.4, 14.4], t: 0.15, openings: [] },
  { id: 'staffS',  a: [9.2, 14.4],  b: [12.4, 14.4], t: 0.15, openings: [] },
  { id: 'c5n',     a: [12.4, 11.0], b: [16.9, 11.0], t: 0.15, openings: [
    { at: 3.2, w: 0.9, kind: 'door', label: 'Consultant 5' },
  ] },
  { id: 'c5e',     a: [16.9, 11.0], b: [16.9, 17.6], t: 0.15, openings: [] },
  { id: 'c4n',     a: [16.9, 12.8], b: [21.0, 12.8], t: 0.15, openings: [
    { at: 0.6, w: 0.9, kind: 'door', label: 'Consultant 4' },
  ] },
  { id: 'utilW',   a: [18.2, 8.8], b: [18.2, 12.8], t: 0.15, openings: [
    { at: 1.2, w: 0.9, kind: 'door', label: 'Utility' },
  ] },
  { id: 'utilE',   a: [21.0, 8.8], b: [21.0, 17.6], t: 0.15, openings: [] },

  // ── CT wing ──────────────────────────────────────────────────────────────
  { id: 'chgW',    a: [24.0, 8.8], b: [24.0, 12.0], t: 0.15, openings: [
    { at: 0.8, w: 0.9, kind: 'door', label: 'Change' },
    { at: 2.4, w: 0.9, kind: 'door', label: 'Change' },
  ] },
  { id: 'chgMid',  a: [24.0, 10.4], b: [26.9, 10.4], t: 0.15, openings: [] },
  { id: 'chgS',    a: [24.0, 12.0], b: [26.9, 12.0], t: 0.15, openings: [] },
  { id: 'chgE',    a: [26.9, 8.8],  b: [26.9, 12.0], t: 0.15, openings: [] },
  { id: 'ctW',     a: [26.9, 12.0], b: [26.9, 13.6], t: 0.15, openings: [
    { at: 0.7, w: 1.4, kind: 'wide', label: 'CT' },
  ] },
  { id: 'ctS',     a: [26.9, 13.6], b: [36.0, 13.6], t: 0.15, openings: [
    { at: 1.1, w: 0.9, kind: 'door', label: 'CT Control' },
    { at: 3.0, w: 1.6, kind: 'glass' },   // CT control window
    { at: 6.6, w: 0.9, kind: 'door', label: 'Plant' },
  ] },
  { id: 'ctcPlant',a: [31.9, 13.6], b: [31.9, 17.6], t: 0.15, openings: [] },
  { id: 'ctcW',    a: [26.9, 13.6], b: [26.9, 17.6], t: 0.15, openings: [
    { at: 0.8, w: 0.9, kind: 'door', label: 'CT Control' },
  ] },
  { id: 'accChN',  a: [21.0, 15.0], b: [24.0, 15.0], t: 0.15, openings: [
    { at: 1.5, w: 1.0, kind: 'door', label: 'Acc. Change' },
  ] },
  { id: 'accChE',  a: [24.0, 15.0], b: [24.0, 17.6], t: 0.15, openings: [] },
]

/** Leaf widths per opening kind (m). 'open', 'win', 'glass' have no leaf. */
export const LEAF_WIDTH = { door: 0.9, wide: 1.4, double: 2.0 }

/** First-person spawn: outside the main entrance, facing the building (north = -z). */
export const SPAWN = { pos: [3.9, 20.6], yaw: 0 }

/** Helpers shared by scene + validator (pure math). */
export function polyBounds(poly) {
  let minX = Infinity, minZ = Infinity, maxX = -Infinity, maxZ = -Infinity
  for (const [x, z] of poly) {
    if (x < minX) minX = x; if (x > maxX) maxX = x
    if (z < minZ) minZ = z; if (z > maxZ) maxZ = z
  }
  return { minX, minZ, maxX, maxZ, w: maxX - minX, d: maxZ - minZ }
}

export function pointInPoly(poly, x, z) {
  let inside = false
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, zi] = poly[i], [xj, zj] = poly[j]
    if ((zi > z) !== (zj > z) && x < ((xj - xi) * (z - zi)) / (zj - zi) + xi) inside = !inside
  }
  return inside
}

export function polyArea(poly) {
  let s = 0
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    s += (poly[j][0] + poly[i][0]) * (poly[j][1] - poly[i][1])
  }
  return Math.abs(s) / 2
}

export function polyCentroid(poly) {
  const b = polyBounds(poly)
  // bbox centre is fine for labels except the two L-shapes / chamfer rooms,
  // where we nudge toward the dominant rectangle.
  return [(b.minX + b.maxX) / 2, (b.minZ + b.maxZ) / 2]
}
