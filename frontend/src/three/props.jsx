/**
 * props.jsx — realistic 3-D equipment library for data-centres and hospitals.
 *
 * All builders return a THREE.Group. Props are unit-scaled (~feet); Scene.jsx
 * applies PROP_SCALE = 0.32 so 1 unit ≈ 0.32 m.
 *
 * Real-world reference dimensions guide every builder so proportions match
 * actual equipment (42U racks, Siemens MRI gantry, GE CT bore, etc.).
 **/
import * as THREE from 'three'

/* ── low-level geometry helpers ──────────────────────────────────────────── */
const box = (w, h, d, mat, x = 0, y = 0, z = 0) => {
  const m = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), mat)
  m.position.set(x, y, z); m.castShadow = true; m.receiveShadow = true
  return m
}
const cyl = (rt, rb, h, mat, x = 0, y = 0, z = 0, seg = 18) => {
  const m = new THREE.Mesh(new THREE.CylinderGeometry(rt, rb, h, seg), mat)
  m.position.set(x, y, z); m.castShadow = true; m.receiveShadow = true
  return m
}
const grp = (...ch) => { const g = new THREE.Group(); ch.flat().forEach((c) => c && g.add(c)); return g }
const torus = (r, tube, mat, x = 0, y = 0, z = 0, seg = 8, rSeg = 24) => {
  const m = new THREE.Mesh(new THREE.TorusGeometry(r, tube, seg, rSeg), mat)
  m.position.set(x, y, z); return m
}

/* ── status pin ─────────────────────────────────────────────────────────── */
export function pStatusPin(color) {
  const g = new THREE.Group()
  const m = new THREE.MeshStandardMaterial({ color, emissive: color, emissiveIntensity: 1.6 })
  const s = new THREE.Mesh(new THREE.SphereGeometry(0.32, 16, 16), m)
  const halo = new THREE.Mesh(new THREE.SphereGeometry(0.55, 16, 16),
    new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.22 }))
  g.add(s, halo); g.userData.pin = true
  return g
}

/* ═══════════════════════════════════════════════════════════════════════════
   DATA-CENTRE
═══════════════════════════════════════════════════════════════════════════ */

/* 42 U open-frame rack — 600 mm wide × 1000 mm deep × 2000 mm tall */
function pRack(M) {
  const g = grp()
  const W = 2, H = 7.4, D = 2.6, hH = H / 2, hD = D / 2

  g.add(box(W, H, D, M.rackBlack, 0, hH, 0))
  // perforated front door (subtly lighter face)
  g.add(box(W - 0.06, H - 0.1, 0.06, M.rack, 0, hH, hD + 0.01))

  // individual server / switch / storage faceplates
  const units = [
    { y: 0.35, h: 0.42, t: 'srv' }, { y: 0.82, h: 0.42, t: 'srv' },
    { y: 1.29, h: 0.42, t: 'srv' }, { y: 1.76, h: 0.42, t: 'srv' },
    { y: 2.23, h: 0.85, t: 'sw' },  { y: 3.13, h: 0.42, t: '' },
    { y: 3.60, h: 0.42, t: 'srv' }, { y: 4.07, h: 0.42, t: 'srv' },
    { y: 4.54, h: 0.42, t: 'srv' }, { y: 5.01, h: 0.85, t: 'sto' },
    { y: 5.91, h: 0.42, t: 'srv' }, { y: 6.38, h: 0.42, t: 'srv' },
  ]
  units.forEach(({ y, h, t }) => {
    const fz = hD + 0.04
    if (!t) { g.add(box(1.7, h - 0.02, 0.04, M.dark, 0, y + h / 2, fz)); return }
    const col = t === 'sw' ? 0x1c2030 : t === 'sto' ? 0x151820 : 0x18191f
    const fm = new THREE.MeshStandardMaterial({ color: col, roughness: 0.55, metalness: 0.5 })
    g.add(box(1.7, h - 0.04, 0.05, fm, 0, y + h / 2, fz))
    const lc = t === 'sw' ? 8 : t === 'sto' ? 6 : 4
    for (let l = 0; l < lc; l++) {
      const on = Math.random() > 0.25
      const c = t === 'sw' ? (Math.random() > 0.5 ? 0x22d3ee : 0xf59e0b)
               : t === 'sto' ? (Math.random() > 0.2 ? 0x22d3ee : 0xf43f5e)
               : on ? 0x16a34a : 0x22d3ee
      const led = new THREE.Mesh(new THREE.BoxGeometry(0.08, 0.08, 0.04), M.led(c))
      led.position.set(-0.6 + l * (1.2 / lc), y + h / 2 + 0.02, fz + 0.03)
      if (on) led.userData.blink = true
      g.add(led)
    }
    const pwr = new THREE.Mesh(new THREE.BoxGeometry(0.12, 0.12, 0.04), M.led(0x16a34a))
    pwr.position.set(0.74, y + h / 2, fz + 0.03); g.add(pwr)
    if (t === 'sw') g.add(box(0.5, 0.3, 0.04, M.screen, -0.3, y + h / 2, fz + 0.04))
    if (t === 'srv') g.add(box(0.18, 0.14, 0.06, M.dark, 0.35, y + h / 2, fz + 0.04))
  })

  // top fan tray with 4 fans + ring guards
  g.add(box(W, 0.35, D, M.dark, 0, H + 0.175, 0))
  for (let f = 0; f < 4; f++) {
    const fx = -0.7 + f * 0.48
    const ring = torus(0.32, 0.05, M.steel, fx, H + 0.22, 0); ring.rotation.x = Math.PI / 2; g.add(ring)
    const fan = grp()
    for (let b = 0; b < 4; b++) {
      const bl = box(0.5, 0.06, 0.14, M.steel); bl.rotation.y = b * Math.PI / 2; fan.add(bl)
    }
    fan.position.set(fx, H + 0.23, 0); fan.userData.spin = 2.6 + Math.random() * 0.8; g.add(fan)
  }

  // base with levelling castors
  g.add(box(W + 0.1, 0.15, D + 0.1, M.dark, 0, 0.075, 0))
  ;[[-0.8, -1.1], [0.8, -1.1], [-0.8, 1.1], [0.8, 1.1]].forEach(([cx, cz]) =>
    g.add(cyl(0.15, 0.15, 0.18, M.rubber, cx, -0.08, cz)))

  // right-side cable-management channel + D-rings
  g.add(box(0.18, H - 0.3, 0.4, M.dark, W / 2 + 0.04, hH, hD - 0.12))
  for (let i = 0; i < 6; i++) {
    const r = torus(0.12, 0.04, M.steel, W / 2 + 0.04, 1 + i * 1.0, hD); g.add(r)
  }
  return g
}

/* CRAC — Computer Room Air Conditioner (downflow, 3-fan front) */
function pCRAC(M) {
  const g = grp()
  g.add(box(3.4, 6.2, 2.4, M.metal, 0, 3.1, 0))
  // louvred front panel
  g.add(box(3.2, 5.8, 0.07, M.dark, 0, 3.1, 1.22))
  for (let i = 0; i < 14; i++) g.add(box(2.8, 0.12, 0.08, M.dark, 0, 0.7 + i * 0.4, 1.26))
  // 3 axial fans top section
  for (let f = 0; f < 3; f++) {
    const fx = -1.0 + f * 1.0
    const rng = torus(0.7, 0.07, M.dark, fx, 5.6, 1.24); rng.rotation.x = Math.PI / 2; g.add(rng)
    const fan = grp()
    for (let b = 0; b < 5; b++) {
      const bl = box(1.2, 0.07, 0.28, M.steel); bl.rotation.y = b * Math.PI * 2 / 5; fan.add(bl)
    }
    fan.position.set(fx, 5.6, 1.28); fan.userData.spin = 1.1 + f * 0.15; g.add(fan)
  }
  // control display panel
  g.add(box(1.6, 0.7, 0.07, M.screen, 0, 4.0, 1.27))
  const led = new THREE.Mesh(new THREE.BoxGeometry(0.55, 0.12, 0.05), M.led(0x22d3ee))
  led.position.set(-0.55, 4.65, 1.27); led.userData.blink = true; g.add(led)
  // hot-gas discharge duct on top
  g.add(cyl(0.6, 0.6, 0.45, M.dark, 0, 6.58, 0))
  // base feet
  ;[[-1.3, -1], [1.3, -1], [-1.3, 1], [1.3, 1]].forEach(([cx, cz]) =>
    g.add(box(0.3, 0.12, 0.3, M.dark, cx, 0.06, cz)))
  return g
}

/* Tower UPS — real proportions ≈ 900H × 440W × 700D mm */
function pUPS(M) {
  const g = grp()
  g.add(box(2.6, 5.4, 2.0, M.dark, 0, 2.7, 0))
  // inset front bezel
  g.add(box(2.3, 5.0, 0.06, new THREE.MeshStandardMaterial({ color: 0x1a1d28, roughness: 0.5, metalness: 0.55 }), 0, 2.7, 1.03))
  // large colour LCD
  g.add(box(1.8, 1.0, 0.05, M.screen, 0, 4.3, 1.06))
  // battery status bar (3 green segments)
  for (let i = 0; i < 3; i++) {
    const b = new THREE.Mesh(new THREE.BoxGeometry(0.38, 0.22, 0.04), M.led(0x16a34a))
    b.position.set(-0.42 + i * 0.42, 2.95, 1.06); g.add(b)
  }
  // input/output sockets panel
  g.add(box(2.2, 0.6, 0.06, M.dark, 0, 1.0, 1.04))
  for (let i = 0; i < 4; i++) g.add(box(0.3, 0.22, 0.08, M.plastic, -0.78 + i * 0.52, 1.0, 1.08))
  // power-good LED
  const pl = new THREE.Mesh(new THREE.BoxGeometry(0.22, 0.22, 0.04), M.led(0x16a34a))
  pl.position.set(1.0, 3.5, 1.06); g.add(pl)
  return g
}

/* 1RU network switch — 48-port chassis */
function pNetwork(M) {
  const g = grp()
  g.add(box(2.2, 0.7, 2.4, M.rackBlack, 0, 0.35, 0))
  g.add(box(2.0, 0.6, 0.06, M.dark, 0, 0.35, 1.23))
  // SFP port block (right)
  g.add(box(0.5, 0.3, 0.08, M.dark, 0.7, 0.42, 1.26))
  // port LEDs (48-port pairs)
  for (let i = 0; i < 12; i++) {
    const c = Math.random() > 0.35 ? 0x22d3ee : 0xf59e0b
    const l = new THREE.Mesh(new THREE.BoxGeometry(0.09, 0.09, 0.04), M.led(c))
    l.position.set(-0.75 + i * 0.13, 0.44, 1.27); l.userData.blink = Math.random() > 0.3; g.add(l)
  }
  // console + management ports
  g.add(box(0.22, 0.18, 0.08, M.dark, -0.75, 0.26, 1.27))
  // power LED
  const pw = new THREE.Mesh(new THREE.BoxGeometry(0.11, 0.11, 0.04), M.led(0x16a34a))
  pw.position.set(0.88, 0.26, 1.27); g.add(pw)
  return g
}

/* AHU — Air Handling Unit */
function pAHU(M) {
  const g = grp()
  g.add(box(5, 2.4, 2.6, M.metal, 0, 1.2, 0))
  g.add(cyl(0.9, 0.9, 0.4, M.dark, -1.4, 1.2, 1.4))
  const fan = grp()
  for (let i = 0; i < 4; i++) { const b = box(1.4, 0.05, 0.25, M.steel); b.rotation.y = i * Math.PI / 2; fan.add(b) }
  fan.position.set(-1.4, 1.2, 1.62); fan.rotation.x = Math.PI / 2; fan.userData.spin = 1.2; g.add(fan)
  g.add(box(2, 1, 2.6, M.dark, 1.6, 1.2, 0))
  return g
}

function pVAV(M) {
  const g = grp(); g.add(box(2.4, 1, 1.4, M.metal, 0, 0.5, 0))
  const d = cyl(0.5, 0.5, 1.2, M.dark, 0, 0.5, 1.1); d.rotation.x = Math.PI / 2; g.add(d); return g
}

function pLight(M) {
  return grp(box(3, 0.2, 1, M.white, 0, 0, 0), box(2.7, 0.05, 0.7, M.warm, 0, -0.12, 0))
}

function pDoor(M) {
  const g = grp()
  g.add(box(0.2, 5, 3, M.steel, 0, 2.5, 0))
  g.add(box(0.25, 4.4, 1.3, M.glass, 0, 2.5, 0.7))
  const r = new THREE.Mesh(new THREE.BoxGeometry(0.1, 0.3, 0.5), M.led(0x16a34a)); r.position.set(0.16, 3, -0.9); g.add(r)
  return g
}

function pMeter(M) {
  return grp(box(1.6, 3, 0.8, M.white, 0, 1.5, 0), box(1.1, 1, 0.06, M.screen, 0, 2, 0.42))
}

function pSmallRack(M) {
  const g = grp(); g.add(box(1.6, 3.4, 1.6, M.rack, 0, 1.7, 0))
  for (let i = 0; i < 5; i++) {
    const l = new THREE.Mesh(new THREE.BoxGeometry(0.1, 0.1, 0.04), M.led(0xe11d48))
    l.position.set(-0.4, 1 + i * 0.5, 0.81); l.userData.blink = true; g.add(l)
  }
  return g
}

