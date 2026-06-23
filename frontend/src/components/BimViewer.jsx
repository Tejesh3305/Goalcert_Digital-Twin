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
import { OrbitControls } from '@react-three/drei'
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
      <Canvas shadows dpr={[1, 2]} gl={{ antialias: true }}
              camera={{ position: fit.position, fov: 42, near: 0.5, far: 2000 }}
              onPointerMissed={() => setSelected(null)}>
        <color attach="background" args={['#0b0d18']} />
        <fog attach="fog" args={['#0b0d18', 120, 360]} />
        <hemisphereLight args={['#cfe0ff', '#1a1d2e', 0.55]} />
        <ambientLight intensity={0.25} />
        <directionalLight position={[40, 80, 30]} intensity={1.4} color="#fff3df"
                          castShadow shadow-mapSize={[2048, 2048]}
                          shadow-camera-left={-120} shadow-camera-right={120}
                          shadow-camera-top={120} shadow-camera-bottom={-120} />
        <RoomEnv />
        <Suspense fallback={null}>
          <Scene scene={scene} statusMap={statusMap} visibleLevels={visSet}
                 selectedId={selected?.entityId}
                 onPick={(node) => setSelected(node)} />
        </Suspense>
        <OrbitControls target={fit.target} maxPolarAngle={Math.PI / 2.05}
                       enableDamping minDistance={6} maxDistance={600} makeDefault />
        <EffectComposer disableNormalPass>
          <Bloom intensity={0.7} luminanceThreshold={0.65} luminanceSmoothing={0.2} mipmapBlur />
        </EffectComposer>
      </Canvas>

      {/* HUD: floor isolation */}
      {multi && (
        <div className="bim-hud">
          <div className={`chip ${!visibleLevels ? 'active' : ''}`} onClick={() => setVisibleLevels(null)}>All floors</div>
          {levels.map((l) => (
            <div key={l.index} className={`chip ${visSet?.has(l.index) ? 'active' : ''}`}
                 onClick={() => toggleLevel(l.index)}>L{l.index}</div>
          ))}
        </div>
      )}

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
