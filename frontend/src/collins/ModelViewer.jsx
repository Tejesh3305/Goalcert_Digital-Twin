// ModelViewer.jsx — a generic glTF/GLB viewer for the reconstructed 3-D models
// (RunPod/TRELLIS output). Loads any .glb, normalises + auto-frames it, orbit
// controls, studio lighting, and optional live sensor hotspots pinned around it.
import React, { Suspense, useEffect, useMemo } from 'react'
import { Canvas, useThree } from '@react-three/fiber'
import { OrbitControls, useGLTF, Html, Bounds, ContactShadows } from '@react-three/drei'
import * as THREE from 'three'
import { RoomEnvironment } from 'three/examples/jsm/environments/RoomEnvironment.js'
import { SIG, sevClass, fmt } from './lib.jsx'

const HOT_COLOR = { '': '#16a34a', warn: '#d97706', crit: '#e11d48' }

/** Offline image-based lighting (procedural RoomEnvironment) so reconstructed
 *  metal/PBR meshes render bright and natural rather than flat/dim. */
function NeutralEnv() {
  const { gl, scene } = useThree()
  useEffect(() => {
    const pmrem = new THREE.PMREMGenerator(gl)
    const env = pmrem.fromScene(new RoomEnvironment(), 0.04)
    scene.environment = env.texture
    return () => { env.texture.dispose(); pmrem.dispose(); scene.environment = null }
  }, [gl, scene])
  return null
}

function Model({ url }) {
  const { scene } = useGLTF(url)
  // center at origin + scale to a ~3-unit box so the camera framing is stable
  const cloned = useMemo(() => {
    const s = scene.clone(true)
    const box = new THREE.Box3().setFromObject(s)
    const size = new THREE.Vector3(); box.getSize(size)
    const center = new THREE.Vector3(); box.getCenter(center)
    const scale = 3 / (Math.max(size.x, size.y, size.z) || 1)
    s.position.sub(center)
    s.scale.setScalar(scale)
    s.traverse(o => {
      if (!o.isMesh) return
      o.castShadow = true; o.receiveShadow = true
      const mats = Array.isArray(o.material) ? o.material : [o.material]
      mats.forEach((m) => { if (m && 'envMapIntensity' in m) { m.envMapIntensity = 1.3; m.needsUpdate = true } })
    })
    return s
  }, [scene])
  return <primitive object={cloned} />
}

function Hotspot({ pos, sig, value }) {
  const [open, setOpen] = React.useState(false)
  const sev = sevClass(sig, value)
  const color = HOT_COLOR[sev]
  const m = SIG[sig] || { label: sig, unit: '' }
  return (
    <group position={pos}>
      <Html center distanceFactor={8}>
        <div onClick={() => setOpen(o => !o)} style={{ cursor: 'pointer' }}>
          <div style={{ width: 14, height: 14, borderRadius: '50%', background: color,
            border: '2px solid #fff', boxShadow: `0 0 0 4px ${color}44`, margin: '0 auto' }} />
          {open && (
            <div style={{ marginTop: 6, background: 'rgba(12,14,28,.92)', color: '#fff',
              border: `1px solid ${color}`, borderRadius: 8, padding: '5px 9px',
              fontFamily: 'JetBrains Mono, monospace', fontSize: 11, whiteSpace: 'nowrap' }}>
              {m.label}: <b>{fmt(value)}</b> {m.unit}
            </div>
          )}
        </div>
      </Html>
    </group>
  )
}

function Fallback({ label }) {
  return <Html center><div style={{ color: '#aab0e0', fontFamily: 'JetBrains Mono, monospace', fontSize: 12,
    display: 'flex', alignItems: 'center', gap: 8 }}><span className="spinner" style={{ borderTopColor: '#7c3aed' }} />{label}</div></Html>
}

export default function ModelViewer({ url, height = 320, autoRotate = true, hotspots = [], latest = {}, badge }) {
  return (
    <div style={{ height, borderRadius: 18, overflow: 'hidden', position: 'relative',
      background: 'radial-gradient(circle at 50% 34%, #33405f 0%, #141a2c 52%, #0a0c16 100%)' }}>
      <Canvas shadows camera={{ position: [3.2, 2, 3.6], fov: 46 }} dpr={[1, 2]}
        gl={{ alpha: true, antialias: true, toneMapping: THREE.ACESFilmicToneMapping, toneMappingExposure: 1.15 }}>
        <NeutralEnv />
        <hemisphereLight args={['#eef3ff', '#26304a', 1.0]} />
        <ambientLight intensity={0.5} />
        <directionalLight position={[5, 8, 5]} intensity={1.9} castShadow />
        <directionalLight position={[-5, 3, -4]} intensity={0.7} color="#a8cbff" />
        <directionalLight position={[0, 2, -6]} intensity={0.6} color="#ffd9a8" />
        <Suspense fallback={<Fallback label="Loading 3D model…" />}>
          <Bounds fit clip observe margin={1.2}>
            <Model url={url} />
          </Bounds>
          {hotspots.filter(([s]) => latest[s] != null).map(([s, pos]) => (
            <Hotspot key={s} pos={pos} sig={s} value={latest[s]} />
          ))}
        </Suspense>
        <ContactShadows position={[0, -1.55, 0]} opacity={0.5} scale={10} blur={2.4} far={4} />
        <OrbitControls enablePan={false} autoRotate={autoRotate} autoRotateSpeed={0.7} minDistance={2} maxDistance={40} />
      </Canvas>
      {badge && (
        <div style={{ position: 'absolute', top: 12, left: 12, background: 'rgba(12,14,28,.72)',
          border: '1px solid rgba(124,58,237,.4)', color: '#dfe3ff', fontFamily: 'JetBrains Mono, monospace',
          fontSize: 11, padding: '6px 12px', borderRadius: 999, display: 'flex', alignItems: 'center', gap: 6 }}>
          {badge}
        </div>
      )}
    </div>
  )
}
