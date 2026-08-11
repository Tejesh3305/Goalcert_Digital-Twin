/**
 * HospitalFloorPlanScene — walkable 3D reconstruction of the reference
 * imaging-suite floor plan. Brand-new scene: it does NOT reuse the BIM
 * viewer's scene graph or the legacy collinsEngine hospital — only the
 * shared prop/material library (frontend/src/three/).
 *
 * Modes: Walk (first-person, pointer lock, collision) · Overview (orbit,
 * dollhouse — ceilings off) · Plan (top-down orthographic, matches the
 * reference drawing) · Debug (1 m/0.1 m grid, collider boxes, room dims).
 *
 * Scale: 1 unit = 1 metre throughout.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { Canvas, useFrame, useThree } from '@react-three/fiber'
import { OrbitControls, OrthographicCamera } from '@react-three/drei'
import * as THREE from 'three'
import { RoomEnvironment } from 'three/examples/jsm/environments/RoomEnvironment.js'
import Architecture from './architecture/Architecture'
import Assets from './assets/Assets'
import PlayerRig from './systems/PlayerRig'
import DebugLayer from './systems/DebugLayer'
import FaultLayer, { EQ_PLACEMENTS } from './systems/FaultLayer'
import useImagingTwin from './systems/useImagingTwin'
import EquipmentPanel from './EquipmentPanel'
import { playerState } from './systems/collision'
import { PLAN, OFFSET, ROOM_COLORS } from './data/floorPlan'
import { POINT_LIGHTS } from './data/assetPlacements'
import { api } from '../../api/client'
import Maintenance from '../../collins/Maintenance'
import { toCollinsTwin } from '../../collins/adapter'
import '../../styles/copilot.css'
import './hospitalFloorPlan.css'

function RoomEnv() {
  const { gl, scene } = useThree()
  useEffect(() => {
    const pmrem = new THREE.PMREMGenerator(gl)
    const env = pmrem.fromScene(new RoomEnvironment(), 0.04)
    scene.environment = env.texture
    return () => { scene.environment = null; env.dispose(); pmrem.dispose() }
  }, [gl, scene])
  return null
}

function Lights({ mode }) {
  const walk = mode === 'walk'
  return (
    <group>
      <hemisphereLight args={['#e9f0fb', '#5c5446', walk ? 0.4 : 0.38]} />
      <ambientLight intensity={walk ? 0.34 : 0.26} />
      <directionalLight
        position={[42, 52, 26]} intensity={walk ? 0.7 : 1.6} castShadow
        shadow-mapSize={[2048, 2048]}
        shadow-camera-left={-32} shadow-camera-right={32}
        shadow-camera-top={32} shadow-camera-bottom={-32}
        shadow-camera-far={160} shadow-bias={-0.0004}
      />
      {POINT_LIGHTS.map(([x, z], i) => (
        <pointLight key={i}
          position={[x + OFFSET.x, PLAN.ceil - 0.35, z + OFFSET.z]}
          intensity={walk ? 4.5 : 2.5} distance={9} decay={2} color="#ffeacb" />
      ))}
    </group>
  )
}

function CameraRig({ mode, resetNonce, focusPos }) {
  const { camera, size } = useThree()
  const controlsRef = useRef(null)
  const flying = useRef(false)
  const goalPos = useRef(new THREE.Vector3())
  const goalLook = useRef(new THREE.Vector3())

  useEffect(() => {
    if (mode !== 'orbit') return
    if (focusPos) {
      // Repair-with-AI: fly in close to the faulted machine instead of the
      // default wide dollhouse view.
      goalPos.current.set(focusPos[0] + 3.2, focusPos[1] + 2.3, focusPos[2] + 3.2)
      goalLook.current.set(focusPos[0], focusPos[1] + PLAN.eyeHeight * 0.7, focusPos[2])
      flying.current = true
    } else {
      camera.position.set(27, 21, 31)
      camera.lookAt(0, 0, 0)
      controlsRef.current?.target.set(0, 0, 0)
      flying.current = false
    }
  }, [mode, camera, resetNonce, focusPos])

  useFrame(() => {
    if (!flying.current || !controlsRef.current) return
    camera.position.lerp(goalPos.current, 0.07)
    controlsRef.current.target.lerp(goalLook.current, 0.07)
    controlsRef.current.update()
    if (camera.position.distanceTo(goalPos.current) < 0.05) flying.current = false
  })

  if (mode === 'orbit') {
    return (
      <OrbitControls ref={controlsRef} maxPolarAngle={Math.PI / 2.05}
                     minDistance={1.2} maxDistance={140} enableDamping makeDefault />
    )
  }
  if (mode === 'top') {
    const zoom = Math.min(size.width / (PLAN.width + 5), size.height / (PLAN.depth + 5))
    return (
      <>
        <OrthographicCamera makeDefault position={[0, 60, 0]} zoom={zoom}
                            near={0.5} far={200} up={[0, 0, -1]} />
        <OrbitControls enableRotate={false} enableDamping screenSpacePanning makeDefault />
      </>
    )
  }
  return null
}

const LEGEND = [
  ['Consultants', ROOM_COLORS.consult],
  ['Reception / Waiting', ROOM_COLORS.reception],
  ['MRI zone', ROOM_COLORS.imaging],
  ['CT', ROOM_COLORS.ct],
  ['WC / Change', ROOM_COLORS.sanitary],
  ['Office / Staff / Store', ROOM_COLORS.support],
  ['Plant', ROOM_COLORS.plant],
  ['Circulation', ROOM_COLORS.corridor],
]

export default function HospitalFloorPlanScene() {
  const [mode, setMode] = useState('orbit')      // 'walk' | 'orbit' | 'top'
  const [debug, setDebug] = useState(false)
  const [locked, setLocked] = useState(false)
  const [resetNonce, setResetNonce] = useState(0)
  const [coords, setCoords] = useState(null)

  // ── live fault mechanism (hospital-imaging machine twin) ──
  const twin = useImagingTwin()
  const [selectedEq, setSelectedEq] = useState(null)
  const [faultMenu, setFaultMenu] = useState(false)
  const [repairOpen, setRepairOpen] = useState(false)
  const [wo, setWo] = useState({ loading: false })

  const selectedUnit = twin.equipment.find((e) => e.id === selectedEq) || null
  const alarms = twin.equipment.filter((e) => e.status !== 'ok').length

  // Repair-with-AI focuses whichever unit is actually faulted — the same id
  // FaultLayer red-highlights — falling back to the open panel's own unit so
  // the camera still has somewhere to go if nothing is faulted yet.
  const repairFocusId = twin.faultTarget || selectedUnit?.id || null
  const repairFocusPos = useMemo(() => {
    const p = repairFocusId && EQ_PLACEMENTS[repairFocusId]
    return p ? [p.pos[0] + OFFSET.x, p.y || 0, p.pos[1] + OFFSET.z] : null
  }, [repairFocusId])

  // Zoom the camera in close on the faulted machine the moment repair
  // starts. Setting mode + repairOpen together (one batched render) means
  // CameraRig sees `mode === 'orbit'` and the focus target on the very
  // first render it's asked to fly — no lagging frame stuck in walk/top view.
  const openRepair = () => {
    setMode('orbit')
    setLocked(false)
    setRepairOpen(true)
  }

  const generateWorkOrder = async () => {
    setWo({ loading: true })
    try {
      // Anchor the AI on the unit whose panel is actually open — otherwise
      // every panel requests the same suite-wide work order and the result
      // reads identically regardless of which machine is faulted.
      const machine = selectedUnit
        ? `${selectedUnit.label} (${selectedUnit.id}) — Hospital Imaging Suite`
        : 'Hospital Imaging Suite'
      const data = await api.copilot.workOrder({
        tenant: twin.tenant, machine,
        domain: 'hospital-imaging', horizon_label: '2 hours',
      })
      setWo({ loading: false, data })
    } catch (e) {
      setWo({ loading: false, error: e?.message || 'request failed' })
    }
  }

  const injectFault = async (fid) => {
    setFaultMenu(false)
    const target = twin.faultInfo?.[fid]?.target
    if (target) setSelectedEq(target)
    await twin.inject(fid)
  }

  // Optional ?spawn=x,z,yaw (plan metres) — QA / deep-link starting point.
  const spawnOverride = useMemo(() => {
    const raw = new URLSearchParams(window.location.search).get('spawn')
    if (!raw) return undefined
    const [x, z, yaw = 0] = raw.split(',').map(Number)
    return Number.isFinite(x) && Number.isFinite(z) ? { pos: [x, z], yaw: Number(yaw) || 0 } : undefined
  }, [])

  useEffect(() => {
    if (mode !== 'walk') { setCoords(null); return }
    const id = setInterval(() => {
      setCoords({ x: playerState.x - OFFSET.x, z: playerState.z - OFFSET.z })
    }, 200)
    return () => clearInterval(id)
  }, [mode])

  const chip = (m, label, icon) => (
    <button key={m} className={`hfp-chip ${mode === m ? 'active' : ''}`}
            onClick={() => { setMode(m); setLocked(false) }}>
      <i className={`ti ${icon}`} /> {label}
    </button>
  )

  return (
    <div className="hfp-root">
      <Canvas
        shadows dpr={[1, 2]}
        gl={{ antialias: true, toneMapping: THREE.ACESFilmicToneMapping, toneMappingExposure: 0.95 }}
        camera={{ position: [27, 21, 31], fov: 55, near: 0.1, far: 400 }}
      >
        <color attach="background" args={['#c9d5e0']} />
        <RoomEnv />
        <Lights mode={mode} />
        <group position={[OFFSET.x, 0, OFFSET.z]}>
          <Architecture mode={mode} />
          <Assets mode={mode} onSelectEquipment={setSelectedEq} />
          <FaultLayer equipment={twin.equipment} faultTarget={twin.faultTarget}
                      selectedEq={selectedEq} />
        </group>
        {debug && <DebugLayer />}
        <PlayerRig active={mode === 'walk'} resetNonce={resetNonce}
                   onLockChange={setLocked} {...(spawnOverride ? { spawn: spawnOverride } : {})} />
        <CameraRig mode={mode} resetNonce={resetNonce} focusPos={repairOpen ? repairFocusPos : null} />
      </Canvas>

      {/* enter-walk overlay: stays mounted so PointerLockControls keeps its click target */}
      {mode === 'walk' && (
        <div className={`hfp-enter hfp-lock-target ${locked ? 'hidden' : ''}`}>
          <div className="hfp-enter-card">
            <div className="hfp-enter-title">Click to walk</div>
            <div className="hfp-enter-keys">
              <span>W A S D</span> move · <span>Mouse</span> look ·
              <span> Shift</span> run · <span>Esc</span> release
            </div>
          </div>
        </div>
      )}

      {mode === 'walk' && locked && <div className="hfp-reticle" />}

      <div className="hfp-hud">
        {chip('walk', 'Walk', 'ti-walk')}
        {chip('orbit', 'Overview', 'ti-rotate-360')}
        {chip('top', 'Plan', 'ti-map-2')}
        <span className="hfp-sep" />
        <button className={`hfp-chip ${faultMenu ? 'active' : ''} ${alarms ? 'alarm' : ''}`}
                onClick={() => setFaultMenu((v) => !v)}>
          <i className="ti ti-alert-triangle" /> Faults{alarms ? ` (${alarms})` : ''}
        </button>
        {twin.state && (
          <span className={`hfp-chip hfp-health-chip ${twin.state.health < 0.4 ? 'alarm' : ''}`}>
            <i className="ti ti-heartbeat" /> {Math.round((twin.state.health ?? 1) * 100)}%
          </span>
        )}
        <span className="hfp-sep" />
        <button className={`hfp-chip ${debug ? 'active' : ''}`} onClick={() => setDebug(!debug)}>
          <i className="ti ti-ruler-measure" /> Debug
        </button>
        <button className="hfp-chip" onClick={() => setResetNonce((n) => n + 1)}>
          <i className="ti ti-refresh" /> Reset
        </button>
        <button className="hfp-chip" onClick={() => window.history.back()}>
          <i className="ti ti-arrow-left" /> Exit
        </button>
      </div>

      {faultMenu && (
        <div className="hfp-faultmenu">
          <div className="hfp-faultmenu-title">
            Inject a fault
            {twin.available === false && <span className="hfp-err"> · twin offline</span>}
          </div>
          {twin.activeFault && (
            <button className="hfp-faultmenu-item hfp-faultmenu-clear" disabled={twin.busy}
                    onClick={() => { setFaultMenu(false); twin.clear() }}>
              <i className="ti ti-checkbox" /> Clear active fault
              <span>{twin.faultInfo?.[twin.activeFault]?.label || twin.activeFault}</span>
            </button>
          )}
          {Object.entries(twin.faultInfo).map(([id, f]) => (
            <button key={id} className="hfp-faultmenu-item"
                    disabled={twin.busy || twin.available === false}
                    onClick={() => injectFault(id)}>
              <i className="ti ti-bolt" /> {f.label}
              <span>{f.target}</span>
            </button>
          ))}
        </div>
      )}

      {selectedUnit && !repairOpen && (
        <EquipmentPanel
          unit={selectedUnit}
          faultInfo={twin.faultInfo}
          activeFault={twin.activeFault}
          busy={twin.busy}
          offline={twin.available === false}
          onInject={(fid) => injectFault(fid)}
          onClear={twin.clear}
          onRepair={openRepair}
          onWorkOrder={generateWorkOrder}
          workOrder={wo}
          onClose={() => { setSelectedEq(null); setWo({ loading: false }) }}
        />
      )}

      {repairOpen && (
        <Maintenance
          domain="hospital-imaging"
          machineName={selectedUnit?.label || 'Hospital Imaging Suite'}
          twin={toCollinsTwin('hospital-imaging', twin.state)}
          focusUnit={repairFocusId}
          reuseScene
          claudeOn
          onExit={() => setRepairOpen(false)}
        />
      )}

      {mode !== 'walk' && (
        <div className="hfp-legend">
          <div className="hfp-legend-title">Hospital Imaging Suite — 36.0 × 17.6 m</div>
          {LEGEND.map(([name, color]) => (
            <div key={name} className="hfp-legend-row">
              <span className="hfp-swatch" style={{ background: color }} />{name}
            </div>
          ))}
        </div>
      )}

      <div className="hfp-status">
        {coords && <span>x {coords.x.toFixed(1)} m · z {coords.z.toFixed(1)} m</span>}
        {debug && <span> · grid 1 m / 0.1 m · scale 1 unit = 1 m</span>}
        {mode === 'top' && <span> · drag to pan · scroll to zoom</span>}
      </div>
    </div>
  )
}
