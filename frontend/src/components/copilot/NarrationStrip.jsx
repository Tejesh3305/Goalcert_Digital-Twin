/**
 * NarrationStrip — the AI co-pilot's running commentary on the live twin, plus
 * a predictive alert when an operating limit is projected to be crossed.
 *
 * Narration is polled rather than streamed: each call is a fresh observation on
 * the current frame, and the interval is deliberately slow (60s default). This
 * is a real model call per tick — polling it every few seconds would burn tokens
 * for no operational benefit, since the physics moves far slower than that.
 *
 * The alert is the part that matters operationally, so it renders above the
 * narration and only appears when there is genuinely something to warn about
 * (the agent returns nothing when no limit is projected to be crossed).
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { AiBadge } from './AiBadge'
import api from '../../api/client'

export default function NarrationStrip({ tenant, machine, intervalMs = 60000, horizon = '6 hours' }) {
  const [narration, setNarration] = useState(null)
  const [ai, setAi] = useState(null)
  const [alert, setAlert] = useState(null)
  const [loading, setLoading] = useState(false)
  const alive = useRef(true)

  // Reset alive in the effect body — a ref survives StrictMode's dev
  // remount, so relying on useRef(true) alone leaves this false after the
  // first cleanup and every setNarration is then skipped (strip stuck on
  // "Reading the live telemetry…"). See useAgent.js for the same fix.
  useEffect(() => {
    alive.current = true
    return () => { alive.current = false }
  }, [])

  const tick = useCallback(async () => {
    if (!tenant) return
    setLoading(true)
    try {
      const [n, a] = await Promise.allSettled([
        api.copilot.narrateLive(tenant, machine),
        api.copilot.predictAlert({ tenant, machine, horizon_label: horizon }),
      ])
      if (!alive.current) return
      if (n.status === 'fulfilled') { setNarration(n.value.narration); setAi(n.value.ai) }
      if (a.status === 'fulfilled') setAlert(a.value.alert || null)
    } finally {
      if (alive.current) setLoading(false)
    }
  }, [tenant, machine, horizon])

  useEffect(() => {
    setNarration(null); setAlert(null)
    tick()
    const id = setInterval(tick, intervalMs)
    return () => clearInterval(id)
  }, [tick, intervalMs])

  if (!tenant) return null

  return (
    <div className="narration-strip">
      {alert && (
        <div className="narration-alert">
          <i className="ti ti-alert-triangle" />
          <div>
            <div className="narration-alert-label">Predictive alert</div>
            {alert}
          </div>
        </div>
      )}

      <div className="narration-row">
        <div className="narration-icon"><i className="ti ti-eye" /></div>
        <div className="narration-body">
          <div className="narration-label">
            AI co-pilot
            {ai && <AiBadge ai={ai} />}
          </div>
          <div className="narration-text">
            {narration || (loading ? 'Reading the live telemetry…' : 'No observation yet.')}
          </div>
        </div>
        <button className="btn btn-ghost narration-refresh" onClick={tick} disabled={loading}
                title="Take a fresh reading">
          {loading ? <span className="spinner" /> : <i className="ti ti-refresh" />}
        </button>
      </div>
    </div>
  )
}
