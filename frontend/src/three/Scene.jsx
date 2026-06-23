/**
 * Scene.jsx — assembles an `nxr-scene/1` scene graph into r3f content:
 * floor slabs, walls, room pads, and equipment props. Drives the demo's
 * animations (blink / spin / press / belt / robot / pin pulse) via one
 * useFrame loop, paints live status onto rooms/equipment, and reports picks.
 *
 * Building geometry is in metres; equipment props are unit-modelled, so each
 * placed prop is scaled by PROP_SCALE to fit.
 */
import { useEffect, useMemo, useRef } from 'react'
import { useFrame } from '@react-three/fiber'
import * as THREE from 'three'
import { makeMats, STATUS_COLOR, disposeMats } from './materials'
import { buildProp, pStatusPin } from './props'

const PROP_SCALE = 0.32

export default function Scene({ scene, statusMap = {}, visibleLevels = null, onPick, selectedId }) {
  const M = useMemo(() => makeMats(), [])
  const rootRef = useRef()

  // Room status materials, cached by severity.
  const roomMats = useMemo(() => {
    const mk = (hex) => new THREE.MeshStandardMaterial({
      color: hex, emissive: hex, emissiveIntensity: 0.35,
      roughness: 0.8, metalness: 0.1, transparent: true, opacity: 0.5,
    })
    return { ok: M.floorPad, warn: mk(STATUS_COLOR.warn), crit: mk(STATUS_COLOR.crit) }
  }, [M])

  // Free shared materials when the viewer unmounts (props dispose their own
  // geometries in <Equipment>; r3f disposes JSX-created geometries itself).
  useEffect(() => () => {
    disposeMats(M)
    roomMats.warn.dispose(); roomMats.crit.dispose()
  }, [M, roomMats])

  useFrame((state, dt) => {
    const t = state.clock.elapsedTime
    const root = rootRef.current
    if (!root) return
    root.traverse((o) => {
      const u = o.userData
      if (!u) return
      if (u.blink) o.visible = Math.sin(t * 6 + o.position.x * 3 + o.position.y) > -0.25
      if (u.spin) o.rotation.y += u.spin * dt
      if (u.press) { if (u.baseY === undefined) u.baseY = o.position.y; o.position.y = u.baseY + Math.sin(t * 2) * 0.35 }
      if (u.cargo) { o.position.x += dt * 1.4; if (o.position.x > 4) o.position.x = -4 }
      if (u.robot) { u.robot.arm1.rotation.y = Math.sin(t * 0.8) * 0.7; u.robot.arm2.rotation.x = Math.sin(t * 1.2) * 0.5 }
      if (u.pin) o.scale.setScalar(1 + Math.sin(t * 4) * 0.14)
    })
  })

  const visible = (node) =>
    !(visibleLevels && node.level != null && !visibleLevels.has(node.level))

  return (
    <group ref={rootRef}>
      {scene.nodes.map((node) => {
        if (!visible(node)) return null
        if (node.geometry?.kind === 'prop') {
          return (
            <Equipment key={node.id} node={node} M={M} onPick={onPick}
                       selected={selectedId && node.entityId === selectedId} />
          )
        }
        const [w, h, d] = node.geometry.size || [1, 1, 1]
        const { pos, rotY } = node.transform
        const isRoom = node.kind === 'room'
        const st = node.entityId ? statusMap[node.entityId] : null
        const mat = node.kind === 'slab' ? M.slab
          : node.kind === 'wall' ? M.wall
          : (roomMats[st === 'crit' ? 'crit' : st === 'warn' ? 'warn' : 'ok'])
        return (
          <mesh key={node.id} position={pos} rotation={[0, rotY, 0]} material={mat}
                receiveShadow castShadow={node.kind === 'wall'}
                onClick={isRoom ? (e) => { e.stopPropagation(); onPick && onPick(node) } : undefined}>
            <boxGeometry args={[w, h, d]} />
          </mesh>
        )
      })}

      {/* glowing status pins on warn/crit rooms & equipment */}
      {scene.nodes.map((node) => {
        if (!visible(node) || !node.entityId) return null
        const st = statusMap[node.entityId]
        if (!st || st === 'ok' || st === 'info') return null
        const [, , ] = node.transform.pos
        const y = node.transform.pos[1] + (node.kind === 'equipment' ? 3.2 : 1.6)
        return (
          <Pin key={`pin-${node.id}`} color={STATUS_COLOR[st] || STATUS_COLOR.warn}
               position={[node.transform.pos[0], y, node.transform.pos[2]]} />
        )
      })}
    </group>
  )
}

function Equipment({ node, M, onPick, selected }) {
  const obj = useMemo(() => buildProp(node.geometry.prop, M), [node.geometry.prop, M])
  useEffect(() => () => {
    obj.traverse((o) => { if (o.geometry) o.geometry.dispose() })
  }, [obj])
  const { pos, rotY } = node.transform
  return (
    <group position={pos} onClick={(e) => { e.stopPropagation(); onPick && onPick(node) }}>
      <group rotation={[0, rotY, 0]} scale={PROP_SCALE}>
        <primitive object={obj} />
      </group>
      {selected && (
        <mesh position={[0, 0.06, 0]} rotation={[-Math.PI / 2, 0, 0]}>
          <ringGeometry args={[1.1, 1.4, 32]} />
          <meshBasicMaterial color={0x7c3aed} transparent opacity={0.85} side={THREE.DoubleSide} />
        </mesh>
      )}
    </group>
  )
}

function Pin({ color, position }) {
  const obj = useMemo(() => pStatusPin(color), [color])
  return <primitive object={obj} position={position} />
}