/* Overhead cable tray (new) — runs ceiling-level */
function pCableTray(M) {
  const g = grp()
  const trayMat = new THREE.MeshStandardMaterial({ color: 0x8a9099, roughness: 0.55, metalness: 0.7 })
  // side rails
  for (const sx of [-1.1, 1.1]) g.add(box(0.08, 0.25, 8, trayMat, sx, 6.3, 0))
  // cross bars every 0.8 units
  for (let i = 0; i < 10; i++) g.add(box(2.3, 0.06, 0.06, trayMat, 0, 6.18, -3.6 + i * 0.8))
  // cable bundles (decorative)
  for (let b = 0; b < 3; b++) {
    const c = [0xf59e0b, 0x22d3ee, 0xe11d48][b]
    g.add(box(0.12, 0.12, 7.8, new THREE.MeshStandardMaterial({ color: c, roughness: 0.9 }), -0.6 + b * 0.6, 6.1, 0))
  }
  return g
}

/* ═══════════════════════════════════════════════════════════════════════════
   HOSPITAL
═══════════════════════════════════════════════════════════════════════════ */

/* MRI — Siemens-style wide-bore horizontal gantry
   bore Ø70 cm / gantry Ø200 cm / tunnel depth 165 cm → ~5.2 units */
function pMRI(M) {
  const g = grp()
  const boreR = 1.1, outerR = 3.3, depth = 5.2
  const cy = outerR    // centre height so bottom sits near floor

  const gantryMat = M.medWhite
  const faceMat = new THREE.MeshStandardMaterial({ color: 0xf0f4f8, roughness: 0.35, metalness: 0.08, side: THREE.DoubleSide })
  const boreMat = new THREE.MeshStandardMaterial({ color: 0x060912, roughness: 0.35, metalness: 0.4, side: THREE.BackSide })

  // outer cylindrical shell (open-ended)
  const outer = new THREE.Mesh(new THREE.CylinderGeometry(outerR, outerR, depth, 48, 1, true), gantryMat)
  outer.rotation.x = Math.PI / 2; outer.position.set(0, cy, 0); g.add(outer)

  // bore interior
  const bore = new THREE.Mesh(new THREE.CylinderGeometry(boreR, boreR, depth + 0.8, 36, 1, true), boreMat)
  bore.rotation.x = Math.PI / 2; bore.position.set(0, cy, 0); g.add(bore)

  // annular end faces
  const ringGeo = new THREE.RingGeometry(boreR, outerR, 48)
  for (const sz of [-1, 1]) {
    const face = new THREE.Mesh(ringGeo, faceMat)
    face.position.set(0, cy, sz * depth / 2); g.add(face)
  }

  // decorative concentric bands on each face
  ;[boreR + 0.18, boreR + 0.45, outerR - 0.22, outerR - 0.52].forEach(r => {
    for (const sz of [-1, 1]) {
      const t = torus(r, 0.042, M.steel, 0, cy, sz * (depth / 2 + 0.015))
      t.rotation.x = Math.PI / 2; g.add(t)
    }
  })

  // bore-entry warning ring (yellow)
  const warn = torus(boreR + 0.06, 0.11, new THREE.MeshStandardMaterial({ color: 0xf59e0b, roughness: 0.4, metalness: 0.15, emissive: 0xd47708, emissiveIntensity: 0.18 }), 0, cy, depth / 2 + 0.06)
  warn.rotation.x = Math.PI / 2; g.add(warn)

  // gradient stripe (yellow/red caution band, partial torus arc on side)
  const cautionArc = new THREE.Mesh(new THREE.TorusGeometry(outerR - 0.08, 0.14, 8, 36, Math.PI * 0.6),
    new THREE.MeshStandardMaterial({ color: 0xf59e0b, roughness: 0.55 }))
  cautionArc.rotation.x = Math.PI / 2; cautionArc.rotation.z = -Math.PI * 0.3
  cautionArc.position.set(0, cy, 0); g.add(cautionArc)

  // patient table
  const tableY = cy - outerR * 0.52
  g.add(box(1.5, 0.14, 10, M.medWhite, 0, tableY, 0.6))
  g.add(box(1.18, 0.07, 6.5, M.mattress, 0, tableY + 0.105, 0))
  g.add(box(2.0, tableY - 0.07, 2.6, M.medWhite, 0, (tableY - 0.07) / 2, 3.8))
  // table rails
  for (const sx of [-0.78, 0.78]) g.add(box(0.06, 0.12, 6.5, M.chrome, sx, tableY + 0.14, 0))

  // RF coil connector port (side of bore entry)
  g.add(cyl(0.12, 0.12, 0.3, M.dark, 1.3, cy - outerR + 0.5, depth / 2 + 0.02))

  // operator touch-panel (left side)
  g.add(box(0.35, 1.8, 1.4, M.medWhite, -(outerR + 0.18), cy - outerR + 1.0, 0))
  g.add(box(0.06, 1.4, 1.0, M.screen, -(outerR + 0.15), cy - outerR + 1.1, 0))

  // top status LED strip
  const topLed = new THREE.Mesh(new THREE.BoxGeometry(1.8, 0.09, 0.15), M.led(0x22d3ee))
  topLed.position.set(0, cy + outerR - 0.16, depth / 2 + 0.02); topLed.userData.blink = true; g.add(topLed)

  // vent slots on lower front
  for (let v = 0; v < 5; v++) g.add(box(2.2, 0.06, 0.06, M.dark, 0, cy - outerR * 0.72 + v * 0.2, depth / 2 + 0.02))

  return g
}

/* CT Scanner — GE-style gantry, bore Ø70 cm, thin ring ~55 cm deep */
function pCTScanner(M) {
  const g = grp()
  const boreR = 0.88, outerR = 2.55, depth = 2.0, cy = outerR

  const faceMat = new THREE.MeshStandardMaterial({ color: 0xf0f4f8, roughness: 0.3, metalness: 0.1, side: THREE.DoubleSide })
  const boreMat = new THREE.MeshStandardMaterial({ color: 0x060912, roughness: 0.4, side: THREE.BackSide })

  const outer = new THREE.Mesh(new THREE.CylinderGeometry(outerR, outerR, depth, 40, 1, true), M.medWhite)
  outer.rotation.x = Math.PI / 2; outer.position.set(0, cy, 0); g.add(outer)

  const bore = new THREE.Mesh(new THREE.CylinderGeometry(boreR, boreR, depth + 0.5, 32, 1, true), boreMat)
  bore.rotation.x = Math.PI / 2; bore.position.set(0, cy, 0); g.add(bore)

  const rGeo = new THREE.RingGeometry(boreR, outerR, 40)
  for (const sz of [-1, 1]) {
    const f = new THREE.Mesh(rGeo, faceMat); f.position.set(0, cy, sz * depth / 2); g.add(f)
    // gantry tilt arc
    const arc = new THREE.Mesh(new THREE.TorusGeometry(outerR - 0.12, 0.07, 8, 32, Math.PI * 0.45), M.dark)
    arc.rotation.x = Math.PI / 2; arc.position.set(0, cy, sz * (depth / 2 + 0.015)); g.add(arc)
  }

  // X-ray tube housing bump on top
  g.add(box(0.45, 0.45, depth, M.dark, 0, cy + outerR - 0.25, 0))

  // laser positioning lights
  ;[[0, depth / 2 + 0.07], [0, -depth / 2 - 0.07]].forEach(([lx, lz]) => {
    const l = new THREE.Mesh(new THREE.SphereGeometry(0.09, 8, 8), M.led(0xe11d48))
    l.position.set(lx, cy, lz); g.add(l)
  })

  // warning ring
  const wr = torus(boreR + 0.05, 0.09, new THREE.MeshStandardMaterial({ color: 0xf59e0b, roughness: 0.4, emissive: 0xd47708, emissiveIntensity: 0.12 }), 0, cy, depth / 2 + 0.05)
  wr.rotation.x = Math.PI / 2; g.add(wr)

  // decorative rings
  ;[boreR + 0.2, outerR - 0.18].forEach(r => {
    for (const sz of [-1, 1]) {
      const t = torus(r, 0.038, M.steel, 0, cy, sz * (depth / 2 + 0.01))
      t.rotation.x = Math.PI / 2; g.add(t)
    }
  })

  // patient table
  const tY = cy - outerR * 0.5
  g.add(box(1.25, 0.12, 10.5, M.medWhite, 0, tY, 0.9))
  g.add(box(1.0, 0.06, 6.5, M.mattress, 0, tY + 0.09, 0))
  g.add(box(1.7, tY - 0.06, 1.6, M.medWhite, 0, (tY - 0.06) / 2, 4.2))

  return g
}

/* X-Ray — ceiling C-arm (improved) */
function pXray(M) {
  const g = grp()
  g.add(box(1.6, 0.4, 1.6, M.dark, 0, 0.2, 0))
  g.add(cyl(0.3, 0.3, 4.5, M.metal, 0, 2.65, 0))
  // C-arm arc
  const arc = new THREE.Mesh(new THREE.TorusGeometry(1.9, 0.22, 12, 20, Math.PI), M.metal)
  arc.position.set(0, 4.5, 0); arc.rotation.z = Math.PI; g.add(arc)
  // X-ray tube head
  g.add(box(1.1, 1.0, 0.55, M.stainless, 0, 2.5, 1.75))
  const tl = new THREE.Mesh(new THREE.CircleGeometry(0.28, 12), M.led(0xffee99))
  tl.rotation.x = Math.PI / 2; tl.position.set(0, 2.08, 1.75); g.add(tl)
  // image detector (flat panel)
  g.add(box(1.2, 0.12, 1.2, M.stainless, 0, 6.15, 0.5))
  g.add(box(0.95, 0.04, 0.95, M.dark, 0, 6.22, 0.5))
  return g
}

/* Hospital Bed — ICU-style articulated bed with proper rails */
function pHospitalBed(M) {
  const g = grp()
  const BW = 2.6, BL = 6.8, hBL = BL / 2, hBW = BW / 2

  // tubular steel frame
  const fr = M.chrome
  for (const sx of [-hBW + 0.1, hBW - 0.1]) {
    g.add(box(0.1, 0.1, BL, fr, sx, 1.62, 0))
    ;[-hBL + 0.1, 0, hBL - 0.1].forEach(sz => g.add(box(0.1, 1.62, 0.1, fr, sx, 0.81, sz)))
  }
  ;[-hBL + 0.15, -1.0, 1.0, hBL - 0.15].forEach(sz => g.add(box(BW, 0.1, 0.1, fr, 0, 1.62, sz)))

  // deck / mattress base
  g.add(box(BW - 0.25, 0.26, BL - 0.12, M.metal, 0, 1.62, 0))

  // articulated mattress segments (head / torso / leg sections)
  const mm = new THREE.MeshStandardMaterial({ color: 0xeef1f6, roughness: 0.88 })
  g.add(box(BW - 0.38, 0.52, BL * 0.24, mm, 0, 1.98, -BL * 0.38))   // head
  g.add(box(BW - 0.38, 0.49, BL * 0.44, mm, 0, 1.96, BL * 0.06))    // torso
  g.add(box(BW - 0.38, 0.42, BL * 0.26, mm, 0, 1.89, BL * 0.36))    // leg

  // pillow
  g.add(box(BW - 0.58, 0.33, 1.45, M.cushion, 0, 2.13, -hBL + 0.96))

  // headboard (blue-grey medical)
  const hbm = new THREE.MeshStandardMaterial({ color: 0x3b5c8a, roughness: 0.38, metalness: 0.3 })
  g.add(box(BW, 1.9, 0.17, hbm, 0, 2.55, -hBL - 0.09))
  ;[-0.9, -0.3, 0.3, 0.9].forEach(sx => g.add(box(0.06, 1.5, 0.06, fr, sx, 2.42, -hBL - 0.09)))

  // footboard
  g.add(box(BW, 1.0, 0.14, hbm, 0, 2.1, hBL + 0.07))

  // fold-down side rails (shown raised)
  for (const sx of [-hBW - 0.04, hBW + 0.04]) {
    g.add(box(0.055, 0.46, BL * 0.74, fr, sx, 2.32, 0))
    g.add(box(0.055, 0.055, BL * 0.74, fr, sx, 2.07, 0))
    g.add(box(0.055, 0.055, BL * 0.72, fr, sx, 2.54, 0))
    for (let v = 0; v < 5; v++) g.add(box(0.04, 0.46, 0.04, fr, sx, 2.32, -BL * 0.29 + v * BL * 0.145))
  }

  // IV pole socket (right, head end)
  g.add(cyl(0.075, 0.075, 5.8, M.chrome, hBW - 0.18, 3.95, -hBL + 0.75))
  g.add(box(0.055, 0.55, 0.055, M.chrome, hBW - 0.18, 6.65, -hBL + 0.68))

  // call-button rail panel
  g.add(box(0.52, 0.38, 0.24, M.plastic, hBW + 0.14, 2.42, -BL * 0.1))
  const call = new THREE.Mesh(new THREE.SphereGeometry(0.08, 8, 8), M.led(0xe11d48))
  call.position.set(hBW + 0.14, 2.42, -BL * 0.1 - 0.14); g.add(call)

  // castors (4-wheel braking castors)
  ;[[-hBW + 0.2, -hBL + 0.2], [hBW - 0.2, -hBL + 0.2], [-hBW + 0.2, hBL - 0.2], [hBW - 0.2, hBL - 0.2]].forEach(([wx, wz]) => {
    g.add(box(0.24, 0.33, 0.24, M.dark, wx, 0.41, wz))
    const w = torus(0.33, 0.11, M.rubber, wx, 0.33, wz); w.rotation.y = Math.PI / 2; g.add(w)
  })

  // foot pedals
  ;[-0.5, 0.5].forEach(sx => g.add(box(0.34, 0.06, 0.5, M.steel, sx, 0.1, hBL - 0.1)))

  // under-bed drawer
  g.add(box(BW * 0.6, 0.45, BL * 0.28, M.metal, 0, 0.6, hBL * 0.35))
  g.add(box(0.55, 0.09, 0.06, M.chrome, 0, 0.6, hBL * 0.35 + 0.16))

  return g
}

