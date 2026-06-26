/**
 * BimViewer — the interactive 3-D twin.
 *
 * Renders an `nxr-scene/1` scene with archviz lighting (IBL via <Environment>),
 * bloom, and the ported equipment props. Adds the operator controls the roadmap
 * calls for: orbit/pan/zoom, floor isolation, click-an-asset → properties +
 * findings drawer, and live severity colouring keyed by entityId from
 * /topology (+ the live event stream), so a Tier-C finding lights the real room.
 */
import { Suspense, useEffect, useMemo, useState } from 'react'
import { Canvas, useThree } from '@react-three/fiber'
import { OrbitControls, ContactShadows } from '@react-three/drei'
import { EffectComposer, Bloom } from '@react-three/postprocessing'
import * as THREE from 'three'
import { RoomEnvironment } from 'three/examples/jsm/environments/RoomEnvironment.js'
import Scene from '../three/Scene'
import { usePolling } from '../hooks/useApi'
import { useEventStream } from '../hooks/useEventStream'
import { STATUS_COLOR } from '../three/materials'
import api from '../api/client'

function deriveStatus(nodes = []) {
  const map = {}
  for (const n of nodes) {
    const fc = n.findings
    if (fc?.critical > 0) map[n.id] = 'crit'
    else if (fc?.warning > 0) map[n.id] = 'warn'
    else if (fc?.total > 0) map[n.id] = 'info'
    else if (n.severity === 'critical') map[n.id] = 'crit'
    else if (n.severity === 'warning') map[n.id] = 'warn'
  }
  return map
}

/** Procedural image-based lighting (no network) — the demo's RoomEnvironment. */
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
  const w = maxx - minx, l = maxz - minz
  const d = Math.max(w, l, top) * 1.35
  return { position: [d * 0.62, Math.max(top * 1.4, d * 0.55), d * 0.78], target: [0, top * 0.28, 0] }
}

export default function BimViewer({ scene: sceneProp, tenant }) {
  // When no scene is passed, rebuild it from the twin's graph by tenant.
  const [fetched, setFetched] = useState(null)
  useEffect(() => {
    if (sceneProp || !tenant) return
    let alive = true
    api.twinSceneByTenant(tenant)
      .then((r) => { if (alive) setFetched(r.scene_result) })
      .catch(() => {})
    return () => { alive = false }
  }, [sceneProp, tenant])
  const scene = sceneProp || fetched

  const fit = useMemo(() => cameraFit(scene), [scene])
  const levels = scene?.levels || []
  const multi = levels.length > 1

  const [visibleLevels, setVisibleLevels] = useState(null) // null = all
  const [selected, setSelected] = useState(null) // scene node
  const [showRoof, setShowRoof] = useState(false) // dollhouse (roof off) by default

  // Live status: poll topology; nudge on new bus events.
  const { events } = useEventStream(tenant, { max: 20 })
  const { data: topo, refetch } = usePolling(
    () => api.topology(tenant), 2500, [tenant], { skip: !tenant },
  )
  useEffect(() => { if (events.length) refetch() }, [events.length]) // eslint-disable-line
  const statusMap = useMemo(() => deriveStatus(topo?.nodes), [topo])
  const nodeInfo = useMemo(() => {
    const m = {}
    for (const n of (topo?.nodes || [])) m[n.id] = n
    return m
  }, [topo])

  const counts = useMemo(() => {
    let crit = 0, warn = 0
    for (const v of Object.values(statusMap)) { if (v === 'crit') crit++; else if (v === 'warn') warn++ }
    return { crit, warn }
  }, [statusMap])

  const toggleLevel = (idx) => {
    setVisibleLevels((cur) => {
      const all = levels.map((l) => l.index)
      const set = new Set(cur || all)
      if (set.has(idx)) set.delete(idx); else set.add(idx)
      return set.size === 0 || set.size === all.length ? null : set
    })
  }
  const visSet = visibleLevels ? new Set(visibleLevels) : null

  if (!scene) {
    return <div className="bim-loading"><span className="spinner" /> Reconstructing scene…</div>
  }
  if (!scene.nodes?.length) {
    return (
      <div className="bim-loading" style={{ flexDirection: 'column', gap: 6 }}>
        <i className="ti ti-cube-off" style={{ fontSize: 28, opacity: 0.6 }} />
        <div>This twin has no 3-D geometry yet.</div>
        <div style={{ fontSize: 11 }}>Build one from a 2-D plan in BIM Studio.</div>
      </div>
    )
  }

  return (
    <div className="bim-viewer">
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
          <Scene scene={scene} statusMap={statusMap} visibleLevels={visSet}
                 selectedId={selected?.entityId} showRoof={showRoof}
                 onPick={(node) => setSelected(node)} />
        </Suspense>
        <ContactShadows position={[0, 0.02, 0]} opacity={0.5} scale={Math.max(60, (scene.bbox?.max?.[0] || 30) * 4)}
                        blur={2.4} far={40} resolution={1024} color="#000000" />
        <OrbitControls target={fit.target} maxPolarAngle={Math.PI / 2.05}
                       enableDamping minDistance={4} maxDistance={900} makeDefault />
        <EffectComposer disableNormalPass>
          <Bloom intensity={0.5} luminanceThreshold={0.72} luminanceSmoothing={0.25} mipmapBlur />
        </EffectComposer>
      </Canvas>

      {/* HUD: roof toggle + floor isolation */}
      <div className="bim-hud">
        <div className={`chip ${showRoof ? '' : 'active'}`} onClick={() => setShowRoof((v) => !v)}>
          <i className={`ti ${showRoof ? 'ti-home' : 'ti-home-2'}`} /> {showRoof ? 'Roof on' : 'Dollhouse'}
        </div>
        {multi && (
          <div className={`chip ${!visibleLevels ? 'active' : ''}`} onClick={() => setVisibleLevels(null)}>All floors</div>
        )}
        {multi && levels.map((l) => (
          <div key={l.index} className={`chip ${visSet?.has(l.index) ? 'active' : ''}`}
               onClick={() => toggleLevel(l.index)}>L{l.index}</div>
        ))}
      </div>

      {/* Legend */}
      <div className="bim-legend">
        <span><i style={{ background: '#16a34a' }} /> healthy</span>
        <span><i style={{ background: '#f59e0b' }} /> warning {counts.warn ? `· ${counts.warn}` : ''}</span>
        <span><i style={{ background: '#f43f5e' }} /> critical {counts.crit ? `· ${counts.crit}` : ''}</span>
      </div>

      {/* Click → details drawer */}
      {selected && (
        <Drawer node={selected} tenant={tenant} info={nodeInfo[selected.entityId]}
                onClose={() => setSelected(null)} />
      )}
    </div>
  )
}

