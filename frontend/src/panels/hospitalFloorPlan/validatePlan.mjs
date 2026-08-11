/**
 * validatePlan.mjs — offline consistency checker for the hospital floor plan.
 * Run:  node src/panels/hospitalFloorPlan/validatePlan.mjs   (from frontend/)
 *
 * Checks:
 *   1. Room polygons tile the 36 × 17.6 m envelope exactly (no gaps/overlaps).
 *   2. Every opening fits inside its wall segment, without overlaps.
 *   3. Every door connects two different spaces (or the outside).
 *   4. Every asset anchor lies inside its declared room.
 *   5. Clearance warnings: assets close to door swings or wall lines.
 *   6. Dimension table for the scale audit.
 */
import { PLAN, ROOMS, WALLS, polyArea, polyBounds, pointInPoly } from './data/floorPlan.js'
import { PLACEMENTS } from './data/assetPlacements.js'

let errors = 0, warnings = 0
const err = (m) => { errors++; console.log('  ERROR  ' + m) }
const warn = (m) => { warnings++; console.log('  warn   ' + m) }

const roomAt = (x, z) => ROOMS.filter((r) => pointInPoly(r.poly, x, z))

/* 1 ── tiling */
console.log('\n[1] Room tiling of the envelope')
const areaSum = ROOMS.reduce((s, r) => s + polyArea(r.poly), 0)
const envArea = PLAN.width * PLAN.depth
console.log(`  rooms area sum = ${areaSum.toFixed(2)} m² · envelope = ${envArea.toFixed(2)} m²`)
if (Math.abs(areaSum - envArea) > 0.05) err(`area mismatch: ${(areaSum - envArea).toFixed(2)} m²`)

let gaps = 0, overlaps = 0
for (let x = 0.05; x < PLAN.width; x += 0.1) {
  for (let z = 0.05; z < PLAN.depth; z += 0.1) {
    const n = roomAt(x, z).length
    if (n === 0) { gaps++; if (gaps < 6) err(`gap at (${x.toFixed(2)}, ${z.toFixed(2)})`) }
    if (n > 1) { overlaps++; if (overlaps < 6) err(`overlap at (${x.toFixed(2)}, ${z.toFixed(2)}): ${roomAt(x, z).map((r) => r.id).join('+')}`) }
  }
}
console.log(`  sampled ${Math.round(PLAN.width / 0.1) * Math.round(PLAN.depth / 0.1)} points → gaps: ${gaps}, overlaps: ${overlaps}`)

/* 2 ── openings fit walls */
console.log('\n[2] Openings inside wall segments')
const doors = []
for (const w of WALLS) {
  const len = Math.hypot(w.b[0] - w.a[0], w.b[1] - w.a[1])
  const dir = [(w.b[0] - w.a[0]) / len, (w.b[1] - w.a[1]) / len]
  const sorted = [...w.openings].sort((p, q) => p.at - q.at)
  let prevEnd = -1
  for (const op of sorted) {
    const s = op.at - op.w / 2, e = op.at + op.w / 2
    if (s < -0.01 || e > len + 0.01) err(`${w.id}: opening '${op.kind}' at ${op.at} (w ${op.w}) exceeds wall length ${len.toFixed(2)}`)
    if (s < prevEnd - 0.001) err(`${w.id}: openings overlap near at=${op.at}`)
    prevEnd = e
    if (op.kind === 'door' || op.kind === 'wide' || op.kind === 'double') {
      doors.push({
        wall: w.id, label: op.label || '', w: op.w,
        x: w.a[0] + dir[0] * op.at, z: w.a[1] + dir[1] * op.at,
        nx: -dir[1], nz: dir[0],
      })
    }
  }
}
console.log(`  ${WALLS.length} walls, ${doors.length} door openings checked`)

/* 3 ── door connectivity */
console.log('\n[3] Door connectivity (probe ±0.45 m each side)')
for (const d of doors) {
  const side = (s) => {
    const x = d.x + d.nx * s * 0.45, z = d.z + d.nz * s * 0.45
    if (x < 0 || x > PLAN.width || z < 0 || z > PLAN.depth) return 'OUTSIDE'
    const rs = roomAt(x, z)
    return rs.length ? rs[0].id : 'VOID'
  }
  const a = side(1), b = side(-1)
  if (a === 'VOID' || b === 'VOID') err(`door '${d.label}' on ${d.wall}: probes into void (${a} | ${b})`)
  else if (a === b) err(`door '${d.label}' on ${d.wall}: both sides land in '${a}'`)
  else console.log(`  ok  ${d.wall.padEnd(9)} ${String(d.label).padEnd(14)} ${a} ↔ ${b}`)
}

/* 4 ── asset containment */
console.log('\n[4] Asset anchors inside their rooms')
let good = 0
for (const p of PLACEMENTS) {
  const room = ROOMS.find((r) => r.id === p.room)
  if (!room) { err(`${p.key}: unknown room '${p.room}'`); continue }
  if (!pointInPoly(room.poly, p.pos[0], p.pos[1])) {
    err(`${p.key} in '${p.room}' anchor (${p.pos}) is outside the room polygon`)
  } else good++
}
console.log(`  ${good}/${PLACEMENTS.length} placements contained`)

/* 5 ── clearances */
console.log('\n[5] Clearance warnings')
for (const p of PLACEMENTS) {
  if (p.y && p.y > 1.4) continue // wall/ceiling mounted
  for (const d of doors) {
    const dist = Math.hypot(p.pos[0] - d.x, p.pos[1] - d.z)
    if (dist < 0.55 + d.w / 2) warn(`${p.key} in '${p.room}' is ${dist.toFixed(2)} m from door '${d.label}' (${d.wall})`)
  }
}
if (!warnings) console.log('  none')

/* 6 ── dimension table */
console.log('\n[6] Scale audit — key dimensions')
console.log(`  envelope            ${PLAN.width} × ${PLAN.depth} m`)
console.log(`  ceiling height      ${PLAN.ceil} m · door opening ${PLAN.doorH} m`)
console.log(`  wall thickness      ext ${PLAN.wallExt} m · int ${PLAN.wallInt} m`)
for (const r of ROOMS) {
  const b = polyBounds(r.poly)
  console.log(`  ${(r.label || r.id).padEnd(20)} ${b.w.toFixed(1)} × ${b.d.toFixed(1)} m  (${polyArea(r.poly).toFixed(1)} m²)`)
}

console.log(`\n${errors} errors, ${warnings} warnings`)
process.exit(errors ? 1 : 0)
