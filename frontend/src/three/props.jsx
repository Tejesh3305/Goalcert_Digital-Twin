/**
 * props.jsx — the procedural equipment library, ported from demo.html.
 *
 * Each builder returns a THREE.Group modelled exactly like the demo (same
 * dimensions, LEDs, materials) so the reconstructed twin looks identical. They
 * are rendered into the r3f scene with <primitive>. Per-object animation flags
 * (userData.blink / spin / press / belt / cargo / robot) are driven by the
 * animation loop in Scene.jsx, just like the demo's RAF loop.
 *
 * Builders are unit-modelled (~feet); Scene.jsx scales each placed prop down to
 * metres with PROP_SCALE so it fits the metre-scaled building shell.
 */
import * as THREE from 'three'

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

export function pStatusPin(color) {
  const g = new THREE.Group()
  const m = new THREE.MeshStandardMaterial({ color, emissive: color, emissiveIntensity: 1.6 })
  const s = new THREE.Mesh(new THREE.SphereGeometry(0.32, 16, 16), m)
  const halo = new THREE.Mesh(new THREE.SphereGeometry(0.55, 16, 16),
    new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.22 }))
  g.add(s, halo); g.userData.pin = true
  return g
}

function pRack(M) {
  const g = grp(); g.add(box(2, 7.4, 2.6, M.rack, 0, 3.7, 0)); g.add(box(1.7, 7, 0.1, M.dark, 0, 3.7, 1.31))
  for (let i = 0; i < 10; i++) {
    const c = Math.random() > 0.5 ? 0x22d3ee : 0x16a34a
    const led = new THREE.Mesh(new THREE.BoxGeometry(0.12, 0.16, 0.04), M.led(c))
    led.position.set(-0.7, 1.1 + i * 0.62, 1.34); led.userData.blink = true; g.add(led)
  }
  g.add(box(2.1, 0.2, 2.7, M.dark, 0, 0.1, 0)); return g
}
function pCRAC(M) {
  const g = grp(); g.add(box(3.4, 6, 2.4, M.metal, 0, 3, 0))
  for (let i = 0; i < 6; i++) g.add(box(2.8, 0.18, 0.06, M.dark, 0, 1.2 + i * 0.7, 1.22))
  g.add(cyl(1, 1, 0.3, M.dark, 0, 6.2, 0))
  const blades = grp(); for (let i = 0; i < 4; i++) { const b = box(1.8, 0.06, 0.3, M.steel); b.rotation.y = i * Math.PI / 2; blades.add(b) }
  blades.position.y = 6.4; blades.userData.spin = 0.9; g.add(blades); return g
}
function pUPS(M) {
  const g = grp(); g.add(box(2.6, 5.4, 2, M.dark, 0, 2.7, 0)); g.add(box(2, 1.2, 0.08, M.screen, 0, 4.2, 1.01))
  const led = new THREE.Mesh(new THREE.BoxGeometry(0.5, 0.16, 0.05), M.led(0x16a34a)); led.position.set(0, 2.6, 1.01); g.add(led); return g
}
function pNetwork(M) {
  const g = grp(); g.add(box(2, 4.6, 2.4, M.rack, 0, 2.3, 0))
  for (let i = 0; i < 8; i++) {
    const c = Math.random() > 0.4 ? 0x22d3ee : 0xf59e0b
    const l = new THREE.Mesh(new THREE.BoxGeometry(0.1, 0.1, 0.04), M.led(c))
    l.position.set(-0.6 + (i % 4) * 0.4, 1.4 + Math.floor(i / 4) * 0.5, 1.21); l.userData.blink = true; g.add(l)
  }
  return g
}
function pAHU(M) {
  const g = grp(); g.add(box(5, 2.4, 2.6, M.metal, 0, 1.2, 0)); g.add(cyl(0.9, 0.9, 0.4, M.dark, -1.4, 1.2, 1.4))
  const fan = grp(); for (let i = 0; i < 4; i++) { const b = box(1.4, 0.05, 0.25, M.steel); b.rotation.y = i * Math.PI / 2; fan.add(b) }
  fan.position.set(-1.4, 1.2, 1.62); fan.rotation.x = Math.PI / 2; fan.userData.spin = 1.2; g.add(fan)
  g.add(box(2, 1, 2.6, M.dark, 1.6, 1.2, 0)); return g
}
function pVAV(M) {
  const g = grp(); g.add(box(2.4, 1, 1.4, M.metal, 0, 0.5, 0))
  const d = cyl(0.5, 0.5, 1.2, M.dark, 0, 0.5, 1.1); d.rotation.x = Math.PI / 2; g.add(d); return g
}
function pLight(M) {
  const g = grp(); g.add(box(3, 0.2, 1, M.white, 0, 0, 0)); g.add(box(2.7, 0.05, 0.7, M.warm, 0, -0.12, 0)); return g
}
function pDoor(M) {
  const g = grp(); g.add(box(0.2, 5, 3, M.steel, 0, 2.5, 0)); g.add(box(0.25, 4.4, 1.3, M.glass, 0, 2.5, 0.7))
  const r = new THREE.Mesh(new THREE.BoxGeometry(0.1, 0.3, 0.5), M.led(0x16a34a)); r.position.set(0.16, 3, -0.9); g.add(r); return g
}
function pMeter(M) {
  const g = grp(); g.add(box(1.6, 3, 0.8, M.white, 0, 1.5, 0)); g.add(box(1.1, 1, 0.06, M.screen, 0, 2, 0.42)); return g
}
function pSmallRack(M) {
  const g = grp(); g.add(box(1.6, 3.4, 1.6, M.rack, 0, 1.7, 0))
  for (let i = 0; i < 5; i++) { const l = new THREE.Mesh(new THREE.BoxGeometry(0.1, 0.1, 0.04), M.led(0xe11d48)); l.position.set(-0.4, 1 + i * 0.5, 0.81); l.userData.blink = true; g.add(l) }
  return g
}
function pMRI(M) {
  const g = grp(); g.add(box(5, 3.6, 4.5, M.white, 0, 1.8, 0))
  const ring = new THREE.Mesh(new THREE.TorusGeometry(1.5, 0.7, 18, 32), M.metal); ring.position.set(0, 1.9, 2.4); g.add(ring)
  const hole = new THREE.Mesh(new THREE.CircleGeometry(1.4, 32), M.screen); hole.position.set(0, 1.9, 2.41); g.add(hole)
  g.add(box(1, 0.4, 3, M.white, 0, 1.4, 4)); return g
}
function pGas(M) {
  const g = grp(); g.add(box(2.4, 3.2, 1, M.white, 0, 1.6, 0))
  for (let i = 0; i < 3; i++) g.add(cyl(0.35, 0.35, 2.2, M.led([0x16a34a, 0x2563eb, 0xf59e0b][i]), -0.7 + i * 0.7, 1.4, 0.5)); return g
}
function pLAF(M) {
  const g = grp(); g.add(box(4, 0.5, 4, M.white, 0, 0, 0)); g.add(box(3.6, 0.1, 3.6, M.cool, 0, -0.25, 0)); return g
}
function pNurse(M) {
  const g = grp(); g.add(box(1.4, 3, 0.6, M.white, 0, 1.5, 0)); g.add(box(1, 1.1, 0.05, M.screen, 0, 2, 0.32))
  const l = new THREE.Mesh(new THREE.BoxGeometry(0.4, 0.2, 0.1), M.led(0xe11d48)); l.position.set(0, 2.8, 0.3); g.add(l); return g
}
function pFridge(M) {
  const g = grp(); g.add(box(2, 3.6, 2, M.white, 0, 1.8, 0)); g.add(box(1.4, 3, 0.06, M.glass, 0, 1.9, 1.01))
  const l = new THREE.Mesh(new THREE.BoxGeometry(0.8, 0.2, 0.05), M.led(0x2563eb)); l.position.set(0, 3.3, 1.01); g.add(l); return g
}
function pPress(M) {
  const g = grp(); g.add(box(3.4, 5.5, 3, M.steel, 0, 2.75, 0)); g.add(box(2.6, 0.6, 2.4, M.dark, 0, 4.6, 0))
  const ram = box(2.2, 1.4, 2, M.metal, 0, 2.6, 0); ram.userData.press = true; g.add(ram); g.add(box(3.8, 0.8, 3.4, M.dark, 0, 0.4, 0)); return g
}
function pRobot(M) {
  const g = grp(); g.add(cyl(1, 1.3, 0.6, M.dark, 0, 0.3, 0))
  g.add(cyl(0.8, 0.8, 1.2, M.amber, 0, 1.1, 0))
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
function pBox(M) {
  const g = grp(); g.add(box(2, 2.4, 2, M.metal, 0, 1.2, 0)); g.add(box(1.4, 0.6, 0.06, M.screen, 0, 1.8, 1.01)); return g
}

/* ───────────────────────── OFFICE / FURNITURE (decor) ───────────────────── */
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
  const g = grp(); g.add(box(4, 0.18, 2.2, M.wood, 0, 2.4, 0)); legs4(M, 4, 2.2, 2.4, M.steel).forEach((l) => g.add(l))
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
function pTable(M) { const g = grp(); g.add(box(5, 0.2, 2.6, M.wood, 0, 2.3, 0)); legs4(M, 5, 2.6, 2.3, M.woodDark).forEach((l) => g.add(l)); return g }
function pCoffeeTable(M) { const g = grp(); g.add(box(3, 0.16, 1.6, M.woodDark, 0, 1.2, 0)); legs4(M, 3, 1.6, 1.2, M.woodDark).forEach((l) => g.add(l)); return g }
function pBookshelf(M) {
  const g = grp(); g.add(box(3, 5, 1, M.wood, 0, 2.5, 0))
  for (let i = 1; i < 5; i++) g.add(box(2.8, 0.08, 0.95, M.woodDark, 0, i, 0))
  const cols = [0x7c3aed, 0x2563eb, 0x16a34a, 0xf59e0b, 0xe11d48]
  for (let s = 0; s < 4; s++) for (let b = 0; b < 6; b++) g.add(box(0.18, 0.7, 0.6, new THREE.MeshStandardMaterial({ color: cols[(s + b) % cols.length], roughness: 0.8 }), -1.2 + b * 0.4, s + 0.5, 0.1)); return g
}
function pCabinet(M) {
  const g = grp(); g.add(box(2.4, 3, 1.4, M.woodDark, 0, 1.5, 0))
  for (let i = 0; i < 3; i++) { g.add(box(2.2, 0.05, 0.05, M.steel, 0, 0.7 + i * 0.9, 0.71)); g.add(box(0.4, 0.1, 0.08, M.chrome, 0, 0.95 + i * 0.9, 0.72)) } return g
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
  const g = grp(); for (let i = 0; i < 3; i++) { g.add(box(1.3, 4, 1.4, M.steel, -1.4 + i * 1.4, 2, 0)); g.add(box(0.1, 0.4, 0.06, M.chrome, -1.4 + i * 1.4 + 0.4, 2.6, 0.71)); for (let v = 0; v < 3; v++) g.add(box(0.7, 0.04, 0.04, M.dark, -1.4 + i * 1.4, 3.4 + v * 0.12, 0.71)) } return g
}
function pWhiteboard(M) { const g = grp(); g.add(box(4.4, 2.8, 0.16, M.steel, 0, 3, 0)); g.add(box(4, 2.4, 0.04, M.white, 0, 3, 0.1)); return g }
function pTV(M) { const g = grp(); g.add(box(4, 2.3, 0.16, M.dark, 0, 3, 0)); g.add(box(3.7, 2.05, 0.02, M.screen, 0, 3, 0.1)); return g }
function pPrinter(M) {
  const g = grp(); g.add(box(2, 1.8, 1.8, M.white, 0, 0.9, 0)); g.add(box(1.7, 0.12, 1.4, M.dark, 0, 1.5, 0.2)); g.add(box(1.6, 0.5, 0.05, M.plastic, 0, 1, 0.91))
  const l = new THREE.Mesh(new THREE.BoxGeometry(0.3, 0.1, 0.05), M.led(0x16a34a)); l.position.set(0.6, 1.6, 0.91); l.userData.blink = true; g.add(l); return g
}

/* ───────────────────────── HOSPITAL ─────────────────────────────────────── */
function pBed(M) {
  const g = grp(); g.add(box(3, 0.5, 6, M.wood, 0, 1.2, 0)); g.add(box(2.8, 0.5, 5.6, M.mattress, 0, 1.6, 0.1))
  g.add(box(1.2, 0.4, 0.7, M.cushion, 0, 2, -2.2)); g.add(box(3, 1.8, 0.3, M.woodDark, 0, 1.8, -3)); return g
}
function pHospitalBed(M) {
  const g = grp(); g.add(box(2.4, 0.3, 6, M.metal, 0, 1.5, 0)); g.add(box(2.2, 0.45, 5.4, M.mattress, 0, 1.85, 0))
  g.add(box(2.4, 1.4, 0.18, M.white, 0, 2.1, -2.9)); g.add(box(2.4, 0.9, 0.15, M.white, 0, 2, 2.9))
  for (const sx of [-1.25, 1.25]) g.add(box(0.1, 0.7, 3.6, M.chrome, sx, 2.1, 0))
  for (const [wx, wz] of [[-1, -2.5], [1, -2.5], [-1, 2.5], [1, 2.5]]) { const w = new THREE.Mesh(new THREE.TorusGeometry(0.35, 0.12, 8, 14), M.dark); w.rotation.y = Math.PI / 2; w.position.set(wx, 0.4, wz); g.add(w) } return g
}
function pStretcher(M) {
  const g = grp(); g.add(box(2, 0.25, 5.5, M.metal, 0, 2, 0)); g.add(box(1.9, 0.35, 5.2, M.mattress, 0, 2.3, 0))
  for (const [wx, wz] of [[-0.8, -2.2], [0.8, -2.2], [-0.8, 2.2], [0.8, 2.2]]) { const w = new THREE.Mesh(new THREE.TorusGeometry(0.45, 0.14, 8, 14), M.dark); w.rotation.y = Math.PI / 2; w.position.set(wx, 0.5, wz); g.add(w) }
  g.add(cyl(0.08, 0.08, 2.5, M.chrome, 0.8, 3.5, -2.4)); return g
}
function pWheelchair(M) {
  const g = grp(); g.add(box(1.6, 0.16, 1.6, M.fabric, 0, 1.6, 0)); g.add(box(1.6, 1.7, 0.16, M.fabric, 0, 2.4, -0.7))
  for (const sx of [-1, 1]) { const w = new THREE.Mesh(new THREE.TorusGeometry(1.1, 0.12, 8, 20), M.dark); w.rotation.y = Math.PI / 2; w.position.set(sx, 1.1, -0.2); g.add(w) }
  for (const sx of [-0.7, 0.7]) { const c = new THREE.Mesh(new THREE.TorusGeometry(0.4, 0.1, 8, 14), M.dark); c.rotation.y = Math.PI / 2; c.position.set(sx, 0.4, 1.1); g.add(c) } return g
}
function pIVStand(M) {
  const g = grp(); g.add(cyl(0.1, 0.1, 5, M.chrome, 0, 2.5, 0)); g.add(cyl(0.6, 0.6, 0.1, M.dark, 0, 0.1, 0, 5))
  g.add(box(0.5, 0.8, 0.2, new THREE.MeshStandardMaterial({ color: 0xffe08a, transparent: true, opacity: 0.7, roughness: 0.3 }), 0.5, 4.4, 0)); g.add(box(0.8, 0.08, 0.08, M.chrome, 0.3, 4.9, 0)); return g
}
function pPatientMonitor(M) {
  const g = grp(); g.add(cyl(0.5, 0.5, 0.1, M.dark, 0, 0.1, 0, 5)); g.add(cyl(0.1, 0.1, 4, M.chrome, 0, 2, 0))
  g.add(box(1.8, 1.3, 0.5, M.dark, 0, 4.2, 0)); const s = box(1.5, 1, 0.04, M.led(0x16a34a), 0, 4.2, 0.27); g.add(s); return g
}
function pOperatingTable(M) {
  const g = grp(); g.add(box(2.2, 0.4, 5.5, M.metal, 0, 3, 0)); g.add(box(2, 0.3, 5.2, M.mattress, 0, 3.3, 0))
  g.add(cyl(0.6, 0.8, 3, M.chrome, 0, 1.5, 0)); g.add(box(2, 0.3, 2, M.dark, 0, 0.15, 0)); return g
}
function pCTScanner(M) {
  const g = grp(); g.add(box(5, 4, 3, M.white, 0, 2, 0)); const ring = new THREE.Mesh(new THREE.TorusGeometry(1.6, 0.9, 18, 32), M.white); ring.position.set(0, 2.1, 1.6); g.add(ring)
  const hole = new THREE.Mesh(new THREE.CircleGeometry(1.5, 32), M.screen); hole.position.set(0, 2.1, 1.62); g.add(hole); g.add(box(1, 0.4, 4, M.white, 0, 1.6, 4.5)); return g
}
function pXray(M) {
  const g = grp(); g.add(box(1.6, 0.4, 1.6, M.dark, 0, 0.2, 0)); g.add(cyl(0.3, 0.3, 4, M.metal, 0, 2.2, 0))
  const arc = new THREE.Mesh(new THREE.TorusGeometry(1.8, 0.25, 10, 20, Math.PI), M.metal); arc.position.set(0, 4, 0); arc.rotation.z = Math.PI; g.add(arc)
  g.add(box(1, 1, 0.5, M.steel, 0, 2.2, 1.6)); g.add(box(0.9, 0.9, 0.5, M.steel, 0, 5.6, 0)); return g
}

/* ───────────────────────── FACTORY / LOGISTICS ──────────────────────────── */
function pForklift(M) {
  const g = grp(); g.add(box(2.4, 1.6, 4, M.amber, 0, 1.2, -0.5)); g.add(box(2.2, 2, 1.8, M.dark, 0, 3, -1.2))
  g.add(box(2, 0.1, 1.8, M.glass, 0, 4, -1.2)); for (const sx of [-1.1, 1.1]) g.add(cyl(0.15, 0.15, 4, M.steel, sx, 3, 1.2))
  g.add(box(2, 0.15, 0.15, M.steel, 0, 1, 1.2)); for (const sx of [-0.6, 0.6]) g.add(box(0.2, 0.12, 2, M.steel, sx, 0.4, 2.4))
  for (const [wx, wz] of [[-1.1, -1.8], [1.1, -1.8], [-1.1, 1], [1.1, 1]]) { const w = cyl(0.6, 0.6, 0.4, M.rubber, wx, 0.6, wz); w.rotation.z = Math.PI / 2; g.add(w) } return g
}
function pPalletRack(M) {
  const g = grp(); for (const sx of [-3, 3]) for (const sz of [-1, 1]) g.add(box(0.25, 8, 0.25, M.amber, sx, 4, sz))
  for (let lvl = 0; lvl < 3; lvl++) { g.add(box(6.2, 0.2, 0.25, M.amber, 0, 2.5 + lvl * 2.5, -1)); g.add(box(6.2, 0.2, 0.25, M.amber, 0, 2.5 + lvl * 2.5, 1))
    for (let p = -1; p <= 1; p++) g.add(box(1.6, 1.4, 1.8, M.wood, p * 2, 3.3 + lvl * 2.5, 0)) } return g
}
function pWorkbench(M) {
  const g = grp(); g.add(box(5, 0.3, 2.4, M.steel, 0, 2.4, 0)); legs4(M, 5, 2.4, 2.4, M.dark).forEach((l) => g.add(l))
  g.add(box(5, 2, 0.1, M.dark, 0, 3.6, -1.1)); for (let i = 0; i < 4; i++) g.add(box(0.1, 0.6, 0.05, M.steel, -1.8 + i * 1.2, 3.6, -1)); return g
}
function pStorageTank(M) {
  const g = grp(); g.add(cyl(1.8, 1.8, 6, M.metal, 0, 3.5, 0)); const dome = new THREE.Mesh(new THREE.SphereGeometry(1.8, 18, 10, 0, Math.PI * 2, 0, Math.PI / 2), M.metal); dome.position.y = 6.5; g.add(dome)
  for (const a of [0, 1, 2]) { const ang = a * 2.1; g.add(box(0.2, 1, 0.2, M.steel, Math.cos(ang) * 1.7, 0.5, Math.sin(ang) * 1.7)) } g.add(cyl(0.25, 0.25, 1.5, M.steel, 1.8, 1, 0)); return g
}

/* ───────────────────────── BUILDING SERVICES ────────────────────────────── */
function pPDU(M) {
  const g = grp(); g.add(box(1, 5.5, 1, M.dark, 0, 2.75, 0)); for (let i = 0; i < 12; i++) { const l = new THREE.Mesh(new THREE.BoxGeometry(0.5, 0.08, 0.04), M.led(i % 3 ? 0x22d3ee : 0x16a34a)); l.position.set(0, 0.8 + i * 0.38, 0.51); l.userData.blink = true; g.add(l) } return g
}
function pTransformer(M) {
  const g = grp(); g.add(box(3.4, 3, 2.6, M.steel, 0, 1.7, 0)); for (let i = 0; i < 8; i++) g.add(box(0.1, 2.6, 2.6, M.dark, -1.6 + i * 0.45, 1.6, 0))
  for (const sx of [-0.9, 0, 0.9]) { g.add(cyl(0.22, 0.3, 1.2, M.glass, sx, 3.8, 0)); g.add(cyl(0.12, 0.12, 0.4, M.metal, sx, 4.5, 0)) } return g
}
function pSwitchgear(M) {
  const g = grp(); for (let i = 0; i < 3; i++) { g.add(box(1.8, 4, 2, M.metal, -1.9 + i * 1.9, 2, 0)); g.add(box(1.4, 1, 0.05, M.screen, -1.9 + i * 1.9, 3, 1.01)); const l = new THREE.Mesh(new THREE.BoxGeometry(0.5, 0.1, 0.05), M.led(0x16a34a)); l.position.set(-1.9 + i * 1.9, 2.2, 1.01); l.userData.blink = true; g.add(l) } return g
}
function pGenerator(M) {
  const g = grp(); g.add(box(7, 0.5, 3, M.dark, 0, 0.25, 0)); g.add(box(4, 2.4, 2.6, M.amber, -0.5, 1.7, 0))
  g.add(box(2.4, 2.6, 2.4, M.steel, 2.4, 1.8, 0)); for (let i = 0; i < 5; i++) g.add(box(0.06, 2.2, 2.2, M.dark, 1.4 + i * 0.25, 1.8, 0))
  g.add(cyl(0.35, 0.35, 3, M.dark, -2, 3.5, -1)); g.add(box(1.4, 1.6, 0.4, M.dark, 0, 1.6, 1.5)); g.add(box(1.1, 0.8, 0.05, M.screen, 0, 1.9, 1.71)); return g
}
function pBoiler(M) {
  const g = grp(); g.add(cyl(1.4, 1.4, 5, M.steel, 0, 3, 0)); const top = new THREE.Mesh(new THREE.SphereGeometry(1.4, 16, 8, 0, Math.PI * 2, 0, Math.PI / 2), M.steel); top.position.y = 5.5; g.add(top)
  g.add(box(1.2, 1.4, 0.4, M.dark, 0, 1.5, 1.4)); g.add(box(0.9, 0.6, 0.05, M.led(0xf59e0b), 0, 1.7, 1.61)); g.add(cyl(0.2, 0.2, 2, M.steel, 1.4, 4, 0)); return g
}
function pChiller(M) {
  const g = grp(); g.add(box(6, 3, 3, M.metal, 0, 1.7, 0)); for (let i = 0; i < 10; i++) g.add(box(0.05, 2.6, 2.8, M.dark, -2.7 + i * 0.55, 1.6, 0))
  for (const fx of [-1.5, 1.5]) { const fan = grp(); for (let i = 0; i < 4; i++) { const b = box(1.3, 0.06, 0.3, M.steel); b.rotation.y = i * Math.PI / 2; fan.add(b) } fan.position.set(fx, 3.3, 0); fan.userData.spin = 1.1; g.add(fan); const ring = new THREE.Mesh(new THREE.TorusGeometry(1.4, 0.1, 8, 20), M.dark); ring.rotation.x = Math.PI / 2; ring.position.set(fx, 3.3, 0); g.add(ring) } return g
}
function pCoolingTower(M) {
  const g = grp(); g.add(box(4, 4, 4, M.white, 0, 2, 0)); for (let i = 0; i < 8; i++) g.add(box(3.6, 0.12, 0.06, M.dark, 0, 0.8 + i * 0.4, 2.01))
  const fan = grp(); for (let i = 0; i < 5; i++) { const b = box(2.6, 0.08, 0.5, M.steel); b.rotation.y = i * (Math.PI * 2 / 5); fan.add(b) } fan.position.y = 4.3; fan.userData.spin = 1.4; g.add(fan)
  g.add(cyl(2, 2, 0.4, M.dark, 0, 4.1, 0, 24)); return g
}
function pPump(M) {
  const g = grp(); g.add(box(2.4, 0.4, 1.4, M.dark, 0, 0.2, 0)); g.add(cyl(0.8, 0.8, 1.8, M.steel, -0.6, 1.1, 0, 18)); g.children[1].rotation.z = Math.PI / 2
  const motor = cyl(0.7, 0.7, 1.6, M.amber, 0.8, 1.1, 0); motor.rotation.z = Math.PI / 2; g.add(motor); g.add(cyl(0.25, 0.25, 1, M.steel, -0.6, 2, 0)); return g
}
function pWaterTank(M) {
  const g = grp(); g.add(cyl(2.2, 2.2, 5, M.white, 0, 3, 0, 24)); const dome = new THREE.Mesh(new THREE.SphereGeometry(2.2, 20, 10, 0, Math.PI * 2, 0, Math.PI / 2), M.white); dome.position.y = 5.5; g.add(dome)
  for (let i = 0; i < 6; i++) g.add(box(0.12, 0.08, 0.3, M.steel, 2.1, 0.6 + i * 0.7, 0)); g.add(cyl(0.3, 0.3, 1, M.steel, 0, 6.4, 0)); return g
}
function pValve(M) {
  const g = grp(); const pipe = cyl(0.4, 0.4, 3, M.steel, 0, 1, 0); pipe.rotation.z = Math.PI / 2; g.add(pipe)
  g.add(box(0.9, 0.9, 0.9, M.amber, 0, 1, 0)); const wheel = new THREE.Mesh(new THREE.TorusGeometry(0.6, 0.1, 8, 18), M.red); wheel.position.set(0, 1.8, 0); g.add(wheel); g.add(cyl(0.08, 0.08, 0.8, M.steel, 0, 1.4, 0)); return g
}
function pSolarPanel(M) {
  const g = grp(); const panel = box(5, 0.15, 3, M.solar, 0, 2.4, 0); panel.rotation.x = -0.4; g.add(panel)
  for (let i = 0; i < 4; i++) { const l = box(5, 0.02, 0.05, M.dark, 0, 2.42 + i * 0.0, -1.2 + i * 0.8); l.rotation.x = -0.4; g.add(l) }
  g.add(box(0.15, 2.4, 0.15, M.steel, -2, 1.2, 0.8)); g.add(box(0.15, 1.4, 0.15, M.steel, 2, 0.7, -0.6)); return g
}
function pBattery(M) {
  const g = grp(); g.add(box(4, 4.5, 2, M.dark, 0, 2.25, 0)); for (let r = 0; r < 4; r++) for (let c = 0; c < 3; c++) { g.add(box(1.1, 0.8, 0.1, M.steel, -1.2 + c * 1.2, 0.8 + r * 1, 1.01)); const l = new THREE.Mesh(new THREE.BoxGeometry(0.2, 0.1, 0.05), M.led(0x16a34a)); l.position.set(-1.6 + c * 1.2, 0.8 + r * 1, 1.06); l.userData.blink = true; g.add(l) } return g
}
function pEVCharger(M) {
  const g = grp(); g.add(box(1.2, 4, 0.8, M.white, 0, 2, 0)); g.add(box(1, 1.4, 0.06, M.screen, 0, 3, 0.41))
  const l = new THREE.Mesh(new THREE.BoxGeometry(0.8, 0.15, 0.05), M.led(0x16a34a)); l.position.set(0, 1.6, 0.41); l.userData.blink = true; g.add(l); g.add(box(0.5, 0.6, 0.4, M.dark, 0.7, 1.4, 0.3)); return g
}
function pFireExtinguisher(M) {
  const g = grp(); g.add(cyl(0.4, 0.4, 1.6, M.red, 0, 0.9, 0)); const top = new THREE.Mesh(new THREE.SphereGeometry(0.4, 12, 8, 0, Math.PI * 2, 0, Math.PI / 2), M.red); top.position.y = 1.7; g.add(top)
  g.add(box(0.3, 0.25, 0.3, M.dark, 0, 1.95, 0)); g.add(cyl(0.06, 0.06, 1.2, M.dark, 0.35, 1, 0)); return g
}
function pFirePanel(M) {
  const g = grp(); g.add(box(2, 3, 0.6, M.red, 0, 2.5, 0)); g.add(box(1.6, 1, 0.05, M.screen, 0, 3.2, 0.31))
  for (let i = 0; i < 4; i++) { const l = new THREE.Mesh(new THREE.BoxGeometry(0.2, 0.15, 0.05), M.led(i ? 0x16a34a : 0xe11d48)); l.position.set(-0.5 + i * 0.35, 2.3, 0.31); l.userData.blink = i === 0; g.add(l) } return g
}
function pSmokeDetector(M) { const g = grp(); g.add(cyl(0.7, 0.6, 0.3, M.white, 0, 4.5, 0, 18)); const l = new THREE.Mesh(new THREE.SphereGeometry(0.12, 10, 10), M.led(0x16a34a)); l.position.set(0, 4.32, 0.3); l.userData.blink = true; g.add(l); return g }
function pSprinkler(M) { const g = grp(); g.add(cyl(0.12, 0.12, 0.6, M.chrome, 0, 4.5, 0)); g.add(cyl(0.35, 0.2, 0.25, M.chrome, 0, 4.15, 0, 12)); g.add(new THREE.Mesh(new THREE.SphereGeometry(0.14, 10, 10), M.red).translateY(4)); return g }
function pExitSign(M) { const g = grp(); g.add(box(2, 0.9, 0.2, new THREE.MeshStandardMaterial({ color: 0x16a34a, emissive: 0x16a34a, emissiveIntensity: 1.2 }), 0, 4.6, 0)); g.add(box(1.6, 0.5, 0.06, M.white, 0, 4.6, 0.12)); return g }
function pElevator(M) {
  const g = grp(); g.add(box(4.4, 6, 4.4, M.steel, 0, 3, 0)); g.add(box(2.6, 5, 0.1, M.dark, 0, 2.6, 2.2)); g.add(box(0.08, 5, 0.1, M.chrome, 0, 2.6, 2.25))
  g.add(box(1, 0.5, 0.05, M.led(0xf59e0b), 0, 5.4, 2.23)); return g
}
function pEscalator(M) {
  const g = grp(); const ramp = box(3, 0.6, 8, M.dark, 0, 2.5, 0); ramp.rotation.x = -0.5; g.add(ramp)
  for (let i = 0; i < 8; i++) { const s = box(3, 0.4, 0.9, M.steel, 0, 1 + i * 0.5, -3 + i * 0.85); g.add(s) }
  for (const sx of [-1.6, 1.6]) { const rail = box(0.2, 0.3, 8.5, M.glass, sx, 3.4, 0); rail.rotation.x = -0.5; g.add(rail) } return g
}

/* ───────────────────────── RESIDENTIAL FIXTURES + FURNITURE ──────────────── */
function pSplitAC(M) {
  const g = grp(); g.add(box(3.4, 1, 0.8, M.white, 0, 0, 0))
  g.add(box(3.0, 0.14, 0.06, M.dark, 0, -0.34, 0.41))
  const l = new THREE.Mesh(new THREE.BoxGeometry(0.25, 0.1, 0.04), M.led(0x22d3ee)); l.position.set(1.2, 0.2, 0.41); l.userData.blink = true; g.add(l); return g
}
function pCeilingLight(M) {
  const g = grp(); g.add(box(2.6, 0.16, 2.6, M.white, 0, 0, 0)); g.add(box(2.3, 0.06, 2.3, M.warm, 0, -0.1, 0)); return g
}
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
  for (const [x, z] of [[-0.5, -0.4], [0.5, -0.4], [-0.5, 0.4], [0.5, 0.4]]) { const b = cyl(0.32, 0.32, 0.06, M.rubber, x, 2.5, z); g.add(b) }
  g.add(box(2.0, 1.2, 0.05, M.glass, 0, 1.1, 1.01)); return g
}
function pSink(M) {
  const g = grp(); g.add(box(2.2, 1.9, 1.2, M.white, 0, 0.95, 0)); g.add(box(1.6, 0.16, 0.9, M.porcelain, 0, 1.95, 0))
  g.add(box(1.2, 0.12, 0.6, M.dark, 0, 1.9, 0)); const f = cyl(0.07, 0.07, 0.9, M.chrome, 0, 2.3, -0.35); g.add(f); return g
}
function pToilet(M) {
  const g = grp(); g.add(cyl(0.85, 0.7, 1.1, M.porcelain, 0, 0.6, 0.1, 18)); g.add(box(1.4, 0.25, 1.3, M.porcelain, 0, 1.2, 0.1))
  g.add(box(1.5, 1.6, 0.6, M.porcelain, 0, 1.6, -0.7)); return g
}
function pShower(M) {
  const g = grp(); g.add(box(3, 0.2, 3, M.porcelain, 0, 0.1, 0))
  g.add(box(0.1, 6, 3, M.glass, 1.5, 3, 0)); g.add(box(3, 6, 0.1, M.glass, 0, 3, -1.5))
  const head = cyl(0.4, 0.4, 0.15, M.chrome, 1.0, 5.5, -1.2); g.add(head); return g
}
function pBathtub(M) {
  const g = grp(); g.add(box(5, 1.8, 2.6, M.porcelain, 0, 0.9, 0)); g.add(box(4.4, 1.0, 2.0, new THREE.MeshStandardMaterial({ color: 0xdfe6ee, roughness: 0.2 }), 0, 1.3, 0))
  const f = cyl(0.07, 0.07, 0.8, M.chrome, -2.2, 1.6, 0); g.add(f); return g
}
function pCar(M) {
  const g = grp(); const body = new THREE.MeshStandardMaterial({ color: 0xcfd3da, roughness: 0.35, metalness: 0.6, envMapIntensity: 1.2 })
  g.add(box(4.6, 1.5, 9, body, 0, 1.4, 0)); g.add(box(4.0, 1.4, 4.6, M.glass, 0, 2.55, -0.4))
  for (const [x, z] of [[-2.0, -2.8], [2.0, -2.8], [-2.0, 2.8], [2.0, 2.8]]) { const w = cyl(0.95, 0.95, 0.5, M.rubber, x, 0.9, z); w.rotation.z = Math.PI / 2; g.add(w) }
  g.add(box(0.5, 0.4, 0.2, M.led(0xffe9b0), 0, 1.5, 4.5)); return g
}