function Drawer({ node, tenant, info, onClose }) {
  const [entity, setEntity] = useState(null)
  useEffect(() => {
    let alive = true
    if (node.entityId && tenant) {
      api.getEntity(node.entityId, tenant).then((e) => { if (alive) setEntity(e) }).catch(() => {})
    } else setEntity(null)
    return () => { alive = false }
  }, [node.entityId, tenant])

  const typeShort = (node.type || '').split('#').pop()
  const fc = info?.findings
  const props = entity?.node || {}
  const skip = new Set(['id', 'tenantId', 'canonicalType', 'createdBy', 'changeLogRef',
    'createdAt', 'updatedAt', 'displayName', 'tags'])
  const rows = Object.entries(props)
    .filter(([k, v]) => !skip.has(k) && v != null && typeof v !== 'object')
    .slice(0, 12)

  return (
    <div className="bim-drawer">
      <i className="ti ti-x close" onClick={onClose} />
      <h4>{node.label || typeShort}</h4>
      <div className="sub">{typeShort} · {node.kind}</div>

      {fc && (fc.critical || fc.warning) ? (
        <div style={{ margin: '12px 0', display: 'flex', gap: 8 }}>
          {fc.critical > 0 && <span style={{ color: '#f43f5e', fontSize: 12 }}>● {fc.critical} critical</span>}
          {fc.warning > 0 && <span style={{ color: '#f59e0b', fontSize: 12 }}>● {fc.warning} warning</span>}
        </div>
      ) : node.entityId ? (
        <div style={{ margin: '12px 0', color: '#5fd08a', fontSize: 12 }}>● healthy — no active findings</div>
      ) : null}

      <div style={{ marginTop: 10 }}>
        {rows.length === 0 && <div className="sub">No additional properties.</div>}
        {rows.map(([k, v]) => (
          <div className="kv" key={k}><span className="k">{k}</span><span>{String(v)}</span></div>
        ))}
      </div>
      {!node.entityId && <div className="sub" style={{ marginTop: 12 }}>Structural element (not a tracked asset).</div>}
    </div>
  )
}
