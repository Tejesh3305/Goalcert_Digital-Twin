/**
 * materials.js — the PBR material library, ported from demo.html's makeMats().
 *
 * Same colours / roughness / metalness / emissive values as the demo so the
 * reconstructed building reads with the identical archviz feel. Materials are
 * plain THREE.MeshStandardMaterial instances, shared across the scene; drei's
 * <Environment> supplies the image-based lighting (envMap) automatically.
 */
import * as THREE from 'three'

export function makeMats() {
  return {
    concrete: new THREE.MeshStandardMaterial({ color: 0x70747e, roughness: 0.96, metalness: 0 }),
    slab: new THREE.MeshStandardMaterial({ color: 0xc9ccd4, roughness: 0.85, metalness: 0.05 }),
    metal: new THREE.MeshStandardMaterial({ color: 0xa7adb8, roughness: 0.34, metalness: 0.95, envMapIntensity: 1 }),
    steel: new THREE.MeshStandardMaterial({ color: 0x6b7280, roughness: 0.45, metalness: 0.85 }),
    dark: new THREE.MeshStandardMaterial({ color: 0x20232c, roughness: 0.5, metalness: 0.6 }),
    rack: new THREE.MeshStandardMaterial({ color: 0x17181e, roughness: 0.55, metalness: 0.4 }),
    white: new THREE.MeshStandardMaterial({ color: 0xeef1f5, roughness: 0.6, metalness: 0.05 }),
    plastic: new THREE.MeshStandardMaterial({ color: 0x2a2e38, roughness: 0.7, metalness: 0.1 }),
    rubber: new THREE.MeshStandardMaterial({ color: 0x16181d, roughness: 0.95, metalness: 0 }),
    glass: new THREE.MeshStandardMaterial({ color: 0x9fc2e8, roughness: 0.05, metalness: 0, transparent: true, opacity: 0.22, envMapIntensity: 1.6 }),
    wall: new THREE.MeshStandardMaterial({ color: 0xe7e9f2, roughness: 0.92, metalness: 0.02, side: THREE.DoubleSide }),
    accent: new THREE.MeshStandardMaterial({ color: 0x7c3aed, roughness: 0.4, metalness: 0.3, emissive: 0x3b1378, emissiveIntensity: 0.4 }),
    amber: new THREE.MeshStandardMaterial({ color: 0xf59e0b, roughness: 0.5, metalness: 0.4 }),
    warm: new THREE.MeshStandardMaterial({ color: 0xfff1d6, emissive: 0xffcf8a, emissiveIntensity: 1.6 }),
    cool: new THREE.MeshStandardMaterial({ color: 0xeaf3ff, emissive: 0x9ec9ff, emissiveIntensity: 1.4 }),
    screen: new THREE.MeshStandardMaterial({ color: 0x0a1530, emissive: 0x2f6bd6, emissiveIntensity: 0.9 }),
    floorPad: new THREE.MeshStandardMaterial({ color: 0x2b2f44, roughness: 0.8, metalness: 0.1, transparent: true, opacity: 0.5 }),
    // ── furniture / decor / fit-out ──
    wood: new THREE.MeshStandardMaterial({ color: 0x9c6b3f, roughness: 0.78, metalness: 0.02 }),
    woodDark: new THREE.MeshStandardMaterial({ color: 0x5a3d28, roughness: 0.7, metalness: 0.02 }),
    fabric: new THREE.MeshStandardMaterial({ color: 0x707688, roughness: 0.96, metalness: 0 }),
    fabricBlue: new THREE.MeshStandardMaterial({ color: 0x3d5a8c, roughness: 0.95, metalness: 0 }),
    leather: new THREE.MeshStandardMaterial({ color: 0x3a2a22, roughness: 0.55, metalness: 0.05 }),
    foliage: new THREE.MeshStandardMaterial({ color: 0x2f7d4f, roughness: 0.85, metalness: 0 }),
    cushion: new THREE.MeshStandardMaterial({ color: 0xd8dbe6, roughness: 0.9, metalness: 0 }),
    chrome: new THREE.MeshStandardMaterial({ color: 0xc8ccd6, roughness: 0.2, metalness: 0.95, envMapIntensity: 1.2 }),
    mattress: new THREE.MeshStandardMaterial({ color: 0xeef1f6, roughness: 0.85, metalness: 0 }),
    red: new THREE.MeshStandardMaterial({ color: 0xc0392b, roughness: 0.5, metalness: 0.3 }),
    solar: new THREE.MeshStandardMaterial({ color: 0x14213d, roughness: 0.25, metalness: 0.4, emissive: 0x0a1a3a, emissiveIntensity: 0.25 }),
    // ── architectural shell + site ──
    // Warm neutral greige — reads far better than flat white under the scene's
    // hemisphere + directional lighting.
    plaster: new THREE.MeshStandardMaterial({ color: 0xe4ddcf, roughness: 0.88, metalness: 0.02, side: THREE.DoubleSide }),
    // See-through wall mode: frosted glazing that still reads as a wall plane
    // (more opaque + more neutral than windowGlass, which is for real windows).
    wallGlass: new THREE.MeshStandardMaterial({ color: 0xcdd8e4, roughness: 0.08, metalness: 0.1, transparent: true, opacity: 0.34, side: THREE.DoubleSide, envMapIntensity: 1.4 }),
    floorWood: new THREE.MeshStandardMaterial({ color: 0xb9895a, roughness: 0.55, metalness: 0.04 }),
    floorTile: new THREE.MeshStandardMaterial({ color: 0xd9dde3, roughness: 0.28, metalness: 0.06 }),
    floorConcrete: new THREE.MeshStandardMaterial({ color: 0x9aa0a8, roughness: 0.9, metalness: 0.02 }),
    floorStone: new THREE.MeshStandardMaterial({ color: 0xbab6ad, roughness: 0.82, metalness: 0.02 }),
    windowGlass: new THREE.MeshStandardMaterial({ color: 0x2b3a4f, roughness: 0.05, metalness: 0.1, transparent: true, opacity: 0.45, envMapIntensity: 1.6 }),
    roofMat: new THREE.MeshStandardMaterial({ color: 0x5b616b, roughness: 0.85, metalness: 0.05 }),
    grass: new THREE.MeshStandardMaterial({ color: 0x4f7c3a, roughness: 1, metalness: 0 }),
    asphalt: new THREE.MeshStandardMaterial({ color: 0x6f7480, roughness: 0.85, metalness: 0.05 }),
    trunk: new THREE.MeshStandardMaterial({ color: 0x6b4a2f, roughness: 0.9 }),
    porcelain: new THREE.MeshStandardMaterial({ color: 0xf4f6f9, roughness: 0.25, metalness: 0.05 }),
    // medical / hospital
    medWhite: new THREE.MeshStandardMaterial({ color: 0xf0f4f8, roughness: 0.35, metalness: 0.08 }),
    medBlue: new THREE.MeshStandardMaterial({ color: 0x6b9fb5, roughness: 0.3, metalness: 0.5 }),
    stainless: new THREE.MeshStandardMaterial({ color: 0xd0d4da, roughness: 0.18, metalness: 0.92, envMapIntensity: 1.4 }),
    ivFluid: new THREE.MeshStandardMaterial({ color: 0xd4eaf7, roughness: 0.1, metalness: 0, transparent: true, opacity: 0.75 }),
    ivTube: new THREE.MeshStandardMaterial({ color: 0xa8d0e8, roughness: 0.3, metalness: 0, transparent: true, opacity: 0.8 }),
    // data center
    rackBlack: new THREE.MeshStandardMaterial({ color: 0x111318, roughness: 0.6, metalness: 0.5 }),
    serverFace: new THREE.MeshStandardMaterial({ color: 0x18191f, roughness: 0.55, metalness: 0.5 }),
    // generic
    orange: new THREE.MeshStandardMaterial({ color: 0xe67e22, roughness: 0.5, metalness: 0.2 }),
    led: (c) => new THREE.MeshStandardMaterial({ color: c, emissive: c, emissiveIntensity: 1.8 }),
  }
}

// Floor material lookup by room type key (from the scene node's `material`).
export function floorMaterial(M, key) {
  return { wood: M.floorWood, tile: M.floorTile, concrete: M.floorConcrete,
           stone: M.floorStone }[key] || M.floorWood
}

// Severity → colour, matching the app's finding palette.
export const STATUS_COLOR = {
  ok: 0x16a34a,
  warn: 0xf59e0b,
  crit: 0xf43f5e,
  info: 0x22d3ee,
}

// Clinical sector → colour (floor tint + HUD legend), so a building reads as
// organised into departments at a glance. Keys match room.sector from the
// hospital layout generator.
export const SECTOR_COLOR = {
  Emergency: 0xef4444,
  Surgical: 0x3b82f6,
  'Diagnostic Imaging': 0x8b5cf6,
  'Critical Care': 0xf97316,
  Wards: 0x10b981,
  'Support Services': 0x64748b,
  Facilities: 0x0891b2,
  Admin: 0xeab308,
  Circulation: 0x9ca3af,
}

export function disposeMats(M) {
  if (!M) return
  Object.values(M).forEach((m) => { if (m && m.dispose) m.dispose() })
}
