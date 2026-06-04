import { useState } from 'react'
import { PanelHeader, Card } from '../components/ui/Card'
import { Loading, ErrorBox, Empty } from '../components/ui/States'
import NoTwin from '../components/NoTwin'
import AddAssetModal from '../components/AddAssetModal'
import { usePolling, useApi } from '../hooks/useApi'
import { useTwin } from '../context/TwinContext'
import { useToast } from '../context/ToastContext'
import { labelMeta, localName, shortId } from '../lib/format'
import api from '../api/client'

const LABELS = ['PhysicalAsset', 'Location', 'Incident', 'Process',
  'Capability', 'Document', 'Actor', 'MobileAsset', 'Observation']

/** Asset Graph — the knowledge graph for the active twin: every entity, its
 *  properties, and its relationships. Plus Add Asset (generic create through
 *  the validated write path). */
export default function Assets() {
  const { activeTenant } = useTwin()
  const [selected, setSelected] = useState(null)
  const [showAdd, setShowAdd] = useState(false)

  // Pull each label's entities and merge into one list.
  const { data, error, refetch } = usePolling(
    async () => {
      const lists = await Promise.all(
        LABELS.map((label) =>
          api.listEntities(activeTenant, label, 100)
            .then((d) => (d.nodes || []).map((n) => ({ ...n, _label: label })))
            .catch(() => [])),
      )
      return lists.flat()
    },
    4000, [activeTenant], { skip: !activeTenant },
  )

  if (!activeTenant) return <NoTwin />

  const entities = data || []

  return (
    <div className="panel">
      <PanelHeader
        title="Asset Knowledge Graph"
        subtitle={`${entities.length} entities · validated against the NextXR ontology`}
      >
        <button className="btn btn-primary" onClick={() => setShowAdd(true)}>
          <i className="ti ti-plus" /> Add Asset
        </button>
      </PanelHeader>

      {error && <ErrorBox error={error} />}
      {!data && !error && <Loading label="Loading entities…" />}

      <div className="grid-2">
        <Card title={`Entities (${entities.length})`}>
          {entities.length === 0
            ? <Empty label="No entities. Add one or start the feed." icon="ti-cube" />
            : entities.map((n) => {
                const m = labelMeta(n._label)
                return (
                  <div key={n.id}
                       className={`entity-row ${selected?.id === n.id ? 'selected' : ''}`}
                       onClick={() => setSelected(n)}>
                    <i className={`ti ${m.icon}`} style={{ color: m.color, fontSize: 15 }} />
                    <span className="entity-name">{n.displayName || localName(n.canonicalType) || '(unnamed)'}</span>
                    <span className="pill pill-surface">{n._label}</span>
                    <span className="entity-id">{shortId(n.id)}</span>
                  </div>
                )
              })}
        </Card>

        <EntityDetail tenant={activeTenant} entity={selected} onChanged={refetch} />
      </div>

      {showAdd && (
        <AddAssetModal
          tenant={activeTenant}
          existing={entities}
          onClose={() => setShowAdd(false)}
          onCreated={refetch}
        />
      )}
    </div>
  )
}

