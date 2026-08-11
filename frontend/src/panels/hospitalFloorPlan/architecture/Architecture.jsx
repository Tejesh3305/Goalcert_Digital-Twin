/**
 * Architecture.jsx — generates the building from data/floorPlan.js:
 * floor slabs (colour-coded per room, like the reference plan), walls with
 * real openings (doors, archways, windows, control-room glass), hinged
 * auto-opening door leaves, ceilings (walk mode only), signage and the
 * exterior surroundings. Registers all wall colliders.
 *
 * Everything is built in PLAN coordinates inside a parent group that the
 * scene offsets by (-W/2, 0, -D/2); colliders are registered in WORLD coords.
 */
import { useEffect, useMemo, useRef } from 'react'
import { useFrame } from '@react-three/fiber'
import * as THREE from 'three'
import {
  PLAN, OFFSET, ROOMS, WALLS, ROOM_COLORS, polyBounds,
} from '../data/floorPlan'
import { addCollider, removeColliders, playerState } from '../systems/collision'
import { Label } from './labels'

/* ─── materials (module-scope, shared) ──────────────────────────────────── */

const MATS = {
  wallInt: new THREE.MeshStandardMaterial({ color: '#f3f0e9', roughness: 0.92 }),
  wallExt: new THREE.MeshStandardMaterial({ color: '#e2dccf', roughness: 0.95 }),
  ceiling: new THREE.MeshStandardMaterial({ color: '#f8f7f4', roughness: 0.96, side: THREE.DoubleSide }),
  glass: new THREE.MeshPhysicalMaterial({
    color: '#cfe4ed', roughness: 0.08, metalness: 0, transparent: true,
    opacity: 0.28, side: THREE.DoubleSide,
  }),
  frame: new THREE.MeshStandardMaterial({ color: '#fdfdfb', roughness: 0.55 }),
  leaf: new THREE.MeshStandardMaterial({ color: '#d8b078', roughness: 0.6 }),   // timber veneer
  leafSteel: new THREE.MeshStandardMaterial({ color: '#9fb2bd', roughness: 0.45, metalness: 0.35 }),
  handle: new THREE.MeshStandardMaterial({ color: '#c8ccd2', roughness: 0.3, metalness: 0.8 }),
  slab: new THREE.MeshStandardMaterial({ color: '#c9cbc9', roughness: 0.9 }),
  grass: new THREE.MeshStandardMaterial({ color: '#9dc48c', roughness: 1 }),
  asphalt: new THREE.MeshStandardMaterial({ color: '#8d939a', roughness: 0.95 }),
}
const FLOOR_MATS = Object.fromEntries(Object.entries(ROOM_COLORS).map(([k, c]) => [
  k, new THREE.MeshStandardMaterial({ color: c, roughness: 0.5, metalness: 0.03, side: THREE.DoubleSide, envMapIntensity: 0.55 }),
]))

/* ─── geometry helpers ──────────────────────────────────────────────────── */

function polyGeometry(poly, depth, yTop) {
  const shape = new THREE.Shape(poly.map(([x, z]) => new THREE.Vector2(x, z)))
  const geo = new THREE.ExtrudeGeometry(shape, { depth, bevelEnabled: false })
  geo.rotateX(Math.PI / 2)              // plan z → world z, extrusion → downward
  geo.translate(0, yTop, 0)             // lift so the top face sits at yTop
  geo.computeVertexNormals()
  return geo
}

/* ─── wall computation ──────────────────────────────────────────────────── */