const PROP_FOR = {
  // data center / IT
  rack: pRack, smallrack: pSmallRack, network: pNetwork, pdu: pPDU,
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
  light: pLight,
  // hospital
  mri: pMRI, ctscanner: pCTScanner, xray: pXray, gas: pGas, laf: pLAF, nurse: pNurse,
  fridge: pFridge, hospitalbed: pHospitalBed, bed: pBed, stretcher: pStretcher,
  wheelchair: pWheelchair, ivstand: pIVStand, patientmonitor: pPatientMonitor,
  operatingtable: pOperatingTable,
  // factory / logistics
  robot: pRobot, conveyor: pConveyor, cnc: pCNC, welder: pWelder, press: pPress,
  forklift: pForklift, palletrack: pPalletRack, workbench: pWorkbench,
  // office / furniture / decor
  desk: pDesk, chair: pChair, stool: pStool, sofa: pSofa, table: pTable,
  coffeetable: pCoffeeTable, bookshelf: pBookshelf, cabinet: pCabinet, plant: pPlant,
  watercooler: pWaterCooler, reception: pReception, locker: pLocker,
  whiteboard: pWhiteboard, tv: pTV, monitor: (M) => pMonitor(M), printer: pPrinter,
  // residential fixtures + furniture
  splitac: pSplitAC, ceilinglight: pCeilingLight, nightstand: pNightstand, rug: pRug,
  kitchen: pKitchen, stove: pStove, sink: pSink, toilet: pToilet, shower: pShower,
  bathtub: pBathtub, car: pCar,
  box: pBox,
}

/** Build a prop group for a scene-graph prop key. */
export function buildProp(key, M) {
  const fn = PROP_FOR[key] || pBox
  return fn(M)
}

/** Build a tree/shrub for site greenery (scene 'green' geometry). */
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
