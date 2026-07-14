/**
 * Viewer.jsx — standalone 3-D viewer for the 2-D→3-D app.
 *
 * Pure renderer of an `nxr-scene/1` scene graph: archviz lighting (procedural
 * RoomEnvironment IBL), bloom, soft + contact shadows, dollhouse roof toggle,
 * floor isolation, and click-an-element → info drawer. No backend / live status
 * here — this app is about turning a plan into a beautiful, navigable 3-D model.
 */
import { Suspense, useEffect, useMemo, useState } from 'react'
import { Canvas, useThree } from '@react-three/fiber'
import { OrbitControls, ContactShadows } from '@react-three/drei'
import { EffectComposer, Bloom } from '@react-three/postprocessing'
import * as THREE from 'three'
import { RoomEnvironment } from 'three/examples/jsm/environments/RoomEnvironment.js'
import Scene from './three/Scene'

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

function cameraFit(scene) {
  const bb = scene?.bbox
  if (!bb) return { position: [60, 50, 70], target: [0, 6, 0] }
  const [minx, , minz] = bb.min
  const [maxx, top, maxz] = bb.max
  const d = Math.max(maxx - minx, maxz - minz, top) * 1.35
  return { position: [d * 0.62, Math.max(top * 1.4, d * 0.55), d * 0.78], target: [0, top * 0.28, 0] }
}

export default function Viewer({ scene }) {
  const fit = useMemo(() => cameraFit(scene), [scene])
  const levels = scene?.levels || []
  const multi = levels.length > 1
  const [visibleLevels, setVisibleLevels] = useState(null)
  const [showRoof, setShowRoof] = useState(false)
  const [selected, setSelected] = useState(null)

  const toggleLevel = (idx) => setVisibleLevels((cur) => {
    const all = levels.map((l) => l.index)
    const set = new Set(cur || all)
    set.has(idx) ? set.delete(idx) : set.add(idx)
    return set.size === 0 || set.size === all.length ? null : set
  })
  const visSet = visibleLevels ? new Set(visibleLevels) : null

  if (!scene?.nodes?.length) {
    return <div className="viewer empty">No 3-D yet — drop a plan and press Build.</div>
  }

  return (
    <div className="viewer">
      <Canvas shadows dpr={[1, 2]}
              gl={{ antialias: true, toneMapping: THREE.ACESFilmicToneMapping, toneMappingExposure: 1.05 }}
              camera={{ position: fit.position, fov: 40, near: 0.5, far: 3000 }}
              onPointerMissed={() => setSelected(null)}>
        <color attach="background" args={['#1b1e26']} />
        <fog attach="fog" args={['#1b1e26', 160, 520]} />
        <hemisphereLight args={['#dCE8ff', '#3a3326', 0.7]} />
        <ambientLight intensity={0.28} />
        <directionalLight position={[60, 110, 50]} intensity={2.4} color="#fff2dc"
                          castShadow shadow-mapSize={[2048, 2048]} shadow-bias={-0.0004}
                          shadow-camera-left={-140} shadow-camera-right={140}
                          shadow-camera-top={140} shadow-camera-bottom={-140} shadow-camera-far={400} />
        <RoomEnv />
        <Suspense fallback={null}>
          <Scene scene={scene} visibleLevels={visSet} showRoof={showRoof}
                 selectedId={selected?.id} onPick={(n) => setSelected(n)} />
        </Suspense>
        <ContactShadows position={[0, 0.02, 0]} opacity={0.5}
                        scale={Math.max(60, (scene.bbox?.max?.[0] || 30) * 4)}
                        blur={2.4} far={40} resolution={1024} color="#000000" />
        <OrbitControls target={fit.target} maxPolarAngle={Math.PI / 2.05}
                       enableDamping minDistance={4} maxDistance={900} makeDefault />
        <EffectComposer disableNormalPass>
          <Bloom intensity={0.5} luminanceThreshold={0.72} luminanceSmoothing={0.25} mipmapBlur />
        </EffectComposer>
      </Canvas>

      <div className="hud">
        <div className={`chip ${showRoof ? '' : 'active'}`} onClick={() => setShowRoof((v) => !v)}>
          {showRoof ? 'Roof on' : 'Dollhouse'}
        </div>
        {multi && <div className={`chip ${!visibleLevels ? 'active' : ''}`} onClick={() => setVisibleLevels(null)}>All floors</div>}
        {multi && levels.map((l) => (
          <div key={l.index} className={`chip ${visSet?.has(l.index) ? 'active' : ''}`}
               onClick={() => toggleLevel(l.index)}>L{l.index}</div>
        ))}
      </div>

      {selected && (
        <div className="drawer">
          <i className="close" onClick={() => setSelected(null)}>×</i>
          <h4>{selected.label || selected.id}</h4>
          <div className="sub">{(selected.type || '').split('#').pop()} · {selected.kind}</div>
          {selected.roomType && <div className="kv"><span>type</span><b>{selected.roomType}</b></div>}
          {selected.material && <div className="kv"><span>floor</span><b>{selected.material}</b></div>}
          {selected.geometry?.prop && <div className="kv"><span>prop</span><b>{selected.geometry.prop}</b></div>}
        </div>
      )}
    </div>
  )
}