function computeWalls() {
  const H = PLAN.ceil
  const solids = []    // {cx, cz, len, y, h, t, angle, ext}
  const panes = []     // glass: same shape
  const doors = []     // {px, pz, angle, kind, w, t, label}
  const signs = []     // {px, pz, angle, nx, nz, t, label}
  const colliders = [] // world AABBs

  const pushCollider = (cx, cz, len, angle, t, minY, maxY, dirX, dirZ) => {
    // Split diagonal walls into short chunks so AABBs stay tight.
    const axis = Math.abs(dirX) < 1e-6 || Math.abs(dirZ) < 1e-6
    const n = axis ? 1 : Math.max(1, Math.ceil(len / 0.35))
    const step = len / n
    for (let i = 0; i < n; i++) {
      const m = -len / 2 + step * (i + 0.5)
      const x = cx + dirX * m, z = cz + dirZ * m
      const hx = Math.abs(dirX) * step / 2 + Math.abs(dirZ) * t / 2
      const hz = Math.abs(dirZ) * step / 2 + Math.abs(dirX) * t / 2
      colliders.push({
        minX: x - hx + OFFSET.x, maxX: x + hx + OFFSET.x,
        minZ: z - hz + OFFSET.z, maxZ: z + hz + OFFSET.z,
        minY, maxY, tag: 'arch',
      })
    }
  }

  for (const wall of WALLS) {
    const [ax, az] = wall.a, [bx, bz] = wall.b
    const len = Math.hypot(bx - ax, bz - az)
    const dirX = (bx - ax) / len, dirZ = (bz - az) / len
    const angle = -Math.atan2(dirZ, dirX)
    const t = wall.t
    const at = (m) => [ax + dirX * m, az + dirZ * m]

    const piece = (s, e, y0, y1, arr = solids) => {
      if (e - s < 0.01 || y1 - y0 < 0.01) return null
      const [cx, cz] = at((s + e) / 2)
      const p = { cx, cz, len: e - s, y: (y0 + y1) / 2, h: y1 - y0, t, angle, ext: wall.ext }
      arr.push(p)
      return p
    }

    const ops = [...wall.openings].sort((p, q) => p.at - q.at)
    let cursor = 0
    for (const op of ops) {
      const s = op.at - op.w / 2, e = op.at + op.w / 2
      if (piece(cursor, s, 0, H)) pushCollider(...at((cursor + s) / 2), s - cursor, angle, t, 0, H, dirX, dirZ)
      const [px, pz] = at(op.at)
      if (op.kind === 'door' || op.kind === 'wide' || op.kind === 'double') {
        piece(s, e, PLAN.doorH, H)  // lintel (above head, no collider needed)
        doors.push({ px, pz, angle, kind: op.kind, w: op.w, t, label: op.label })
        if (op.label) signs.push({ px, pz, angle, dirX, dirZ, t, label: op.label })
      } else if (op.kind === 'open') {
        piece(s, e, PLAN.openH, H)  // archway header
      } else if (op.kind === 'win') {
        piece(s, e, 0, PLAN.winSill)
        piece(s, e, PLAN.winHead, H)
        piece(s, e, PLAN.winSill, PLAN.winHead, panes)
        pushCollider(px, pz, op.w, angle, t, 0, PLAN.winHead, dirX, dirZ)
      } else if (op.kind === 'glass') {
        piece(s, e, 0, PLAN.glassSill)
        piece(s, e, PLAN.glassHead, H)
        piece(s, e, PLAN.glassSill, PLAN.glassHead, panes)
        pushCollider(px, pz, op.w, angle, t, 0, PLAN.glassHead, dirX, dirZ)
      }
      cursor = e
    }
    if (piece(cursor, len, 0, H)) pushCollider(...at((cursor + len) / 2), len - cursor, angle, t, 0, H, dirX, dirZ)
  }
  return { solids, panes, doors, signs, colliders }
}

/* ─── door with hinged auto-opening leaves ──────────────────────────────── */

function DoorSet({ door, mode }) {
  const { px, pz, angle, kind, w, t, label } = door
  const pivotL = useRef(), pivotR = useRef()
  const openAmount = useRef(0)
  const worldX = px + OFFSET.x, worldZ = pz + OFFSET.z
  const leafMat = label && /Plant|Equip|Store|Utility/.test(label) ? MATS.leafSteel : MATS.leaf
  const leafW = kind === 'double' ? w / 2 - 0.015 : w - 0.03

  useFrame((_, dt) => {
    let target = 0
    if (mode === 'walk') {
      const dx = playerState.x - worldX, dz = playerState.z - worldZ
      target = playerState.active && dx * dx + dz * dz < 1.7 * 1.7 ? 1 : 0
    } else {
      target = 0.94 // plan/overview modes show doors open, like an architectural drawing
    }
    const cur = openAmount.current
    const next = cur + (target - cur) * Math.min(1, dt * 4.5)
    if (Math.abs(next - cur) > 1e-4) {
      openAmount.current = next
      const a = next * 1.62
      if (pivotL.current) pivotL.current.rotation.y = a
      if (pivotR.current) pivotR.current.rotation.y = -a
    }
  })

  const jamb = (off) => (
    <mesh position={[off, PLAN.doorH / 2, 0]} material={MATS.frame} castShadow>
      <boxGeometry args={[0.07, PLAN.doorH, t + 0.05]} />
    </mesh>
  )
  const leaf = (ref, hingeX, sign) => (
    <group ref={ref} position={[hingeX, 0, 0]}>
      <mesh position={[sign * leafW / 2, PLAN.doorH / 2 - 0.02, 0]} material={leafMat} castShadow>
        <boxGeometry args={[leafW, PLAN.doorH - 0.06, 0.045]} />
      </mesh>
      <mesh position={[sign * (leafW - 0.12), 1.02, 0.05]} material={MATS.handle}>
        <boxGeometry args={[0.14, 0.03, 0.03]} />
      </mesh>
    </group>
  )

  return (
    <group position={[px, 0, pz]} rotation={[0, angle, 0]}>
      {jamb(-w / 2 - 0.02)}
      {jamb(w / 2 + 0.02)}
      <mesh position={[0, PLAN.doorH + 0.03, 0]} material={MATS.frame} castShadow>
        <boxGeometry args={[w + 0.18, 0.07, t + 0.05]} />
      </mesh>
      {kind === 'double'
        ? <>{leaf(pivotL, -w / 2, +1)}{leaf(pivotR, w / 2, -1)}</>
        : leaf(pivotL, -w / 2, +1)}
    </group>
  )
}

