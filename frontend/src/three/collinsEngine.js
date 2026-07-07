// engine.js — the procedural 3-D twin engine, ported from the demo.html viewer.
// Vanilla Three.js (mounted by Scene3D.jsx into a host div). Builds a realistic,
// orbitable scene per domain with walls, floors, status pins, an add-asset
// palette, and a click-to-inspect panel whose "Ask AI" button calls back into
// the app for a Claude status of that specific component.
import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import { RoomEnvironment } from 'three/examples/jsm/environments/RoomEnvironment.js'

let Bloom = null
async function loadBloom() {
  if (Bloom !== null) return Bloom
  try {
    const [{ EffectComposer }, { RenderPass }, { UnrealBloomPass }, { OutputPass }] = await Promise.all([
      import('three/examples/jsm/postprocessing/EffectComposer.js'),
      import('three/examples/jsm/postprocessing/RenderPass.js'),
      import('three/examples/jsm/postprocessing/UnrealBloomPass.js'),
      import('three/examples/jsm/postprocessing/OutputPass.js'),
    ])
    Bloom = { EffectComposer, RenderPass, UnrealBloomPass, OutputPass }
  } catch (e) { Bloom = false }
  return Bloom
}

// ── tiny DOM helper ──────────────────────────────────────────────────
function el(tag, props, ...kids) {
  const n = document.createElement(tag)
  if (props) for (const [k, v] of Object.entries(props)) {
    if (v == null || v === false) continue
    if (k === 'class') n.className = v
    else if (k === 'html') n.innerHTML = v
    else if (k === 'style' && typeof v === 'object') Object.assign(n.style, v)
    else if (k.startsWith('on') && typeof v === 'function') n.addEventListener(k.slice(2).toLowerCase(), v)
    else n.setAttribute(k, v === true ? '' : v)
  }
  for (const c of kids.flat()) { if (c == null || c === false) continue; n.append(c.nodeType ? c : document.createTextNode(c)) }
  return n
}
const icon = (n) => el('i', { class: `ti ${n}` })
const rnd = (a, b) => a + Math.random() * (b - a)
const fmt = (n) => (n >= 1000 ? (n / 1000).toFixed(n % 1000 ? 1 : 0) + 'k' : '' + Math.round(n))
const statusColor = { ok: '#16a34a', warn: '#d97706', crit: '#e11d48' }

// ── geometry helpers ─────────────────────────────────────────────────
const box = (w, h, d, mat, x = 0, y = 0, z = 0) => { const m = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), mat); m.position.set(x, y, z); m.castShadow = true; m.receiveShadow = true; return m }
const cyl = (rt, rb, h, mat, x = 0, y = 0, z = 0, seg = 20) => { const m = new THREE.Mesh(new THREE.CylinderGeometry(rt, rb, h, seg), mat); m.position.set(x, y, z); m.castShadow = true; m.receiveShadow = true; return m }
const grp = (...ch) => { const g = new THREE.Group(); ch.flat().forEach(c => c && g.add(c)); return g }

function makeMats() {
  return {
    concrete: new THREE.MeshStandardMaterial({ color: 0x70747e, roughness: .96 }),
    slab: new THREE.MeshStandardMaterial({ color: 0xc9ccd4, roughness: .85, metalness: .05 }),
    metal: new THREE.MeshStandardMaterial({ color: 0xa7adb8, roughness: .34, metalness: .95, envMapIntensity: 1 }),
    steel: new THREE.MeshStandardMaterial({ color: 0x6b7280, roughness: .45, metalness: .85 }),
    dark: new THREE.MeshStandardMaterial({ color: 0x20232c, roughness: .5, metalness: .6 }),
    rack: new THREE.MeshStandardMaterial({ color: 0x17181e, roughness: .55, metalness: .4 }),
    white: new THREE.MeshStandardMaterial({ color: 0xeef1f5, roughness: .6, metalness: .05 }),
    plastic: new THREE.MeshStandardMaterial({ color: 0x2a2e38, roughness: .7, metalness: .1 }),
    rubber: new THREE.MeshStandardMaterial({ color: 0x16181d, roughness: .95 }),
    glass: new THREE.MeshStandardMaterial({ color: 0x9fc2e8, roughness: .05, transparent: true, opacity: .22, envMapIntensity: 1.6 }),
    accent: new THREE.MeshStandardMaterial({ color: 0x7c3aed, roughness: .4, metalness: .3, emissive: 0x3b1378, emissiveIntensity: .4 }),
    amber: new THREE.MeshStandardMaterial({ color: 0xf59e0b, roughness: .5, metalness: .4 }),
    brass: new THREE.MeshStandardMaterial({ color: 0xd8b48a, roughness: .4, metalness: .7 }),
    warm: new THREE.MeshStandardMaterial({ color: 0xfff1d6, emissive: 0xffcf8a, emissiveIntensity: 1.6 }),
    cool: new THREE.MeshStandardMaterial({ color: 0xeaf3ff, emissive: 0x9ec9ff, emissiveIntensity: 1.4 }),
    screen: new THREE.MeshStandardMaterial({ color: 0x0a1530, emissive: 0x2f6bd6, emissiveIntensity: .9 }),
    led: (c) => new THREE.MeshStandardMaterial({ color: c, emissive: c, emissiveIntensity: 1.8 }),
  }
}

