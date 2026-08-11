/**
 * PlayerRig.jsx — first-person walk navigation.
 *
 * PointerLockControls (drei) for mouse-look + WASD/arrow movement with
 * circle-vs-AABB collision (systems/collision.js). Eye height 1.65 m,
 * walk 2.6 m/s, run (Shift) 4.6 m/s. Movement is resolved per-axis so the
 * player slides along walls instead of sticking.
 */
import { useEffect, useRef } from 'react'
import { useFrame, useThree } from '@react-three/fiber'
import { PointerLockControls } from '@react-three/drei'
import * as THREE from 'three'
import { PLAN, OFFSET, SPAWN } from '../data/floorPlan'
import { resolveMovement, playerState } from './collision'

const WALK_SPEED = 2.6
const RUN_SPEED = 4.6

export default function PlayerRig({ active, onLockChange, resetNonce = 0, spawn = SPAWN }) {
  const { camera } = useThree()
  const keys = useRef({})
  const controls = useRef(null)
  const vec = useRef({ fwd: new THREE.Vector3(), right: new THREE.Vector3() })

  // Spawn outside the main entrance, facing the building, whenever walk mode starts.
  useEffect(() => {
    if (!active) { playerState.active = false; return }
    camera.position.set(spawn.pos[0] + OFFSET.x, PLAN.eyeHeight, spawn.pos[1] + OFFSET.z)
    camera.rotation.set(0, spawn.yaw, 0, 'YXZ')
    playerState.active = true
    return () => { playerState.active = false }
  }, [active, camera, resetNonce, spawn])

  useEffect(() => {
    const down = (e) => {
      if (e.target && /INPUT|TEXTAREA|SELECT/.test(e.target.tagName)) return
      keys.current[e.code] = true
    }
    const up = (e) => { keys.current[e.code] = false }
    const blur = () => { keys.current = {} }
    window.addEventListener('keydown', down)
    window.addEventListener('keyup', up)
    window.addEventListener('blur', blur)
    return () => {
      window.removeEventListener('keydown', down)
      window.removeEventListener('keyup', up)
      window.removeEventListener('blur', blur)
    }
  }, [])

  useFrame((_, dt) => {
    if (!active) return
    const k = keys.current
    const fwdIn = (k.KeyW || k.ArrowUp ? 1 : 0) - (k.KeyS || k.ArrowDown ? 1 : 0)
    const rightIn = (k.KeyD || k.ArrowRight ? 1 : 0) - (k.KeyA || k.ArrowLeft ? 1 : 0)

    if (fwdIn !== 0 || rightIn !== 0) {
      const { fwd, right } = vec.current
      camera.getWorldDirection(fwd)
      fwd.y = 0; fwd.normalize()
      right.crossVectors(fwd, camera.up).normalize()
      const speed = (k.ShiftLeft || k.ShiftRight ? RUN_SPEED : WALK_SPEED) * Math.min(dt, 0.1)
      const dx = (fwd.x * fwdIn + right.x * rightIn) * speed
      const dz = (fwd.z * fwdIn + right.z * rightIn) * speed
      const [nx, nz] = resolveMovement(camera.position.x, camera.position.z, dx, dz)
      camera.position.x = nx
      camera.position.z = nz
    }
    camera.position.y = PLAN.eyeHeight

    playerState.x = camera.position.x
    playerState.y = camera.position.y
    playerState.z = camera.position.z
  })

  if (!active) return null
  return (
    <PointerLockControls
      ref={controls}
      selector=".hfp-lock-target"
      onLock={() => onLockChange?.(true)}
      onUnlock={() => onLockChange?.(false)}
    />
  )
}
