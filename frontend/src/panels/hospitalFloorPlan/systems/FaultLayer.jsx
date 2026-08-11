/**
 * FaultLayer.jsx — 3-D fault indication inside the floor plan (plan coords,
 * rendered inside the scene's offset group).
 *
 * For every machine whose live status is not "ok": a pulsing status ring on
 * the floor + a translucent glow column around the unit. The actively-faulted
 * machine (the twin's fault target) additionally gets a red point light and a
 * billboard label naming the fault — the "what is broken, where" read.
 * The selected machine gets a steady blue selection ring.
 */
import { useMemo, useRef } from 'react'
import { useFrame } from '@react-three/fiber'
import { Billboard } from '@react-three/drei'
import * as THREE from 'three'
import { PLACEMENTS } from '../data/assetPlacements'
import { Label } from '../architecture/labels'

const STATUS_COLOR = { critical: '#f43f5e', warning: '#f59e0b' }

// Beacon/label height + ring radius per prop kind (m).
const KIND_FX = {
  mri: { h: 2.6, r: 1.9 }, ctscanner: { h: 2.3, r: 1.9 },
  ahu: { h: 2.0, r: 1.1 }, splitac: { h: 2.95, r: 0.8 },
  ups: { h: 2.1, r: 0.8 }, switchgear: { h: 2.0, r: 1.1 },
  pump: { h: 1.6, r: 0.7 }, boiler: { h: 1.9, r: 0.8 },
  gas: { h: 1.8, r: 0.7 }, patientmonitor: { h: 2.0, r: 0.6 },
}

/** plan-coord position of each tagged machine. */
export const EQ_PLACEMENTS = Object.fromEntries(
  PLACEMENTS.filter((p) => p.eq).map((p) => [p.eq, p]),
)

function FaultMarker({ unit, isTarget }) {
  const p = EQ_PLACEMENTS[unit.id]
  const ringRef = useRef()
  const glowRef = useRef()
  const lightRef = useRef()
  if (!p) return null
  const fx = KIND_FX[p.key] || { h: 2.0, r: 0.9 }
  const color = STATUS_COLOR[unit.status] || STATUS_COLOR.warning

  return (
    <group position={[p.pos[0], 0, p.pos[1]]}>
      <mesh ref={ringRef} position={[0, 0.07, 0]} rotation={[-Math.PI / 2, 0, 0]}>
        <ringGeometry args={[fx.r * 0.78, fx.r, 40]} />
        <meshBasicMaterial color={color} transparent opacity={0.85} side={THREE.DoubleSide} depthWrite={false} />
      </mesh>
      <mesh ref={glowRef} position={[0, fx.h / 2, 0]}>
        <cylinderGeometry args={[fx.r * 0.82, fx.r * 0.82, fx.h, 24, 1, true]} />
        <meshBasicMaterial color={color} transparent opacity={0.1} side={THREE.DoubleSide} depthWrite={false} />
      </mesh>
      {isTarget && (
        <>
          <pointLight ref={lightRef} position={[0, fx.h * 0.55, 0]} color="#ff4560"
                      intensity={7} distance={7} decay={2} />
          <Billboard position={[0, fx.h + 0.45, 0]}>
            <Label text={`⚠ ${unit.fault_label || unit.fault || 'FAULT'}`}
                   width={Math.min(3.4, 1.2 + (unit.fault_label || '').length * 0.085)}
                   fg="#ffffff" bg="rgba(190,18,60,0.92)" />
          </Billboard>
          <Billboard position={[0, fx.h + 0.14, 0]}>
            <Label text={unit.label} width={1.9} fg="#ffd7de" />
          </Billboard>
        </>
      )}
      {!isTarget && (
        <Billboard position={[0, fx.h + 0.18, 0]}>
          <Label text={`${unit.label} — ${unit.status}`} width={2.2}
                 fg="#ffffff" bg={unit.status === 'critical' ? 'rgba(190,18,60,0.85)' : 'rgba(180,110,10,0.85)'} />
        </Billboard>
      )}
      <FaultPulse ringRef={ringRef} glowRef={glowRef} lightRef={lightRef} />
    </group>
  )
}

function FaultPulse({ ringRef, glowRef, lightRef }) {
  useFrame(({ clock }) => {
    const t = clock.getElapsedTime()
    const s = 1 + Math.sin(t * 3.4) * 0.12
    const o = 0.55 + (Math.sin(t * 3.4) + 1) * 0.2
    if (ringRef.current) {
      ringRef.current.scale.setScalar(s)
      ringRef.current.material.opacity = o
    }
    if (glowRef.current) glowRef.current.material.opacity = 0.06 + (Math.sin(t * 3.4) + 1) * 0.045
    if (lightRef.current) lightRef.current.intensity = 5.5 + (Math.sin(t * 3.4) + 1) * 2.2
  })
  return null
}

export default function FaultLayer({ equipment = [], faultTarget = null, selectedEq = null }) {
  const notOk = useMemo(() => equipment.filter((e) => e.status !== 'ok'), [equipment])
  const selected = selectedEq && EQ_PLACEMENTS[selectedEq]
  return (
    <group>
      {notOk.map((u) => (
        <FaultMarker key={u.id} unit={u} isTarget={u.id === faultTarget} />
      ))}
      {selected && (
        <mesh position={[selected.pos[0], 0.05, selected.pos[1]]} rotation={[-Math.PI / 2, 0, 0]}>
          <ringGeometry args={[(KIND_FX[selected.key]?.r || 0.9) * 0.85, (KIND_FX[selected.key]?.r || 0.9) * 1.02, 40]} />
          <meshBasicMaterial color="#3b82f6" transparent opacity={0.9} side={THREE.DoubleSide} depthWrite={false} />
        </mesh>
      )}
    </group>
  )
}
