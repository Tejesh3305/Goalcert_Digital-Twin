/**
 * DebugLayer.jsx — measurement & validation overlay (world coordinates):
 *   · 1 m / 0.1 m floor grid (drei shader grid)
 *   · wireframe of every collision box (walls + furniture)
 *   · per-room dimension read-outs
 *   · origin axes at the building centre
 */
import { useMemo } from 'react'
import * as THREE from 'three'
import { Grid } from '@react-three/drei'
import { PLAN, OFFSET, ROOMS, polyBounds } from '../data/floorPlan'
import { getColliders } from './collision'
import { Label } from '../architecture/labels'

function colliderEdges() {
  const pts = []
  const push = (a, b) => { pts.push(a[0], a[1], a[2], b[0], b[1], b[2]) }
  for (const c of getColliders()) {
    const { minX: x0, maxX: x1, minZ: z0, maxZ: z1 } = c
    const y0 = Math.max(0, c.minY), y1 = Math.min(PLAN.ceil, c.maxY)
    const v = [
      [x0, y0, z0], [x1, y0, z0], [x1, y0, z1], [x0, y0, z1],
      [x0, y1, z0], [x1, y1, z0], [x1, y1, z1], [x0, y1, z1],
    ]
    push(v[0], v[1]); push(v[1], v[2]); push(v[2], v[3]); push(v[3], v[0])
    push(v[4], v[5]); push(v[5], v[6]); push(v[6], v[7]); push(v[7], v[4])
    push(v[0], v[4]); push(v[1], v[5]); push(v[2], v[6]); push(v[3], v[7])
  }
  const geo = new THREE.BufferGeometry()
  geo.setAttribute('position', new THREE.Float32BufferAttribute(pts, 3))
  return geo
}

export default function DebugLayer() {
  const edges = useMemo(colliderEdges, [])
  const lineMat = useMemo(() => new THREE.LineBasicMaterial({
    color: '#ff5f8f', transparent: true, opacity: 0.55, depthWrite: false,
  }), [])

  return (
    <group>
      <Grid
        position={[0, 0.05, 0]}
        args={[PLAN.width + 10, PLAN.depth + 10]}
        cellSize={0.1} cellThickness={0.55} cellColor="#8b97a3"
        sectionSize={1} sectionThickness={1.15} sectionColor="#42525f"
        fadeDistance={90} fadeStrength={1}
      />
      <lineSegments geometry={edges} material={lineMat} />
      <axesHelper args={[2]} position={[0, 0.08, 0]} />
      {ROOMS.filter((r) => r.label).map((r) => {
        const b = polyBounds(r.poly)
        return (
          <Label key={r.id} width={2.0} fg="#b8236e"
                 text={`${b.w.toFixed(1)} × ${b.d.toFixed(1)} m`}
                 position={[(b.minX + b.maxX) / 2 + OFFSET.x, 0.09, (b.minZ + b.maxZ) / 2 + OFFSET.z + 0.75]}
                 rotation={[-Math.PI / 2, 0, 0]} />
        )
      })}
    </group>
  )
}