/* ─── main component ────────────────────────────────────────────────────── */

const LABEL_POS = {   // overrides where a room's bbox centre falls outside it
  lobby: [26.8, 7.7], vcorrS: [22.5, 13.6], recep: [3.1, 13.4], c5: [14.6, 14.6],
}

export default function Architecture({ mode }) {
  const walls = useMemo(computeWalls, [])
  const floors = useMemo(() => ROOMS.map((r) => ({
    room: r, geo: polyGeometry(r.poly, 0.03, 0.03),
  })), [])
  const ceilings = useMemo(() => ROOMS.map((r) => ({
    room: r, geo: polyGeometry(r.poly, 0.1, PLAN.ceil + 0.1),
  })), [])

  useEffect(() => {
    walls.colliders.forEach(addCollider)
    // Outer bounds so the player can't wander off the site.
    const B = 30
    addCollider({ minX: -B - 1, maxX: B + 1, minZ: -B - 1, maxZ: -B, minY: 0, maxY: 3, tag: 'arch' })
    addCollider({ minX: -B - 1, maxX: B + 1, minZ: B, maxZ: B + 1, minY: 0, maxY: 3, tag: 'arch' })
    addCollider({ minX: -B - 1, maxX: -B, minZ: -B, maxZ: B, minY: 0, maxY: 3, tag: 'arch' })
    addCollider({ minX: B, maxX: B + 1, minZ: -B, maxZ: B, minY: 0, maxY: 3, tag: 'arch' })
    return () => removeColliders('arch')
  }, [walls])

  const showLabels = mode !== 'walk'

  return (
    <group>
      {/* site */}
      <mesh position={[PLAN.width / 2, -0.09, PLAN.depth / 2]} material={MATS.grass} receiveShadow>
        <boxGeometry args={[110, 0.1, 90]} />
      </mesh>
      <mesh position={[PLAN.width / 2, -0.035, PLAN.depth / 2]} material={MATS.slab} receiveShadow>
        <boxGeometry args={[PLAN.width + 2.4, 0.07, PLAN.depth + 2.4]} />
      </mesh>
      <mesh position={[3.9, -0.02, PLAN.depth + 2.6]} material={MATS.asphalt} receiveShadow>
        <boxGeometry args={[6, 0.06, 4.6]} />
      </mesh>
      <mesh position={[19.7, -0.02, -2.4]} material={MATS.asphalt} receiveShadow>
        <boxGeometry args={[4, 0.06, 4]} />
      </mesh>

      {/* room floors */}
      {floors.map(({ room, geo }) => (
        <mesh key={room.id} geometry={geo} material={FLOOR_MATS[room.type]} receiveShadow />
      ))}

      {/* ceilings — only when walking inside */}
      <group visible={mode === 'walk'}>
        {ceilings.map(({ room, geo }) => (
          <mesh key={room.id} geometry={geo} material={MATS.ceiling} />
        ))}
      </group>

      {/* walls */}
      {walls.solids.map((p, i) => (
        <mesh key={i} position={[p.cx, p.y, p.cz]} rotation={[0, p.angle, 0]}
              material={p.ext ? MATS.wallExt : MATS.wallInt} castShadow receiveShadow>
          <boxGeometry args={[p.len, p.h, p.t]} />
        </mesh>
      ))}
      {walls.panes.map((p, i) => (
        <mesh key={i} position={[p.cx, p.y, p.cz]} rotation={[0, p.angle, 0]} material={MATS.glass}>
          <boxGeometry args={[p.len, p.h, p.t * 0.25]} />
        </mesh>
      ))}

      {/* doors */}
      {walls.doors.map((d, i) => <DoorSet key={i} door={d} mode={mode} />)}

      {/* door signs (both faces of the wall) */}
      {walls.signs.map((s, i) => {
        const nx = -s.dirZ, nz = s.dirX  // wall normal
        return [+1, -1].map((side) => (
          <Label key={`${i}.${side}`} text={s.label} width={1.1} fg="#33404e"
                 position={[s.px + nx * side * (s.t / 2 + 0.02), 2.42, s.pz + nz * side * (s.t / 2 + 0.02)]}
                 rotation={[0, Math.atan2(side * nx, side * nz), 0]} />
        ))
      })}

      {/* room name labels on the floor — overview / plan modes */}
      {showLabels && ROOMS.filter((r) => r.label).map((r) => {
        const b = polyBounds(r.poly)
        const [lx, lz] = LABEL_POS[r.id] || [(b.minX + b.maxX) / 2, (b.minZ + b.maxZ) / 2]
        const dark = r.type === 'imaging'
        const w = Math.min(Math.max(Math.min(b.w, b.d) * 0.85, 1.6), 4.2)
        return (
          <Label key={r.id} text={r.label} width={w} fg={dark ? '#e8eef6' : '#26323e'}
                 position={[lx, 0.06, lz]} rotation={[-Math.PI / 2, 0, 0]} />
        )
      })}
    </group>
  )
}