/* Patient monitor — pole-mount vitals display with ECG / SpO₂ waveforms */
function pPatientMonitor(M) {
  const g = grp()

  // 5-spoke star base
  g.add(cyl(0.55, 0.55, 0.09, M.dark, 0, 0.045, 0, 5))
  for (let i = 0; i < 5; i++) {
    const a = (i / 5) * Math.PI * 2
    g.add(box(0.65, 0.06, 0.08, M.dark, Math.cos(a) * 0.32, 0.09, Math.sin(a) * 0.32))
    const w = torus(0.12, 0.04, M.rubber, Math.cos(a) * 0.62, 0.12, Math.sin(a) * 0.62, 6, 12)
    w.rotation.y = Math.PI / 2; g.add(w)
  }
  g.add(cyl(0.07, 0.07, 3.9, M.chrome, 0, 1.95, 0))

  // monitor housing
  const mY = 4.1
  g.add(box(2.3, 1.7, 0.6, M.dark, 0, mY, 0))
  // main screen
  g.add(box(2.05, 1.42, 0.04, M.screen, 0, mY, 0.32))

  // ECG waveform (bright green)
  const ecgM = new THREE.MeshStandardMaterial({ color: 0x00ff88, emissive: 0x00ff88, emissiveIntensity: 2.8 })
  const ecgLine = new THREE.Mesh(new THREE.BoxGeometry(1.55, 0.04, 0.02), ecgM)
  ecgLine.position.set(0, mY + 0.28, 0.34); g.add(ecgLine)

  // SpO₂ waveform (cyan)
  const spo2M = new THREE.MeshStandardMaterial({ color: 0x00ccff, emissive: 0x00ccff, emissiveIntensity: 2.2 })
  const spo2 = new THREE.Mesh(new THREE.BoxGeometry(1.55, 0.04, 0.02), spo2M)
  spo2.position.set(0, mY + 0.02, 0.34); g.add(spo2)

  // numeric parameter panels (HR / SpO2 / NIBP / Temp)
  const paramC = [0x00ff88, 0x00ccff, 0xf59e0b, 0xffffff]
  paramC.forEach((c, i) => {
    const p = new THREE.Mesh(new THREE.BoxGeometry(0.5, 0.28, 0.02), M.led(c))
    p.position.set(0.68, mY + 0.46 - i * 0.33, 0.33); g.add(p)
  })

  // alarm strip
  ;[[-0.72, 0x16a34a], [0, 0xf59e0b], [0.72, 0xe11d48]].forEach(([lx, c]) => {
    const al = new THREE.Mesh(new THREE.BoxGeometry(0.4, 0.1, 0.04), M.led(c))
    al.position.set(lx, mY + 0.85, 0.32); g.add(al)
  })

  // connector ports (bottom)
  for (let i = 0; i < 3; i++) g.add(cyl(0.07, 0.07, 0.24, M.dark, -0.45 + i * 0.45, mY - 0.9, 0.35))

  // tilt arm
  g.add(box(0.14, 0.48, 0.38, M.dark, 0, mY - 1.0, -0.08))
  g.add(box(0.14, 0.36, 0.14, M.dark, 0, mY - 1.4, -0.16))

  return g
}

/* IV Stand — with bag, drip chamber, and tube */
function pIVStand(M) {
  const g = grp()

  // 5-spoke base with castors
  for (let i = 0; i < 5; i++) {
    const a = (i / 5) * Math.PI * 2
    g.add(box(0.65, 0.055, 0.08, M.chrome, Math.cos(a) * 0.32, 0.055, Math.sin(a) * 0.32))
    const w = torus(0.11, 0.04, M.rubber, Math.cos(a) * 0.62, 0.11, Math.sin(a) * 0.62, 6, 12)
    w.rotation.y = Math.PI / 2; g.add(w)
  }

  // telescoping pole
  g.add(cyl(0.05, 0.05, 5.4, M.chrome, 0, 2.8, 0))
  g.add(cyl(0.09, 0.09, 0.18, M.dark, 0, 4.42, 0))  // height collar

  // hook arm
  g.add(box(0.045, 0.045, 0.65, M.chrome, 0, 5.55, 0))
  ;[-0.32, -0.1, 0.1, 0.32].forEach(hx => {
    const hook = cyl(0.035, 0.035, 0.5, M.chrome, hx, 5.72, 0); hook.rotation.x = -0.75; g.add(hook)
  })

  // primary IV bag (saline — translucent)
  g.add(box(0.62, 1.05, 0.14, M.ivFluid, -0.14, 4.88, 0))
  // bag ports
  g.add(cyl(0.04, 0.04, 0.32, M.plastic, -0.14, 4.27, 0))

  // drip chamber
  const dcMat = new THREE.MeshStandardMaterial({ color: 0xc6e3f5, transparent: true, opacity: 0.58, roughness: 0.05 })
  g.add(cyl(0.08, 0.06, 0.6, dcMat, -0.14, 3.87, 0))

  // tubing (CatmullRom spline)
  const pts = [
    new THREE.Vector3(-0.14, 3.57, 0),
    new THREE.Vector3(-0.28, 2.6, 0.3),
    new THREE.Vector3(-0.2, 1.6, 0.52),
    new THREE.Vector3(0, 0.55, 0.44),
  ]
  const tubeGeo = new THREE.TubeGeometry(new THREE.CatmullRomCurve3(pts), 16, 0.026, 6, false)
  g.add(new THREE.Mesh(tubeGeo, M.ivTube))

  return g
}

/* Gas panel (wall-mounted medical gas outlets) */
function pGas(M) {
  const g = grp(); g.add(box(2.4, 3.2, 1, M.white, 0, 1.6, 0))
  for (let i = 0; i < 3; i++) g.add(cyl(0.35, 0.35, 2.2, M.led([0x16a34a, 0x2563eb, 0xf59e0b][i]), -0.7 + i * 0.7, 1.4, 0.5)); return g
}

function pLAF(M) {
  const g = grp(); g.add(box(4, 0.5, 4, M.white, 0, 0, 0)); g.add(box(3.6, 0.1, 3.6, M.cool, 0, -0.25, 0)); return g
}

/* Nurse station */
function pNurse(M) {
  const g = grp(); g.add(box(1.4, 3, 0.6, M.white, 0, 1.5, 0)); g.add(box(1, 1.1, 0.05, M.screen, 0, 2, 0.32))
  const l = new THREE.Mesh(new THREE.BoxGeometry(0.4, 0.2, 0.1), M.led(0xe11d48)); l.position.set(0, 2.8, 0.3); g.add(l); return g
}

function pFridge(M) {
  const g = grp(); g.add(box(2, 3.6, 2, M.white, 0, 1.8, 0)); g.add(box(1.4, 3, 0.06, M.glass, 0, 1.9, 1.01))
  const l = new THREE.Mesh(new THREE.BoxGeometry(0.8, 0.2, 0.05), M.led(0x2563eb)); l.position.set(0, 3.3, 1.01); g.add(l); return g
}

/* Blood-bank / cold-storage cabinet — taller, twin glass-door refrigerator */
function pBloodBank(M) {
  const g = grp()
  const W = 4.6, H = 6.6, D = 2.6
  g.add(box(W, H, D, M.stainless, 0, H / 2, 0))            // body
  // two glass doors with handles
  for (const sx of [-W / 4, W / 4]) {
    g.add(box(W / 2 - 0.14, H - 0.7, 0.06, M.glass, sx, H / 2 + 0.1, D / 2 + 0.02))
    g.add(cyl(0.06, 0.06, 1.6, M.chrome, sx + (sx < 0 ? 0.7 : -0.7), H / 2 + 0.1, D / 2 + 0.08))
    // stocked shelves faintly visible through the glass
    for (let s = 0; s < 4; s++) g.add(box(W / 2 - 0.4, 0.08, 0.4, M.medBlue, sx, 1.4 + s * 1.15, D / 2 - 0.35))
  }
  // digital temperature readout + alarm LED
  g.add(box(1.1, 0.6, 0.05, M.screen, 0, H - 0.5, D / 2 + 0.03))
  const led = new THREE.Mesh(new THREE.BoxGeometry(0.5, 0.16, 0.05), M.led(0x2563eb))
  led.position.set(0, H - 0.06, D / 2 + 0.03); led.userData.blink = true; g.add(led)
  g.add(box(W + 0.1, 0.2, D + 0.1, M.dark, 0, 0.1, 0))    // plinth
  return g
}

/* Autoclave / CSSD steam steriliser — stainless chamber with circular door */
function pAutoclave(M) {
  const g = grp()
  const W = 3.4, H = 5.6, D = 4.0
  g.add(box(W, H, D, M.stainless, 0, H / 2, 0))            // cabinet
  // recessed chamber face + round pressure door
  g.add(box(W - 0.3, H - 1.2, 0.12, M.medWhite, 0, H / 2 + 0.3, D / 2 + 0.02))
  const door = cyl(1.25, 1.25, 0.45, M.steel, 0, H / 2 + 0.3, D / 2 + 0.05); door.rotation.x = Math.PI / 2; g.add(door)
  g.add(torus(1.25, 0.12, M.chrome, 0, H / 2 + 0.3, D / 2 + 0.18, 10, 28))   // door rim
  const spokeV = cyl(0.09, 0.09, 1.9, M.chrome, 0, H / 2 + 0.3, D / 2 + 0.28)   // spoke handle (cross)
  const spokeH = cyl(0.09, 0.09, 1.9, M.chrome, 0, H / 2 + 0.3, D / 2 + 0.28); spokeH.rotation.z = Math.PI / 2
  g.add(spokeV, spokeH)
  // temp/pressure gauge cluster + control screen
  for (let i = 0; i < 2; i++) g.add(torus(0.28, 0.05, M.chrome, -1.0 + i * 0.75, H - 0.7, D / 2 + 0.05, 8, 20))
  g.add(box(1.0, 0.8, 0.05, M.screen, 0.7, H - 0.7, D / 2 + 0.04))
  const led = new THREE.Mesh(new THREE.BoxGeometry(0.4, 0.14, 0.05), M.led(0x16a34a))
  led.position.set(1.3, H - 0.15, D / 2 + 0.04); led.userData.blink = true; g.add(led)
  g.add(box(W + 0.1, 0.2, D + 0.1, M.dark, 0, 0.1, 0))    // plinth
  return g
}

/* Bulk medical-gas cylinder bank — manifold room centrepiece (O2 / N2O) */
function pGasCylinderBank(M) {
  const g = grp()
  const shoulderO2 = new THREE.MeshStandardMaterial({ color: 0x2f7d4f, roughness: 0.4, metalness: 0.7 })
  const shoulderN2O = new THREE.MeshStandardMaterial({ color: 0x2563eb, roughness: 0.4, metalness: 0.7 })
  const bodyMat = new THREE.MeshStandardMaterial({ color: 0xd8dbe1, roughness: 0.5, metalness: 0.55 })
  // support frame
  g.add(box(6.6, 0.25, 1.6, M.steel, 0, 0.12, 0))
  g.add(box(6.6, 3.4, 0.15, M.steel, 0, 1.9, -0.7))       // back rail
  // two banks of 3 cylinders (left = O2, right = N2O)
  const positions = [-2.6, -1.9, -1.2, 1.2, 1.9, 2.6]
  positions.forEach((x, i) => {
    const shoulder = i < 3 ? shoulderO2 : shoulderN2O
    g.add(cyl(0.32, 0.32, 4.0, bodyMat, x, 2.25, 0))       // cylinder body
    g.add(cyl(0.24, 0.32, 0.5, shoulder, x, 4.4, 0))        // coloured shoulder
    g.add(cyl(0.1, 0.1, 0.35, M.chrome, x, 4.75, 0))        // valve
    // pigtail to the header
    g.add(cyl(0.04, 0.04, 0.7, M.chrome, x, 4.6, -0.35))
  })
  // manifold header pipes (horizontal) + changeover control box
  for (const hx of [-1.9, 1.9]) {
    const header = cyl(0.12, 0.12, 3.2, M.chrome, hx, 4.9, -0.35); header.rotation.z = Math.PI / 2; g.add(header)
  }
  g.add(box(1.2, 1.8, 0.6, M.white, 0, 2.6, -0.75))        // control panel
  g.add(box(0.9, 0.7, 0.05, M.screen, 0, 3.1, -0.42))      // pressure display
  for (let i = 0; i < 2; i++) {
    const led = new THREE.Mesh(new THREE.BoxGeometry(0.22, 0.14, 0.05),
      M.led(i === 0 ? 0x16a34a : 0x2563eb))
    led.position.set(-0.25 + i * 0.5, 2.3, -0.42); led.userData.blink = i === 0; g.add(led)
  }
  return g
}

