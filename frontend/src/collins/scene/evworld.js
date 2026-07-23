// evworld.js — the "living energy site": a stylised miniature world twin of a
// GoalCert-managed charging hub. A commercial lot with DC-fast + AC bays, an
// on-site BESS container, a solar canopy, a grid transformer and the building it
// serves — with EVs driving in, chargers pulsing, and power visibly flowing
// between grid ⇄ transformer ⇄ BESS ⇄ solar ⇄ chargers. Vanilla Three.js, mounted
// by EVWorld.jsx. Reuses the .hero3d / .v-chip / .inspector overlay CSS.
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

// ── tiny DOM helper (mirror of engine.js) ────────────────────────────
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
const clamp = (v, a, b) => Math.max(a, Math.min(b, v))
const STATUS = { ok: '#10b981', warn: '#f59e0b', crit: '#ef4444' }

// ── geometry helpers ─────────────────────────────────────────────────
const box = (w, h, d, mat, x = 0, y = 0, z = 0) => { const m = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), mat); m.position.set(x, y, z); m.castShadow = true; m.receiveShadow = true; return m }
const cyl = (rt, rb, h, mat, x = 0, y = 0, z = 0, seg = 18) => { const m = new THREE.Mesh(new THREE.CylinderGeometry(rt, rb, h, seg), mat); m.position.set(x, y, z); m.castShadow = true; return m }
const grp = (...ch) => { const g = new THREE.Group(); ch.flat().forEach(c => c && g.add(c)); return g }

// rounded-rect flat slab (site pad / bays)
function slab(w, d, mat, r = 1.2) {
  const s = new THREE.Shape()
  const x = -w / 2, y = -d / 2
  s.moveTo(x + r, y)
  s.lineTo(x + w - r, y); s.quadraticCurveTo(x + w, y, x + w, y + r)
  s.lineTo(x + w, y + d - r); s.quadraticCurveTo(x + w, y + d, x + w - r, y + d)
  s.lineTo(x + r, y + d); s.quadraticCurveTo(x, y + d, x, y + d - r)
  s.lineTo(x, y + r); s.quadraticCurveTo(x, y, x + r, y)
  const g = new THREE.ExtrudeGeometry(s, { depth: 0.12, bevelEnabled: false })
  g.rotateX(-Math.PI / 2)
  const m = new THREE.Mesh(g, mat); m.receiveShadow = true; return m
}

function makeMats() {
  return {
    pad: new THREE.MeshStandardMaterial({ color: 0x1a2033, roughness: 0.92, metalness: 0.05 }),
    plaza: new THREE.MeshStandardMaterial({ color: 0x232a40, roughness: 0.85 }),
    road: new THREE.MeshStandardMaterial({ color: 0x14182a, roughness: 0.95 }),
    curb: new THREE.MeshStandardMaterial({ color: 0x3a4260, roughness: 0.8 }),
    metal: new THREE.MeshStandardMaterial({ color: 0xaeb6c6, roughness: 0.35, metalness: 0.9, envMapIntensity: 1 }),
    steel: new THREE.MeshStandardMaterial({ color: 0x6b7488, roughness: 0.5, metalness: 0.8 }),
    dark: new THREE.MeshStandardMaterial({ color: 0x171b2b, roughness: 0.6, metalness: 0.4 }),
    white: new THREE.MeshStandardMaterial({ color: 0xe8ecf6, roughness: 0.55, metalness: 0.05 }),
    glass: new THREE.MeshStandardMaterial({ color: 0x8fd6ff, roughness: 0.06, metalness: 0.1, transparent: true, opacity: 0.34, envMapIntensity: 1.6 }),
    container: new THREE.MeshStandardMaterial({ color: 0x0f6b57, roughness: 0.55, metalness: 0.35 }),
    solar: new THREE.MeshStandardMaterial({ color: 0x0b1c3a, roughness: 0.25, metalness: 0.5, emissive: 0x0a2f6b, emissiveIntensity: 0.35 }),
    tyre: new THREE.MeshStandardMaterial({ color: 0x0c0e16, roughness: 0.95 }),
    screen: new THREE.MeshStandardMaterial({ color: 0x06122b, emissive: 0x2f7be0, emissiveIntensity: 0.9 }),
    green: new THREE.MeshStandardMaterial({ color: 0x10b981, emissive: 0x10b981, emissiveIntensity: 1.4 }),
    emissive: (c, i = 1.6) => new THREE.MeshStandardMaterial({ color: c, emissive: c, emissiveIntensity: i }),
  }
}

// ── EV car (stylised) ────────────────────────────────────────────────
const CAR_COLORS = [0x2563eb, 0x10b981, 0xf59e0b, 0xe2e8f0, 0x8b5cf6, 0x0ea5e9, 0xef4444]
function makeCar(M, color) {
  const g = grp()
  const bodyMat = new THREE.MeshStandardMaterial({ color, roughness: 0.35, metalness: 0.55, envMapIntensity: 1.1 })
  g.add(box(1.5, 0.42, 3.0, bodyMat, 0, 0.5, 0))            // lower body
  const cabin = box(1.32, 0.5, 1.7, bodyMat, 0, 0.9, -0.05)  // roof
  g.add(cabin)
  g.add(box(1.2, 0.34, 1.5, M.glass, 0, 0.92, -0.05))        // greenhouse
  // wheels
  const wheel = () => { const w = cyl(0.28, 0.28, 0.22, M.tyre); w.rotation.z = Math.PI / 2; return w }
  ;[[-0.72, 0.9], [0.72, 0.9], [-0.72, -0.95], [0.72, -0.95]].forEach(([x, z]) => { const w = wheel(); w.position.set(x, 0.3, z); g.add(w) })
  // lights
  const head = box(0.5, 0.12, 0.06, M.emissive(0xfff3c4, 1.3), 0, 0.52, 1.5)
  const tail = box(0.5, 0.12, 0.06, M.emissive(0xff4d4d, 1.2), 0, 0.52, -1.5)
  g.add(head, tail)
  g.traverse(o => { o.castShadow = true })
  return g
}

