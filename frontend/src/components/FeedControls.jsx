import { usePolling } from '../hooks/useApi'
import { useTwin } from '../context/TwinContext'
import { useToast } from '../context/ToastContext'
import api from '../api/client'

/** Start/Stop the simulated telemetry feed for the active twin, with a live
 *  status readout. The feed runs server-side and targets the twin's seeded
 *  asset; findings stream back through the graph + event bus. */
export default function FeedControls() {
  const { activeTenant } = useTwin()
  const toast = useToast()
  const { data: status, refetch } = usePolling(() => api.feedStatus(), 1500, [])

  const running = status?.running
  // The feed is process-wide (one loop). Show which tenant it's bound to.
  const boundTenant = status?.tenant
  const mismatch = running && boundTenant && boundTenant !== activeTenant

  const start = async () => {
    try {
      await api.startFeed(activeTenant)
      toast.ok('Feed started', `Streaming telemetry into ${activeTenant}`)
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
                {status.samples_processed || 0} samples · {status.findings_emitted || 0} findings
                {status.latest_value != null && <> · {Number(status.latest_value).toFixed(1)}°C</>}</>
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
        : <button className="btn btn-primary" onClick={start} disabled={!activeTenant}>
            <i className="ti ti-player-play" /> Start Feed
          </button>}
    </div>
  )
}