/* Operating table — more detailed */
function pOperatingTable(M) {
  const g = grp()
  // table segments (3-section break)
  g.add(box(2.2, 0.38, 2.4, M.stainless, 0, 3.1, -0.8))   // head
  g.add(box(2.2, 0.38, 3.4, M.stainless, 0, 3.1, 0.9))    // body
  // pad sections
  g.add(box(1.95, 0.22, 2.3, M.mattress, 0, 3.32, -0.8))
  g.add(box(1.95, 0.22, 3.3, M.mattress, 0, 3.32, 0.9))
  // support column
  g.add(cyl(0.55, 0.78, 3.1, M.chrome, 0, 1.55, 0))
  // base
  g.add(box(2.2, 0.28, 2.2, M.stainless, 0, 0.14, 0))
  ;[[-0.8, -0.8], [0.8, -0.8], [-0.8, 0.8], [0.8, 0.8]].forEach(([cx, cz]) =>
    g.add(box(0.22, 0.12, 0.22, M.rubber, cx, 0, cz)))
  // arm boards (folded)
  for (const sx of [-1.25, 1.25]) g.add(box(0.1, 0.18, 2.0, M.medWhite, sx, 3.2, 0.9))
  // anesthesia screen rail
  g.add(cyl(0.05, 0.05, 1.5, M.chrome, 0, 4.0, -1.95))
  return g
}

/* Crash cart (code cart with defibrillator) — new */
function pCrashCart(M) {
  const g = grp()
  g.add(box(2.2, 3.6, 1.8, M.medBlue, 0, 1.8, 0))

  // colour-coded drawers
  const drawerDef = [
    { y: 0.32, h: 0.55, c: 0x2563eb }, { y: 0.9, h: 0.55, c: 0x2563eb },
    { y: 1.48, h: 0.88, c: 0xe11d48 }, { y: 2.4, h: 0.55, c: 0xf59e0b },
    { y: 2.98, h: 0.55, c: 0xf59e0b },
  ]
  drawerDef.forEach(({ y, h, c }) => {
    const dm = new THREE.MeshStandardMaterial({ color: c, roughness: 0.28, metalness: 0.45 })
    g.add(box(2.0, h - 0.06, 0.08, dm, 0, y + h / 2, 0.94))
    g.add(box(1.15, 0.1, 0.06, M.chrome, 0, y + h / 2, 1.0))
    const lk = new THREE.Mesh(new THREE.BoxGeometry(0.12, 0.12, 0.05), M.led(c === 0xe11d48 ? 0xe11d48 : 0x16a34a))
    lk.position.set(0.8, y + h / 2, 0.99); g.add(lk)
  })

  // stainless top surface
  g.add(box(2.3, 0.1, 1.9, M.stainless, 0, 3.68, 0))

  // defibrillator on top
  g.add(box(1.7, 0.85, 1.05, M.dark, 0, 4.15, -0.38))
  g.add(box(0.95, 0.52, 0.05, M.led(0x16a34a), 0, 4.18, 0.12))
  for (const sx of [-0.52, 0.52]) g.add(cyl(0.08, 0.08, 0.62, M.dark, sx, 4.2, 0.52))
  const dl = new THREE.Mesh(new THREE.BoxGeometry(0.32, 0.12, 0.05), M.led(0xe11d48))
  dl.position.set(0, 4.55, 0.12); dl.userData.blink = true; g.add(dl)

  // O₂ cylinder on side (green shoulder)
  const cylM = new THREE.MeshStandardMaterial({ color: 0x3a7a3a, roughness: 0.4, metalness: 0.7 })
  g.add(cyl(0.28, 0.28, 2.5, cylM, 1.25, 1.85, 0))
  const capGeo = new THREE.SphereGeometry(0.28, 12, 8, 0, Math.PI * 2, 0, Math.PI / 2)
  const cap = new THREE.Mesh(capGeo, cylM); cap.position.set(1.25, 3.1, 0); g.add(cap)
  g.add(box(0.22, 2.6, 0.22, M.dark, 1.25, 2.55, 0))  // retention strap

  // push handle
  g.add(box(2.2, 0.1, 0.1, M.dark, 0, 4.82, 0.85))
  ;[-0.9, 0.9].forEach(sx => g.add(box(0.1, 1.22, 0.1, M.dark, sx, 4.22, 0.85)))

  // castors
  ;[[-0.8, -0.68], [0.8, -0.68], [-0.8, 0.68], [0.8, 0.68]].forEach(([cx, cz]) => {
    const w = torus(0.28, 0.09, M.rubber, cx, 0.28, cz); w.rotation.y = Math.PI / 2; g.add(w)
  })
  return g
}

/* Medication dispensing cart (Pyxis / Omnicell style) — new */
function pMedCart(M) {
  const g = grp()
  g.add(box(2.1, 4.9, 1.65, M.medWhite, 0, 2.45, 0))

  // large touch-screen
  g.add(box(1.5, 1.25, 0.05, M.screen, 0, 3.85, 0.84))

  // card-reader / scanner
  g.add(box(0.5, 0.38, 0.14, M.dark, -0.55, 4.55, 0.82))
  const sl = new THREE.Mesh(new THREE.BoxGeometry(0.3, 0.06, 0.04), M.led(0x22d3ee))
  sl.position.set(-0.55, 4.55, 0.88); sl.userData.blink = true; g.add(sl)

  // colour-coded drawers
  ;[0.44, 1.14, 1.84, 2.54].forEach((y, i) => {
    const c = [0x3b82f6, 0x3b82f6, 0x8b5cf6, 0x8b5cf6][i]
    const dm = new THREE.MeshStandardMaterial({ color: c, roughness: 0.28, metalness: 0.45 })
    g.add(box(1.88, 0.62, 0.07, dm, 0, y + 0.31, 0.84))
    g.add(box(0.82, 0.12, 0.05, M.chrome, 0, y + 0.31, 0.9))
    const ll = new THREE.Mesh(new THREE.BoxGeometry(0.1, 0.1, 0.04), M.led(0x16a34a))
    ll.position.set(0.62, y + 0.31, 0.89); g.add(ll)
  })

  // stainless top
  g.add(box(2.15, 0.09, 1.7, M.stainless, 0, 4.94, 0))
  // status light
  const sb = new THREE.Mesh(new THREE.BoxGeometry(0.65, 0.1, 0.09), M.led(0x16a34a))
  sb.position.set(0, 5.05, 0.82); g.add(sb)

  // castors
  ;[[-0.7, -0.6], [0.7, -0.6], [-0.7, 0.6], [0.7, 0.6]].forEach(([cx, cz]) => {
    const w = torus(0.24, 0.08, M.rubber, cx, 0.24, cz); w.rotation.y = Math.PI / 2; g.add(w)
  })
  return g
}

/* OR Surgical Light — ceiling-mount LED cluster */
function pSurgicalLight(M) {
  const g = grp()
  // ceiling stalk
  g.add(cyl(0.14, 0.14, 0.55, M.stainless, 0, 7.27, 0))
  // horizontal arm
  g.add(box(0.09, 0.09, 3.0, M.stainless, 0, 7.0, 0.85))
  // vertical drop
  g.add(box(0.09, 2.55, 0.09, M.stainless, 0, 5.72, 2.35))
  // tilt link
  g.add(box(0.09, 0.09, 1.1, M.stainless, 0, 4.45, 1.75))

  const hX = 0, hY = 4.45, hZ = 2.35
  // housing (shallow cone)
  g.add(cyl(1.55, 1.75, 0.38, M.stainless, hX, hY, hZ))

  // LED concentric rings
  const ledM = new THREE.MeshStandardMaterial({ color: 0xfff5e0, emissive: 0xfff5e0, emissiveIntensity: 3.8 })
  ;[[0.0, 1], [0.32, 5], [0.62, 10], [0.94, 15]].forEach(([r, cnt]) => {
    for (let i = 0; i < cnt; i++) {
      const a = (i / cnt) * Math.PI * 2
      const led = new THREE.Mesh(new THREE.CircleGeometry(r < 0.1 ? 0.12 : 0.07, 8), ledM)
      led.rotation.x = Math.PI / 2
      led.position.set(hX + Math.cos(a) * r, hY - 0.21, hZ + Math.sin(a) * r); g.add(led)
    }
  })

  // rim ring + reflector
  const rim = torus(1.65, 0.08, M.stainless, hX, hY - 0.04, hZ); rim.rotation.x = Math.PI / 2; g.add(rim)
  const reflM = new THREE.MeshStandardMaterial({ color: 0xe0e4ec, roughness: 0.07, metalness: 0.96, side: THREE.BackSide })
  const refl = new THREE.Mesh(new THREE.SphereGeometry(1.48, 24, 12, 0, Math.PI * 2, 0, Math.PI * 0.44), reflM)
  refl.position.set(hX, hY + 0.1, hZ); g.add(refl)

  // sterile handles
  ;[0, Math.PI].forEach(a => g.add(box(0.08, 0.9, 0.08, M.stainless, hX + Math.cos(a) * 1.12, hY + 0.05, hZ + Math.sin(a) * 1.12)))

  return g
}

/* Ventilator — ICU mechanical ventilator (new) */
function pVentilator(M) {
  const g = grp()
  g.add(box(2.0, 4.5, 1.8, M.medWhite, 0, 2.25, 0))

  // large touch-screen
  g.add(box(1.65, 1.85, 0.05, M.screen, 0, 3.5, 0.93))
  const glow = new THREE.Mesh(new THREE.BoxGeometry(1.75, 1.95, 0.03),
    new THREE.MeshStandardMaterial({ color: 0x1a3a5c, emissive: 0x1a3a5c, emissiveIntensity: 0.45 }))
  glow.position.set(0, 3.5, 0.92); g.add(glow)

  // waveform lines on screen (3 colour-coded traces)
  ;[[0x00ff88, 0.3], [0xffdd44, 0], [0x00ccff, -0.3]].forEach(([c, dy]) => {
    const wm = new THREE.MeshStandardMaterial({ color: c, emissive: c, emissiveIntensity: 2.6 })
    const wl = new THREE.Mesh(new THREE.BoxGeometry(1.4, 0.04, 0.02), wm)
    wl.position.set(0, 3.5 + dy, 0.95); g.add(wl)
  })

  // control knobs
  for (let i = 0; i < 4; i++) g.add(cyl(0.14, 0.14, 0.08, M.dark, -0.55 + i * 0.37, 2.1, 0.94))

  // breathing circuit ports
  ;[[-0.4, 1.45], [0, 1.45], [0.4, 1.45]].forEach(([px, py]) =>
    g.add(cyl(0.11, 0.11, 0.24, M.plastic, px, py, 0.95)))

  // bellows inspection window
  g.add(box(0.62, 0.82, 0.06, M.glass, 0.5, 1.5, 0.91))

  // alarm bar
  const al = new THREE.Mesh(new THREE.BoxGeometry(0.72, 0.17, 0.05), M.led(0x16a34a))
  al.position.set(0, 4.45, 0.93); g.add(al)

  // breathing tube (blue corrugated hose)
  const hoseM = new THREE.MeshStandardMaterial({ color: 0x4a90c8, roughness: 0.4 })
  const hosePts = [
    new THREE.Vector3(-0.4, 1.45, 0.96), new THREE.Vector3(-0.6, 1.2, 1.5),
    new THREE.Vector3(-0.4, 0.8, 2.0),
  ]
  const hoseGeo = new THREE.TubeGeometry(new THREE.CatmullRomCurve3(hosePts), 14, 0.09, 8, false)
  g.add(new THREE.Mesh(hoseGeo, hoseM))

  // castors
  ;[[-0.7, -0.6], [0.7, -0.6], [-0.7, 0.6], [0.7, 0.6]].forEach(([cx, cz]) => {
    const w = torus(0.27, 0.09, M.rubber, cx, 0.27, cz); w.rotation.y = Math.PI / 2; g.add(w)
  })
  return g
}

/* Simple bed (non-ICU) */
function pBed(M) {
  const g = grp(); g.add(box(3, 0.5, 6, M.wood, 0, 1.2, 0)); g.add(box(2.8, 0.5, 5.6, M.mattress, 0, 1.6, 0.1))
  g.add(box(1.2, 0.4, 0.7, M.cushion, 0, 2, -2.2)); g.add(box(3, 1.8, 0.3, M.woodDark, 0, 1.8, -3)); return g
}
function pStretcher(M) {
  const g = grp(); g.add(box(2, 0.25, 5.5, M.metal, 0, 2, 0)); g.add(box(1.9, 0.35, 5.2, M.mattress, 0, 2.3, 0))
  for (const [wx, wz] of [[-0.8, -2.2], [0.8, -2.2], [-0.8, 2.2], [0.8, 2.2]]) {
    const w = torus(0.45, 0.14, M.dark, wx, 0.5, wz); w.rotation.y = Math.PI / 2; g.add(w)
  }
  g.add(cyl(0.08, 0.08, 2.5, M.chrome, 0.8, 3.5, -2.4)); return g
}
function pWheelchair(M) {
  const g = grp(); g.add(box(1.6, 0.16, 1.6, M.fabric, 0, 1.6, 0)); g.add(box(1.6, 1.7, 0.16, M.fabric, 0, 2.4, -0.7))
  for (const sx of [-1, 1]) { const w = torus(1.1, 0.12, M.dark, sx, 1.1, -0.2); w.rotation.y = Math.PI / 2; g.add(w) }
  for (const sx of [-0.7, 0.7]) { const c = torus(0.4, 0.1, M.dark, sx, 0.4, 1.1); c.rotation.y = Math.PI / 2; g.add(c) } return g
}

