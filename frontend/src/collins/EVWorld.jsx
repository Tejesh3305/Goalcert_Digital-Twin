// EVWorld.jsx — React mount for the living energy-site twin (scene/evworld.js).
// Streams the sim/live EV telemetry frame onto the 3-D world (transformer colour,
// BESS fill, charger faults, power-flow intensity) and wires the inspector's
// "Ask AI" to the orchestrator's asset-status agent.
import React, { useEffect, useRef } from 'react'
import { createEVWorld } from './scene/evworld.js'
import api from './api'

export default function EVWorld({ live, machine = 'GoalCert Energy Site', focusId = null, onPick, height = 460 }) {
  const hostRef = useRef(null)
  const worldRef = useRef(null)
  const pickRef = useRef(onPick); pickRef.current = onPick

  useEffect(() => {
    if (!hostRef.current) return
    const onAskAI = async (asset) => {
      const r = await api.assetStatus({ machine, domain: 'ev-network', asset })
      return r?.status || 'No AI status returned.'
    }
    let world
    try {
      world = createEVWorld(hostRef.current, {
        onAskAI,
        onPick: (id, x, y) => (pickRef.current ? pickRef.current(id, x, y) : false),
      })
    } catch (e) { console.error('EV world failed', e) }
    worldRef.current = world
    return () => { try { world && world.dispose() } catch {} worldRef.current = null }
  }, [machine])

  // stream telemetry onto the world
  useEffect(() => {
    const w = worldRef.current
    if (w && w.update) w.update(live || {})
  }, [live])

  // explainable highlight — outline + camera-follow the asset a repair/answer is about
  useEffect(() => {
    const w = worldRef.current
    if (w && w.focusAsset) { try { w.focusAsset(focusId || null) } catch { /* ignore */ } }
  }, [focusId])

  return <div ref={hostRef} className="hero3d scene3d-host evw-host"
    style={{ height, position: 'relative', padding: 0, overflow: 'hidden' }} />
}
