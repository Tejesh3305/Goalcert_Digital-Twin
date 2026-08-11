/**
 * labels.jsx — canvas-texture text labels (no external font fetch, so the
 * scene renders identically offline). Used for door signs, floor room names
 * and debug measurements.
 */
import { useMemo } from 'react'
import * as THREE from 'three'

export function makeLabelTexture(text, { fg = '#2b3644', bg = null, px = 72 } = {}) {
  const canvas = document.createElement('canvas')
  canvas.width = 512; canvas.height = 128
  const ctx = canvas.getContext('2d')
  if (bg) { ctx.fillStyle = bg; ctx.fillRect(0, 0, 512, 128) }
  ctx.font = `600 ${px}px system-ui, 'Segoe UI', sans-serif`
  ctx.textAlign = 'center'; ctx.textBaseline = 'middle'
  ctx.fillStyle = fg
  ctx.fillText(text, 256, 68, 492)
  const tex = new THREE.CanvasTexture(canvas)
  tex.anisotropy = 4
  return tex
}

export function Label({ text, width = 2, fg, bg, ...props }) {
  const mat = useMemo(() => new THREE.MeshBasicMaterial({
    map: makeLabelTexture(text, { fg, bg }), transparent: true, depthWrite: false,
  }), [text, fg, bg])
  return (
    <mesh {...props} material={mat}>
      <planeGeometry args={[width, width * 0.25]} />
    </mesh>
  )
}
