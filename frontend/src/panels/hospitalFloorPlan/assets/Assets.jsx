/**
 * Assets.jsx — places the EXISTING prop library into the floor plan.
 *
 * Props come from frontend/src/three/props.jsx via buildProp(key, M) and are
 * uniformly scaled by PROP_SCALE (0.32 → metres) — the library is calibrated
 * against a 1.73 m human, so no per-instance scale hacks. After mount each
 * prop's world Box3 is measured and registered as a collision obstacle
 * (except wall/ceiling-mounted items), and its real dimensions are kept for
 * the debug layer.
 *
 * Also generates ceiling light fixtures on a per-room grid (walk mode only,
 * since overview modes remove the ceiling) and the greenery outside.
 */
import { useEffect, useMemo, useRef } from 'react'
import { useFrame } from '@react-three/fiber'
import * as THREE from 'three'
import { buildProp, buildGreen } from '../../../three/props'
import { makeMats, disposeMats } from '../../../three/materials'
import { PROP_SCALE } from '../../../three/catalog'
import { PLAN, ROOMS, polyBounds, pointInPoly } from '../data/floorPlan'
import { PLACEMENTS, NO_COLLIDE } from '../data/assetPlacements'
import { addCollider, removeColliders } from '../systems/collision'

const TREES = [[-2.6, -2.2], [-3.2, 9.0], [-2.6, 19.6], [38.6, -2.2], [38.9, 10.2], [38.4, 19.8], [10.0, -2.6], [28.5, 20.4]]
const SHRUBS = [[1.2, 18.9], [6.6, 18.9], [17.8, -1.9], [21.6, -1.9], [-1.9, 4.0], [37.9, 4.5]]

/** Ceiling panel grid per room — denser along corridors. */
function ceilingLightPositions() {
  const out = []
  for (const room of ROOMS) {
    const b = polyBounds(room.poly)
    const spacing = room.type === 'corridor' ? 3.0 : 3.6
    const cols = Math.max(1, Math.round(b.w / spacing))
    const rows = Math.max(1, Math.round(b.d / spacing))
    for (let i = 0; i < cols; i++) {
      for (let j = 0; j < rows; j++) {
        const x = b.minX + ((i + 0.5) / cols) * b.w
        const z = b.minZ + ((j + 0.5) / rows) * b.d
        if (pointInPoly(room.poly, x, z)) out.push([x, z])
      }
    }
  }
  return out
}

export default function Assets({ mode, onSelectEquipment }) {
  const built = useMemo(() => {
    const M = makeMats()
    const propsRoot = new THREE.Group()   // greens only; placements render as own primitives
    const lightsRoot = new THREE.Group()
    const items = []

    for (const p of PLACEMENTS) {
      const inner = buildProp(p.key, M)
      inner.rotation.y = p.rotY || 0
      inner.scale.setScalar(PROP_SCALE)
      const wrapper = new THREE.Group()
      wrapper.position.set(p.pos[0], p.y || 0, p.pos[1])
      wrapper.add(inner)
      items.push({ wrapper, key: p.key, room: p.room, eq: p.eq })
    }

    for (const [x, z] of ceilingLightPositions()) {
      const inner = buildProp('ceilinglight', M)
      inner.scale.setScalar(PROP_SCALE)
      const wrapper = new THREE.Group()
      wrapper.position.set(x, PLAN.ceil - 0.04, z)
      wrapper.add(inner)
      lightsRoot.add(wrapper)
    }

    for (const [x, z] of TREES) {
      const g = buildGreen(M, 'tree')   // authored at metre scale — no PROP_SCALE
      g.position.set(x, 0, z)
      propsRoot.add(g)
    }
    for (const [x, z] of SHRUBS) {
      const g = buildGreen(M, 'shrub')
      g.position.set(x, 0, z)
      propsRoot.add(g)
    }

    // animation contract from the prop library (Scene.jsx mirrors this)
    const animated = []
    const collect = (root) => root.traverse((o) => {
      const u = o.userData
      if (u && (u.blink || u.spin || u.pin)) animated.push(o)
    })
    collect(propsRoot)
    for (const it of items) collect(it.wrapper)
    return { M, propsRoot, lightsRoot, items, animated }
  }, [])

  // Measure world bounding boxes once mounted → collision obstacles + debug dims.
  useEffect(() => {
    const { items } = built
    const box = new THREE.Box3()
    for (const it of items) {
      if (NO_COLLIDE.has(it.key)) continue
      it.wrapper.updateWorldMatrix(true, true)
      box.setFromObject(it.wrapper)
      if (!isFinite(box.min.x) || box.min.y > 1.5) continue
      const shrink = 0.04
      addCollider({
        minX: box.min.x + shrink, maxX: box.max.x - shrink,
        minZ: box.min.z + shrink, maxZ: box.max.z - shrink,
        minY: Math.max(0, box.min.y), maxY: box.max.y,
        tag: 'props', key: it.key, room: it.room,
      })
    }
    for (const [x, z] of TREES) {
      addCollider({
        minX: x - 0.2 - PLAN.width / 2, maxX: x + 0.2 - PLAN.width / 2,
        minZ: z - 0.2 - PLAN.depth / 2, maxZ: z + 0.2 - PLAN.depth / 2,
        minY: 0, maxY: 3, tag: 'props',
      })
    }
    return () => removeColliders('props')
  }, [built])

  // Dispose shared materials + geometries when the scene unmounts.
  useEffect(() => () => {
    const dispose = (root) => root.traverse((o) => { if (o.geometry) o.geometry.dispose() })
    dispose(built.propsRoot); dispose(built.lightsRoot)
    for (const it of built.items) dispose(it.wrapper)
    disposeMats(built.M)
  }, [built])

  const t = useRef(0)
  useFrame((_, dt) => {
    t.current += dt
    const now = t.current
    for (const o of built.animated) {
      const u = o.userData
      if (u.blink) o.visible = Math.sin(now * 6 + o.position.x * 3 + o.position.y) > -0.25
      if (u.spin) o.rotation.y += u.spin * dt
      if (u.pin) o.scale.setScalar(1 + Math.sin(now * 4) * 0.14)
    }
  })

  // Machines (eq-tagged) are clickable in orbit/plan modes for inspection.
  const clickable = mode !== 'walk'
  return (
    <group>
      {built.items.map((it, i) => it.eq ? (
        <primitive key={i} object={it.wrapper}
          onClick={clickable ? (e) => { e.stopPropagation(); onSelectEquipment?.(it.eq) } : undefined}
          onPointerOver={clickable ? (e) => { e.stopPropagation(); document.body.style.cursor = 'pointer' } : undefined}
          onPointerOut={clickable ? () => { document.body.style.cursor = '' } : undefined}
        />
      ) : (
        <primitive key={i} object={it.wrapper} />
      ))}
      <primitive object={built.propsRoot} />
      <primitive object={built.lightsRoot} visible={mode === 'walk'} />
    </group>
  )
}
