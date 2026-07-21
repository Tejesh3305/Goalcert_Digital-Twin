/**
 * AssetPreview.jsx — renders ONE catalog asset in its own canvas, in real-world
 * scale (ported from the 2d-to-3d Asset Library, the proven per-asset renderer).
 *
 * The prop is built, scaled by PROP_SCALE into metres, recentred to sit on the
 * floor at the origin, and shown on a 1 m grid. An optional 1.7 m human
 * silhouette gives instant scale reference.
 *
 * Two modes:
 *   • thumbnail  (interactive=false) — one on-demand render, fixed 3/4 angle, cheap.
 *   • detail     (interactive=true)  — orbit controls + live prop animations.
 */
import { Suspense, useEffect, useMemo, useRef } from 'react'
import { Canvas, useFrame, useThree } from '@react-three/fiber'
import { OrbitControls, ContactShadows } from '@react-three/drei'
import * as THREE from 'three'
import { RoomEnvironment } from 'three/examples/jsm/environments/RoomEnvironment.js'
import { makeMats, disposeMats } from './materials'
import { buildProp } from './props'
import { PROP_SCALE } from './catalog'

function RoomEnv() {
  const { gl, scene } = useThree()
  useEffect(() => {
    const pmrem = new THREE.PMREMGenerator(gl)
    const env = pmrem.fromScene(new RoomEnvironment(), 0.04)
    scene.environment = env.texture
    return () => { env.texture.dispose(); pmrem.dispose(); scene.environment = null }
  }, [gl, scene])
  return null
}

/** A simple 1.7 m human silhouette for scale. */
function HumanRef({ x = 0, z = 0 }) {
  const mat = useMemo(() => new THREE.MeshStandardMaterial({
    color: 0x4b5670, roughness: 0.9, metalness: 0, transparent: true, opacity: 0.55,
  }), [])
  useEffect(() => () => mat.dispose(), [mat])
  return (
    <group position={[x, 0, z]}>
      <mesh material={mat} position={[0, 0.78, 0]}><capsuleGeometry args={[0.17, 1.0, 6, 12]} /></mesh>
      <mesh material={mat} position={[0, 1.6, 0]}><sphereGeometry args={[0.13, 16, 16]} /></mesh>
      <mesh material={mat} position={[0, 0.05, 0]} rotation={[-Math.PI / 2, 0, 0]}>
        <ringGeometry args={[0.18, 0.26, 24]} />
      </mesh>
    </group>
  )
}

/** Build + place the prop, return the group and its metre-space size. */
function PropModel({ assetKey, M, animate }) {
  const ref = useRef()
  const { obj } = useMemo(() => {
    const o = buildProp(assetKey || 'box', M)
    o.scale.setScalar(PROP_SCALE)
    o.updateMatrixWorld(true)
    const bb = new THREE.Box3().setFromObject(o)
    const c = new THREE.Vector3(); bb.getCenter(c)
    // recentre x/z, drop to floor
    o.position.set(-c.x, -bb.min.y, -c.z)
    return { obj: o }
  }, [assetKey, M])

  useEffect(() => () => obj.traverse((o) => o.geometry && o.geometry.dispose()), [obj])

  useFrame((state, dt) => {
    if (!animate) return
    const t = state.clock.elapsedTime
    obj.traverse((o) => {
      const u = o.userData; if (!u) return
      if (u.blink) o.visible = Math.sin(t * 6 + o.position.x * 3 + o.position.y) > -0.25
      if (u.spin) o.rotation.y += u.spin * dt
      if (u.press) { if (u.baseY === undefined) u.baseY = o.position.y; o.position.y = u.baseY + Math.sin(t * 2) * 0.35 }
      if (u.robot) { u.robot.arm1.rotation.y = Math.sin(t * 0.8) * 0.7; u.robot.arm2.rotation.x = Math.sin(t * 1.2) * 0.5 }
    })
  })

  return <primitive ref={ref} object={obj} />
}

/** Fits the camera to the prop's metre-space size on first mount. */
function FitCamera({ assetKey, M, interactive }) {
  const { camera } = useThree()
  useEffect(() => {
    const o = buildProp(assetKey || 'box', M); o.scale.setScalar(PROP_SCALE); o.updateMatrixWorld(true)
    const bb = new THREE.Box3().setFromObject(o); const s = new THREE.Vector3(); bb.getSize(s)
    o.traverse((m) => m.geometry && m.geometry.dispose())
    const r = Math.max(s.x, s.y, s.z, 0.8)
    const d = r * (interactive ? 1.7 : 1.95)
    camera.position.set(d * 0.85, Math.max(s.y * 0.75, d * 0.55), d * 1.0)
    camera.near = 0.05; camera.far = d * 20
    camera.lookAt(0, s.y * 0.42, 0)
    camera.updateProjectionMatrix()
  }, [assetKey, M, interactive, camera])
  return null
}

export default function AssetPreview({ assetKey, interactive = false, showHuman = true,
                                       showGrid = true, background = '#10131b' }) {
  const M = useMemo(() => makeMats(), [])
  useEffect(() => () => disposeMats(M), [M])

  return (
    <Canvas shadows={interactive} dpr={interactive ? [1, 2] : 1}
            frameloop={interactive ? 'always' : 'demand'}
            gl={{ antialias: true, toneMapping: THREE.ACESFilmicToneMapping, toneMappingExposure: 1.05 }}
            camera={{ fov: 42, position: [4, 3, 5] }}
            style={{ width: '100%', height: '100%' }}>
      <color attach="background" args={[background]} />
      <hemisphereLight args={['#dCE8ff', '#33312a', 0.85]} />
      <ambientLight intensity={0.35} />
      <directionalLight position={[6, 11, 5]} intensity={2.2} color="#fff2dc"
                        castShadow={interactive} shadow-mapSize={[1024, 1024]} />
      <RoomEnv />
      <FitCamera assetKey={assetKey} M={M} interactive={interactive} />
      <Suspense fallback={null}>
        <PropModel assetKey={assetKey} M={M} animate={interactive} />
      </Suspense>

      {showGrid && (
        <gridHelper args={[16, 16, '#33405a', '#222834']} position={[0, 0.001, 0]} />
      )}
      {showHuman && <HumanRef x={-1.6} z={1.0} />}

      <ContactShadows position={[0, 0.002, 0]} opacity={0.55} scale={14} blur={2.2} far={8}
                      resolution={interactive ? 1024 : 256} color="#000000" />
      {interactive && (
        <OrbitControls enableDamping autoRotate autoRotateSpeed={0.6}
                       minDistance={0.6} maxDistance={60} target={[0, 0.6, 0]} makeDefault />
      )}
    </Canvas>
  )
}