/* ═══════════════════════════════════════════════════════════════════════════
   FACTORY / LOGISTICS
═══════════════════════════════════════════════════════════════════════════ */
function pPress(M) {
  const g = grp(); g.add(box(3.4, 5.5, 3, M.steel, 0, 2.75, 0)); g.add(box(2.6, 0.6, 2.4, M.dark, 0, 4.6, 0))
  const ram = box(2.2, 1.4, 2, M.metal, 0, 2.6, 0); ram.userData.press = true; g.add(ram); g.add(box(3.8, 0.8, 3.4, M.dark, 0, 0.4, 0)); return g
}
function pRobot(M) {
  const g = grp(); g.add(cyl(1, 1.3, 0.6, M.dark, 0, 0.3, 0)); g.add(cyl(0.8, 0.8, 1.2, M.amber, 0, 1.1, 0))
  const arm1 = grp(); arm1.add(box(0.7, 3, 0.7, M.amber, 0, 1.5, 0)); arm1.position.set(0, 1.7, 0)
  const arm2 = grp(); arm2.add(box(0.55, 2.4, 0.55, M.amber, 0, 1.2, 0)); arm2.position.set(0, 3, 0)
  arm2.add(box(0.4, 0.8, 0.4, M.dark, 0, 2.4, 0)); arm1.add(arm2); g.add(arm1)
  g.userData.robot = { arm1, arm2 }; return g
}
function pConveyor(M) {
  const g = grp(); const belt = box(8, 0.4, 2, M.rubber, 0, 1.4, 0); belt.userData.belt = true; g.add(belt)
  for (let i = -3; i <= 3; i++) g.add(box(0.3, 1.4, 2.2, M.steel, i * 1.2, 0.7, 0))
  for (let i = 0; i < 5; i++) { const bx = box(0.7, 0.7, 0.7, M.amber, -3 + i * 1.5, 1.9, 0); bx.userData.cargo = true; g.add(bx) }
  g.add(box(8.2, 0.2, 2.4, M.dark, 0, 1.62, 0)); return g
}
function pCNC(M) {
  const g = grp(); g.add(box(4, 4, 3, M.white, 0, 2, 0)); g.add(box(2.2, 2, 0.06, M.glass, 0, 2.2, 1.51))
  g.add(box(1, 2.6, 0.5, M.dark, 2.2, 1.6, 0)); g.add(box(0.06, 1, 0.8, M.screen, 2.46, 2.4, 0))
  const l = new THREE.Mesh(new THREE.BoxGeometry(0.6, 0.2, 0.1), M.led(0xe11d48)); l.position.set(0, 4.2, 1); g.add(l); return g
}
function pWelder(M) {
  const g = grp(); g.add(box(2, 3, 1.6, M.dark, 0, 1.5, 0))
  const arm = cyl(0.15, 0.15, 2.4, M.steel, 0, 2.4, 1); arm.rotation.x = 0.5; g.add(arm)
  const tip = new THREE.Mesh(new THREE.SphereGeometry(0.25, 12, 12), M.led(0x66ccff)); tip.position.set(0, 1.4, 2); tip.userData.blink = true; g.add(tip); return g
}
function pCompressor(M) {
  const g = grp(); g.add(box(3, 2.4, 2, M.amber, 0, 1.2, 0))
  const tank = cyl(0.8, 0.8, 3.4, M.metal); tank.rotation.z = Math.PI / 2; tank.position.set(0, 1.6, -0.4); g.add(tank)
  const motor = cyl(0.6, 0.6, 1.4, M.steel, 0, 2.4, 0.6); motor.rotation.z = Math.PI / 2; g.add(motor); return g
}
function pSensor(M) {
  const g = grp(); g.add(cyl(0.4, 0.5, 0.8, M.white, 0, 0.4, 0))
  const l = new THREE.Mesh(new THREE.SphereGeometry(0.18, 12, 12), M.led(0x22d3ee)); l.position.set(0, 0.9, 0); l.userData.blink = true; g.add(l); return g
}
function pCamera(M) {
  const g = grp(); g.add(cyl(0.25, 0.25, 1.6, M.dark, 0, 0.8, 0)); g.add(box(1, 0.7, 0.7, M.white, 0, 1.6, 0.2))
  const lens = cyl(0.25, 0.25, 0.3, M.dark, 0, 1.6, 0.7); lens.rotation.x = Math.PI / 2; g.add(lens); return g
}
function pBox(M) { return grp(box(2, 2.4, 2, M.metal, 0, 1.2, 0), box(1.4, 0.6, 0.06, M.screen, 0, 1.8, 1.01)) }
function pForklift(M) {
  const g = grp(); g.add(box(2.4, 1.6, 4, M.amber, 0, 1.2, -0.5)); g.add(box(2.2, 2, 1.8, M.dark, 0, 3, -1.2))
  g.add(box(2, 0.1, 1.8, M.glass, 0, 4, -1.2)); for (const sx of [-1.1, 1.1]) g.add(cyl(0.15, 0.15, 4, M.steel, sx, 3, 1.2))
  g.add(box(2, 0.15, 0.15, M.steel, 0, 1, 1.2)); for (const sx of [-0.6, 0.6]) g.add(box(0.2, 0.12, 2, M.steel, sx, 0.4, 2.4))
  for (const [wx, wz] of [[-1.1, -1.8], [1.1, -1.8], [-1.1, 1], [1.1, 1]]) { const w = cyl(0.6, 0.6, 0.4, M.rubber, wx, 0.6, wz); w.rotation.z = Math.PI / 2; g.add(w) } return g
}
function pPalletRack(M) {
  const g = grp(); for (const sx of [-3, 3]) for (const sz of [-1, 1]) g.add(box(0.25, 8, 0.25, M.amber, sx, 4, sz))
  for (let lvl = 0; lvl < 3; lvl++) {
    g.add(box(6.2, 0.2, 0.25, M.amber, 0, 2.5 + lvl * 2.5, -1)); g.add(box(6.2, 0.2, 0.25, M.amber, 0, 2.5 + lvl * 2.5, 1))
    for (let p = -1; p <= 1; p++) g.add(box(1.6, 1.4, 1.8, M.wood, p * 2, 3.3 + lvl * 2.5, 0))
  }; return g
}
function pWorkbench(M) {
  const g = grp(); g.add(box(5, 0.3, 2.4, M.steel, 0, 2.4, 0))
  const legs4 = (W, D, H, mat) => [[-W / 2 + 0.2, H / 2, -D / 2 + 0.2], [W / 2 - 0.2, H / 2, -D / 2 + 0.2], [-W / 2 + 0.2, H / 2, D / 2 - 0.2], [W / 2 - 0.2, H / 2, D / 2 - 0.2]].map(([x, y, z]) => box(0.15, H, 0.15, mat, x, y, z))
  legs4(5, 2.4, 2.4, M.dark).forEach(l => g.add(l))
  g.add(box(5, 2, 0.1, M.dark, 0, 3.6, -1.1))
  for (let i = 0; i < 4; i++) g.add(box(0.1, 0.6, 0.05, M.steel, -1.8 + i * 1.2, 3.6, -1)); return g
}
function pStorageTank(M) {
  const g = grp(); g.add(cyl(1.8, 1.8, 6, M.metal, 0, 3.5, 0))
  const dome = new THREE.Mesh(new THREE.SphereGeometry(1.8, 18, 10, 0, Math.PI * 2, 0, Math.PI / 2), M.metal); dome.position.y = 6.5; g.add(dome)
  for (const a of [0, 1, 2]) { const ang = a * 2.1; g.add(box(0.2, 1, 0.2, M.steel, Math.cos(ang) * 1.7, 0.5, Math.sin(ang) * 1.7)) }
  g.add(cyl(0.25, 0.25, 1.5, M.steel, 1.8, 1, 0)); return g
}

/* ═══════════════════════════════════════════════════════════════════════════
   BUILDING SERVICES
═══════════════════════════════════════════════════════════════════════════ */
function pPDU(M) {
  const g = grp(); g.add(box(1, 5.5, 1, M.dark, 0, 2.75, 0))
  for (let i = 0; i < 12; i++) {
    const l = new THREE.Mesh(new THREE.BoxGeometry(0.5, 0.08, 0.04), M.led(i % 3 ? 0x22d3ee : 0x16a34a))
    l.position.set(0, 0.8 + i * 0.38, 0.51); l.userData.blink = true; g.add(l)
  }; return g
}
function pTransformer(M) {
  const g = grp(); g.add(box(3.4, 3, 2.6, M.steel, 0, 1.7, 0))
  for (let i = 0; i < 8; i++) g.add(box(0.1, 2.6, 2.6, M.dark, -1.6 + i * 0.45, 1.6, 0))
  for (const sx of [-0.9, 0, 0.9]) { g.add(cyl(0.22, 0.3, 1.2, M.glass, sx, 3.8, 0)); g.add(cyl(0.12, 0.12, 0.4, M.metal, sx, 4.5, 0)) } return g
}
function pSwitchgear(M) {
  const g = grp()
  for (let i = 0; i < 3; i++) {
    g.add(box(1.8, 4, 2, M.metal, -1.9 + i * 1.9, 2, 0)); g.add(box(1.4, 1, 0.05, M.screen, -1.9 + i * 1.9, 3, 1.01))
    const l = new THREE.Mesh(new THREE.BoxGeometry(0.5, 0.1, 0.05), M.led(0x16a34a)); l.position.set(-1.9 + i * 1.9, 2.2, 1.01); l.userData.blink = true; g.add(l)
  }; return g
}
function pGenerator(M) {
  const g = grp(); g.add(box(7, 0.5, 3, M.dark, 0, 0.25, 0)); g.add(box(4, 2.4, 2.6, M.amber, -0.5, 1.7, 0))
  g.add(box(2.4, 2.6, 2.4, M.steel, 2.4, 1.8, 0)); for (let i = 0; i < 5; i++) g.add(box(0.06, 2.2, 2.2, M.dark, 1.4 + i * 0.25, 1.8, 0))
  g.add(cyl(0.35, 0.35, 3, M.dark, -2, 3.5, -1)); g.add(box(1.4, 1.6, 0.4, M.dark, 0, 1.6, 1.5)); g.add(box(1.1, 0.8, 0.05, M.screen, 0, 1.9, 1.71)); return g
}
function pBoiler(M) {
  const g = grp(); g.add(cyl(1.4, 1.4, 5, M.steel, 0, 3, 0))
  const top = new THREE.Mesh(new THREE.SphereGeometry(1.4, 16, 8, 0, Math.PI * 2, 0, Math.PI / 2), M.steel); top.position.y = 5.5; g.add(top)
  g.add(box(1.2, 1.4, 0.4, M.dark, 0, 1.5, 1.4)); g.add(box(0.9, 0.6, 0.05, M.led(0xf59e0b), 0, 1.7, 1.61)); g.add(cyl(0.2, 0.2, 2, M.steel, 1.4, 4, 0)); return g
}
function pChiller(M) {
  const g = grp(); g.add(box(6, 3, 3, M.metal, 0, 1.7, 0))
  for (let i = 0; i < 10; i++) g.add(box(0.05, 2.6, 2.8, M.dark, -2.7 + i * 0.55, 1.6, 0))
  for (const fx of [-1.5, 1.5]) {
    const fan = grp()
    for (let i = 0; i < 4; i++) { const b = box(1.3, 0.06, 0.3, M.steel); b.rotation.y = i * Math.PI / 2; fan.add(b) }
    fan.position.set(fx, 3.3, 0); fan.userData.spin = 1.1; g.add(fan)
    const ring = torus(1.4, 0.1, M.dark, fx, 3.3, 0); ring.rotation.x = Math.PI / 2; g.add(ring)
  }; return g
}
function pCoolingTower(M) {
  const g = grp(); g.add(box(4, 4, 4, M.white, 0, 2, 0))
  for (let i = 0; i < 8; i++) g.add(box(3.6, 0.12, 0.06, M.dark, 0, 0.8 + i * 0.4, 2.01))
  const fan = grp(); for (let i = 0; i < 5; i++) { const b = box(2.6, 0.08, 0.5, M.steel); b.rotation.y = i * (Math.PI * 2 / 5); fan.add(b) }
  fan.position.y = 4.3; fan.userData.spin = 1.4; g.add(fan)
  g.add(cyl(2, 2, 0.4, M.dark, 0, 4.1, 0, 24)); return g
}
function pPump(M) {
  const g = grp(); g.add(box(2.4, 0.4, 1.4, M.dark, 0, 0.2, 0))
  g.add(cyl(0.8, 0.8, 1.8, M.steel, -0.6, 1.1, 0, 18)); g.children[1].rotation.z = Math.PI / 2
  const motor = cyl(0.7, 0.7, 1.6, M.amber, 0.8, 1.1, 0); motor.rotation.z = Math.PI / 2; g.add(motor)
  g.add(cyl(0.25, 0.25, 1, M.steel, -0.6, 2, 0)); return g
}
function pWaterTank(M) {
  const g = grp(); g.add(cyl(2.2, 2.2, 5, M.white, 0, 3, 0, 24))
  const dome = new THREE.Mesh(new THREE.SphereGeometry(2.2, 20, 10, 0, Math.PI * 2, 0, Math.PI / 2), M.white); dome.position.y = 5.5; g.add(dome)
  for (let i = 0; i < 6; i++) g.add(box(0.12, 0.08, 0.3, M.steel, 2.1, 0.6 + i * 0.7, 0)); g.add(cyl(0.3, 0.3, 1, M.steel, 0, 6.4, 0)); return g
}
function pValve(M) {
  const g = grp(); const pipe = cyl(0.4, 0.4, 3, M.steel, 0, 1, 0); pipe.rotation.z = Math.PI / 2; g.add(pipe)
  g.add(box(0.9, 0.9, 0.9, M.amber, 0, 1, 0)); const wheel = torus(0.6, 0.1, M.red, 0, 1.8, 0); g.add(wheel); g.add(cyl(0.08, 0.08, 0.8, M.steel, 0, 1.4, 0)); return g
}
function pSolarPanel(M) {
  const g = grp(); const panel = box(5, 0.15, 3, M.solar, 0, 2.4, 0); panel.rotation.x = -0.4; g.add(panel)
  for (let i = 0; i < 4; i++) { const l = box(5, 0.02, 0.05, M.dark, 0, 2.42, -1.2 + i * 0.8); l.rotation.x = -0.4; g.add(l) }
  g.add(box(0.15, 2.4, 0.15, M.steel, -2, 1.2, 0.8)); g.add(box(0.15, 1.4, 0.15, M.steel, 2, 0.7, -0.6)); return g
}
function pBattery(M) {
  const g = grp(); g.add(box(4, 4.5, 2, M.dark, 0, 2.25, 0))
  for (let r = 0; r < 4; r++) for (let c = 0; c < 3; c++) {
    g.add(box(1.1, 0.8, 0.1, M.steel, -1.2 + c * 1.2, 0.8 + r * 1, 1.01))
    const l = new THREE.Mesh(new THREE.BoxGeometry(0.2, 0.1, 0.05), M.led(0x16a34a)); l.position.set(-1.6 + c * 1.2, 0.8 + r * 1, 1.06); l.userData.blink = true; g.add(l)
  }; return g
}
function pEVCharger(M) {
  const g = grp(); g.add(box(1.2, 4, 0.8, M.white, 0, 2, 0)); g.add(box(1, 1.4, 0.06, M.screen, 0, 3, 0.41))
  const l = new THREE.Mesh(new THREE.BoxGeometry(0.8, 0.15, 0.05), M.led(0x16a34a)); l.position.set(0, 1.6, 0.41); l.userData.blink = true; g.add(l)
  g.add(box(0.5, 0.6, 0.4, M.dark, 0.7, 1.4, 0.3)); return g
}
function pFireExtinguisher(M) {
  const g = grp(); g.add(cyl(0.4, 0.4, 1.6, M.red, 0, 0.9, 0))
  const top = new THREE.Mesh(new THREE.SphereGeometry(0.4, 12, 8, 0, Math.PI * 2, 0, Math.PI / 2), M.red); top.position.y = 1.7; g.add(top)
  g.add(box(0.3, 0.25, 0.3, M.dark, 0, 1.95, 0)); g.add(cyl(0.06, 0.06, 1.2, M.dark, 0.35, 1, 0)); return g
}
function pFirePanel(M) {
  const g = grp(); g.add(box(2, 3, 0.6, M.red, 0, 2.5, 0)); g.add(box(1.6, 1, 0.05, M.screen, 0, 3.2, 0.31))
  for (let i = 0; i < 4; i++) {
    const l = new THREE.Mesh(new THREE.BoxGeometry(0.2, 0.15, 0.05), M.led(i ? 0x16a34a : 0xe11d48))
    l.position.set(-0.5 + i * 0.35, 2.3, 0.31); l.userData.blink = i === 0; g.add(l)
  }; return g
}
function pSmokeDetector(M) {
  const g = grp(); g.add(cyl(0.7, 0.6, 0.3, M.white, 0, 4.5, 0, 18))
  const l = new THREE.Mesh(new THREE.SphereGeometry(0.12, 10, 10), M.led(0x16a34a)); l.position.set(0, 4.32, 0.3); l.userData.blink = true; g.add(l); return g
}
function pSprinkler(M) {
  const g = grp(); g.add(cyl(0.12, 0.12, 0.6, M.chrome, 0, 4.5, 0)); g.add(cyl(0.35, 0.2, 0.25, M.chrome, 0, 4.15, 0, 12))
  const b = new THREE.Mesh(new THREE.SphereGeometry(0.14, 10, 10), M.red); b.position.y = 4; g.add(b); return g
}
function pExitSign(M) {
  const g = grp()
  g.add(box(2, 0.9, 0.2, new THREE.MeshStandardMaterial({ color: 0x16a34a, emissive: 0x16a34a, emissiveIntensity: 1.2 }), 0, 4.6, 0))
  g.add(box(1.6, 0.5, 0.06, M.white, 0, 4.6, 0.12)); return g
}
function pElevator(M) {
  const g = grp(); g.add(box(4.4, 6, 4.4, M.steel, 0, 3, 0)); g.add(box(2.6, 5, 0.1, M.dark, 0, 2.6, 2.2))
  g.add(box(0.08, 5, 0.1, M.chrome, 0, 2.6, 2.25)); g.add(box(1, 0.5, 0.05, M.led(0xf59e0b), 0, 5.4, 2.23)); return g
}
function pEscalator(M) {
  const g = grp(); const ramp = box(3, 0.6, 8, M.dark, 0, 2.5, 0); ramp.rotation.x = -0.5; g.add(ramp)
  for (let i = 0; i < 8; i++) { const s = box(3, 0.4, 0.9, M.steel, 0, 1 + i * 0.5, -3 + i * 0.85); g.add(s) }
  for (const sx of [-1.6, 1.6]) { const rail = box(0.2, 0.3, 8.5, M.glass, sx, 3.4, 0); rail.rotation.x = -0.5; g.add(rail) } return g
}