// ── props ────────────────────────────────────────────────────────────
function pStatusPin(color) { const g = new THREE.Group(); const m = new THREE.MeshStandardMaterial({ color, emissive: color, emissiveIntensity: 1.6 }); const s = new THREE.Mesh(new THREE.SphereGeometry(.32, 16, 16), m); const halo = new THREE.Mesh(new THREE.SphereGeometry(.55, 16, 16), new THREE.MeshBasicMaterial({ color, transparent: true, opacity: .22 })); g.add(s, halo); g.userData.pin = true; return g }
function pRack(M) { const g = grp(); g.add(box(2, 7.4, 2.6, M.rack, 0, 3.7, 0)); g.add(box(1.7, 7, .1, M.dark, 0, 3.7, 1.31)); for (let i = 0; i < 10; i++) { const c = Math.random() > .5 ? 0x22d3ee : 0x16a34a; const led = new THREE.Mesh(new THREE.BoxGeometry(.12, .16, .04), M.led(c)); led.position.set(-.7, 1.1 + i * .62, 1.34); led.userData.blink = true; g.add(led) } g.add(box(2.1, .2, 2.7, M.dark, 0, .1, 0)); return g }
function pCRAC(M) { const g = grp(); g.add(box(3.4, 6, 2.4, M.metal, 0, 3, 0)); for (let i = 0; i < 6; i++) g.add(box(2.8, .18, .06, M.dark, 0, 1.2 + i * .7, 1.22)); g.add(cyl(1, 1, .3, M.dark, 0, 6.2, 0)); const blades = grp(); for (let i = 0; i < 4; i++) { const b = box(1.8, .06, .3, M.steel); b.rotation.y = i * Math.PI / 2; blades.add(b) } blades.position.y = 6.4; blades.userData.spin = .9; g.add(blades); return g }
function pUPS(M) { const g = grp(); g.add(box(2.6, 5.4, 2, M.dark, 0, 2.7, 0)); g.add(box(2, 1.2, .08, M.screen, 0, 4.2, 1.01)); const led = new THREE.Mesh(new THREE.BoxGeometry(.5, .16, .05), M.led(0x16a34a)); led.position.set(0, 2.6, 1.01); g.add(led); return g }
function pNetwork(M) { const g = grp(); g.add(box(2, 4.6, 2.4, M.rack, 0, 2.3, 0)); for (let i = 0; i < 8; i++) { const c = Math.random() > .4 ? 0x22d3ee : 0xf59e0b; const l = new THREE.Mesh(new THREE.BoxGeometry(.1, .1, .04), M.led(c)); l.position.set(-.6 + (i % 4) * .4, 1.4 + Math.floor(i / 4) * .5, 1.21); l.userData.blink = true; g.add(l) } return g }
function pAHU(M) { const g = grp(); g.add(box(5, 2.4, 2.6, M.metal, 0, 1.2, 0)); g.add(cyl(.9, .9, .4, M.dark, -1.4, 1.2, 1.4)); const fan = grp(); for (let i = 0; i < 4; i++) { const b = box(1.4, .05, .25, M.steel); b.rotation.y = i * Math.PI / 2; fan.add(b) } fan.position.set(-1.4, 1.2, 1.62); fan.rotation.x = Math.PI / 2; fan.userData.spin = 1.2; g.add(fan); g.add(box(2, 1, 2.6, M.dark, 1.6, 1.2, 0)); return g }
function pMeter(M) { const g = grp(); g.add(box(1.6, 3, .8, M.white, 0, 1.5, 0)); g.add(box(1.1, 1, .06, M.screen, 0, 2, .42)); return g }
function pSmallRack(M) { const g = grp(); g.add(box(1.6, 3.4, 1.6, M.rack, 0, 1.7, 0)); for (let i = 0; i < 5; i++) { const l = new THREE.Mesh(new THREE.BoxGeometry(.1, .1, .04), M.led(0xe11d48)); l.position.set(-.4, 1 + i * .5, .81); l.userData.blink = true; g.add(l) } return g }
function pMRI(M) { const g = grp(); g.add(box(5, 3.6, 4.5, M.white, 0, 1.8, 0)); const ring = new THREE.Mesh(new THREE.TorusGeometry(1.5, .7, 16, 28), M.metal); ring.position.set(0, 1.9, 2.4); g.add(ring); const hole = new THREE.Mesh(new THREE.CircleGeometry(1.4, 28), M.screen); hole.position.set(0, 1.9, 2.41); g.add(hole); g.add(box(1, .4, 3, M.white, 0, 1.4, 4)); return g }
function pGas(M) { const g = grp(); g.add(box(2.4, 3.2, 1, M.white, 0, 1.6, 0)); for (let i = 0; i < 3; i++) g.add(cyl(.35, .35, 2.2, M.led([0x16a34a, 0x2563eb, 0xf59e0b][i]), -.7 + i * .7, 1.4, .5)); return g }
function pLAF(M) { const g = grp(); g.add(box(4, .5, 4, M.white, 0, 0, 0)); g.add(box(3.6, .1, 3.6, M.cool, 0, -.25, 0)); return g }
function pNurse(M) { const g = grp(); g.add(box(1.4, 3, .6, M.white, 0, 1.5, 0)); g.add(box(1, 1.1, .05, M.screen, 0, 2, .32)); const l = new THREE.Mesh(new THREE.BoxGeometry(.4, .2, .1), M.led(0xe11d48)); l.position.set(0, 2.8, .3); g.add(l); return g }
function pFridge(M) { const g = grp(); g.add(box(2, 3.6, 2, M.white, 0, 1.8, 0)); g.add(box(1.4, 3, .06, M.glass, 0, 1.9, 1.01)); const l = new THREE.Mesh(new THREE.BoxGeometry(.8, .2, .05), M.led(0x2563eb)); l.position.set(0, 3.3, 1.01); g.add(l); return g }
function pPress(M) { const g = grp(); g.add(box(3.4, 5.5, 3, M.steel, 0, 2.75, 0)); g.add(box(2.6, .6, 2.4, M.dark, 0, 4.6, 0)); const ram = box(2.2, 1.4, 2, M.metal, 0, 2.6, 0); ram.userData.press = true; g.add(ram); g.add(box(3.8, .8, 3.4, M.dark, 0, .4, 0)); return g }
function pRobot(M) { const g = grp(); g.add(cyl(1, 1.3, .6, M.dark, 0, .3, 0)); const base = cyl(.8, .8, 1.2, M.amber, 0, 1.1, 0); g.add(base); const arm1 = grp(); arm1.add(box(.7, 3, .7, M.amber, 0, 1.5, 0)); arm1.position.set(0, 1.7, 0); const arm2 = grp(); arm2.add(box(.55, 2.4, .55, M.amber, 0, 1.2, 0)); arm2.position.set(0, 3, 0); arm2.add(box(.4, .8, .4, M.dark, 0, 2.4, 0)); arm1.add(arm2); g.add(arm1); g.userData.robot = { base, arm1, arm2 }; return g }
function pConveyor(M) { const g = grp(); g.add(box(8, .4, 2, M.rubber, 0, 1.4, 0)); for (let i = -3; i <= 3; i++) g.add(box(.3, 1.4, 2.2, M.steel, i * 1.2, .7, 0)); for (let i = 0; i < 5; i++) { const bx = box(.7, .7, .7, M.amber, -3 + i * 1.5, 1.9, 0); bx.userData.cargo = true; g.add(bx) } g.add(box(8.2, .2, 2.4, M.dark, 0, 1.62, 0)); return g }
function pCNC(M) { const g = grp(); g.add(box(4, 4, 3, M.white, 0, 2, 0)); g.add(box(2.2, 2, .06, M.glass, 0, 2.2, 1.51)); g.add(box(1, 2.6, .5, M.dark, 2.2, 1.6, 0)); g.add(box(.06, 1, .8, M.screen, 2.46, 2.4, 0)); const l = new THREE.Mesh(new THREE.BoxGeometry(.6, .2, .1), M.led(0xe11d48)); l.position.set(0, 4.2, 1); g.add(l); return g }
function pWelder(M) { const g = grp(); g.add(box(2, 3, 1.6, M.dark, 0, 1.5, 0)); const arm = cyl(.15, .15, 2.4, M.steel, 0, 2.4, 1); arm.rotation.x = .5; g.add(arm); const tip = new THREE.Mesh(new THREE.SphereGeometry(.25, 12, 12), M.led(0x66ccff)); tip.position.set(0, 1.4, 2); tip.userData.blink = true; g.add(tip); return g }
function pCompressor(M) { const g = grp(); g.add(box(3, 2.4, 2, M.amber, 0, 1.2, 0)); const tank = cyl(.8, .8, 3.4, M.metal); tank.rotation.z = Math.PI / 2; tank.position.set(0, 1.6, -.4); g.add(tank); const motor = cyl(.6, .6, 1.4, M.steel, 0, 2.4, .6); motor.rotation.z = Math.PI / 2; g.add(motor); return g }
function pSensor(M) { const g = grp(); g.add(cyl(.4, .5, .8, M.white, 0, .4, 0)); const l = new THREE.Mesh(new THREE.SphereGeometry(.18, 12, 12), M.led(0x22d3ee)); l.position.set(0, .9, 0); l.userData.blink = true; g.add(l); return g }