// ── power-flow line ──────────────────────────────────────────────────
// A glowing tube along a poly-line whose emissive "flow" texture scrolls to
// show direction; colour + speed encode power. Returns { mesh, setFlow }.
function flowTexture() {
  const c = document.createElement('canvas'); c.width = 64; c.height = 8
  const ctx = c.getContext('2d')
  ctx.fillStyle = 'rgba(255,255,255,0.10)'; ctx.fillRect(0, 0, 64, 8)
  const grd = ctx.createLinearGradient(0, 0, 64, 0)
  grd.addColorStop(0, 'rgba(255,255,255,0)'); grd.addColorStop(0.5, 'rgba(255,255,255,1)'); grd.addColorStop(1, 'rgba(255,255,255,0)')
  ctx.fillStyle = grd; ctx.fillRect(4, 0, 26, 8)
  const t = new THREE.CanvasTexture(c); t.wrapS = t.wrapT = THREE.RepeatWrapping; t.repeat.set(6, 1)
  return t
}
function buildFlow(points, color = 0x2563eb) {
  const curve = new THREE.CatmullRomCurve3(points.map(p => new THREE.Vector3(...p)))
  const geo = new THREE.TubeGeometry(curve, 40, 0.14, 8, false)
  const tex = flowTexture()
  const mat = new THREE.MeshStandardMaterial({ color, emissive: color, emissiveIntensity: 1.6,
    map: tex, transparent: true, opacity: 0.95 })
  const mesh = new THREE.Mesh(geo, mat)
  mesh.userData.flow = { tex, mat, dir: 1, speed: 1, base: new THREE.Color(color) }
  return mesh
}