/* ═══════════════════════════════════════════════════════════════════════════
   OFFICE / FURNITURE / DECOR
═══════════════════════════════════════════════════════════════════════════ */
const legs4 = (M, w, d, h, mat) => [
  box(0.15, h, 0.15, mat, -w / 2 + 0.2, h / 2, -d / 2 + 0.2),
  box(0.15, h, 0.15, mat, w / 2 - 0.2, h / 2, -d / 2 + 0.2),
  box(0.15, h, 0.15, mat, -w / 2 + 0.2, h / 2, d / 2 - 0.2),
  box(0.15, h, 0.15, mat, w / 2 - 0.2, h / 2, d / 2 - 0.2),
]
function pMonitor(M, x = 0, y = 2.5, z = 0) {
  const g = grp(); g.add(box(1.4, 0.9, 0.08, M.dark, x, y + 0.85, z)); g.add(box(1.25, 0.75, 0.02, M.screen, x, y + 0.85, z + 0.05))
  g.add(box(0.12, 0.5, 0.12, M.dark, x, y + 0.35, z)); g.add(box(0.7, 0.05, 0.4, M.dark, x, y + 0.1, z)); return g
}
function pDesk(M) {
  const g = grp(); g.add(box(4, 0.18, 2.2, M.wood, 0, 2.4, 0)); legs4(M, 4, 2.2, 2.4, M.steel).forEach(l => g.add(l))
  g.add(pMonitor(M, 0, 2.5, -0.6)); g.add(box(1.4, 0.06, 0.5, M.plastic, 0, 2.52, 0.4)); return g
}
function pChair(M) {
  const g = grp(); g.add(box(1.4, 0.16, 1.4, M.fabric, 0, 1.4, 0)); g.add(box(1.4, 1.6, 0.16, M.fabric, 0, 2.2, -0.62))
  g.add(cyl(0.12, 0.12, 1.25, M.dark, 0, 0.75, 0)); g.add(cyl(0.95, 0.95, 0.12, M.dark, 0, 0.16, 0, 5)); return g
}
function pStool(M) { const g = grp(); g.add(cyl(0.7, 0.7, 0.18, M.wood, 0, 1.55, 0)); g.add(cyl(0.1, 0.1, 1.5, M.steel, 0, 0.78, 0)); g.add(cyl(0.6, 0.6, 0.08, M.steel, 0, 0.1, 0)); return g }
function pSofa(M) {
  const g = grp(); g.add(box(5, 0.8, 2.2, M.fabricBlue, 0, 0.9, 0)); g.add(box(5, 1.6, 0.5, M.fabricBlue, 0, 1.7, -0.85))
  g.add(box(0.5, 1.3, 2.2, M.fabricBlue, -2.25, 1.2, 0)); g.add(box(0.5, 1.3, 2.2, M.fabricBlue, 2.25, 1.2, 0))
  g.add(box(2.1, 0.4, 1.8, M.cushion, -1.15, 1.35, 0.1)); g.add(box(2.1, 0.4, 1.8, M.cushion, 1.15, 1.35, 0.1)); return g
}
function pTable(M) { const g = grp(); g.add(box(5, 0.2, 2.6, M.wood, 0, 2.3, 0)); legs4(M, 5, 2.6, 2.3, M.woodDark).forEach(l => g.add(l)); return g }
function pCoffeeTable(M) { const g = grp(); g.add(box(3, 0.16, 1.6, M.woodDark, 0, 1.2, 0)); legs4(M, 3, 1.6, 1.2, M.woodDark).forEach(l => g.add(l)); return g }
function pBookshelf(M) {
  const g = grp(); g.add(box(3, 5, 1, M.wood, 0, 2.5, 0))
  for (let i = 1; i < 5; i++) g.add(box(2.8, 0.08, 0.95, M.woodDark, 0, i, 0))
  const cols = [0x7c3aed, 0x2563eb, 0x16a34a, 0xf59e0b, 0xe11d48]
  for (let s = 0; s < 4; s++) for (let b = 0; b < 6; b++) g.add(box(0.18, 0.7, 0.6, new THREE.MeshStandardMaterial({ color: cols[(s + b) % cols.length], roughness: 0.8 }), -1.2 + b * 0.4, s + 0.5, 0.1)); return g
}
function pCabinet(M) {
  const g = grp(); g.add(box(2.4, 3, 1.4, M.woodDark, 0, 1.5, 0))
  for (let i = 0; i < 3; i++) { g.add(box(2.2, 0.05, 0.05, M.steel, 0, 0.7 + i * 0.9, 0.71)); g.add(box(0.4, 0.1, 0.08, M.chrome, 0, 0.95 + i * 0.9, 0.72)) }; return g
}
function pPlant(M) {
  const g = grp(); g.add(cyl(0.55, 0.42, 1, M.white, 0, 0.5, 0))
  const f1 = new THREE.Mesh(new THREE.SphereGeometry(1.05, 14, 12), M.foliage); f1.position.set(0, 1.7, 0); g.add(f1)
  const f2 = new THREE.Mesh(new THREE.SphereGeometry(0.7, 12, 10), M.foliage); f2.position.set(0.5, 2.3, 0.2); g.add(f2); return g
}
function pWaterCooler(M) { const g = grp(); g.add(box(1.2, 2.6, 1.2, M.white, 0, 1.3, 0)); g.add(cyl(0.55, 0.55, 1, M.glass, 0, 3, 0)); g.add(box(0.5, 0.2, 0.1, M.screen, 0, 1.9, 0.61)); return g }
function pReception(M) {
  const g = grp(); g.add(box(6, 2.4, 2, M.wood, 0, 1.2, 0)); g.add(box(6.6, 0.22, 2.5, M.woodDark, 0, 2.5, 0))
  g.add(box(6, 0.9, 0.2, M.accent, 0, 1.95, 1.05)); return g
}
function pLocker(M) {
  const g = grp()
  for (let i = 0; i < 3; i++) {
    g.add(box(1.3, 4, 1.4, M.steel, -1.4 + i * 1.4, 2, 0))
    g.add(box(0.1, 0.4, 0.06, M.chrome, -1.4 + i * 1.4 + 0.4, 2.6, 0.71))
    for (let v = 0; v < 3; v++) g.add(box(0.7, 0.04, 0.04, M.dark, -1.4 + i * 1.4, 3.4 + v * 0.12, 0.71))
  }; return g
}
function pWhiteboard(M) { const g = grp(); g.add(box(4.4, 2.8, 0.16, M.steel, 0, 3, 0)); g.add(box(4, 2.4, 0.04, M.white, 0, 3, 0.1)); return g }
function pTV(M) { const g = grp(); g.add(box(4, 2.3, 0.16, M.dark, 0, 3, 0)); g.add(box(3.7, 2.05, 0.02, M.screen, 0, 3, 0.1)); return g }
function pPrinter(M) {
  const g = grp(); g.add(box(2, 1.8, 1.8, M.white, 0, 0.9, 0)); g.add(box(1.7, 0.12, 1.4, M.dark, 0, 1.5, 0.2)); g.add(box(1.6, 0.5, 0.05, M.plastic, 0, 1, 0.91))
  const l = new THREE.Mesh(new THREE.BoxGeometry(0.3, 0.1, 0.05), M.led(0x16a34a)); l.position.set(0.6, 1.6, 0.91); l.userData.blink = true; g.add(l); return g
}

