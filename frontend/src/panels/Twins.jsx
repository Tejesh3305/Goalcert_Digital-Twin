import { useState } from 'react'
import { PanelHeader } from '../components/ui/Card'
import { Modal } from '../components/ui/Modal'
import { Loading, ErrorBox } from '../components/ui/States'
import { useApi } from '../hooks/useApi'
import { useTwin } from '../context/TwinContext'
import { useToast } from '../context/ToastContext'
import { dateOf } from '../lib/format'
import api from '../api/client'

/** Twins panel — list, create, select, and delete digital twins.
 *  Creating a twin seeds real entities through the Graph Writer. */
export default function Twins() {
  const { twins, loading, error, refreshTwins, activeTenant, setActiveTenant } = useTwin()
  const [showCreate, setShowCreate] = useState(false)

  return (
    <div className="panel">
      <PanelHeader
        title="Digital Twins"
        subtitle="Each twin is an isolated, ontology-governed model of a real facility."
      >
        <button className="btn btn-primary" onClick={() => setShowCreate(true)}>
          <i className="ti ti-plus" /> Create Twin
        </button>
      </PanelHeader>

      {loading && !twins.length && <Loading label="Loading twins…" />}
      {error && <ErrorBox error={error} hint="Is the backend running? (python -m server.main)" />}

      {!loading && !twins.length && !error && (
        <div className="empty" style={{ marginTop: 20 }}>
          <i className="ti ti-stack-2" style={{ fontSize: 28, display: 'block', marginBottom: 8 }} />
          No twins yet. Create your first one to begin.
        </div>
      )}

      <div className="grid-3">
        {twins.map((t) => (
          <TwinCard
            key={t.tenant_id}
            twin={t}
            active={t.tenant_id === activeTenant}
            onSelect={() => setActiveTenant(t.tenant_id)}
            onDeleted={refreshTwins}
          />
        ))}
      </div>

      {showCreate && (
        <CreateTwinModal
          onClose={() => setShowCreate(false)}
          onCreated={(tenant) => { refreshTwins(); setActiveTenant(tenant) }}
        />
      )}
    </div>
  )
}

function TwinCard({ twin, active, onSelect, onDeleted }) {
  const toast = useToast()
  const [busy, setBusy] = useState(false)

  const remove = async (e) => {
    e.stopPropagation()
    if (!confirm(`Delete twin "${twin.name}"? This removes its graph entities.`)) return
    setBusy(true)
    try {
      await api.deleteTwin(twin.tenant_id)
      toast.ok('Twin deleted', twin.name)
      onDeleted()
    } catch (err) {
      toast.err('Delete failed', err.message)
    } finally {
      setBusy(false)
    }
  }

  const total = twin.summary?.total ?? 0
  return (
    <div className={`twin-card ${active ? 'active' : ''}`} onClick={onSelect}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div>
          <div className="twin-name">{twin.name}</div>
          <div className="twin-domain">{twin.domain}</div>
        </div>
        {active && <span className="pill pill-blue">ACTIVE</span>}
      </div>
      <div className="twin-desc">{twin.description}</div>
      <div className="twin-meta">
        <span><i className="ti ti-cube" /> {total} entities</span>
        <span><i className="ti ti-calendar" /> {dateOf(twin.created_at)}</span>
      </div>
      <div style={{ marginTop: 12, display: 'flex', gap: 6 }}>
        <button className="btn btn-ghost" onClick={remove} disabled={busy}
                style={{ color: 'var(--accent-red)' }}>
          <i className="ti ti-trash" /> Delete
        </button>
      </div>
    </div>
  )
}

function CreateTwinModal({ onClose, onCreated }) {
  const toast = useToast()
  const { data: tplData } = useApi(() => api.twinTemplates(), [])
  const [name, setName] = useState('')
  const [domain, setDomain] = useState('hvac')
  const [busy, setBusy] = useState(false)

  const templates = tplData?.templates || []

  const submit = async () => {
    if (!name.trim()) { toast.err('Name required', 'Give your twin a name.'); return }
    setBusy(true)
    try {
      const res = await api.createTwin({ name: name.trim(), domain })
      toast.ok('Twin created', `${res.twin.name} seeded with ${res.twin.summary?.total ?? 0} entities`)
      onCreated(res.twin.tenant_id)
      onClose()
    } catch (err) {
      toast.err('Create failed', err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal title="Create a Digital Twin"
           subtitle="Seeds a real, ontology-validated facility you can run the live feed against."
           onClose={onClose}>
      <div className="field">
        <label>Twin name</label>
        <input className="input" autoFocus value={name}
               placeholder="e.g. Melbourne Plant"
               onChange={(e) => setName(e.target.value)}
               onKeyDown={(e) => e.key === 'Enter' && submit()} />
      </div>
      <div className="field">
        <label>Domain template</label>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {templates.map((tpl) => (
            <label key={tpl.key}
                   style={{
                     display: 'flex', gap: 10, padding: 10, cursor: 'pointer',
                     border: `1px solid ${domain === tpl.key ? 'var(--accent-blue)' : 'var(--border2)'}`,
                     borderRadius: 8, background: domain === tpl.key ? 'rgba(75,139,245,.06)' : 'transparent',
                   }}>
              <input type="radio" name="domain" checked={domain === tpl.key}
                     onChange={() => setDomain(tpl.key)} style={{ marginTop: 2 }} />
              <div>
                <div style={{ fontWeight: 600, fontSize: 12 }}>{tpl.label}</div>
                <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 2 }}>{tpl.description}</div>
              </div>
            </label>
          ))}
        </div>
      </div>
      <div className="modal-actions">
        <button className="btn" onClick={onClose} disabled={busy}>Cancel</button>
        <button className="btn btn-primary" onClick={submit} disabled={busy}>
          {busy ? <><span className="spinner" /> &nbsp;Seeding…</> : <><i className="ti ti-plus" /> Create</>}
        </button>
      </div>
    </Modal>
  )
}