// EDM subsystem props
function pEdmGenerator(M) { const g = grp(); g.add(box(3, 5, 2.4, M.dark, 0, 2.5, 0)); g.add(box(2.4, 1.4, .08, M.screen, 0, 3.6, 1.21)); for (let i = 0; i < 4; i++) { const l = new THREE.Mesh(new THREE.BoxGeometry(.5, .12, .05), M.led(0x22d3ee)); l.position.set(-.6 + i * .4, 1.6, 1.21); l.userData.blink = true; g.add(l) } return g }
function pEdmDielectric(M) { const g = grp(); g.add(box(4, 3.4, 3, M.metal, 0, 1.7, 0)); g.add(cyl(.7, .7, 2, M.steel, 1.6, 1.7, 1.4)); const pump = cyl(.6, .6, 1.2, M.amber, -1.4, 1.2, 1.5); pump.rotation.x = Math.PI / 2; g.add(pump); g.add(box(2.6, .8, 3, M.glass, 0, 2.6, 0)); return g }
function pEdmWireFeed(M) { const g = grp(); g.add(box(2.4, 4.4, 2, M.dark, 0, 2.2, 0)); const spool = cyl(1.2, 1.2, 1, M.brass, 0, 3.4, 1.1, 24); spool.rotation.x = Math.PI / 2; spool.userData.spin = 1.6; g.add(spool); return g }
function pEdmMachine(M) {
  const g = grp()
  // Light-gray machine body material (RAL 7035)
  const bodyMat = new THREE.MeshStandardMaterial({ color: 0xd4d8de, roughness: .55, metalness: .45 })
  const colMat = new THREE.MeshStandardMaterial({ color: 0xa8adb6, roughness: .6, metalness: .4 })
  const tankGlass = new THREE.MeshStandardMaterial({ color: 0x80c4cc, roughness: .05, transparent: true, opacity: .28, envMapIntensity: 1.6 })
  const fluidMat = new THREE.MeshStandardMaterial({ color: 0x6bbcc8, roughness: .05, transparent: true, opacity: .14 })
  const wpMat = new THREE.MeshStandardMaterial({ color: 0x8892a0, roughness: .35, metalness: .85 })

  // Machine base / bed (wide, low, light gray)
  g.add(box(6, 1.0, 4.5, bodyMat, 0, .5, 0))
  // Lower housing with servo bay doors
  g.add(box(5.6, 1.8, 4.2, bodyMat, 0, 1.9, 0))
  g.add(box(2.4, 1.2, .06, M.dark, -1.2, 1.7, 2.14))  // left door panel
  g.add(box(2.4, 1.2, .06, M.dark, 1.4, 1.7, 2.14))    // right door panel

  // Main column (rear-left, rigid C-frame casting — substantial)
  g.add(box(1.6, 7.2, 1.6, colMat, -2.2, 4.4, -1.4))

  // Horizontal U/V arm (extends from column top over the tank)
  g.add(box(4.2, .45, .55, colMat, .2, 7.7, -1.0))
  // Z-axis carriage (vertical travel on the arm for upper guide head)
  g.add(box(.5, 1.6, .5, M.dark, 1.8, 7.0, -1.0))

  // Upper wire guide head (precision assembly at arm tip, over the tank)
  g.add(box(.35, .45, .35, M.metal, 1.8, 6.1, -0.2))
  // Flush nozzles on guide head (two small cylinders)
  const nL = cyl(.05, .05, .3, M.steel, 1.55, 6.1, -0.2, 8); nL.rotation.z = Math.PI/2; g.add(nL)
  const nR = cyl(.05, .05, .3, M.steel, 2.05, 6.1, -0.2, 8); nR.rotation.z = Math.PI/2; g.add(nR)

  // Lower wire guide head (submerged, below worktable surface)
  g.add(box(.35, .4, .35, M.metal, 1.8, 2.0, -0.2))

  // Dielectric tank (transparent, sits on the worktable — the dominant visual element)
  g.add(box(3.2, 2.4, 2.8, tankGlass, 1.2, 3.6, -0.1))
  // Dielectric fluid fill inside the tank (slightly more opaque, blue-green tint)
  g.add(box(3.0, 2.0, 2.6, fluidMat, 1.2, 3.3, -0.1))

  // Workpiece (metal block inside tank, on the worktable)
  g.add(box(1.2, .65, .8, wpMat, 1.8, 2.7, -0.2))

  // The wire (very thin vertical line between guide heads)
  const wire = cyl(.015, .015, 4.0, M.brass, 1.8, 4.1, -0.2, 6)
  wire.userData.glow = true; g.add(wire)

  // Spark point (tiny, at the wire/workpiece interface — warm yellow)
  const sparkMat = M.led(0xffe566)
  const spk = new THREE.Mesh(new THREE.SphereGeometry(.07, 10, 10), sparkMat)
  spk.position.set(1.8, 3.05, -0.2)
  spk.userData.spark = true; g.add(spk)
  // Add a point light for flickering spark glow
  const sparkLight = new THREE.PointLight(0xffe566, .6, 2.0)
  sparkLight.position.copy(spk.position)
  sparkLight.userData.spark = true; g.add(sparkLight)

  // CNC control panel (pendant on right side)
  g.add(box(.08, 1.8, .06, M.steel, 3.2, 5.5, .8))      // vertical arm
  g.add(box(.8, .5, .06, M.steel, 3.6, 6.2, .8))         // horizontal mount
  g.add(box(.9, .7, .06, M.screen, 3.6, 5.5, .85))       // CNC touchscreen
  g.add(box(.75, .3, .06, M.dark, 3.6, 4.9, .85))        // button cluster
  // E-stop (red circle)
  const estop = new THREE.Mesh(new THREE.CylinderGeometry(.08, .08, .04, 12), M.led(0xe11d48))
  estop.position.set(3.95, 4.9, .88); estop.rotation.x = Math.PI/2; g.add(estop)

  // Wire spool cabinet (adjacent to column, integrated)
  g.add(box(1.0, 2.8, .9, M.dark, -3.6, 1.4, -1.4))
  const spool = cyl(.5, .5, .45, M.brass, -3.6, 2.6, -.9, 24)
  spool.rotation.x = Math.PI/2; spool.userData.spin = .3; g.add(spool)

  // Status tower light on top of column
  const tower = cyl(.07, .07, .35, M.led(0x16a34a), -2.2, 8.2, -1.4, 8)
  tower.userData.blink = true; g.add(tower)

  return g
}

