/**
 * useImagingTwin.js — binds the floor-plan scene to the live `hospital-imaging`
 * machine twin (packs/hospital/imaging.py).
 *
 * Resolves the tenant (the seeded `demo-hospital-imaging`, else the first twin
 * with the right domain), loads the fault catalogue from /twins/domains, polls
 * per-equipment status from /twins/{tenant}/network + /state, and exposes
 * inject/clear actions. Degrades gracefully when the backend is offline: the
 * scene still renders, the fault UI shows an offline hint.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../../../api/client'

export const IMAGING_DOMAIN = 'hospital-imaging'
const DEFAULT_TENANT = 'demo-hospital-imaging'
const POLL_MS = 2000

export default function useImagingTwin() {
  const [tenant, setTenant] = useState(null)
  const [available, setAvailable] = useState(null)   // null probing · true · false
  const [net, setNet] = useState(null)               // per-equipment map
  const [state, setState] = useState(null)           // health + findings + latest
  const [faultInfo, setFaultInfo] = useState({})     // fault id → {label, target, description}
  const [busy, setBusy] = useState(false)
  const dead = useRef(false)

  // ── tenant discovery ──
  useEffect(() => {
    dead.current = false
    ;(async () => {
      const candidates = [DEFAULT_TENANT]
      try {
        const res = await api.listTwins()
        const twins = res?.twins || (Array.isArray(res) ? res : [])
        for (const t of twins) {
          const dom = t.domain || t.template
          const tid = t.tenant_id || t.tenant || t.id
          if (dom === IMAGING_DOMAIN && tid && !candidates.includes(tid)) candidates.push(tid)
        }
      } catch { /* backend may be down — still try the default tenant */ }
      for (const cand of candidates) {
        try {
          await api.twinRuntimeState(cand)
          if (!dead.current) { setTenant(cand); setAvailable(true) }
          return
        } catch { /* try next */ }
      }
      if (!dead.current) setAvailable(false)
    })()
    return () => { dead.current = true }
  }, [])

  // ── fault catalogue (static per session) ──
  useEffect(() => {
    api.machineDomains().then((d) => {
      const dom = (d?.domains || []).find((x) => x.key === IMAGING_DOMAIN)
      if (dom && !dead.current) setFaultInfo(dom.fault_info || {})
    }).catch(() => {})
  }, [])

  // ── polling ──
  const refresh = useCallback(async () => {
    if (!tenant) return
    try {
      const [n, s] = await Promise.all([
        api.twinNetwork(tenant),
        api.twinRuntimeState(tenant),
      ])
      if (!dead.current) { setNet(n); setState(s) }
    } catch { /* transient poll failure — keep last state */ }
  }, [tenant])

  useEffect(() => {
    if (!tenant) return
    refresh()
    const id = setInterval(refresh, POLL_MS)
    return () => clearInterval(id)
  }, [tenant, refresh])

  // ── actions ──
  const inject = useCallback(async (fault, severity = 0.9) => {
    if (!tenant) return
    setBusy(true)
    try {
      await api.twinSimulate(tenant, { fault, severity })
      await refresh()
    } finally { setBusy(false) }
  }, [tenant, refresh])

  const clear = useCallback(() => inject('none', 0), [inject])

  const equipment = net?.equipment || []
  return {
    tenant, available, busy,
    net, state, equipment, faultInfo,
    activeFault: net?.fault && net.fault !== 'none' ? net.fault : null,
    faultTarget: net?.fault_target || null,
    inject, clear, refresh,
  }
}