// ── the world ────────────────────────────────────────────────────────
export function createEVWorld(host, { onAskAI, onReady, onPick } = {}) {
  const M = makeMats()
  const W = () => host.clientWidth || 900, H = () => host.clientHeight || 520
  const scene = new THREE.Scene()

  // sky gradient
  const sky = (() => {
    const c = document.createElement('canvas'); c.width = 8; c.height = 256
    const ctx = c.getContext('2d'); const g = ctx.createLinearGradient(0, 0, 0, 256)
    g.addColorStop(0, '#0a1024'); g.addColorStop(0.5, '#122043'); g.addColorStop(0.8, '#1c3358'); g.addColorStop(1, '#274b6b')
    ctx.fillStyle = g; ctx.fillRect(0, 0, 8, 256)
    const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; return t
  })()
  scene.background = sky
  scene.fog = new THREE.Fog(0x122043, 70, 160)

  const camera = new THREE.PerspectiveCamera(46, W() / H(), 0.1, 1000)
  const renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: 'high-performance' })
  renderer.setSize(W(), H()); renderer.setPixelRatio(Math.min(2, window.devicePixelRatio))
  renderer.shadowMap.enabled = true; renderer.shadowMap.type = THREE.PCFSoftShadowMap
  renderer.toneMapping = THREE.ACESFilmicToneMapping; renderer.toneMappingExposure = 1.05
  host.appendChild(renderer.domElement)

  const pmrem = new THREE.PMREMGenerator(renderer)
  scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture
  scene.add(new THREE.HemisphereLight(0x9fc0ff, 0x0b1020, 0.6))
  const sun = new THREE.DirectionalLight(0xd7e4ff, 1.45); sun.position.set(30, 46, 20); sun.castShadow = true
  sun.shadow.mapSize.set(2048, 2048); sun.shadow.camera.near = 1; sun.shadow.camera.far = 160
  const sc = sun.shadow.camera; sc.left = -46; sc.right = 46; sc.top = 46; sc.bottom = -46; sun.shadow.bias = -0.0004
  scene.add(sun)
  const fill = new THREE.DirectionalLight(0x86f7d6, 0.35); fill.position.set(-26, 18, -18); scene.add(fill)
  const alertLight = new THREE.PointLight(0xff1a2e, 0, 90); alertLight.position.set(0, 16, -2); scene.add(alertLight)

  // ground + site pad
  const ground = new THREE.Mesh(new THREE.PlaneGeometry(400, 400), new THREE.MeshStandardMaterial({ color: 0x0a0d18, roughness: 1 }))
  ground.rotation.x = -Math.PI / 2; ground.position.y = -0.05; ground.receiveShadow = true; scene.add(ground)
  const grid = new THREE.GridHelper(400, 100, 0x1c2b4a, 0x121a30); grid.material.transparent = true; grid.material.opacity = 0.32; scene.add(grid)
  const pad = slab(64, 52, M.pad, 3); scene.add(pad)

  const animators = []   // {kind, obj, ...}
  let alertLevel = 0     // 0..1 site-wide crisis → red alert light + fog

  // spark particles — a burst fires at each cascade stage
  const SPN = 160
  const spPos = new Float32Array(SPN * 3).fill(-999)
  const spVel = new Float32Array(SPN * 3)
  const spLife = new Float32Array(SPN)
  const spGeo = new THREE.BufferGeometry(); spGeo.setAttribute('position', new THREE.BufferAttribute(spPos, 3))
  const spMat = new THREE.PointsMaterial({ color: 0xffb020, size: 0.6, transparent: true, opacity: 0.95, blending: THREE.AdditiveBlending, depthWrite: false })
  const sparks = new THREE.Points(spGeo, spMat); sparks.frustumCulled = false; scene.add(sparks)
  let spHead = 0
  function emitSparks(p, n = 30, spread = 1.6) {
    if (!p) return
    for (let k = 0; k < n; k++) {
      const i = (spHead++) % SPN
      spPos[i * 3] = p.x; spPos[i * 3 + 1] = p.y; spPos[i * 3 + 2] = p.z
      spVel[i * 3] = (Math.random() - 0.5) * spread; spVel[i * 3 + 1] = Math.random() * 2.4 + 1.2; spVel[i * 3 + 2] = (Math.random() - 0.5) * spread
      spLife[i] = 1
    }
    spGeo.attributes.position.needsUpdate = true
  }
  const selectable = []  // clickable asset groups
  const labels = []      // world-anchored HTML callouts {el, pos}
  const flows = {}       // named flow lines
  const store = {}       // live-updatable references

  // ── road loop + lane marks ──
  // NOTE: for a closed CatmullRomCurve3 do NOT repeat the first point — it auto-closes.
  const roadPts = [
    [-26, -20], [22, -20], [26, -16], [26, 16], [22, 20], [-22, 20], [-26, 16],
  ].map(([x, z]) => new THREE.Vector3(x, 0.06, z))
  const roadCurve = new THREE.CatmullRomCurve3(roadPts, true, 'catmullrom', 0.1)
  const roadGeo = new THREE.TubeGeometry(roadCurve, 220, 2.4, 3, true)
  roadGeo.scale(1, 0.02, 1)
  const road = new THREE.Mesh(roadGeo, M.road); road.position.y = 0.12; road.receiveShadow = true; scene.add(road)
  // dashed centre line
  const dashMat = M.emissive(0x2f3a5c, 0.5)
  for (let i = 0; i < 90; i++) {
    const t = i / 90; const p = roadCurve.getPoint(t)
    const d = box(0.16, 0.02, 0.7, dashMat, p.x, 0.2, p.z)
    const tan = roadCurve.getTangent(t); d.rotation.y = Math.atan2(tan.x, tan.z); scene.add(d)
  }

  // ── cars driving the loop ──
  for (let i = 0; i < 7; i++) {
    const car = makeCar(M, CAR_COLORS[i % CAR_COLORS.length])
    car.userData.drive = { t: i / 7, speed: 0.014 + Math.random() * 0.006 }
    scene.add(car); animators.push({ kind: 'car', obj: car })
  }

  // ── charging plaza (solar canopy + AC bays with parked cars) ──
  const plaza = slab(26, 22, M.plaza, 1.6); plaza.position.set(13, 0.14, 8); scene.add(plaza)
  const bays = []
  // two rows of 4 bays under the canopy
  const bayCars = []
  for (let row = 0; row < 2; row++) {
    for (let i = 0; i < 4; i++) {
      const bx = 6 + i * 3.4, bz = 3 + row * 10
      // bay marking
      scene.add(box(2.4, 0.02, 4.6, M.emissive(0x1f2a44, 0.4), bx, 0.16, bz))
      // charger pedestal
      const ped = grp()
      ped.add(box(0.7, 1.7, 0.5, M.white, 0, 0.85, 0))
      ped.add(box(0.52, 0.62, 0.08, M.screen, 0, 1.15, 0.27))
      const led = box(0.4, 0.1, 0.06, M.emissive(0x10b981, 1.8), 0, 1.55, 0.27)
      ped.add(led)
      ped.position.set(bx - 1.4, 0, bz - 1.9)
      const occupied = Math.random() > 0.35
      let car = null, cable = null
      if (occupied) {
        car = makeCar(M, CAR_COLORS[(row * 4 + i) % CAR_COLORS.length])
        car.position.set(bx, 0, bz); car.rotation.y = Math.PI
        scene.add(car); bayCars.push(car)
        // charging cable (arc from charger head to car port)
        const a = new THREE.Vector3(bx - 1.4, 1.15, bz - 1.63)
        const b = new THREE.Vector3(bx - 0.2, 0.9, bz - 1.2)
        const mid = new THREE.Vector3((a.x + b.x) / 2, 0.55, (a.z + b.z) / 2)
        const cc = new THREE.CatmullRomCurve3([a, mid, b])
        cable = new THREE.Mesh(new THREE.TubeGeometry(cc, 16, 0.06, 6, false), M.emissive(0x10b981, 1.2))
        cable.userData.charging = true; scene.add(cable)
      }
      const id = `AC-${row + 1}${i + 1}`
      ped.userData.asset = { id, name: `AC Charger ${id}`, type: 'AC bay (22 kW)', icon: 'ti-plug-connected',
        status: occupied ? 'ok' : 'ok', led, cable, charging: occupied,
        metrics: [['Power', 'kW', occupied ? 22 : 0], ['Session', '', occupied ? 'active' : 'idle'], ['OCPP', '', '2.0.1'], ['Volt', 'V', 415]] }
      ped.traverse(o => { o.userData.pickRoot = ped })
      scene.add(ped); selectable.push(ped); bays.push(ped)
      if (cable) animators.push({ kind: 'cable', obj: cable })
      if (led) animators.push({ kind: 'led', obj: led, base: 0x10b981 })
    }
  }
  // solar canopy over the plaza
  const canopy = grp()
  for (const px of [4.6, 21.4]) for (const pz of [1.5, 14.5]) canopy.add(cyl(0.16, 0.2, 4.2, M.steel, px, 2.1, pz))
  const panelMat = M.solar
  for (let r = 0; r < 2; r++) {
    const panel = box(15.4, 0.12, 5.6, panelMat, 13, 4.2, 3 + r * 9)
    panel.rotation.x = -0.16; canopy.add(panel)
    // panel grid lines (emissive seams)
    for (let s = 1; s < 6; s++) canopy.add(box(0.05, 0.14, 5.6, M.emissive(0x1a3a6b, 0.5), 13 - 7.7 + s * 2.5, 4.24, 3 + r * 9))
  }
  scene.add(canopy)
  store.solarPanels = panelMat

  // ── DC fast-charging island (the hero chargers, near the entry) ──
  const dcfcGroup = []
  for (let i = 0; i < 8; i++) {
    const dz = -15 + i * 2.4
    const u = grp()
    const dcBody = box(1.5, 2.5, 1.1, M.white.clone(), 0, 1.25, 0); u.add(dcBody); dcBody.userData.baseColor = dcBody.material.color.getHex()
    u.add(box(1.2, 1.0, 0.12, M.screen, 0, 1.7, 0.57))
    u.add(box(1.5, 0.28, 1.1, M.dark, 0, 0.14, 0))
    const strip = box(0.12, 2.2, 0.12, M.emissive(0x10b981, 1.8), -0.72, 1.3, 0.5)
    u.add(strip)
    u.position.set(-6, 0, dz)
    const id = `DCFC-0${i + 1}`
    const st = i === 2 ? 'warn' : 'ok'
    u.userData.asset = { id, name: `DC Fast Charger ${id}`, type: 'DC-fast (150 kW)', icon: 'ti-bolt', strip, body: dcBody,
      status: st, metrics: [['Power', 'kW', 148], ['Temp', '°C', 41], ['SoC add', '%/min', 2.4], ['OCPP', '', '1.6J']] }
    u.traverse(o => { o.userData.pickRoot = u })
    scene.add(u); selectable.push(u); dcfcGroup.push(u)
    animators.push({ kind: 'led', obj: strip, base: st === 'warn' ? 0xf59e0b : 0x10b981 })
  }
  store.dcfc = dcfcGroup

  // ── BESS container (fill = SoC) ──
  const bess = grp()
  const bessShell = box(9, 3.2, 3, M.container.clone(), 0, 1.7, 0); bess.add(bessShell); bessShell.userData.baseColor = bessShell.material.color.getHex()   // container shell
  bess.add(box(9.04, 0.4, 3.04, M.dark, 0, 3.3, 0))              // roof cap
  // corrugation ribs
  for (let i = -4; i <= 4; i++) bess.add(box(0.08, 3.0, 3.02, new THREE.MeshStandardMaterial({ color: 0x0c5546, roughness: 0.6 }), i * 0.95, 1.7, 0))
  // translucent inspection window with battery modules inside (front face)
  bess.add(box(7.2, 2.2, 0.06, M.glass, 0, 1.7, 1.52))
  const modules = []
  const MODCOLS = 10, MODROWS = 3
  for (let r = 0; r < MODROWS; r++) for (let c = 0; c < MODCOLS; c++) {
    const m = box(0.6, 0.5, 0.4, M.emissive(0x0e7a5f, 0.5), -3.3 + c * 0.68, 0.95 + r * 0.62, 1.35)
    modules.push(m); bess.add(m)
  }
  // cooling vent panel on the end
  bess.add(box(0.3, 1.4, 2.2, M.dark, 4.65, 1.7, 0))
  bess.position.set(-20, 0, -14)
  bess.userData.asset = { id: 'BESS-A', name: 'BESS Container A', type: 'Battery storage (1.2 MWh)', icon: 'ti-battery-4',
    status: 'ok', metrics: [['SoC', '%', 72], ['Power', 'kW', -60], ['Cell max', '°C', 33], ['Modules', '', 30] ] }
  bess.traverse(o => { o.userData.pickRoot = bess })
  scene.add(bess); selectable.push(bess)
  store.bessModules = modules
  store.bessShell = bessShell

  // ── grid transformer / substation ──
  const tx = grp()
  const txBody = box(3.2, 2.6, 2.2, M.metal.clone(), 0, 1.3, 0); tx.add(txBody)
  for (let i = 0; i < 7; i++) tx.add(box(0.08, 2.2, 2.3, M.steel, -1.5 + i * 0.5, 1.3, 0))   // radiator fins
  const bush = [-0.8, 0, 0.8].map(x => { const b = cyl(0.16, 0.22, 1.2, M.emissive(0x9fd0ff, 0.4), x, 2.9, 0); return b })
  tx.add(...bush)
  const txBand = box(3.24, 0.3, 2.24, M.emissive(0x10b981, 1.2), 0, 2.55, 0)   // utilisation band
  tx.add(txBand)
  const txLight = box(0.3, 0.12, 0.3, M.emissive(0x10b981, 1.8), 0, 3.6, 0); tx.add(txLight)
  tx.position.set(-24, 0, 2)
  tx.userData.asset = { id: 'TX-1', name: 'Grid Transformer T1', type: '11kV/415V · 630 kVA', icon: 'ti-transform',
    status: 'ok', band: txBand, light: txLight, metrics: [['Load', '%', 68], ['Temp', '°C', 66], ['Headroom', '%', 32], ['Peak', 'kW', 420]] }
  tx.traverse(o => { o.userData.pickRoot = tx })
  scene.add(tx); selectable.push(tx)
  store.tx = tx.userData.asset
  store.txBody = txBody; txBody.userData.baseColor = txBody.material.color.getHex()
  // grid feed pole
  const pole = grp(); pole.add(cyl(0.14, 0.18, 6, M.steel, 0, 3, 0)); pole.add(box(2.2, 0.14, 0.14, M.steel, 0, 5.6, 0))
  pole.position.set(-30, 0, -8); scene.add(pole)

  // ── distribution bus (junction) ──
  const bus = grp()
  bus.add(box(1.6, 1.8, 1.0, M.white, 0, 0.9, 0)); bus.add(box(1.2, 0.7, 0.08, M.screen, 0, 1.05, 0.52))
  bus.position.set(-11, 0, -2)
  bus.userData.asset = { id: 'EMS-1', name: 'GoalCert EMS Controller', type: 'Load balancer / peak-shave', icon: 'ti-cpu',
    status: 'ok', metrics: [['Site demand', 'kW', 420], ['Limit', 'kW', 550], ['Headroom', '%', 32], ['Mode', '', 'balancing']] }
  bus.traverse(o => { o.userData.pickRoot = bus })
  scene.add(bus); selectable.push(bus)

  // ── commercial building it serves ──
  const bldg = grp()
  bldg.add(box(10, 12, 8, M.dark, 0, 6, 0))
  bldg.add(box(10.05, 12, 8.05, M.glass, 0, 6, 0))
  // window grid glow
  const winMat = M.emissive(0xffe8a8, 0.6)
  for (let fl = 0; fl < 6; fl++) for (let c = 0; c < 5; c++) {
    if (Math.random() > 0.45) bldg.add(box(1.2, 0.9, 0.05, winMat, -4 + c * 2, 1.6 + fl * 1.9, 4.03))
  }
  bldg.add(box(11, 0.6, 9, M.metal, 0, 12.3, 0))
  bldg.position.set(22, 0, 18)
  bldg.userData.asset = { id: 'BLDG', name: 'Host Property (Mall)', type: 'Commercial load', icon: 'ti-building',
    status: 'ok', metrics: [['Base load', 'kW', 180], ['PV offset', 'kW', 120], ['Tariff', '₹/kWh', 11.5], ['Occupancy', '%', 72]] }
  bldg.traverse(o => { o.userData.pickRoot = bldg })
  scene.add(bldg); selectable.push(bldg)

  // ── power-flow lines (the "alive" network) ──
  const y = 0.35
  flows.gridToTx = buildFlow([[-30, y, -8], [-27, y, -4], [-24, y, 2]], 0x2f7bff)
  flows.txToBus = buildFlow([[-24, y, 2], [-18, y, 0], [-11, y, -2]], 0x38bdf8)
  flows.bessToBus = buildFlow([[-20, y, -14], [-15, y, -8], [-11, y, -2]], 0xf59e0b)
  flows.solarToBus = buildFlow([[13, y, 8], [0, y, 4], [-11, y, -2]], 0x10b981)
  flows.busToDcfc = buildFlow([[-11, y, -2], [-8, y, -8], [-6, y, -12]], 0x38bdf8)
  flows.busToAc = buildFlow([[-11, y, -2], [2, y, 4], [10, y, 8]], 0x38bdf8)
  flows.busToBldg = buildFlow([[-11, y, -2], [6, y, 8], [22, y, 14]], 0x38bdf8)
  Object.values(flows).forEach(f => { scene.add(f); animators.push({ kind: 'flow', obj: f }) })

  // ── HTML world-anchored callouts ──
  function makeLabel(id, cls = '') {
    const n = el('div', { class: `evw-label ${cls}` })
    host.appendChild(n); const rec = { el: n, id, anchor: new THREE.Vector3() }
    labels.push(rec); return rec
  }
  const lblBess = makeLabel('bess'); lblBess.anchor.set(-20, 4.2, -14)
  const lblTx = makeLabel('tx'); lblTx.anchor.set(-24, 4.2, 2)
  const lblSolar = makeLabel('solar'); lblSolar.anchor.set(13, 6.2, 8)
  const lblEms = makeLabel('ems'); lblEms.anchor.set(-11, 2.6, -2)
  const lblDcfc = makeLabel('dcfc'); lblDcfc.anchor.set(-6, 3.4, -8)

  // staged cascade bubbles — numbered callouts anchored above assets
  const stageEls = new Map()
  const stageAnchors = {
    transformer: new THREE.Vector3(-24, 7, 2), dcfc: new THREE.Vector3(-6, 5, -6),
    bess: new THREE.Vector3(-20, 7, -14), ems: new THREE.Vector3(-11, 5, -2),
    building: new THREE.Vector3(22, 15, 18), solar: new THREE.Vector3(13, 8, 8),
    grid: new THREE.Vector3(-36, 7, 2),
  }
  function setLabel(rec, title, val, tone = 'ok') {
    rec.el.innerHTML = `<span class="evw-l-t">${title}</span><span class="evw-l-v evw-${tone}">${val}</span>`
  }
  setLabel(lblBess, 'BESS', '72%'); setLabel(lblTx, 'Transformer T1', '68%'); setLabel(lblSolar, 'Solar', '210 kW')
  setLabel(lblEms, 'EMS headroom', '32%'); setLabel(lblDcfc, 'DC-fast', '148 kW')

  // ── overlays: chips + tools + legend + inspector ──
  host.append(el('div', { class: 'v-top' },
    el('div', { class: 'v-chip' }, icon('ti-charging-pile'), el('b', {}, 'GoalCert Energy Site')),
    el('div', { class: 'v-chip' }, el('span', { class: 'status-dot live', style: { width: '7px', height: '7px' } }), 'LIVE'),
    el('div', { class: 'v-chip', id: 'evw-fps' }, '-- fps')))
  const tAuto = el('div', { class: 'v-tool on', title: 'Auto-orbit' }, icon('ti-rotate-360'))
  const tReset = el('div', { class: 'v-tool', title: 'Reset view' }, icon('ti-focus-2'))
  host.append(el('div', { class: 'v-tools' }, tAuto, tReset))
  host.append(el('div', { class: 'evw-legend' },
    el('span', {}, el('i', { style: { background: '#2f7bff' } }), 'Grid import'),
    el('span', {}, el('i', { style: { background: '#10b981' } }), 'Solar'),
    el('span', {}, el('i', { style: { background: '#f59e0b' } }), 'BESS discharge')))
  const tip = el('div', { class: 'v-tip' }, 'Click any asset to inspect · drag to orbit · scroll to zoom')
  host.append(tip)
  const tooltip = el('div', { class: 'v-tooltip' }); host.append(tooltip)
  const inspector = el('div', { class: 'inspector hidden' }); host.append(inspector)

  // ── camera + controls ──
  camera.position.set(44, 34, 52)
  const camTarget = new THREE.Vector3(-4, 2, 0)
  const controls = new OrbitControls(camera, renderer.domElement)
  controls.enableDamping = true; controls.dampingFactor = 0.08; controls.maxPolarAngle = Math.PI * 0.48
  controls.minDistance = 16; controls.maxDistance = 120; controls.target.copy(camTarget)
  controls.autoRotate = true; controls.autoRotateSpeed = 0.42
  tAuto.onclick = () => { controls.autoRotate = !controls.autoRotate; tAuto.classList.toggle('on', controls.autoRotate) }
  tReset.onclick = () => { camera.position.set(44, 34, 52); controls.target.copy(camTarget) }

  // ── selection / inspector ──
  const ray = new THREE.Raycaster(), ndc = new THREE.Vector2()
  let selected = null, selBox = null
  function pickAt(cx, cy) {
    const r = renderer.domElement.getBoundingClientRect()
    ndc.x = ((cx - r.left) / r.width) * 2 - 1; ndc.y = -((cy - r.top) / r.height) * 2 + 1
    ray.setFromCamera(ndc, camera)
    const hit = ray.intersectObjects(selectable, true)
    return hit.length ? hit[0].object.userData.pickRoot : null
  }
  function metricsObj(a) { const o = {}; (a.metrics || []).forEach(m => { o[m[0]] = `${m[2]}${m[1] ? ' ' + m[1] : ''}` }); return o }
  function openInspector(a) {
    inspector.classList.remove('hidden'); inspector.innerHTML = ''
    inspector.append(el('div', { class: 'insp-head' },
      el('div', { class: 'insp-ic' }, icon(a.icon || 'ti-cube')),
      el('div', {}, el('div', { class: 'insp-title' }, a.name), el('div', { class: 'insp-sub' }, (a.id || '') + ' · ' + a.type)),
      el('div', { class: 'insp-close', onClick: () => { inspector.classList.add('hidden'); clearSel() } }, '✕')))
    const body = el('div', { class: 'insp-body' })
    body.append(el('div', { style: { display: 'flex', gap: '8px', marginBottom: '12px' } },
      el('span', { class: `pill ${a.status === 'crit' ? 'pill-red' : a.status === 'warn' ? 'pill-amber' : 'pill-green'}` },
        a.status === 'crit' ? 'CRITICAL' : a.status === 'warn' ? 'WARNING' : 'HEALTHY'),
      el('span', { class: 'pill pill-green' }, '● LIVE')))
    const mt = el('div', { class: 'insp-metrics' })
    ;(a.metrics || []).forEach(m => mt.append(el('div', { class: 'insp-metric' }, el('div', { class: 'ml' }, m[0]), el('div', { class: 'mv' }, m[2] + (m[1] ? ' ' + m[1] : '')))))
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
    body.append(el('div', { class: 'insp-actions' }, askBtn, aiOut))
    inspector.append(body)
  }
  function clearSel() { if (selBox) { scene.remove(selBox); selBox.geometry.dispose(); selBox = null } selected = null }
  function selectAsset(g, openInsp = true) {
    clearSel(); selected = g
    if (g) { selBox = new THREE.BoxHelper(g, 0x10b981); scene.add(selBox); if (openInsp) openInspector(g.userData.asset) }
  }
  // explainable highlight — outline the asset a simulation/answer is about, in pulsing red
  let focusBox = null, focusTarget = null
  function focusAsset(id) {
    if (focusBox) { scene.remove(focusBox); focusBox.geometry.dispose(); focusBox = null }
    focusTarget = null
    if (!id) return
    const g = selectable.find(s => s.userData.asset.id === id)
    if (!g) return
    focusBox = new THREE.BoxHelper(g, 0x22d3ee); focusBox.material.transparent = true; scene.add(focusBox)
    const p = new THREE.Vector3(); g.getWorldPosition(p); focusTarget = p
  }

  // ── Fix It Live drill: highlight the asset the current step is working on ──
  const STEP_TONE = { work: 0xf59e0b, fault: 0xef4444, done: 0x10b981, safe: 0x3b82f6, locked: 0x60a5fa, focus: 0x22d3ee }
  let stepBox = null, stepAnchorV = null, stepKey = null, stepCol = 0x22d3ee
  const stepTag = el('div', { class: 'evw-steptag' }); host.append(stepTag); stepTag.style.display = 'none'
  function setStep(spec) {
    if (!spec || !spec.asset) {
      if (stepBox) { scene.remove(stepBox); stepBox.geometry.dispose(); stepBox = null }
      stepTag.style.display = 'none'; stepAnchorV = null; stepKey = null; return
    }
    stepCol = STEP_TONE[spec.tone] || STEP_TONE.focus
    const g = selectable.find(s => s.userData.asset.id === spec.asset)
    if (g) {
      if (stepBox) { scene.remove(stepBox); stepBox.geometry.dispose() }
      stepBox = new THREE.BoxHelper(g, stepCol); stepBox.material.transparent = true; scene.add(stepBox)
      const p = new THREE.Vector3(); g.getWorldPosition(p); stepAnchorV = p; focusTarget = p
    }
    stepTag.textContent = spec.label || ''
    stepTag.style.display = spec.label ? 'block' : 'none'
    stepTag.style.setProperty('--tone', '#' + stepCol.toString(16).padStart(6, '0'))
    if (spec.key !== stepKey) { stepKey = spec.key; if (stepAnchorV && spec.ping !== false) emitSparks(stepAnchorV, spec.tone === 'work' ? 34 : 20, 1.4) }
  }

  renderer.domElement.addEventListener('pointerdown', ev => {
    const g = pickAt(ev.clientX, ev.clientY)
    if (g) {
      // in drill mode, the host handles the click (opens an in-world action menu) and we skip the inspector
      if (onPick) { const handled = onPick(g.userData.asset.id, ev.clientX, ev.clientY); if (handled) { selectAsset(g, false); return } }
      selectAsset(g)
    } else { inspector.classList.add('hidden'); clearSel() }   // click empty space → close
  })
  function onMove(ev) {
    const g = pickAt(ev.clientX, ev.clientY)
    if (g) {
      const r = host.getBoundingClientRect()
      tooltip.style.display = 'block'; tooltip.style.left = (ev.clientX - r.left + 12) + 'px'
      tooltip.style.top = (ev.clientY - r.top + 12) + 'px'; tooltip.textContent = g.userData.asset.name
      renderer.domElement.style.cursor = 'pointer'
    } else { tooltip.style.display = 'none'; renderer.domElement.style.cursor = 'grab' }
  }
  renderer.domElement.addEventListener('pointermove', onMove)

  // ── composer / bloom ──
  let composer = null
  loadBloom().then(B => {
    if (!B) return
    composer = new B.EffectComposer(renderer)
    composer.addPass(new B.RenderPass(scene, camera))
    composer.addPass(new B.UnrealBloomPass(new THREE.Vector2(W(), H()), 0.55, 0.6, 0.8))
    composer.addPass(new B.OutputPass()); composer.setSize(W(), H())
  })

  // ── live-data hook: recolour transformer, fill BESS, pulse chargers ──
  const alertRed = new THREE.Color(0xef4444), amber = new THREE.Color(0xf59e0b), green = new THREE.Color(0x10b981)
  const _c = new THREE.Color()
  const dat = { transformerTemp: 66, gridLoad: 68, headroom: 32, peakDemand: 420, bessSoc: 72, bessPower: -60,
    solar: 210, sessions: 18, faulted: 0, dcfcPower: 148, runaway: 2, cellMax: 33, soh: 93 }
  function update(live) {
    if (!live) return
    setStep(live.__step)   // Fix It Live: spotlight + label the asset the current step works on
    // staged cascade bubbles — sync DOM to the active stages
    const stages = live.__stages || []
    const want = new Set(stages.map(s => s.n))
    for (const [n, rec] of stageEls) { if (!want.has(n)) { rec.el.remove(); stageEls.delete(n) } }
    for (const s of stages) {
      if (!stageEls.has(s.n)) {
        const anchor = stageAnchors[s.anchor] || stageAnchors.transformer
        const b = el('div', { class: 'evw-step' }, el('span', { class: 'n' }, String(s.n)), el('span', { class: 't' }, s.label))
        host.append(b)
        stageEls.set(s.n, { el: b, anchor })
        emitSparks(anchor); focusTarget = anchor   // spark burst + camera follows each bubble
      } else { stageEls.get(s.n).anchor = stageAnchors[s.anchor] || stageEls.get(s.n).anchor }
    }
    const g = (k, d) => (live[k] == null ? d : live[k])
    dat.transformerTemp = g('ev:transformerTemp', dat.transformerTemp)
    dat.gridLoad = g('ev:gridLoad', dat.gridLoad)
    dat.headroom = g('ev:loadHeadroom', dat.headroom)
    dat.peakDemand = g('ev:peakDemand', dat.peakDemand)
    dat.bessSoc = clamp(g('ev:bessSoc', dat.bessSoc), 0, 100)
    dat.bessPower = g('ev:bessPower', dat.bessPower)
    dat.solar = g('ev:solarOutput', dat.solar)
    dat.sessions = Math.round(g('ev:sessionsActive', dat.sessions))
    dat.faulted = Math.round(g('ev:faultedChargers', dat.faulted))
    dat.dcfcPower = g('ev:chargingPower', dat.dcfcPower)
    dat.runaway = g('ev:thermalRunawayRisk', dat.runaway)
    dat.cellMax = g('ev:cellTempMax', dat.cellMax)
    dat.soh = g('ev:stateOfHealth', dat.soh)

    // transformer band + light colour by load
    const txTone = dat.gridLoad >= 95 ? 'crit' : dat.gridLoad >= 85 ? 'warn' : 'ok'
    if (store.txBody) store.txBody.userData.tone = txTone
    const txCol = txTone === 'crit' ? alertRed : txTone === 'warn' ? amber : green
    const txa = store.tx
    if (txa) {
      txa.status = txTone
      txa.metrics = [['Load', '%', Math.round(dat.gridLoad)], ['Temp', '°C', Math.round(dat.transformerTemp)],
        ['Headroom', '%', Math.round(dat.headroom)], ['Peak', 'kW', Math.round(dat.peakDemand)]]
      const txI = txTone === 'crit' ? 1.7 : txTone === 'warn' ? 1.2 : 0.7
      if (txa.band) { txa.band.material.color.copy(txCol); txa.band.material.emissive.copy(txCol); txa.band.material.emissiveIntensity = txI }
      if (txa.light) { txa.light.material.color.copy(txCol); txa.light.material.emissive.copy(txCol); txa.light.material.emissiveIntensity = txI }
    }
    setLabel(lblTx, 'Transformer T1', Math.round(dat.gridLoad) + '%', txTone)
    setLabel(lblEms, 'EMS headroom', Math.round(dat.headroom) + '%', dat.headroom < 8 ? 'crit' : dat.headroom < 15 ? 'warn' : 'ok')
    setLabel(lblSolar, 'Solar', Math.round(dat.solar) + ' kW', 'sun')
    setLabel(lblDcfc, 'DC-fast', Math.round(dat.dcfcPower) + ' kW')

    // BESS module fill by SoC
    const mods = store.bessModules
    const lit = Math.round((dat.bessSoc / 100) * mods.length)
    const bessTone = dat.runaway >= 40 ? 'crit' : (dat.cellMax >= 42 || dat.soh <= 85) ? 'warn' : 'ok'
    if (store.bessShell) store.bessShell.userData.tone = bessTone
    for (let i = 0; i < mods.length; i++) {
      const on = i < lit
      const col = bessTone === 'crit' && on ? alertRed : bessTone === 'warn' && on ? amber : on ? green : _c.set(0x0a3a30)
      mods[i].material.emissive.copy(col); mods[i].material.color.copy(col)
      mods[i].material.emissiveIntensity = on ? (bessTone === 'crit' ? 1.7 : 0.9) : 0.25
    }
    setLabel(lblBess, 'BESS', Math.round(dat.bessSoc) + '%', bessTone)
    const bessAsset = selectable.find(s => s.userData.asset.id === 'BESS-A')
    if (bessAsset) { bessAsset.userData.asset.status = bessTone
      bessAsset.userData.asset.metrics = [['SoC', '%', Math.round(dat.bessSoc)], ['Power', 'kW', Math.round(dat.bessPower)],
        ['Cell max', '°C', Math.round(dat.cellMax)], ['Runaway', '%', Math.round(dat.runaway)]] }

    // faulted chargers → recolour their indicator red (deterministic each frame + restore)
    let need = dat.faulted
    for (const u of [...store.dcfc, ...bays]) {
      const a = u.userData.asset
      const ind = a.strip || a.led
      const crit = need > 0; if (crit) need--
      const st = crit ? 'crit' : (a.id === 'DCFC-03' ? 'warn' : 'ok')
      if (a.body) a.body.userData.tone = crit ? 'dead' : st   // faulted charger goes DARK
      if (ind) ind.userData.crit = crit
      if (a.status !== st) {
        a.status = st; a._faulted = crit
        if (ind) { const c = st === 'crit' ? alertRed : st === 'warn' ? amber : green; ind.material.color.copy(c); ind.material.emissive.copy(c) }
      }
    }

    // flow line intensities + direction — more of the network reacts, not just grid→tx
    const norm = (v, m) => clamp(Math.abs(v) / m, 0.15, 1)
    setFlow(flows.gridToTx, 1, 0.6 + norm(dat.peakDemand, 550) * 1.6, dat.gridLoad >= 85 ? 0xef4444 : 0x2f7bff)
    setFlow(flows.txToBus, 1, 0.6 + norm(dat.peakDemand, 550) * 1.6, dat.gridLoad >= 90 ? 0xef4444 : 0x2563eb)
    setFlow(flows.solarToBus, 1, 0.4 + norm(dat.solar, 300) * 1.8, 0x10b981)
    setFlow(flows.bessToBus, dat.bessPower < 0 ? 1 : -1, 0.4 + norm(dat.bessPower, 120) * 1.6, dat.runaway >= 40 ? 0xef4444 : 0xf59e0b)
    setFlow(flows.busToDcfc, 1, 0.5 + norm(dat.dcfcPower, 600) * 1.6, dat.faulted > 0 ? 0xef4444 : 0x2563eb)
    setFlow(flows.busToAc, 1, 0.5 + norm(dat.sessions, 24) * 1.2)
    setFlow(flows.busToBldg, 1, 0.7)

    // site-wide crisis level (0..1) → drives the red alert light + fog in the render loop
    alertLevel = clamp(Math.max(
      dat.gridLoad >= 88 ? (dat.gridLoad - 88) / 14 : 0,
      dat.runaway >= 40 ? (dat.runaway - 40) / 40 : 0,
      dat.faulted > 0 ? Math.min(1, dat.faulted / 6) : 0), 0, 1)

    // if an inspector is open, refresh its metric values in place
    if (selected && !inspector.classList.contains('hidden')) {
      const mvs = inspector.querySelectorAll('.insp-metric .mv')
      ;(selected.userData.asset.metrics || []).forEach((m, i) => { if (mvs[i]) mvs[i].textContent = m[2] + (m[1] ? ' ' + m[1] : '') })
    }
  }
  function setFlow(mesh, dir, speed, color) {
    const f = mesh.userData.flow; f.dir = dir; f.speed = speed
    if (color != null) { f.mat.color.set(color); f.mat.emissive.set(color) }
  }

  // ── loop ──
  let raf, last = performance.now(), fc = 0, ft = 0, t = 0
  const _v = new THREE.Vector3()
  function loop(now) {
    raf = requestAnimationFrame(loop)
    const dt = Math.min(0.05, (now - last) / 1000); last = now; t += dt
    fc++; ft += dt; if (ft >= 0.5) { const f = document.getElementById('evw-fps'); if (f) f.textContent = Math.round(fc / ft) + ' fps'; fc = 0; ft = 0 }

    for (const a of animators) {
      if (a.kind === 'car') {
        const d = a.obj.userData.drive; d.t = (d.t + d.speed * dt) % 1
        const p = roadCurve.getPoint(d.t); const tan = roadCurve.getTangent(d.t)
        a.obj.position.set(p.x, 0.02, p.z); a.obj.rotation.y = Math.atan2(tan.x, tan.z)
      } else if (a.kind === 'led') {
        const cr = a.obj.userData && a.obj.userData.crit
        a.obj.material.emissiveIntensity = (cr ? 1.6 : 1.2) + Math.sin(t * (cr ? 9 : 4) + a.obj.position.x) * (cr ? 0.5 : 0.7)
      } else if (a.kind === 'cable') {
        a.obj.material.emissiveIntensity = 0.7 + Math.abs(Math.sin(t * 3)) * 1.2
      } else if (a.kind === 'flow') {
        const f = a.obj.userData.flow
        f.tex.offset.x -= f.dir * f.speed * dt * 0.9
        f.mat.emissiveIntensity = 1.2 + Math.sin(t * 3) * 0.3
      }
    }
    // gentle BESS label + module shimmer already handled in update()

    // site-wide red alert — light + atmosphere react to the crisis level
    alertLight.intensity = alertLevel * (2.6 + Math.sin(t * 7) * 1.6)
    { const al = alertLevel * 0.82; scene.fog.color.setRGB(0.071 + 0.30 * al, 0.125 - 0.085 * al, 0.263 - 0.195 * al) }

    // spark particles — rise, slow under gravity, fade out
    for (let i = 0; i < SPN; i++) {
      if (spLife[i] > 0) {
        spLife[i] -= dt * 1.4
        spPos[i * 3] += spVel[i * 3] * dt * 7
        spPos[i * 3 + 1] += (spVel[i * 3 + 1] - 5 * (1 - spLife[i])) * dt * 7
        spPos[i * 3 + 2] += spVel[i * 3 + 2] * dt * 7
        if (spLife[i] <= 0) spPos[i * 3 + 1] = -999
      }
    }
    spGeo.attributes.position.needsUpdate = true

    // project world labels to screen
    for (const rec of labels) {
      _v.copy(rec.anchor).project(camera)
      const vis = _v.z < 1
      rec.el.style.display = vis ? 'block' : 'none'
      if (vis) {
        rec.el.style.left = ((_v.x * 0.5 + 0.5) * W()) + 'px'
        rec.el.style.top = ((-_v.y * 0.5 + 0.5) * H()) + 'px'
      }
    }
    for (const [, rec] of stageEls) {
      _v.copy(rec.anchor).project(camera)
      const vis = _v.z < 1
      rec.el.style.display = vis ? 'flex' : 'none'
      if (vis) {
        rec.el.style.left = ((_v.x * 0.5 + 0.5) * W()) + 'px'
        rec.el.style.top = ((-_v.y * 0.5 + 0.5) * H()) + 'px'
      }
    }
    if (stepBox) { stepBox.update(); stepBox.material.color.setHex(stepCol); stepBox.material.opacity = 0.65 + 0.35 * Math.abs(Math.sin(t * 4)) }
    if (stepAnchorV) {
      _v.copy(stepAnchorV).project(camera); const vis = _v.z < 1
      stepTag.style.display = vis && stepTag.textContent ? 'block' : 'none'
      if (vis) { stepTag.style.left = ((_v.x * 0.5 + 0.5) * W()) + 'px'; stepTag.style.top = ((-_v.y * 0.5 + 0.5) * H() - 46) + 'px' }
    }
    // ── dramatic asset state: flush RED when critical, go BLACK when dead, pulsing ──
    {
      const bods = store.__bodies || (store.__bodies = [store.txBody, store.bessShell, ...(store.dcfc || []).map(u => u.userData.asset && u.userData.asset.body)].filter(Boolean))
      const pulse = Math.abs(Math.sin(t * 8)), pulseS = Math.abs(Math.sin(t * 3))
      for (const m of bods) {
        const tone = m.userData.tone || 'ok'; const mat = m.material; const hasE = !!mat.emissive
        if (tone === 'crit') { if (hasE) { mat.emissive.copy(alertRed); mat.emissiveIntensity = 0.9 + 1.4 * pulse } mat.color.setRGB(0.7 + 0.25 * pulse, 0.08, 0.11) }
        else if (tone === 'warn') { if (hasE) { mat.emissive.copy(amber); mat.emissiveIntensity = 0.35 + 0.5 * pulseS } if (m.userData.baseColor != null) mat.color.setHex(m.userData.baseColor) }
        else if (tone === 'dead') { mat.color.setRGB(0.04, 0.045, 0.06); if (hasE) { mat.emissive.setRGB(0.18 + 0.12 * pulseS, 0.02, 0.02); mat.emissiveIntensity = 0.6 } }
        else { if (hasE) { mat.emissive.setHex(0x000000); mat.emissiveIntensity = 0 } if (m.userData.baseColor != null) mat.color.setHex(m.userData.baseColor) }
      }
    }
    if (selBox) selBox.update()
    if (focusBox) { focusBox.update(); focusBox.material.opacity = 0.6 + 0.4 * Math.abs(Math.sin(t * 3)) }
    if (focusTarget) controls.target.lerp(focusTarget, 0.035)
    controls.update()
    if (composer) composer.render(); else renderer.render(scene, camera)
  }
  raf = requestAnimationFrame(loop); onReady && onReady()

  function onResize() { camera.aspect = W() / H(); camera.updateProjectionMatrix(); renderer.setSize(W(), H()); if (composer) composer.setSize(W(), H()) }
  window.addEventListener('resize', onResize)
  const ro = new ResizeObserver(() => onResize()); ro.observe(host)   // shrink canvas to its column

  return {
    update, focusAsset,
    dispose() {
      cancelAnimationFrame(raf)
      window.removeEventListener('resize', onResize); ro.disconnect()
      renderer.domElement.removeEventListener('pointermove', onMove)
      pmrem.dispose(); renderer.dispose()
      scene.traverse(o => { if (o.geometry) o.geometry.dispose(); if (o.material) (Array.isArray(o.material) ? o.material : [o.material]).forEach(m => m.dispose && m.dispose()) })
      try { host.querySelectorAll('canvas,.v-top,.v-tools,.v-tip,.v-tooltip,.inspector,.evw-legend,.evw-label,.evw-steptag,.evw-step').forEach(n => n.remove()) } catch {}
    }
  }
}