const PROP_FOR = {
  'Compute Rack': pRack, 'Cooling Unit': pCRAC, 'Power / UPS': pUPS, 'Network': pNetwork,
  'Air Handler': pAHU, 'Energy': pMeter, 'IT / BMS': pSmallRack, 'Imaging': pMRI,
  'Medical Gas': pGas, 'Clean Air': pLAF, 'Safety': pNurse, 'Cold Chain': pFridge,
  'Stamping': pPress, 'Robotics': pRobot, 'Material': pConveyor, 'Machining': pCNC,
  'Welding': pWelder, 'Utility': pCompressor, 'Sensor': pSensor,
  'EDM Machine': pEdmMachine, 'Discharge Generator': pEdmGenerator,
  'Dielectric System': pEdmDielectric, 'Wire Transport': pEdmWireFeed, 'Guides & Axes': pSmallRack,
}

const PALETTE = {
  datacenter: [['Server Rack', 'ti-server', 'Compute Rack'], ['CRAC Unit', 'ti-snowflake', 'Cooling Unit'], ['UPS', 'ti-battery-charging', 'Power / UPS'], ['Sensor', 'ti-businessplan', 'Sensor']],
  hospital: [['Med Gas', 'ti-vaccine', 'Medical Gas'], ['Cold Chain', 'ti-temperature', 'Cold Chain'], ['Nurse Call', 'ti-urgent', 'Safety'], ['Sensor', 'ti-businessplan', 'Sensor']],
  factory: [['Robot Arm', 'ti-robot', 'Robotics'], ['Conveyor', 'ti-arrows-right', 'Material'], ['CNC', 'ti-settings', 'Machining'], ['Sensor', 'ti-businessplan', 'Sensor']],
  edm: [['Sensor', 'ti-businessplan', 'Sensor'], ['Dielectric Unit', 'ti-droplet', 'Dielectric System']],
}

// ── environment asset layouts (positions in metres; f = floor) ───────
const ENV = {
  datacenter: { name: 'Helix Data Center', icon: 'ti-server-2', accent: 0x22d3ee, floors: 1,
    assets: [
      { id: 'RACK-A1', name: 'Rack A1', type: 'Compute Rack', f: 0, x: -12, z: -6, status: 'ok', icon: 'ti-server', metrics: [['Load', '%', 58], ['Inlet', '°C', 22]] },
      { id: 'RACK-A4', name: 'Rack A4', type: 'Compute Rack', f: 0, x: -4, z: -6, status: 'warn', icon: 'ti-server', metrics: [['Load', '%', 88], ['Inlet', '°C', 29]] },
      { id: 'RACK-B2', name: 'Rack B2', type: 'Compute Rack', f: 0, x: -8, z: 6, status: 'crit', icon: 'ti-server', metrics: [['Load', '%', 97], ['Inlet', '°C', 34]] },
      { id: 'CRAC-1', name: 'CRAC-1', type: 'Cooling Unit', f: 0, x: 14, z: -8, status: 'ok', icon: 'ti-snowflake', metrics: [['Supply', '°C', 16], ['Fan', '%', 64]] },
      { id: 'UPS-1', name: 'UPS-1', type: 'Power / UPS', f: 0, x: 14, z: 6, status: 'ok', icon: 'ti-battery-charging', metrics: [['Charge', '%', 100], ['Load', 'kW', 142]] },
      { id: 'NET-CORE', name: 'Core Switch', type: 'Network', f: 0, x: 14, z: 0, status: 'ok', icon: 'ti-network', metrics: [['Tput', 'Gb', 412], ['Errors', '', 0]] },
    ] },
  hospital: { name: 'St. Vera Hospital', icon: 'ti-building-hospital', accent: 0x14b8a6, floors: 2,
    assets: [
      { id: 'MRI-1', name: 'MRI-1', type: 'Imaging', f: 0, x: 12, z: -7, status: 'ok', icon: 'ti-scan', metrics: [['He', '%', 88], ['Field', 'T', 3.0]] },
      { id: 'ED-HVAC', name: 'ED HVAC', type: 'Air Handler', f: 0, x: -12, z: 8, status: 'warn', icon: 'ti-air-conditioning', metrics: [['ACH', '/h', 9], ['Filter', '%', 62]] },
      { id: 'PHARM', name: 'Pharmacy Fridge', type: 'Cold Chain', f: 0, x: 11, z: 8, status: 'warn', icon: 'ti-temperature', metrics: [['Temp', '°C', 6.4], ['Door', '', 1]] },
      { id: 'OR-GAS', name: 'OR Med Gas', type: 'Medical Gas', f: 1, x: -11, z: -7, status: 'ok', icon: 'ti-vaccine', metrics: [['O₂', 'bar', 4.1], ['Alarms', '', 0]] },
      { id: 'OR-LAF', name: 'OR Laminar Flow', type: 'Clean Air', f: 1, x: -4, z: -7, status: 'crit', icon: 'ti-wind', metrics: [['Vel', 'm/s', 0.18], ['Parts', '', 0]] },
      { id: 'ICU-NC', name: 'ICU Nurse Call', type: 'Safety', f: 1, x: 11, z: 6, status: 'ok', icon: 'ti-urgent', metrics: [['Active', '', 3], ['Resp', 's', 42]] },
    ] },
  factory: { name: 'Forge Plant 7', icon: 'ti-building-factory-2', accent: 0xf59e0b, floors: 1,
    assets: [
      { id: 'PRESS-1', name: 'Press-1', type: 'Stamping', f: 0, x: -13, z: -6, status: 'ok', icon: 'ti-hammer', metrics: [['Cycle', 'spm', 42], ['Vib', 'mm/s', 2.1]] },
      { id: 'ROBOT-3', name: 'Robot Arm 3', type: 'Robotics', f: 0, x: -4, z: -6, status: 'warn', icon: 'ti-robot', metrics: [['Torque', '%', 82], ['Temp', '°C', 58]] },
      { id: 'CONV-A', name: 'Conveyor A', type: 'Material', f: 0, x: 4, z: 2, status: 'ok', icon: 'ti-arrows-right', metrics: [['Speed', 'm/s', 1.2], ['Load', '%', 64]] },
      { id: 'CNC-7', name: 'CNC-7', type: 'Machining', f: 0, x: 13, z: -6, status: 'crit', icon: 'ti-settings', metrics: [['Spindle', 'k', 12], ['Vib', 'mm/s', 7.8]] },
      { id: 'WELD-2', name: 'Welder 2', type: 'Welding', f: 0, x: -13, z: 7, status: 'ok', icon: 'ti-flame', metrics: [['Amp', 'A', 180], ['Duty', '%', 60]] },
      { id: 'COMP-1', name: 'Compressor', type: 'Utility', f: 0, x: 14, z: 8, status: 'ok', icon: 'ti-engine', metrics: [['Bar', 'bar', 7.2], ['kW', '', 45]] },
    ] },
  edm: { name: 'Wire EDM Machine', icon: 'ti-grill', accent: 0x7c3aed, floors: 1,
    assets: [
      { id: 'EDM-1', name: 'Wire EDM Machine', type: 'EDM Machine', f: 0, x: 0, z: 0, status: 'ok', icon: 'ti-grill', metrics: [['Cut', 'mm²/min', 150], ['Gap V', 'V', 52]] },
      { id: 'GEN-1', name: 'Discharge Generator', type: 'Discharge Generator', f: 0, x: -7, z: -5, status: 'ok', icon: 'ti-bolt', metrics: [['Peak I', 'A', 18], ['Short', '%', 5]] },
      { id: 'DIE-1', name: 'Dielectric & Flushing', type: 'Dielectric System', f: 0, x: 7, z: -5, status: 'ok', icon: 'ti-droplet', metrics: [['Cond', 'µS/cm', 10], ['Temp', '°C', 24]] },
      { id: 'WIRE-1', name: 'Wire Transport', type: 'Wire Transport', f: 0, x: -7, z: 5, status: 'ok', icon: 'ti-line-dashed', metrics: [['Tension', 'N', 15], ['Feed', 'm/min', 9]] },
      { id: 'GUIDE-1', name: 'Guides & Axes', type: 'Guides & Axes', f: 0, x: 7, z: 5, status: 'ok', icon: 'ti-square', metrics: [['Wear', '%', 0], ['Ra', 'µm', 1.5]] },
    ] },
}

