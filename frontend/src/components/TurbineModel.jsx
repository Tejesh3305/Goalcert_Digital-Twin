/**
 * TurbineModel — renders the gas-turbine GLB (from the collins-demo _models set)
 * with drei's useGLTF, centred + auto-scaled, health-driven emissive glow, and
 * live sensor hotspots. Ported from the collins TurbineModel; hotspot signal keys
 * are this backend's turbine:* keys. Fully offline (local GLB, local lights).
 */
import React, { Suspense, useMemo, useState } from 'react'
import { Canvas, useFrame } from '@react-three/fiber'
import { OrbitControls, useGLTF, Html, Bounds, ContactShadows } from '@react-three/drei'
import * as THREE from 'three'

const MODEL_URL = '/models/turbine.glb'

// Minimal signal metadata for the hotspots (labels/units + thresholds).
const SIG = {
  'turbine:egt': { label: 'EGT', unit: '°C', warn: 820, crit: 900 },
  'turbine:n1': { label: 'N1', unit: 'RPM' },
  'turbine:vibration': { label: 'Vibration', unit: 'g', warn: 0.9, crit: 1.1 },
  'turbine:oilTemp': { label: 'Oil Temp', unit: '°C', warn: 125, crit: 140 },
  'turbine:oilPressure': { label: 'Oil Press', unit: 'PSI', warnLow: 30, critLow: 25 },
  'turbine:fuelFlow': { label: 'Fuel Flow', unit: 'kg/h' },
}
const HOTSPOTS = [
  ['turbine:egt', [1.1, 0.2, 0]],
  ['turbine:n1', [-1.1, 0, 0]],
  ['turbine:vibration', [0, 1.1, 0.2]],
  ['turbine:oilTemp', [0.2, -0.9, 0.4]],
  ['turbine:oilPressure', [-0.4, -0.4, -1.0]],
  ['turbine:fuelFlow', [0.4, 0.3, 1.0]],
]
const HOT_COLOR = { '': '#16a34a', warn: '#d97706', crit: '#e11d48' }
const fmt = (v) => (v == null ? '—' : Math.abs(v) >= 100 ? Math.round(v).toLocaleString() : Number(v).toFixed(Math.abs(v) < 10 ? 2 : 1))
function sevClass(sig, v) {
  const m = SIG[sig]; if (!m || v == null) return ''
  if (m.crit != null && v >= m.crit) return 'crit'
  if (m.critLow != null && v <= m.critLow) return 'crit'
  if (m.warn != null && v >= m.warn) return 'warn'
  if (m.warnLow != null && v <= m.warnLow) return 'warn'
  return ''
}

function Model({ url, health }) {
  const { scene } = useGLTF(url)
  const ref = React.useRef()
  const cloned = useMemo(() => {
    const s = scene.clone(true)
    const box = new THREE.Box3().setFromObject(s)
    const size = new THREE.Vector3(); box.getSize(size)
    const center = new THREE.Vector3(); box.getCenter(center)
    const scale = 3 / (Math.max(size.x, size.y, size.z) || 1)
    s.position.sub(center)
    s.scale.setScalar(scale)
    s.traverse((o) => { if (o.isMesh) { o.castShadow = true; o.receiveShadow = true } })
    return s
  }, [scene])

  useFrame(() => {
    if (!ref.current || health == null) return
    const h = Math.max(0, Math.min(1, health))
    const emissive = h > 0.7 ? new THREE.Color(0, 0, 0)
      : h > 0.4 ? new THREE.Color(0.15, 0.08, 0)
        : new THREE.Color(0.2, 0.02, 0.02)
    ref.current.traverse((o) => {
      if (o.isMesh && o.material && o.material.emissive) o.material.emissive.lerp(emissive, 0.05)
    })
  })
  return <primitive ref={ref} object={cloned} />
}

function Hotspot({ pos, sig, value }) {
  const [open, setOpen] = useState(false)
  const color = HOT_COLOR[sevClass(sig, value)]
  const m = SIG[sig] || { label: sig, unit: '' }
  return (
    <group position={pos}>
      <Html center distanceFactor={8}>
        <div onClick={() => setOpen((o) => !o)} style={{ cursor: 'pointer' }}>
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
  return <Html center><div style={{ color: '#aab0e0', fontFamily: 'JetBrains Mono, monospace', fontSize: 12 }}>{label}</div></Html>
}

export default function TurbineModel({ url = MODEL_URL, latest = {}, height = 320, health = null }) {
  return (
    <div style={{ height, borderRadius: 12, overflow: 'hidden', background: '#0b0d18', position: 'relative' }}>
      <Canvas shadows camera={{ position: [4, 2.5, 4.5], fov: 48 }} dpr={[1, 2]}>
        <color attach="background" args={['#0b0d18']} />
        <hemisphereLight args={['#e6ecff', '#1a2038', 0.7]} />
        <ambientLight intensity={0.4} />
        <directionalLight position={[5, 8, 5]} intensity={1.2} castShadow />
        <directionalLight position={[-5, 3, -4]} intensity={0.4} color="#9ec9ff" />
        <Suspense fallback={<Fallback label="Loading 3D model…" />}>
          <Bounds fit clip observe margin={1.2}>
            <Model url={url} health={health} />
          </Bounds>
          {HOTSPOTS.filter(([s]) => latest[s] != null).map(([s, pos]) => (
            <Hotspot key={s} pos={pos} sig={s} value={latest[s]} />
          ))}
        </Suspense>
        <ContactShadows position={[0, -1.6, 0]} opacity={0.5} scale={10} blur={2.4} far={4} />
        <OrbitControls enablePan={false} autoRotate autoRotateSpeed={0.6} minDistance={3} maxDistance={12} />
      </Canvas>
      <div style={{ position: 'absolute', top: 12, left: 12, background: 'rgba(12,14,28,.72)',
        border: '1px solid rgba(124,58,237,.4)', color: '#dfe3ff', fontFamily: 'JetBrains Mono, monospace',
        fontSize: 11, padding: '6px 12px', borderRadius: 999 }}>
        ⬡ 3D Twin · click a hotspot for live value
      </div>
    </div>
  )
}

useGLTF.preload(MODEL_URL)
