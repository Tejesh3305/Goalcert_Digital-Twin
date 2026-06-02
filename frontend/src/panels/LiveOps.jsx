import { PanelHeader, Card } from '../components/ui/Card'
import { Empty } from '../components/ui/States'
import NoTwin from '../components/NoTwin'
import FeedControls from '../components/FeedControls'
import { useEventStream } from '../hooks/useEventStream'
import { usePolling } from '../hooks/useApi'
import { useTwin } from '../context/TwinContext'
import { actionColor, localName, shortId, timeOf } from '../lib/format'
import api from '../api/client'

/** Live Operational Layer — the real-time spine. The left column is the SSE
 *  event stream straight off the event bus (every committed mutation). The
 *  right shows live feed sensors + the most recent incident from the graph. */
export default function LiveOps() {
  const { activeTenant } = useTwin()
  const { events, connected } = useEventStream(activeTenant, { max: 60 })
  const { data: feed } = usePolling(() => api.feedStatus(), 1500, [])
  const { data: incData } = usePolling(
    () => api.listEntities(activeTenant, 'Incident', 5), 3000,
    [activeTenant], { skip: !activeTenant },
  )

  if (!activeTenant) return <NoTwin />

  const incidents = incData?.nodes || []
  const temp = feed?.latest_value
  const tempCls = temp == null ? '' : temp > 28 ? 'sensor-crit' : temp > 25 ? 'sensor-warn' : ''
  const signals = feed?.signals || {}

  return (
    <div className="panel">
      <PanelHeader
        title="Live Operational Layer"
        subtitle={
          <>Event bus stream · {connected
            ? <span style={{ color: 'var(--ok)' }}>● connected</span>
            : <span className="muted">○ connecting…</span>}</>
        }
      >
        <FeedControls />
      </PanelHeader>

      <div className="sensor-grid section-gap">
        <div className={`sensor-card ${tempCls}`}>
          <div className="live-indicator" />
          <div className="sensor-label">Air Temperature</div>
          <div className="sensor-value">{temp != null ? Number(temp).toFixed(1) : '—'}<span className="sensor-unit">°C</span></div>
        </div>
        {signals['cfp:upsSoC'] != null && (
          <div className={`sensor-card ${signals['cfp:upsSoC'] < 50 ? 'sensor-crit' : signals['cfp:upsSoC'] < 90 ? 'sensor-warn' : ''}`}>
            <div className="live-indicator" />
            <div className="sensor-label">UPS State of Charge</div>
            <div className="sensor-value">{Number(signals['cfp:upsSoC']).toFixed(0)}<span className="sensor-unit">%</span></div>
          </div>
        )}
        {signals['cfp:oilTemperature'] != null && (
          <div className={`sensor-card ${signals['cfp:oilTemperature'] > 85 ? 'sensor-crit' : signals['cfp:oilTemperature'] > 75 ? 'sensor-warn' : ''}`}>
            <div className="live-indicator" />
            <div className="sensor-label">Transformer Oil Temp</div>
            <div className="sensor-value">{Number(signals['cfp:oilTemperature']).toFixed(1)}<span className="sensor-unit">°C</span></div>
          </div>
        )}
        {signals['cfp:filterDeltaP'] != null && (
          <div className={`sensor-card ${signals['cfp:filterDeltaP'] > 250 ? 'sensor-crit' : signals['cfp:filterDeltaP'] > 200 ? 'sensor-warn' : ''}`}>
            <div className="live-indicator" />
            <div className="sensor-label">Filter Delta P</div>
            <div className="sensor-value">{Number(signals['cfp:filterDeltaP']).toFixed(0)}<span className="sensor-unit">Pa</span></div>
          </div>
        )}
        {signals['cfp:chillerCOP'] != null && (
          <div className={`sensor-card ${signals['cfp:chillerCOP'] < 3.5 ? 'sensor-warn' : ''}`}>
            <div className="live-indicator" />
            <div className="sensor-label">Chiller COP</div>
            <div className="sensor-value">{Number(signals['cfp:chillerCOP']).toFixed(2)}</div>
          </div>
        )}
        <div className="sensor-card">
          <div className="live-indicator" />
          <div className="sensor-label">Samples Processed</div>
          <div className="sensor-value">{feed?.samples_processed ?? 0}</div>
        </div>
        <div className="sensor-card">
          <div className="live-indicator" />
          <div className="sensor-label">Findings Emitted</div>
          <div className="sensor-value" style={{ color: 'var(--accent-amber)' }}>{feed?.findings_emitted ?? 0}</div>
        </div>
      </div>

      <div className="grid-2">
        <Card title={<><i className="ti ti-bolt" /> Live Mutation Stream</>}>
          {events.length === 0
            ? <Empty label="No events yet. Start the feed or add an asset." icon="ti-wave-sine" />
            : (
              <div className="event-list" style={{ maxHeight: 380, overflowY: 'auto' }}>
                {events.map((ev, i) => (
                  <div key={`${ev.event_id}-${i}`} className="event-item">
                    <div className="event-icon" style={{ background: `${actionColor(ev.action)}22`, color: actionColor(ev.action) }}>
                      <i className={`ti ${{ create: 'ti-plus', update: 'ti-pencil', delete: 'ti-trash' }[ev.action] || 'ti-point'}`} />
                    </div>
                    <div className="event-body">
                      <div className="event-title">
                        <span style={{ color: actionColor(ev.action), textTransform: 'uppercase', fontSize: 10, fontWeight: 700 }}>{ev.action}</span>
                        {' '}{ev.label || localName(ev.entity_type)}
                      </div>
                      <div className="event-meta">{ev.actor} · {shortId(ev.entity_id, 10)}</div>
                    </div>
                    <span className="event-time">{timeOf(ev.ts)}</span>
                  </div>
                ))}
              </div>
            )}
        </Card>

        <Card title={<><i className="ti ti-git-merge" /> Correlated Incidents</>}>
          {incidents.length === 0
            ? <Empty label="No incidents. The diagnosis engine groups findings into incidents as the feed runs." icon="ti-shield-check" />
            : incidents.map((inc) => (
                <div key={inc.id} className="event-item" style={{ borderColor: 'rgba(226,86,78,.25)', background: 'rgba(226,86,78,.04)' }}>
                  <div className="event-icon ev-crit"><i className="ti ti-urgent" /></div>
                  <div className="event-body">
                    <div className="event-title">{inc.displayName || 'Incident'}</div>
                    <div className="event-meta">{inc.status || 'open'} · {shortId(inc.id, 10)}</div>
                  </div>
                  <span className="event-time">{timeOf(inc.createdAt)}</span>
                </div>
              ))}
        </Card>
      </div>
    </div>
  )
}