function EntityDetail({ tenant, entity, onChanged }) {
  const toast = useToast()
  const { data } = useApi(
    () => api.getEntity(entity.id, tenant), [entity?.id, tenant], { skip: !entity },
  )

  if (!entity) {
    return <Card title="Details"><Empty label="Select an entity to inspect it." icon="ti-click" /></Card>
  }

  const node = data?.node || entity
  const rels = data?.relationships || []
  const hidden = new Set(['id', 'tenantId', 'canonicalType', 'createdBy', 'changeLogRef'])
  const props = Object.entries(node).filter(([k]) => !hidden.has(k))

  const remove = async () => {
    if (!confirm(`Delete "${node.displayName || entity.id}"?`)) return
    try {
      await api.deleteEntity(entity.id, tenant)
      toast.ok('Entity deleted', node.displayName || entity.id)
      onChanged()
    } catch (e) { toast.err('Delete failed', e.message) }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <Card title={node.displayName || 'Entity'}
            action={<button className="btn btn-ghost" style={{ color: 'var(--accent-red)' }} onClick={remove}>
                      <i className="ti ti-trash" /></button>}>
        <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 8 }}>
          <span className="pill pill-blue">{localName(node.canonicalType)}</span>
          <span className="mono" style={{ marginLeft: 8 }}>{shortId(node.id, 12)}</span>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6, fontSize: 11 }}>
          {props.map(([k, v]) => (
            <div key={k}>
              <span style={{ color: 'var(--muted)' }}>{k}</span><br />
              <b style={{ wordBreak: 'break-word' }}>{String(v)}</b>
            </div>
          ))}
        </div>
      </Card>

      <Card title={`Relationships (${rels.length})`}>
        {rels.length === 0
          ? <div className="muted" style={{ fontSize: 11 }}>No outgoing relationships.</div>
          : rels.map((r, i) => (
              <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11, marginBottom: 5 }}>
                <span className="pill pill-blue">{node.displayName || 'this'}</span>
                <i className="ti ti-arrow-right" style={{ color: 'var(--hint)' }} />
                <span className="pill pill-surface">{r.rel}</span>
                <i className="ti ti-arrow-right" style={{ color: 'var(--hint)' }} />
                <span className="pill pill-surface">{r.node?.displayName || shortId(r.node?.id)}</span>
              </div>
            ))}
      </Card>

      <BehaviorCard canonicalType={node.canonicalType} />
    </div>
  )
}

/** How this entity behaves — its generative dynamics archetype + parameters and
 *  the monitoring rules watching it, resolved from the binding layer (the core's
 *  class→behaviour catalog). This is what makes the twin "alive": the archetype
 *  produces telemetry; the rules raise Findings when it misbehaves. */
function BehaviorCard({ canonicalType }) {
  const { data, loading, error } = useApi(
    () => api.classBehavior(canonicalType), [canonicalType], { skip: !canonicalType },
  )
  if (!canonicalType) return null
  const dyn = data?.dynamics
  const params = dyn?.params || {}
  const mon = data?.monitoring || []
  const sevColor = (s) => ({ critical: 'var(--accent-red)', warning: 'var(--accent-amber)' }[s] || 'var(--accent-blue)')

  return (
    <Card title={<><i className="ti ti-activity-heartbeat" style={{ marginRight: 6 }} />How it behaves</>}>
      {loading && <div className="muted" style={{ fontSize: 11 }}>Resolving behaviour…</div>}
      {error && <div className="muted" style={{ fontSize: 11 }}>No behaviour profile.</div>}
      {data && (
        <div style={{ fontSize: 11 }}>
          <div style={{ marginBottom: 10 }}>
            <span style={{ color: 'var(--muted)' }}>Dynamics archetype</span><br />
            {dyn?.archetype
              ? <span className="pill pill-blue" style={{ marginTop: 3, display: 'inline-block' }}>{dyn.archetype}</span>
              : <span className="muted">none — no generated telemetry</span>}
          </div>

          {Object.keys(params).length > 0 && (
            <div style={{ marginBottom: 10 }}>
              <span style={{ color: 'var(--muted)' }}>Parameters (defaults — overridden by node properties)</span>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 4, marginTop: 4 }}>
                {Object.entries(params).map(([k, v]) => (
                  <div key={k}><span style={{ color: 'var(--hint)' }}>{k}</span> <b>{String(v)}</b></div>
                ))}
              </div>
            </div>
          )}

          <div>
            <span style={{ color: 'var(--muted)' }}>Monitoring ({mon.length})</span>
            {mon.length === 0
              ? <div className="muted" style={{ marginTop: 3 }}>No monitors bound to this class.</div>
              : mon.map((r, i) => (
                  <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 4, flexWrap: 'wrap' }}>
                    <span className="pill" style={{ background: sevColor(r.severity), color: '#fff' }}>{r.kind}</span>
                    <span className="mono" style={{ fontSize: 10 }}>{localName(r.watches)}</span>
                    {r.message && <span style={{ color: 'var(--muted)' }}>— {r.message}</span>}
                  </div>
                ))}
          </div>

          {data.boundOn && (
            <div className="muted" style={{ marginTop: 10, fontSize: 10 }}>
              inherited from <b>{localName(data.boundOn)}</b>
            </div>
          )}
        </div>
      )}
    </Card>
  )
}