// ── structures (building shells, walls, floors) ──────────────────────
const FH = 11
function addWalls(M, scene, w, h, d, col) { const wm = new THREE.MeshStandardMaterial({ color: col, roughness: .9, metalness: .1, side: THREE.DoubleSide }); scene.add(box(w, h, .4, wm, 0, h / 2, -d / 2)); scene.add(box(.4, h, d, wm, -w / 2, h / 2, 0)); scene.add(box(.4, h, d, wm, w / 2, h / 2, 0)) }
function ceilingLights(M, scene, w, d, h, nx, nz) { for (let i = 0; i < nx; i++) for (let j = 0; j < nz; j++) { const x = -w / 2 + w / (nx + 1) * (i + 1), z = -d / 2 + d / (nz + 1) * (j + 1); const p = new THREE.Mesh(new THREE.BoxGeometry(3, .2, 1), M.cool); p.position.set(x, h - .4, z); scene.add(p) } }
function facade(M, scene, w, d, h, y) { const fr = M.dark; scene.add(box(w, h - 1, .12, M.glass, 0, y + h / 2, -d / 2)); scene.add(box(w, h - 1, .12, M.glass, 0, y + h / 2, d / 2)); scene.add(box(.12, h - 1, d, M.glass, -w / 2, y + h / 2, 0)); scene.add(box(.12, h - 1, d, M.glass, w / 2, y + h / 2, 0)); for (let i = -w / 2; i <= w / 2; i += 5) { scene.add(box(.18, h - 1, .18, fr, i, y + h / 2, -d / 2)); scene.add(box(.18, h - 1, .18, fr, i, y + h / 2, d / 2)) } }
function hospBed(M) { const g = grp(); g.add(box(1.4, .1, 3, M.metal, 0, 1.3, 0)); g.add(box(1.3, .3, 2.8, M.white, 0, 1.45, 0)); g.add(box(1.3, .3, .6, M.white, 0, 1.7, -1.1)); g.add(box(1, .4, .5, M.cool, 0, 1.8, -1.2)); return g }
function dividers(M, scene, y) { for (let i = -1; i <= 1; i++) scene.add(box(.15, 3.5, 12, M.glass, i * 9, y + 1.75, 0)) }

function structure(key, M, scene) {
  const e = ENV[key]
  if (key === 'datacenter') {
    scene.add(box(44, .3, 34, M.slab, 0, .15, 0))
    addWalls(M, scene, 44, 12, 34, 0x2a2d3a)
    ceilingLights(M, scene, 44, 34, 12, 4, 3)
    for (let r = 0; r < 2; r++) for (let i = 0; i < 3; i++) { const rk = pRack(M); rk.position.set(-6 + i * 4, 0, r ? 10 : -12); scene.add(rk) }
  } else if (key === 'hospital') {
    for (let f = 0; f < e.floors; f++) {
      const y = f * FH
      scene.add(box(42, .5, 32, M.slab, 0, y + .25, 0))
      facade(M, scene, 42, 32, FH, y)
      for (let i = 0; i < 4; i++) { const b = hospBed(M); b.position.set(-13 + i * 4, y, f === 0 ? 7 : 5); scene.add(b) }
      dividers(M, scene, y)
    }
    scene.add(box(6, e.floors * FH, 6, M.dark, -16, e.floors * FH / 2, -13)) // core/lift
  } else if (key === 'factory') {
    scene.add(box(50, .3, 38, M.concrete, 0, .15, 0))
    addWalls(M, scene, 50, 14, 38, 0x3a3d48)
    for (let i = -4; i <= 4; i++) scene.add(box(50, .3, .3, M.steel, 0, 13.5, i * 4))
    scene.add(box(.6, .6, 38, M.amber, 0, 12, 0)); scene.add(box(2, 1, 2, M.dark, 0, 11, -2))
    ceilingLights(M, scene, 50, 38, 14, 5, 4)
  } else if (key === 'edm') {
    scene.add(box(40, .3, 32, M.concrete, 0, .15, 0))
    addWalls(M, scene, 40, 12, 32, 0x2a2d3a)
    ceilingLights(M, scene, 40, 32, 12, 3, 3)
  }
}

function skyTexture() { const c = document.createElement('canvas'); c.width = 8; c.height = 256; const ctx = c.getContext('2d'); const g = ctx.createLinearGradient(0, 0, 0, 256); g.addColorStop(0, '#0a1230'); g.addColorStop(.45, '#1d2c5a'); g.addColorStop(.75, '#39477f'); g.addColorStop(1, '#5566a0'); ctx.fillStyle = g; ctx.fillRect(0, 0, 8, 256); const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; return t }
function groundMat() { const c = document.createElement('canvas'); c.width = c.height = 512; const ctx = c.getContext('2d'); ctx.fillStyle = '#2c3142'; ctx.fillRect(0, 0, 512, 512); for (let i = 0; i < 2200; i++) { ctx.fillStyle = `rgba(255,255,255,${Math.random() * 0.04})`; ctx.fillRect(Math.random() * 512, Math.random() * 512, 2, 2) } const tex = new THREE.CanvasTexture(c); tex.wrapS = tex.wrapT = THREE.RepeatWrapping; tex.repeat.set(40, 40); return new THREE.MeshStandardMaterial({ map: tex, roughness: .95 }) }

