/**
 * collision.js — shared collision registry + circle-vs-AABB movement solver
 * for the walkable hospital scene. All boxes are WORLD coordinates.
 *
 * Colliders: { minX, maxX, minY, maxY, minZ, maxZ, tag }
 * The player is a vertical capsule approximated by a circle of radius
 * PLAYER_RADIUS at floor level; boxes entirely above head height (lintels
 * over doors, ceiling fixtures) or below toe height are ignored.
 */

export const PLAYER_RADIUS = 0.28
const HEAD_CLEAR = 1.5   // boxes starting above this don't block walking
const TOE_CLEAR = 0.08   // boxes ending below this don't block walking

const colliders = []

export function clearColliders() { colliders.length = 0 }

export function addCollider(box) {
  colliders.push(box)
  return box
}

export function removeColliders(tag) {
  for (let i = colliders.length - 1; i >= 0; i--) {
    if (colliders[i].tag === tag) colliders.splice(i, 1)
  }
}

export function getColliders() { return colliders }

function blocked(x, z, r) {
  for (let i = 0; i < colliders.length; i++) {
    const c = colliders[i]
    if (c.minY > HEAD_CLEAR || c.maxY < TOE_CLEAR) continue
    const cx = x < c.minX ? c.minX : x > c.maxX ? c.maxX : x
    const cz = z < c.minZ ? c.minZ : z > c.maxZ ? c.maxZ : z
    const dx = x - cx, dz = z - cz
    if (dx * dx + dz * dz < r * r) return true
  }
  return false
}

/**
 * Axis-separated movement resolution → natural wall sliding.
 * Returns the allowed new [x, z].
 */
export function resolveMovement(x, z, dx, dz, r = PLAYER_RADIUS) {
  let nx = x, nz = z
  if (dx !== 0 && !blocked(nx + dx, nz, r)) nx += dx
  if (dz !== 0 && !blocked(nx, nz + dz, r)) nz += dz
  return [nx, nz]
}

/** Live player state shared with door auto-openers / HUD readout. */
export const playerState = {
  x: 0, y: 1.65, z: 14,   // world coords, set by PlayerRig each frame
  active: false,
}

// Dev/QA hook: lets the smoke-test harness inspect colliders and hit-tests.
if (typeof window !== 'undefined') {
  window.__hfp = {
    colliders,
    playerState,
    blocked: (x, z, r = PLAYER_RADIUS) => blocked(x, z, r),
  }
}