/* ═══════════════════════════════════════════════════════════════════════════
   RESIDENTIAL FIXTURES
═══════════════════════════════════════════════════════════════════════════ */
function pSplitAC(M) {
  const g = grp(); g.add(box(3.4, 1, 0.8, M.white, 0, 0, 0)); g.add(box(3.0, 0.14, 0.06, M.dark, 0, -0.34, 0.41))
  const l = new THREE.Mesh(new THREE.BoxGeometry(0.25, 0.1, 0.04), M.led(0x22d3ee)); l.position.set(1.2, 0.2, 0.41); l.userData.blink = true; g.add(l); return g
}
function pCeilingLight(M) { return grp(box(2.6, 0.16, 2.6, M.white, 0, 0, 0), box(2.3, 0.06, 2.3, M.warm, 0, -0.1, 0)) }
function pNightstand(M) { const g = grp(); g.add(box(1.3, 1.4, 1.3, M.wood, 0, 0.7, 0)); g.add(box(1.0, 0.08, 0.06, M.woodDark, 0, 0.95, 0.66)); return g }
function pRug(M) { const g = grp(); const m = new THREE.MeshStandardMaterial({ color: 0x8a93b5, roughness: 0.98 }); g.add(box(4.5, 0.06, 3.2, m, 0, 0.03, 0)); return g }
function pKitchen(M) {
  const g = grp(); g.add(box(7, 2.4, 2, M.white, 0, 1.2, 0)); g.add(box(7.2, 0.22, 2.2, M.dark, 0, 2.5, 0))
  g.add(box(7, 1.4, 0.9, M.wood, 0, 4.4, -0.5))
  for (let i = -2; i <= 2; i++) g.add(box(0.05, 1.2, 0.9, M.woodDark, i * 1.4, 4.4, -0.04))
  g.add(box(1.4, 0.06, 1.2, M.metal, 2, 2.62, 0)); return g
}
function pStove(M) {
  const g = grp(); g.add(box(2.4, 2.4, 2, M.steel, 0, 1.2, 0)); g.add(box(2.2, 0.1, 1.8, M.dark, 0, 2.46, 0))
  for (const [x, z] of [[-0.5, -0.4], [0.5, -0.4], [-0.5, 0.4], [0.5, 0.4]]) g.add(cyl(0.32, 0.32, 0.06, M.rubber, x, 2.5, z))
  g.add(box(2.0, 1.2, 0.05, M.glass, 0, 1.1, 1.01)); return g
}
function pSink(M) {
  const g = grp(); g.add(box(2.2, 1.9, 1.2, M.white, 0, 0.95, 0)); g.add(box(1.6, 0.16, 0.9, M.porcelain, 0, 1.95, 0))
  g.add(box(1.2, 0.12, 0.6, M.dark, 0, 1.9, 0)); g.add(cyl(0.07, 0.07, 0.9, M.chrome, 0, 2.3, -0.35)); return g
}
function pToilet(M) {
  const g = grp(); g.add(cyl(0.85, 0.7, 1.1, M.porcelain, 0, 0.6, 0.1, 18)); g.add(box(1.4, 0.25, 1.3, M.porcelain, 0, 1.2, 0.1))
  g.add(box(1.5, 1.6, 0.6, M.porcelain, 0, 1.6, -0.7)); return g
}
function pShower(M) {
  const g = grp(); g.add(box(3, 0.2, 3, M.porcelain, 0, 0.1, 0))
  g.add(box(0.1, 6, 3, M.glass, 1.5, 3, 0)); g.add(box(3, 6, 0.1, M.glass, 0, 3, -1.5))
  g.add(cyl(0.4, 0.4, 0.15, M.chrome, 1.0, 5.5, -1.2)); return g
}
function pBathtub(M) {
  const g = grp(); g.add(box(5, 1.8, 2.6, M.porcelain, 0, 0.9, 0))
  g.add(box(4.4, 1.0, 2.0, new THREE.MeshStandardMaterial({ color: 0xdfe6ee, roughness: 0.2 }), 0, 1.3, 0))
  g.add(cyl(0.07, 0.07, 0.8, M.chrome, -2.2, 1.6, 0)); return g
}
function pCar(M) {
  const g = grp()
  const body = new THREE.MeshStandardMaterial({ color: 0xcfd3da, roughness: 0.35, metalness: 0.6, envMapIntensity: 1.2 })
  g.add(box(4.6, 1.5, 9, body, 0, 1.4, 0)); g.add(box(4.0, 1.4, 4.6, M.glass, 0, 2.55, -0.4))
  for (const [x, z] of [[-2.0, -2.8], [2.0, -2.8], [-2.0, 2.8], [2.0, 2.8]]) { const w = cyl(0.95, 0.95, 0.5, M.rubber, x, 0.9, z); w.rotation.z = Math.PI / 2; g.add(w) }
  g.add(box(0.5, 0.4, 0.2, M.led(0xffe9b0), 0, 1.5, 4.5)); return g
}

/* ═══════════════════════════════════════════════════════════════════════════
   ADDED HOSPITAL ASSETS
═══════════════════════════════════════════════════════════════════════════ */

/* Ultrasound machine — cart console with articulating LCD + probes (~1.4 m tall) */
function pUltrasound(M) {
  const g = grp()
  // console body
  g.add(box(1.8, 2.8, 2.2, M.medWhite, 0, 1.4, 0))
  // angled control console with trackball + keyboard
  const ctrl = box(1.9, 0.18, 1.4, M.dark, 0, 2.92, 0.35); ctrl.rotation.x = -0.18; g.add(ctrl)
  g.add(cyl(0.13, 0.13, 0.1, M.chrome, 0.3, 3.05, 0.5))   // trackball
  for (let r = 0; r < 3; r++) for (let c = 0; c < 6; c++)
    g.add(box(0.13, 0.05, 0.13, M.plastic, -0.5 + c * 0.16, 3.0 + r * 0.0, 0.0 - r * 0.18))
  // articulating arm + LCD
  g.add(box(0.12, 1.5, 0.12, M.dark, -0.3, 3.7, -0.4))
  g.add(box(0.1, 0.1, 0.7, M.dark, -0.3, 4.4, -0.1))
  g.add(box(2.0, 1.3, 0.08, M.dark, 0, 4.55, 0.2))
  const scr = box(1.8, 1.12, 0.03, M.screen, 0, 4.55, 0.25)
  g.add(scr)
  // ultrasound image glow
  g.add(box(1.5, 0.95, 0.02, new THREE.MeshStandardMaterial({ color: 0x223040, emissive: 0x33506e, emissiveIntensity: 0.5 }), 0, 4.55, 0.27))
  // probe holders (3 transducers on left)
  for (let i = 0; i < 3; i++) {
    g.add(cyl(0.09, 0.12, 0.5, M.plastic, -1.0, 2.7 - i * 0.0, -0.4 + i * 0.4))
    // coiled cable
    const c = [0x2563eb, 0x16a34a, 0xe11d48][i]
    g.add(cyl(0.05, 0.05, 0.4, new THREE.MeshStandardMaterial({ color: c, roughness: 0.7 }), -1.05, 2.4, -0.4 + i * 0.4))
  }
  // status LED
  const led = new THREE.Mesh(new THREE.BoxGeometry(0.3, 0.08, 0.04), M.led(0x16a34a))
  led.position.set(0.7, 5.15, 0.25); led.userData.blink = true; g.add(led)
  // castor base
  g.add(box(2.0, 0.18, 2.4, M.dark, 0, 0.18, 0))
  ;[[-0.75, -0.85], [0.75, -0.85], [-0.75, 0.85], [0.75, 0.85]].forEach(([cx, cz]) => {
    const w = torus(0.22, 0.08, M.rubber, cx, 0.2, cz); w.rotation.y = Math.PI / 2; g.add(w)
  })
  return g
}

/* Anesthesia machine — gas station w/ vaporizers, bellows, monitor (~1.5 m tall) */
function pAnesthesia(M) {
  const g = grp()
  // main body / cabinet
  g.add(box(3.0, 3.4, 2.0, M.medWhite, 0, 1.9, 0))
  // worktop
  g.add(box(3.2, 0.12, 2.1, M.stainless, 0, 3.66, 0))
  // colour-coded vaporizers (yellow Sevo / purple Iso)
  g.add(box(0.7, 0.9, 0.7, new THREE.MeshStandardMaterial({ color: 0xf2c200, roughness: 0.4 }), -0.7, 4.15, -0.2))
  g.add(box(0.7, 0.9, 0.7, new THREE.MeshStandardMaterial({ color: 0x7c3aed, roughness: 0.4 }), 0.1, 4.15, -0.2))
  // flowmeter tube bank (glass columns)
  for (let i = 0; i < 3; i++) {
    g.add(cyl(0.08, 0.08, 1.2, M.glass, 0.7 + i * 0.18, 4.3, 0.3))
    const ball = new THREE.Mesh(new THREE.SphereGeometry(0.06, 8, 8), M.chrome)
    ball.position.set(0.7 + i * 0.18, 4.1 + i * 0.15, 0.3); g.add(ball)
  }
  // ventilator bellows in glass cylinder
  g.add(cyl(0.42, 0.42, 1.1, M.glass, -1.1, 4.3, 0.4))
  for (let i = 0; i < 5; i++) g.add(cyl(0.38 - i * 0.0, 0.38, 0.04, M.medBlue, -1.1, 3.95 + i * 0.16, 0.4))
  // top patient monitor
  g.add(box(0.12, 1.6, 0.12, M.dark, 1.1, 5.0, -0.5))
  g.add(box(1.8, 1.3, 0.5, M.dark, 1.1, 5.9, -0.3))
  g.add(box(1.6, 1.1, 0.04, M.led(0x00ff88), 1.1, 5.9, -0.04))
  // breathing circuit hoses (blue corrugated loop)
  const hoseM = new THREE.MeshStandardMaterial({ color: 0x4a90c8, roughness: 0.45 })
  const hp = [new THREE.Vector3(-1.1, 3.7, 1.0), new THREE.Vector3(-0.6, 3.2, 1.6), new THREE.Vector3(0.2, 3.5, 1.2)]
  g.add(new THREE.Mesh(new THREE.TubeGeometry(new THREE.CatmullRomCurve3(hp), 14, 0.08, 8, false), hoseM))
  // drawers (colour bands)
  ;[0.6, 1.4, 2.2].forEach((y, i) => {
    g.add(box(2.7, 0.62, 0.07, M.medBlue, 0, y, 1.01))
    g.add(box(1.0, 0.1, 0.05, M.chrome, 0, y, 1.06))
  })
  // O2 / N2O pipeline hoses to wall (green + blue)
  g.add(cyl(0.05, 0.05, 1.5, new THREE.MeshStandardMaterial({ color: 0x3a7a3a }), -1.3, 1.0, -1.0))
  g.add(cyl(0.05, 0.05, 1.5, new THREE.MeshStandardMaterial({ color: 0x2563eb }), -1.15, 1.0, -1.0))
  // castors
  ;[[-1.1, -0.8], [1.1, -0.8], [-1.1, 0.8], [1.1, 0.8]].forEach(([cx, cz]) => {
    const w = torus(0.24, 0.08, M.rubber, cx, 0.22, cz); w.rotation.y = Math.PI / 2; g.add(w)
  })
  return g
}

/* Examination table / couch — padded, paper roll, drawers, step */
function pExamTable(M) {
  const g = grp()
  const upholst = new THREE.MeshStandardMaterial({ color: 0x2e6e8e, roughness: 0.5, metalness: 0.05 })
  // base cabinet
  g.add(box(2.2, 2.0, 5.8, M.medWhite, 0, 1.0, 0))
  // drawers on side
  for (let i = 0; i < 3; i++) {
    g.add(box(0.06, 0.5, 1.4, M.stainless, 1.11, 0.6 + i * 0.55, -1.4))
    g.add(box(0.04, 0.08, 0.4, M.chrome, 1.14, 0.6 + i * 0.55, -1.4))
  }
  // padded top (raised back section)
  g.add(box(2.3, 0.5, 5.9, upholst, 0, 2.25, 0))
  const backrest = box(2.3, 0.45, 2.0, upholst, 0, 2.55, -1.9); backrest.rotation.x = 0.25; g.add(backrest)
  // paper roll at head
  g.add(cyl(0.32, 0.32, 2.2, M.white, 0, 2.85, -2.95)); g.children[g.children.length - 1].rotation.z = Math.PI / 2
  // pull-out step
  g.add(box(2.0, 0.5, 0.9, M.stainless, 0, 0.55, 3.3))
  g.add(box(1.8, 0.06, 0.7, M.rubber, 0, 0.83, 3.3))
  // stirrup sockets (folded)
  for (const sx of [-1.0, 1.0]) g.add(cyl(0.06, 0.06, 0.6, M.chrome, sx, 2.4, 2.6))
  return g
}

