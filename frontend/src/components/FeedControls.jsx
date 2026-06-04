import { useState } from 'react'
import { usePolling } from '../hooks/useApi'
import { useTwin } from '../context/TwinContext'
import { useToast } from '../context/ToastContext'
import api from '../api/client'

/** Start/Stop the telemetry feed for the active twin, with a live status readout.
 *
 *  Two engines:
 *   - dynamics : the generative, relationship-coupled physics engine (the core's
 *                real behaviour layer) — every entity produces telemetry from its
 *                bound archetype, monitoring is driven by the binding layer.
 *   - scripted : the original canned profiles (back-compat).
 *  Findings stream back through the graph + event bus either way. */
export default function FeedControls() {
  const { activeTenant } = useTwin()
  const toast = useToast()
  const [mode, setMode] = useState('dynamics')
  const { data: status, refetch } = usePolling(() => api.feedStatus(), 1500, [])

  const running = status?.running
  const boundTenant = status?.tenant
  const mismatch = running && boundTenant && boundTenant !== activeTenant
  const runMode = status?.mode || 'scripted'

  const start = async () => {
    try {
      await api.startFeed(activeTenant, mode)
      toast.ok(`Feed started (${mode})`,
        mode === 'dynamics'
          ? `Generative engine driving ${activeTenant}`
          : `Streaming canned telemetry into ${activeTenant}`)
      refetch()
    } catch (e) { toast.err('Could not start feed', e.message) }
  }
  const stop = async () => {
    try { await api.stopFeed(); toast.info('Feed stopped'); refetch() }
    catch (e) { toast.err('Could not stop feed', e.message) }
  }

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      {status && (
        <div className="topbar-stat" title="live feed status">
          {running
            ? <><span className="status-dot" style={{ display: 'inline-block', marginRight: 5 }} />
                <span className="pill pill-blue" style={{ marginRight: 6 }}>{runMode}</span>
                {status.samples_processed || 0} samples · {status.findings_emitted || 0} findings</>
            : <span className="muted">feed idle</span>}
        </div>
      )}
      {mismatch && (
        <span className="demo-note" title={`Feed is bound to ${boundTenant}`}>
          feed on {boundTenant}
        </span>
      )}
      {running
        ? <button className="btn btn-danger" onClick={stop}><i className="ti ti-player-stop" /> Stop Feed</button>
        : <>
            <select className="input" value={mode} onChange={(e) => setMode(e.target.value)}
                    title="Telemetry engine" disabled={!activeTenant}
                    style={{ height: 30, padding: '0 8px', fontSize: 12 }}>
              <option value="dynamics">Dynamics (physics)</option>
              <option value="scripted">Scripted</option>
            </select>
            <button className="btn btn-primary" onClick={start} disabled={!activeTenant}>
              <i className="ti ti-player-play" /> Start Feed
            </button>
          </>}
    </div>
  )
}
