import { PanelHeader, Card } from '../components/ui/Card'
import { Empty, ErrorBox } from '../components/ui/States'
import NoTwin from '../components/NoTwin'
import { usePolling } from '../hooks/useApi'
import { useTwin } from '../context/TwinContext'
import { actionColor, localName, shortId, timeOf } from '../lib/format'
import api from '../api/client'

/** Change Log — the tamper-evident, hash-chained mutation ledger for this twin.
 *  Every create/update/delete that passed the Graph Writer appears here. */
export default function Changelog() {
  const { activeTenant } = useTwin()
  const { data, error } = usePolling(
    () => api.changelog(activeTenant, 100), 3000, [activeTenant], { skip: !activeTenant },
  )

  if (!activeTenant) return <NoTwin />

  const events = data?.events || []

  return (
    <div className="panel">
      <PanelHeader
        title="Change Log"
        subtitle={`${data?.count ?? 0} events · SHA-256 hash-chained · tamper-evident`}
      />
      {error && <ErrorBox error={error} />}
      <Card>
        {events.length === 0
          ? <Empty label="No events yet. Every committed mutation is recorded here." icon="ti-history" />
          : (
            <div className="event-list">
              {events.map((e) => (
                <div key={e.event_id} className="event-item">
                  <div className="event-icon" style={{ background: `${actionColor(e.action)}22`, color: actionColor(e.action) }}>
                    <i className={`ti ${{ create: 'ti-plus', update: 'ti-pencil', delete: 'ti-trash' }[e.action] || 'ti-point'}`} />
                  </div>
                  <div className="event-body">
                    <div className="event-title">
                      <span style={{ color: actionColor(e.action), textTransform: 'uppercase', fontWeight: 700, fontSize: 10 }}>{e.action}</span>
                      {' '}<span className="muted">by</span> {e.actor}
                    </div>
                    <div className="event-meta">entity {shortId(e.entity_id, 12)}</div>
                  </div>
                  <span className="event-time">{timeOf(e.ts)}</span>
                </div>
              ))}
            </div>
          )}
      </Card>
    </div>
  )
}