/* Dialysis machine — tall tower with screen, blood lines, dialyzer */
function pDialysis(M) {
  const g = grp()
  g.add(box(2.0, 5.2, 1.8, M.medWhite, 0, 2.6, 0))
  // screen
  g.add(box(1.55, 1.5, 0.05, M.screen, 0, 4.2, 0.92))
  g.add(box(1.4, 1.3, 0.02, new THREE.MeshStandardMaterial({ color: 0x14304a, emissive: 0x2a5a86, emissiveIntensity: 0.5 }), 0, 4.2, 0.94))
  // dialyzer cartridge (vertical clear tube, amber)
  g.add(cyl(0.22, 0.22, 1.4, new THREE.MeshStandardMaterial({ color: 0xe8b96a, transparent: true, opacity: 0.7, roughness: 0.2 }), -0.7, 2.7, 0.95))
  // blood pump (rotor)
  const pump = grp()
  for (let i = 0; i < 3; i++) { const b = cyl(0.08, 0.08, 0.5, M.chrome); b.rotation.z = Math.PI / 2; b.rotation.y = i * Math.PI * 2 / 3; pump.add(b) }
  pump.position.set(0.6, 3.1, 0.95); pump.userData.spin = 0.8; g.add(pump)
  const pr = torus(0.32, 0.05, M.dark, 0.6, 3.1, 0.92); g.add(pr)
  // blood lines (red arterial / blue venous)
  const redM = new THREE.MeshStandardMaterial({ color: 0xc0392b, roughness: 0.5 })
  const blueM = new THREE.MeshStandardMaterial({ color: 0x2563eb, roughness: 0.5 })
  const rp = [new THREE.Vector3(-0.7, 2.0, 0.96), new THREE.Vector3(0.2, 1.5, 1.2), new THREE.Vector3(0.6, 2.8, 0.96)]
  g.add(new THREE.Mesh(new THREE.TubeGeometry(new THREE.CatmullRomCurve3(rp), 16, 0.04, 6, false), redM))
  const bp = [new THREE.Vector3(-0.5, 3.4, 0.96), new THREE.Vector3(-0.9, 2.4, 1.2), new THREE.Vector3(-0.7, 1.6, 0.96)]
  g.add(new THREE.Mesh(new THREE.TubeGeometry(new THREE.CatmullRomCurve3(bp), 16, 0.04, 6, false), blueM))
  // status bar
  const led = new THREE.Mesh(new THREE.BoxGeometry(0.6, 0.1, 0.04), M.led(0x16a34a))
  led.position.set(0, 5.1, 0.92); g.add(led)
  // castors
  ;[[-0.7, -0.6], [0.7, -0.6], [-0.7, 0.6], [0.7, 0.6]].forEach(([cx, cz]) => {
    const w = torus(0.24, 0.08, M.rubber, cx, 0.22, cz); w.rotation.y = Math.PI / 2; g.add(w)
  })
  return g
}

/* Infusion pump on a slim pole (volumetric IV pump) */
function pInfusionPump(M) {
  const g = grp()
  // 5-spoke base
  for (let i = 0; i < 5; i++) {
    const a = (i / 5) * Math.PI * 2
    g.add(box(0.6, 0.05, 0.07, M.chrome, Math.cos(a) * 0.3, 0.05, Math.sin(a) * 0.3))
    const w = torus(0.1, 0.035, M.rubber, Math.cos(a) * 0.58, 0.1, Math.sin(a) * 0.58, 6, 12); w.rotation.y = Math.PI / 2; g.add(w)
  }
  g.add(cyl(0.045, 0.045, 5.0, M.chrome, 0, 2.6, 0))
  // two stacked pump modules clamped to pole
  for (let i = 0; i < 2; i++) {
    const py = 3.0 + i * 0.95
    g.add(box(1.1, 0.85, 0.6, M.medWhite, 0.35, py, 0))
    g.add(box(0.7, 0.5, 0.04, M.led(0x00ccff), 0.55, py + 0.1, 0.31))
    const l = new THREE.Mesh(new THREE.BoxGeometry(0.12, 0.08, 0.04), M.led(0x16a34a))
    l.position.set(0.05, py + 0.3, 0.31); l.userData.blink = true; g.add(l)
  }
  // small bag on hook
  g.add(box(0.5, 0.85, 0.12, M.ivFluid, 0, 4.85, 0))
  g.add(box(0.04, 0.04, 0.5, M.chrome, 0, 5.4, 0))
  return g
}

/* Mayo instrument stand — tray on cantilever post, weighted base + wheels */
function pMayoStand(M) {
  const g = grp()
  // weighted H base
  g.add(box(0.18, 0.12, 2.2, M.stainless, -0.5, 0.12, 0))
  g.add(box(0.18, 0.12, 2.2, M.stainless, 0.5, 0.12, 0))
  g.add(box(1.2, 0.12, 0.18, M.stainless, 0, 0.12, 0))
  ;[[-0.5, -1.0], [0.5, -1.0], [-0.5, 1.0], [0.5, 1.0]].forEach(([cx, cz]) => {
    const w = torus(0.12, 0.04, M.rubber, cx, 0.12, cz, 6, 12); w.rotation.y = Math.PI / 2; g.add(w)
  })
  // cantilever post (offset)
  g.add(cyl(0.07, 0.07, 3.4, M.stainless, 0, 1.8, -0.4))
  g.add(box(0.1, 0.1, 0.6, M.stainless, 0, 3.5, -0.1))
  // tray
  g.add(box(2.2, 0.07, 1.5, M.stainless, 0, 3.55, 0.3))
  g.add(box(2.1, 0.04, 1.4, new THREE.MeshStandardMaterial({ color: 0x6fa8c7, roughness: 0.4 }), 0, 3.6, 0.3))
  // a few "instruments" on the tray
  for (let i = 0; i < 4; i++) g.add(box(0.06, 0.05, 0.7, M.chrome, -0.6 + i * 0.4, 3.62, 0.3))
  return g
}

/* ═══════════════════════════════════════════════════════════════════════════
   ADDED DATA-CENTRE ASSETS
═══════════════════════════════════════════════════════════════════════════ */

/* Clean-agent fire suppression — bank of red cylinders w/ manifold + nozzle */
function pCleanAgent(M) {
  const g = grp()
  const redM = new THREE.MeshStandardMaterial({ color: 0xc0392b, roughness: 0.4, metalness: 0.5 })
  const cylN = 3
  for (let i = 0; i < cylN; i++) {
    const cx = -1.1 + i * 1.1
    g.add(cyl(0.5, 0.5, 4.2, redM, cx, 2.1, 0))
    const dome = new THREE.Mesh(new THREE.SphereGeometry(0.5, 14, 8, 0, Math.PI * 2, 0, Math.PI / 2), redM)
    dome.position.set(cx, 4.2, 0); g.add(dome)
    // valve head
    g.add(cyl(0.16, 0.16, 0.45, M.brass || M.amber, cx, 4.5, 0))
    g.add(box(0.3, 0.2, 0.3, M.dark, cx, 4.7, 0))
    // mounting strap
    g.add(box(1.0, 0.12, 0.12, M.steel, cx, 3.0, 0.5))
    // label band
    g.add(cyl(0.51, 0.51, 0.6, M.white, cx, 2.4, 0))
  }
  // wall bracket
  g.add(box(cylN * 1.1 + 0.4, 0.18, 0.4, M.steel, 0, 1.0, 0.55))
  g.add(box(cylN * 1.1 + 0.4, 0.18, 0.4, M.steel, 0, 3.6, 0.55))
  // discharge manifold (red pipe across tops + riser)
  const pipe = cyl(0.13, 0.13, cylN * 1.1 + 0.3, redM, 0, 5.0, -0.4); pipe.rotation.z = Math.PI / 2; g.add(pipe)
  g.add(cyl(0.13, 0.13, 1.8, redM, 1.45, 5.9, -0.4))
  // ceiling nozzle
  g.add(cyl(0.18, 0.1, 0.3, M.brass || M.amber, 1.45, 6.85, -0.4))
  // pressure gauges
  for (let i = 0; i < cylN; i++) {
    const ga = new THREE.Mesh(new THREE.CircleGeometry(0.12, 12), M.white)
    ga.position.set(-1.1 + i * 1.1, 4.6, 0.18); g.add(ga)
  }
  return g
}

/* In-row cooling unit — narrow tall unit that sits between server racks */
function pInRowCooler(M) {
  const g = grp()
  const W = 1.0, H = 7.4, D = 2.6
  g.add(box(W, H, D, M.rackBlack, 0, H / 2, 0))
  // front perforated intake
  g.add(box(W - 0.06, H - 0.3, 0.05, M.dark, 0, H / 2, D / 2 + 0.01))
  for (let i = 0; i < 18; i++) g.add(box(W - 0.2, 0.1, 0.06, M.steel, 0, 0.5 + i * 0.38, D / 2 + 0.04))
  // stacked fans visible through top section
  for (let i = 0; i < 3; i++) {
    const fy = 2.0 + i * 1.6
    const ring = torus(0.42, 0.05, M.dark, 0, fy, D / 2 + 0.05); ring.rotation.x = Math.PI / 2; g.add(ring)
    const fan = grp()
    for (let b = 0; b < 5; b++) { const bl = box(0.7, 0.05, 0.18, M.steel); bl.rotation.y = b * Math.PI * 2 / 5; fan.add(bl) }
    fan.position.set(0, fy, D / 2 + 0.06); fan.userData.spin = 1.6 + i * 0.2; g.add(fan)
  }
  // control display at top
  g.add(box(0.7, 0.55, 0.05, M.screen, 0, 6.7, D / 2 + 0.04))
  const led = new THREE.Mesh(new THREE.BoxGeometry(0.3, 0.09, 0.04), M.led(0x22d3ee))
  led.position.set(0, 7.05, D / 2 + 0.04); led.userData.blink = true; g.add(led)
  // chilled-water pipes at rear
  g.add(cyl(0.12, 0.12, H * 0.7, new THREE.MeshStandardMaterial({ color: 0x2563eb, roughness: 0.5 }), -0.25, H / 2, -D / 2 - 0.05))
  g.add(cyl(0.12, 0.12, H * 0.7, M.red, 0.25, H / 2, -D / 2 - 0.05))
  // base + castors
  g.add(box(W + 0.1, 0.15, D + 0.1, M.dark, 0, 0.075, 0))
  ;[[-0.35, -1.1], [0.35, -1.1], [-0.35, 1.1], [0.35, 1.1]].forEach(([cx, cz]) =>
    g.add(cyl(0.13, 0.13, 0.16, M.rubber, cx, -0.07, cz)))
  return g
}

/* ═══════════════════════════════════════════════════════════════════════════
   PROP REGISTRY
═══════════════════════════════════════════════════════════════════════════ */
const PROP_FOR = {
  // data centre / IT
  rack: pRack, smallrack: pSmallRack, network: pNetwork, pdu: pPDU, cabletray: pCableTray,
  inrowcooler: pInRowCooler, cleanagent: pCleanAgent,
  // cooling / HVAC
  crac: pCRAC, ahu: pAHU, vav: pVAV, chiller: pChiller, coolingtower: pCoolingTower, boiler: pBoiler,
  // power
  ups: pUPS, transformer: pTransformer, switchgear: pSwitchgear, generator: pGenerator,
  meter: pMeter, battery: pBattery, solar: pSolarPanel, evcharger: pEVCharger,
  // water / mechanical
  pump: pPump, watertank: pWaterTank, valve: pValve, compressor: pCompressor, tank: pStorageTank,
  // fire / safety / security
  extinguisher: pFireExtinguisher, firepanel: pFirePanel, smoke: pSmokeDetector,
  sprinkler: pSprinkler, exitsign: pExitSign, sensor: pSensor, camera: pCamera,
  // access / vertical transport
  door: pDoor, elevator: pElevator, escalator: pEscalator,
  // lighting
  light: pLight, ceilinglight: pCeilingLight, surgicallight: pSurgicalLight,
  // hospital — imaging
  mri: pMRI, ctscanner: pCTScanner, xray: pXray,
  // hospital — equipment
  gas: pGas, laf: pLAF, nurse: pNurse, fridge: pFridge,
  bloodbank: pBloodBank, autoclave: pAutoclave, gascylinderbank: pGasCylinderBank,
  hospitalbed: pHospitalBed, bed: pBed, stretcher: pStretcher,
  wheelchair: pWheelchair, ivstand: pIVStand, patientmonitor: pPatientMonitor,
  operatingtable: pOperatingTable, crashcart: pCrashCart, medcart: pMedCart,
  ventilator: pVentilator, ultrasound: pUltrasound, anesthesia: pAnesthesia,
  examtable: pExamTable, dialysis: pDialysis, infusionpump: pInfusionPump,
  mayostand: pMayoStand,
  // factory / logistics
  robot: pRobot, conveyor: pConveyor, cnc: pCNC, welder: pWelder, press: pPress,
  forklift: pForklift, palletrack: pPalletRack, workbench: pWorkbench,
  // office / furniture / decor
  desk: pDesk, chair: pChair, stool: pStool, sofa: pSofa, table: pTable,
  coffeetable: pCoffeeTable, bookshelf: pBookshelf, cabinet: pCabinet, plant: pPlant,
  watercooler: pWaterCooler, reception: pReception, locker: pLocker,
  whiteboard: pWhiteboard, tv: pTV, monitor: (M) => pMonitor(M), printer: pPrinter,
  // residential fixtures
  splitac: pSplitAC, nightstand: pNightstand, rug: pRug,
  kitchen: pKitchen, stove: pStove, sink: pSink, toilet: pToilet, shower: pShower,
  bathtub: pBathtub, car: pCar, box: pBox,
}

export function buildProp(key, M) {
  const fn = PROP_FOR[key] || pBox
  return fn(M)
}

export function buildGreen(M, kind = 'shrub') {
  const g = new THREE.Group()
  if (kind === 'tree') {
    const trunk = new THREE.Mesh(new THREE.CylinderGeometry(0.18, 0.26, 2.2, 8), M.trunk)
    trunk.position.y = 1.1; trunk.castShadow = true; g.add(trunk)
    const crown = new THREE.Mesh(new THREE.SphereGeometry(1.5, 12, 10), M.foliage)
    crown.position.y = 3.0; crown.castShadow = true; g.add(crown)
  } else {
    const b = new THREE.Mesh(new THREE.SphereGeometry(0.7, 10, 8), M.foliage)
    b.scale.set(1, 0.8, 1); b.position.y = 0.5; b.castShadow = true; g.add(b)
  }
  return g
}
