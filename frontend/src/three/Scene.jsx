/**
 * Scene.jsx — assembles an `nxr-scene/1` scene graph into r3f content:
 * floors (per-room material), plaster walls (exterior full-height, interior
 * lowered for a readable dollhouse), windows/doors, roof (toggleable),
 * ground + driveway + greenery, furniture + equipment props, and ceiling
 * lights. Drives the prop animations, paints live status, and reports picks.
 *
 * Building geometry is in metres; props are unit-modelled, scaled by PROP_SCALE.
 */
import { useEffect, useMemo, useRef } from 'react'
import { useFrame } from '@react-three/fiber'
import * as THREE from 'three'
import { makeMats, STATUS_COLOR, disposeMats, floorMaterial } from './materials'
import { buildProp, buildGreen, pStatusPin } from './props'

const PROP_SCALE = 0.32

export default function Scene({ scene, statusMap = {}, visibleLevels = null,
                               onPick, selectedId, showRoof = false }) {
  const M = useMemo(() => makeMats(), [])
  const rootRef = useRef()

  const roomMats = useMemo(() => {
    const mk = (hex) => new THREE.MeshStandardMaterial({
      color: hex, emissive: hex, emissiveIntensity: 0.4,
      roughness: 0.8, metalness: 0.05, transparent: true, opacity: 0.6,
    })
    return { warn: mk(STATUS_COLOR.warn), crit: mk(STATUS_COLOR.crit) }
  }, [])

  useEffect(() => () => { disposeMats(M); roomMats.warn.dispose(); roomMats.crit.dispose() }, [M, roomMats])

  useFrame((state, dt) => {
    const t = state.clock.elapsedTime
    rootRef.current?.traverse((o) => {
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

  const visible = (n) => !(visibleLevels && n.level != null && !visibleLevels.has(n.level))
  const statusOf = (n) => (n.entityId ? statusMap[n.entityId] : null)
  let lightBudget = 10   // cap real point lights for performance

  return (
    <group ref={rootRef}>
      {scene.nodes.map((node) => {
        if (!visible(node)) return null
        const g = node.geometry || {}
        const { pos, rotY = 0 } = node.transform || { pos: [0, 0, 0] }

        // furniture / equipment props
        if (g.kind === 'prop') {
          const els = [
            <Equipment key={node.id} node={node} M={M} onPick={onPick}
                       selected={selectedId && node.entityId === selectedId} />,
          ]
          if (g.prop === 'ceilinglight' && lightBudget > 0) {
            lightBudget -= 1
            els.push(<pointLight key={node.id + '-l'} position={pos} intensity={6}
                                 distance={9} decay={2} color="#ffe7c2" />)
          }
          return els
        }

        // roof — hidden in dollhouse/cutaway mode
        if (node.kind === 'roof') {
          if (!showRoof) return null
          return <Box key={node.id} pos={pos} rotY={rotY} size={g.size} mat={M.roofMat} shadow />
        }
        if (node.kind === 'ground')
          return <Box key={node.id} pos={pos} rotY={rotY} size={g.size} mat={M.grass} receive />
        if (node.kind === 'driveway')
          return <Box key={node.id} pos={pos} rotY={rotY} size={g.size} mat={M.asphalt} receive />
        if (node.kind === 'tree' || node.kind === 'shrub')
          return <Green key={node.id} pos={pos} M={M} kind={node.kind} />
        if (node.kind === 'window')
          return <Box key={node.id} pos={pos} rotY={rotY} size={g.size} mat={M.windowGlass} />
        if (node.kind === 'door')
          return <Box key={node.id} pos={pos} rotY={rotY} size={g.size} mat={M.woodDark} />
        if (node.kind === 'slab')
          return <Box key={node.id} pos={pos} rotY={rotY} size={g.size} mat={M.slab} receive />
        if (node.kind === 'wall')
          return <Box key={node.id} pos={pos} rotY={rotY} size={g.size} mat={M.plaster} shadow receive />

        // room floor — material by room type, tinted on warn/crit
        if (node.kind === 'room') {
          const st = statusOf(node)
          const mat = st === 'crit' ? roomMats.crit : st === 'warn' ? roomMats.warn
            : floorMaterial(M, node.material)
          const click = (e) => { e.stopPropagation(); onPick && onPick(node) }
          if (g.kind === 'floorpoly')
            return <FloorPoly key={node.id} footprint={g.footprint} y={g.y || pos[1]}
                              thickness={g.thickness || 0.08} mat={mat} onClick={click} />
          return <Box key={node.id} pos={pos} rotY={rotY} size={g.size} mat={mat} receive onClick={click} />
        }
        // fallback
        return g.size ? <Box key={node.id} pos={pos} rotY={rotY} size={g.size} mat={M.slab} /> : null
      })}

      {/* glowing status pins on warn/crit rooms & equipment */}
      {scene.nodes.map((node) => {
        if (!visible(node) || !node.entityId) return null
        const st = statusMap[node.entityId]
        if (!st || st === 'ok' || st === 'info') return null
        const y = node.transform.pos[1] + (node.kind === 'equipment' ? 3.0 : 1.4)
        return <Pin key={`pin-${node.id}`} color={STATUS_COLOR[st] || STATUS_COLOR.warn}
                    position={[node.transform.pos[0], y, node.transform.pos[2]]} />
      })}
    </group>
  )
}

function Box({ pos, rotY = 0, size = [1, 1, 1], mat, shadow, receive, onClick }) {
  return (
    <mesh position={pos} rotation={[0, rotY, 0]} material={mat}
          castShadow={!!shadow} receiveShadow={!!receive} onClick={onClick}>
      <boxGeometry args={size} />
    </mesh>
  )
}

function FloorPoly({ footprint, y, thickness, mat, onClick }) {
  const geo = useMemo(() => {
    const shape = new THREE.Shape()
    footprint.forEach(([x, z], i) => (i ? shape.lineTo(x, z) : shape.moveTo(x, z)))
    shape.closePath()
    const g = new THREE.ExtrudeGeometry(shape, { depth: thickness, bevelEnabled: false })
    g.rotateX(-Math.PI / 2)
    return g
  }, [footprint, thickness])
  useEffect(() => () => geo.dispose(), [geo])
  return <mesh geometry={geo} position={[0, y, 0]} material={mat} receiveShadow onClick={onClick} />
}

function Equipment({ node, M, onPick, selected }) {
  const obj = useMemo(() => buildProp(node.geometry.prop, M), [node.geometry.prop, M])
  useEffect(() => () => obj.traverse((o) => o.geometry && o.geometry.dispose()), [obj])
  const { pos, rotY = 0 } = node.transform
  return (
    <group position={pos} onClick={(e) => { e.stopPropagation(); onPick && onPick(node) }}>
      <group rotation={[0, rotY, 0]} scale={PROP_SCALE}><primitive object={obj} /></group>
      {selected && (
        <mesh position={[0, 0.06, 0]} rotation={[-Math.PI / 2, 0, 0]}>
          <ringGeometry args={[1.1, 1.4, 32]} />
          <meshBasicMaterial color={0x7c3aed} transparent opacity={0.85} side={THREE.DoubleSide} />
        </mesh>
      )}
    </group>
  )
}

function Green({ pos, M, kind }) {
  const obj = useMemo(() => buildGreen(M, kind), [M, kind])
  useEffect(() => () => obj.traverse((o) => o.geometry && o.geometry.dispose()), [obj])
  return <primitive object={obj} position={pos} />
}

function Pin({ color, position }) {
  const obj = useMemo(() => pStatusPin(color), [color])
  return <primitive object={obj} position={position} />
}
