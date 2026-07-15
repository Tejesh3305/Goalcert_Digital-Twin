/**
 * GlbViewer — generic GLB preview (auto-centred + auto-scaled), no hotspots.
 * Renders TRELLIS/RunPod-reconstructed object scans (Build a Twin + the twin's
 * dashboard hero), where the generated mesh — not a stock model — is the point.
 * Wrapped in an error boundary so a failed/missing GLB shows a message instead
 * of crashing the panel (drei's useGLTF throws on load failure).
 */
import React, { Component, Suspense, useMemo } from 'react'
import { Canvas } from '@react-three/fiber'
import { OrbitControls, useGLTF, Html, Bounds, ContactShadows } from '@react-three/drei'

function Model({ url }) {
  const { scene } = useGLTF(url)
  const cloned = useMemo(() => {
    const s = scene.clone(true)
    s.traverse((o) => { if (o.isMesh) { o.castShadow = true; o.receiveShadow = true } })
    return s
  }, [scene])
  return <primitive object={cloned} />
}

function Center({ children }) {
  return <Html center><div style={{ color: '#aab0e0', fontFamily: 'JetBrains Mono, monospace', fontSize: 12, textAlign: 'center' }}>{children}</div></Html>
}

/** Catches GLTF load errors thrown through Suspense so the canvas doesn't crash. */
class GlbErrorBoundary extends Component {
  constructor(props) { super(props); this.state = { failed: false } }
  static getDerivedStateFromError() { return { failed: true } }
  componentDidCatch(err) { console.error('GLB load failed', err) }
  render() {
    if (this.state.failed) return <Center>Could not load the 3-D model.<br />It may still be exporting — reopen in a moment.</Center>
    return this.props.children
  }
}

export default function GlbViewer({ url, height = 420, label = 'TRELLIS reconstruction' }) {
  if (!url) return (
    <div style={{ height, display: 'flex', alignItems: 'center', justifyContent: 'center', background: '#0b0d18', color: '#aab0e0', fontFamily: 'JetBrains Mono, monospace', fontSize: 12, borderRadius: 12 }}>
      No model to display.
    </div>
  )
  return (
    <div style={{ height, borderRadius: 12, overflow: 'hidden', background: '#0b0d18', position: 'relative' }}>
      <Canvas shadows camera={{ position: [4, 2.5, 4.5], fov: 48 }} dpr={[1, 2]}>
        <color attach="background" args={['#0b0d18']} />
        <hemisphereLight args={['#e6ecff', '#1a2038', 0.7]} />
        <ambientLight intensity={0.4} />
        <directionalLight position={[5, 8, 5]} intensity={1.2} castShadow />
        <directionalLight position={[-5, 3, -4]} intensity={0.4} color="#9ec9ff" />
        <GlbErrorBoundary>
          <Suspense fallback={<Center>Loading 3-D model…</Center>}>
            <Bounds fit clip observe margin={1.2}>
              <Model url={url} />
            </Bounds>
          </Suspense>
        </GlbErrorBoundary>
        <ContactShadows position={[0, -1.6, 0]} opacity={0.5} scale={10} blur={2.4} far={4} />
        <OrbitControls enablePan autoRotate autoRotateSpeed={0.6} minDistance={0.5} maxDistance={20} />
      </Canvas>
      <div style={{ position: 'absolute', top: 12, left: 12, background: 'rgba(12,14,28,.72)',
        border: '1px solid rgba(124,58,237,.4)', color: '#dfe3ff', fontFamily: 'JetBrains Mono, monospace',
        fontSize: 11, padding: '6px 12px', borderRadius: 999 }}>
        ⬡ {label}
      </div>
    </div>
  )
}