// ── the viewer ───────────────────────────────────────────────────────
export function createViewer(host, { domain, machine, onAskAI, onReady, cinematic = false } = {}) {
  const key = ({ 'datacenter': 'datacenter', 'hospital': 'hospital', 'manufacturing': 'factory', 'edm-machine': 'edm' })[domain] || 'datacenter'
  const e = ENV[key], M = makeMats()
  const W = () => host.clientWidth || 800, H = () => host.clientHeight || 540
  const scene = new THREE.Scene()
  scene.background = skyTexture()
  scene.fog = new THREE.Fog(0x1a2444, 80, 220)
  const camera = new THREE.PerspectiveCamera(48, W() / H(), 0.1, 2000)
  const renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: 'high-performance' })
  renderer.setSize(W(), H()); renderer.setPixelRatio(Math.min(2, window.devicePixelRatio))
  renderer.shadowMap.enabled = true; renderer.shadowMap.type = THREE.PCFSoftShadowMap
  renderer.toneMapping = THREE.ACESFilmicToneMapping; renderer.toneMappingExposure = 1.08
  host.appendChild(renderer.domElement)
  const pmrem = new THREE.PMREMGenerator(renderer)
  scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture
  scene.add(new THREE.HemisphereLight(0x9fb4ff, 0x202734, 0.55))
  const sun = new THREE.DirectionalLight(0xbcd0ff, 1.5); sun.position.set(38, 60, 28); sun.castShadow = true
  sun.shadow.mapSize.set(2048, 2048); sun.shadow.camera.near = 1; sun.shadow.camera.far = 200
  const sc = sun.shadow.camera; sc.left = -60; sc.right = 60; sc.top = 60; sc.bottom = -60; sun.shadow.bias = -0.0004; scene.add(sun)
  const fill = new THREE.DirectionalLight(0xffe9c2, 0.4); fill.position.set(-30, 25, -20); scene.add(fill)
  scene.add(new THREE.PointLight(e.accent, 0.7, 120).translateY(18))
  const ground = new THREE.Mesh(new THREE.PlaneGeometry(400, 400), groundMat()); ground.rotation.x = -Math.PI / 2; ground.receiveShadow = true; scene.add(ground)
  const grid = new THREE.GridHelper(400, 80, 0x2a3358, 0x1b2138); grid.material.transparent = true; grid.material.opacity = .35; grid.position.y = .01; scene.add(grid)

  const selectable = [], animators = []
  try { structure(key, M, scene) } catch (err) { /* keep going */ }
  scene.traverse(o => { if (o.userData && (o.userData.blink || o.userData.spin || o.userData.robot || o.userData.cargo || o.userData.press || o.userData.spark || o.userData.glow)) animators.push(o) })

  function propTop(g) { const b = new THREE.Box3().setFromObject(g); return isFinite(b.max.y) ? b.max.y : 4 }
  function placeAsset(data) {
    const make = PROP_FOR[data.type] || pSensor
    const g = make(M)
    g.position.set(data.x, (data.f || 0) * FH, data.z)
    g.userData.topY = propTop(g)
    const pin = pStatusPin(statusColor[data.status] || statusColor.ok); pin.position.set(0, g.userData.topY + 1.2, 0); g.add(pin)
    g.userData.asset = data; g.userData.pin = pin
    g.traverse(o => { o.userData.pickRoot = g })
    // clone every mesh material so we can pulse emissive without affecting other assets
    // save original emissive on each mesh for restore
    const blinkMeshes = []
    g.traverse(o => {
      if (!o.isMesh || !o.material) return
      // skip pin children, spark, LED blink, wire glow — those have their own animations
      if (o.userData.spark || o.userData.blink || o.userData.glow) return
      let inPin = false
      let p = o.parent
      while (p && p !== g) { if (p.userData.pin) { inPin = true; break } p = p.parent }
      if (inPin) return
      o.material = o.material.clone()
      o.userData._origEmissive = o.material.emissive.clone()
      o.userData._origEmissiveIntensity = o.material.emissiveIntensity
      blinkMeshes.push(o)
    })
    g.userData._blinkMeshes = blinkMeshes
    scene.add(g); selectable.push(g)
    g.traverse(o => { if (o.userData && (o.userData.blink || o.userData.spin || o.userData.robot || o.userData.cargo || o.userData.press || o.userData.spark || o.userData.glow)) animators.push(o) })
    return g
  }
  e.assets.forEach(a => placeAsset(a))

  const span = key === 'factory' ? 70 : key === 'edm' ? 40 : 56
  camera.position.set(span * 0.7, span * 0.55, span * 0.85)
  const camTarget = new THREE.Vector3(0, key === 'hospital' ? e.floors * FH * 0.4 : 6, 0)
  const controls = new OrbitControls(camera, renderer.domElement)
  controls.enableDamping = true; controls.dampingFactor = .08; controls.maxPolarAngle = Math.PI * 0.49
  controls.minDistance = 12; controls.maxDistance = 200; controls.target.copy(camTarget)
  controls.autoRotate = !cinematic; controls.autoRotateSpeed = .45

  // ── cinematic camera director (smooth fly-to / frame / reset) ────────
  let camAnim = null   // { fromPos,toPos,fromTgt,toTgt,t,dur }
  function flyCamera(toPos, toTgt, dur = 1.7) {
    camAnim = { fromPos: camera.position.clone(), toPos: toPos.clone(),
      fromTgt: controls.target.clone(), toTgt: toTgt.clone(), t: 0, dur }
  }
  function assetById(id) { return selectable.find(g => g.userData.asset && g.userData.asset.id === id) }
  function boxOf(g) { return new THREE.Box3().setFromObject(g) }
  function focusAsset(id, opts = {}) {
    const g = assetById(id); if (!g) return false
    const b = boxOf(g); const c = new THREE.Vector3(); b.getCenter(c)
    const size = Math.max(b.max.x - b.min.x, b.max.y - b.min.y, b.max.z - b.min.z) || 6
    const dist = opts.dist || Math.max(13, size * 2.4)
    const dir = (opts.dir ? new THREE.Vector3(...opts.dir) : new THREE.Vector3(0.62, 0.46, 0.9)).normalize()
    flyCamera(c.clone().add(dir.multiplyScalar(dist)), c, opts.dur || 1.7)
    return true
  }
  const homePos = new THREE.Vector3(span * 0.7, span * 0.55, span * 0.85)
  function resetView(dur = 1.8) { flyCamera(homePos.clone(), camTarget.clone(), dur) }
  // Project an asset's top-of-bounds to host pixel coords (for glued 2-D callouts).
  function worldToScreen(id) {
    const g = assetById(id); if (!g) return null
    const b = boxOf(g)
    const p = new THREE.Vector3((b.min.x + b.max.x) / 2, b.max.y + 1.2, (b.min.z + b.max.z) / 2)
    const v = p.project(camera)
    return { x: (v.x * 0.5 + 0.5) * W(), y: (-v.y * 0.5 + 0.5) * H(), visible: v.z < 1 }
  }
  function setAutoRotate(on) { controls.autoRotate = on; const tA = host.querySelector('.v-tools .v-tool'); if (tA) tA.classList.toggle('on', on) }

  // ── overlays ──
  host.append(el('div', { class: 'v-top' },
    el('div', { class: 'v-chip' }, icon(e.icon), el('b', {}, e.name)),
    el('div', { class: 'v-chip' }, el('span', { class: 'status-dot live', style: { width: '7px', height: '7px' } }), 'LIVE'),
    el('div', { class: 'v-chip', id: 'v-fps' }, '-- fps')))
  const tAuto = el('div', { class: 'v-tool on', title: 'Auto-orbit' }, icon('ti-rotate-360'))
  const tReset = el('div', { class: 'v-tool', title: 'Reset view' }, icon('ti-focus-2'))
  tAuto.onclick = () => { controls.autoRotate = !controls.autoRotate; tAuto.classList.toggle('on', controls.autoRotate) }
  tReset.onclick = () => { camera.position.set(span * 0.7, span * 0.55, span * 0.85); controls.target.copy(camTarget) }
  host.append(el('div', { class: 'v-tools' }, tAuto, tReset))

  // floors
  let fitems = []
  if (e.floors > 1) {
    const fb = el('div', { class: 'v-floors' })
    const all = el('div', { class: 'v-floor on' }, 'All floors'); fb.append(all); fitems = [all]
    for (let f = e.floors - 1; f >= 0; f--) { const it = el('div', { class: 'v-floor' }, 'Floor ' + (f + 1)); it.onclick = () => { fitems.forEach(x => x.classList.remove('on')); it.classList.add('on'); showFloor(f) }; fitems.push(it); fb.append(it) }
    all.onclick = () => { fitems.forEach(x => x.classList.remove('on')); all.classList.add('on'); showFloor('all') }
    host.append(fb)
  }
  function showFloor(f) { selectable.forEach(g => { const af = g.userData.asset.f || 0; g.visible = (f === 'all') || af === f }) }

  // palette
  const pal = el('div', { class: 'v-palette' });
  (PALETTE[key] || []).forEach(([label, ic, type]) => pal.append(el('div', { class: 'v-pal-item', title: 'Add ' + label, onClick: () => addFromPalette(type, label) }, icon(ic), label)))
  if (!cinematic) host.append(pal)
  const tip = el('div', { class: 'v-tip' }, 'Click equipment to inspect · drag to orbit · add assets from the palette'); if (!cinematic) host.append(tip)
  const tooltip = el('div', { class: 'v-tooltip' }); host.append(tooltip)
  const inspector = el('div', { class: 'inspector hidden' }); host.append(inspector)

  function addFromPalette(type, label) {
    const id = type.slice(0, 3).toUpperCase() + '-' + Math.floor(rnd(10, 99))
    const data = { id, name: label + ' ' + id.split('-')[1], type, f: 0, x: rnd(-8, 8), z: rnd(-2, 4), status: 'ok', icon: 'ti-circle-plus', metrics: [['Status', '', 'OK'], ['Health', '%', Math.round(rnd(85, 99))]] }
    const g = placeAsset(data); selectAsset(g)
  }

  // ── selection / pick / drag ──
  const ray = new THREE.Raycaster(), ndc = new THREE.Vector2()
  let selected = null, selBox = null, dragging = null, downPos = null, moved = false
  function pickAt(cx, cy) { const r = renderer.domElement.getBoundingClientRect(); ndc.x = ((cx - r.left) / r.width) * 2 - 1; ndc.y = -((cy - r.top) / r.height) * 2 + 1; ray.setFromCamera(ndc, camera); const hit = ray.intersectObjects(selectable, true); return hit.length ? hit[0].object.userData.pickRoot : null }
  function groundAt(cx, cy) { const r = renderer.domElement.getBoundingClientRect(); ndc.x = ((cx - r.left) / r.width) * 2 - 1; ndc.y = -((cy - r.top) / r.height) * 2 + 1; ray.setFromCamera(ndc, camera); const pt = new THREE.Vector3(); ray.ray.intersectPlane(new THREE.Plane(new THREE.Vector3(0, 1, 0), 0), pt); return pt }
  function selectAsset(g) { if (selBox) { scene.remove(selBox); selBox = null } selected = g; if (g) { selBox = new THREE.BoxHelper(g, 0x7c3aed); scene.add(selBox); openInspector(g.userData.asset, g) } }
  function deleteAsset(g) { const i = selectable.indexOf(g); if (i >= 0) selectable.splice(i, 1); if (selBox) { scene.remove(selBox); selBox = null } scene.remove(g); if (selected === g) selected = null; inspector.classList.add('hidden') }
  renderer.domElement.addEventListener('pointerdown', ev => { const g = pickAt(ev.clientX, ev.clientY); if (g) { dragging = g; downPos = { x: ev.clientX, y: ev.clientY }; moved = false; controls.enabled = false; selectAsset(g) } })
  function onMove(ev) {
    if (!dragging) { const g = pickAt(ev.clientX, ev.clientY); if (g) { const r = host.getBoundingClientRect(); tooltip.style.display = 'block'; tooltip.style.left = (ev.clientX - r.left + 12) + 'px'; tooltip.style.top = (ev.clientY - r.top + 12) + 'px'; tooltip.textContent = g.userData.asset.name; renderer.domElement.style.cursor = 'pointer' } else { tooltip.style.display = 'none'; renderer.domElement.style.cursor = 'grab' } return }
    if (downPos && (Math.abs(ev.clientX - downPos.x) + Math.abs(ev.clientY - downPos.y)) > 4) moved = true
    const pt = groundAt(ev.clientX, ev.clientY), yBase = (dragging.userData.asset.f || 0) * FH
    dragging.position.x = Math.max(-22, Math.min(22, pt.x)); dragging.position.z = Math.max(-16, Math.min(16, pt.z)); dragging.position.y = yBase
    dragging.userData.asset.x = dragging.position.x; dragging.userData.asset.z = dragging.position.z
    if (selBox) selBox.update()
  }
  function onUp() { if (dragging) { controls.enabled = true; dragging = null } }
  window.addEventListener('pointermove', onMove); window.addEventListener('pointerup', onUp)

  function metricsObj(a) { const o = {}; (a.metrics || []).forEach(m => { o[m[0]] = `${m[2]}${m[1] ? ' ' + m[1] : ''}` }); return o }

  function openInspector(a, g) {
    inspector.classList.remove('hidden'); inspector.innerHTML = ''
    const col = statusColor[a.status] || statusColor.ok
    inspector.append(el('div', { class: 'insp-head' },
      el('div', { class: 'insp-ic' }, icon(a.icon || 'ti-cube')),
      el('div', {}, el('div', { class: 'insp-title' }, a.name), el('div', { class: 'insp-sub' }, (a.id || '') + ' · ' + a.type)),
      el('div', { class: 'insp-close', onClick: () => { inspector.classList.add('hidden'); if (selBox) { scene.remove(selBox); selBox = null } selected = null } }, '✕')))
    const body = el('div', { class: 'insp-body' })
    body.append(el('div', { style: { display: 'flex', gap: '8px', marginBottom: '12px' } },
      el('span', { class: `pill ${a.status === 'crit' ? 'pill-red' : a.status === 'warn' ? 'pill-amber' : 'pill-green'}` }, a.status === 'crit' ? 'CRITICAL' : a.status === 'warn' ? 'WARNING' : 'HEALTHY'),
      el('span', { class: 'pill pill-green' }, '● LIVE')))
    const mt = el('div', { class: 'insp-metrics' });
    (a.metrics || []).forEach(m => mt.append(el('div', { class: 'insp-metric' }, el('div', { class: 'ml' }, m[0]), el('div', { class: 'mv' }, m[2] + (m[1] ? ' ' + m[1] : '')))))
    body.append(mt)
    const aiOut = el('div', { class: 'insp-ai', style: { display: 'none' } })
    const askBtn = el('button', { class: 'btn insp-btn primary' }, icon('ti-sparkles'), ' Ask AI for status')
    askBtn.onclick = async () => {
      if (!onAskAI) return
      askBtn.disabled = true; askBtn.innerHTML = ''; askBtn.append(el('span', { class: 'spinner' }), ' Analysing…')
      aiOut.style.display = 'block'; aiOut.textContent = ''
      try { aiOut.textContent = await onAskAI({ id: a.id, name: a.name, type: a.type, status: a.status, metrics: metricsObj(a) }) }
      catch { aiOut.textContent = 'AI status unavailable.' }
      askBtn.disabled = false; askBtn.innerHTML = ''; askBtn.append(icon('ti-refresh'), ' Re-ask AI')
    }
    body.append(el('div', { class: 'insp-actions' }, askBtn,
      el('button', { class: 'btn insp-btn danger', onClick: () => deleteAsset(g) }, icon('ti-trash'), ' Remove asset'),
      aiOut))
    inspector.append(body)
  }

  // ── composer / bloom ──
  let composer = null
  loadBloom().then(B => { if (!B) return; composer = new B.EffectComposer(renderer); composer.addPass(new B.RenderPass(scene, camera)); composer.addPass(new B.UnrealBloomPass(new THREE.Vector2(W(), H()), 0.32, 0.5, 0.85)); composer.addPass(new B.OutputPass()); composer.setSize(W(), H()) })

  let raf, last = performance.now(), fc = 0, ft = 0, t = 0
  function loop(now) {
    raf = requestAnimationFrame(loop); const dt = Math.min(.05, (now - last) / 1000); last = now; t += dt
    fc++; ft += dt; if (ft >= .5) { const f = document.getElementById('v-fps'); if (f) f.textContent = Math.round(fc / ft) + ' fps'; fc = 0; ft = 0 }
    for (const o of animators) { const u = o.userData
      if (u.blink) o.material.emissiveIntensity = 1 + Math.sin(t * 6 + o.position.y) * 0.8 + (Math.random() < .02 ? 2 : 0)
      if (u.spin) o.rotation.y += dt * u.spin * 3
      if (u.cargo) { o.position.x += dt * 1.2; if (o.position.x > 4) o.position.x = -4 }
      if (u.press) o.position.y = 2.6 + Math.sin(t * 2) * 0.5
      if (u.spark && o.isLight) o.intensity = .3 + Math.abs(Math.sin(t * 20)) * .8
      else if (u.spark) o.scale.setScalar(0.5 + Math.abs(Math.sin(t * 20)) * 1.0)
      if (u.glow && o.material) o.material.emissiveIntensity = .3 + Math.sin(t * 12) * .3
      if (u.robot) { const r = u.robot; r.arm1.rotation.y = Math.sin(t * 0.6) * 0.8; r.arm1.rotation.z = Math.sin(t * 0.8) * 0.25; r.arm2.rotation.z = Math.sin(t * 0.7 + 1) * 0.5; r.base.rotation.y += dt * 0.3 }
    }
    const alertRed = _alertRed
    selectable.forEach(g => {
      if (g.userData.pin) g.userData.pin.position.y = (g.userData.topY || 4) + 1.2 + Math.sin(t * 2 + g.position.x) * 0.15
      // critical blink: pulse the actual asset meshes red
      const isCrit = g.userData.asset && g.userData.asset.status === 'crit'
      const meshes = g.userData._blinkMeshes
      if (meshes && meshes.length > 0) {
        if (isCrit) {
          const pulse = Math.sin(t * 5) * 0.5 + 0.5  // 0..1
          for (let i = 0; i < meshes.length; i++) {
            const o = meshes[i]
            o.material.emissive.copy(o.userData._origEmissive).lerp(alertRed, pulse)
            o.material.emissiveIntensity = o.userData._origEmissiveIntensity + pulse * 1.5
          }
        } else {
          for (let i = 0; i < meshes.length; i++) {
            const o = meshes[i]
            if (!o.material.emissive.equals(o.userData._origEmissive) || o.material.emissiveIntensity !== o.userData._origEmissiveIntensity) {
              o.material.emissive.copy(o.userData._origEmissive)
              o.material.emissiveIntensity = o.userData._origEmissiveIntensity
            }
          }
        }
      }
    })
    // cinematic camera animation
    if (camAnim) {
      camAnim.t += dt; const k = Math.min(1, camAnim.t / camAnim.dur)
      const e = k < 0.5 ? 4 * k * k * k : 1 - Math.pow(-2 * k + 2, 3) / 2
      camera.position.lerpVectors(camAnim.fromPos, camAnim.toPos, e)
      controls.target.lerpVectors(camAnim.fromTgt, camAnim.toTgt, e)
      if (k >= 1) camAnim = null
    }
    controls.update()
    if (composer) composer.render(); else renderer.render(scene, camera)
  }
  raf = requestAnimationFrame(loop); onReady && onReady()
  function onResize() { camera.aspect = W() / H(); camera.updateProjectionMatrix(); renderer.setSize(W(), H()); if (composer) composer.setSize(W(), H()) }
  window.addEventListener('resize', onResize)

  // Push live data onto placed assets: recolor status pins, refresh stored
  // metrics, and update an open inspector in-place (without re-rendering it, so
  // the AI output is preserved).
  function recolorPin(g, status) {
    const col = new THREE.Color(statusColor[status] || statusColor.ok)
    if (g.userData.pin) g.userData.pin.traverse(o => {
      if (o.material) { if (o.material.emissive) o.material.emissive.copy(col); if (o.material.color) o.material.color.copy(col) }
    })
  }

  // ── alert color constant ──
  const _alertRed = new THREE.Color(0xe11d48)

  function updateAssets(updates) {
    if (!updates) return
    for (const g of selectable) {
      const a = g.userData.asset, u = updates[a.id]
      if (!u) continue
      if (u.metrics) a.metrics = u.metrics
      if (u.status && u.status !== a.status) {
        a.status = u.status
        recolorPin(g, u.status)
      }
      if (selected === g && !inspector.classList.contains('hidden')) {
        const mvs = inspector.querySelectorAll('.insp-metric .mv')
        ;(a.metrics || []).forEach((m, i) => { if (mvs[i]) mvs[i].textContent = m[2] + (m[1] ? ' ' + m[1] : '') })
      }
    }
  }

  return {
    updateAssets, focusAsset, resetView, worldToScreen, setAutoRotate,
    dispose() {
      cancelAnimationFrame(raf); window.removeEventListener('resize', onResize)
      window.removeEventListener('pointermove', onMove); window.removeEventListener('pointerup', onUp)
      pmrem.dispose(); renderer.dispose()
      scene.traverse(o => { if (o.geometry) o.geometry.dispose(); if (o.material) (Array.isArray(o.material) ? o.material : [o.material]).forEach(m => m.dispose && m.dispose()) })
      try { host.querySelectorAll('canvas,.v-top,.v-tools,.v-floors,.v-palette,.v-tip,.v-tooltip,.inspector').forEach(n => n.remove()) } catch {}
    }
  }
}
