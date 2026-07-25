/**
 * NarrationStrip — an on-demand AI co-pilot reading of the live twin, plus a
 * predictive alert when an operating limit is projected to be crossed.
 *
 * This used to monitor continuously: it fetched on mount and re-polled the
 * co-pilot on a slow interval. It no longer does — nothing is fetched until the
 * user explicitly asks for a reading. Each call is a real model call on the
 * current frame, so keeping it click-driven stops it burning tokens in the
 * background (the physics moves far slower than any useful poll rate anyway).
 *
 * Once invoked, the alert renders above the narration and only appears when
 * there is genuinely something to warn about (the agent returns nothing when no
 * limit is projected to be crossed). A twin switch retires the reading so the
 * co-pilot goes back to waiting rather than showing stale narration.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { AiBadge } from './AiBadge'
import api from '../../api/client'

export default function NarrationStrip({ tenant, machine, horizon = '6 hours' }) {
  const [active, setActive] = useState(false)
  const [narration, setNarration] = useState(null)
  const [ai, setAi] = useState(null)
  const [alert, setAlert] = useState(null)
  const [loading, setLoading] = useState(false)
  const alive = useRef(true)

  // Reset alive in the effect body — a ref survives StrictMode's dev remount,
  // so relying on useRef(true) alone leaves this false after the first cleanup
  // and every setNarration is then skipped. See useAgent.js for the same fix.
  useEffect(() => {
    alive.current = true
    return () => { alive.current = false }
  }, [])

  // Switching twins retires the previous reading: the co-pilot goes back to
  // waiting to be asked, rather than showing another twin's stale narration.
  useEffect(() => {
    setActive(false); setNarration(null); setAi(null); setAlert(null)
  }, [tenant, machine])

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

  const activate = () => { setActive(true); tick() }

  if (!tenant) return null

  // Idle: nothing fetched yet. Offer a trigger, not a background monitor.
  if (!active) {
    return (
      <div className="narration-strip">
        <div className="narration-row">
          <div className="narration-icon"><i className="ti ti-eye" /></div>
          <div className="narration-body">
            <div className="narration-label">AI co-pilot</div>
            <div className="narration-text hint">
              Ask the co-pilot to read this twin’s live telemetry.
            </div>
          </div>
          <button className="btn btn-primary narration-refresh" onClick={activate}
                  title="Read the live telemetry now">
            <i className="ti ti-eye" /> Read now
          </button>
        </div>
      </div>
    )
  }

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
