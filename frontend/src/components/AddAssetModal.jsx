import { useMemo, useState } from 'react'
import { Modal } from './ui/Modal'
import { useApi } from '../hooks/useApi'
import { useToast } from '../context/ToastContext'
import { localName } from '../lib/format'
import api from '../api/client'

/**
 * AddAssetModal — create any ontology entity through the validated write path
 * (POST /entities -> Graph Writer -> SHACL gate -> Neo4j + Change Log + bus).
 *
 * The type list spans core + packs (/schema/asset-types). An optional
 * relationship can be attached to an existing entity. If the SHACL gate rejects
 * the entity, the violations are surfaced — proving the platform's "rejects
 * malformed entities live" guarantee.
 */
export default function AddAssetModal({ tenant, existing = [], onClose, onCreated }) {
  const toast = useToast()
  const { data: typeData } = useApi(() => api.assetTypes(), [])
  const [type, setType] = useState('')
  const [name, setName] = useState('')
  const [status, setStatus] = useState('')
  const [setpoint, setSetpoint] = useState('')
  const [relPredicate, setRelPredicate] = useState('')
  const [relTarget, setRelTarget] = useState('')
  const [busy, setBusy] = useState(false)
  const [violations, setViolations] = useState(null)

  // Flatten {category: [types]} into grouped <optgroup> options.
  const grouped = typeData?.categories || {}
  const selectedMeta = useMemo(() => {
    for (const list of Object.values(grouped)) {
      const hit = list.find((t) => t.iri === type)
      if (hit) return hit
    }
    return null
  }, [type, grouped])

  const isAsset = selectedMeta?.category === 'PhysicalAsset'

  const submit = async () => {
    setViolations(null)
    if (!type) { toast.err('Type required', 'Pick an entity type.'); return }
    if (!name.trim()) { toast.err('Name required', 'Give the entity a display name.'); return }

    const properties = { displayName: name.trim() }
    if (status.trim()) properties.status = status.trim()
    if (setpoint !== '') properties.setpoint = Number(setpoint)

    const relationships = (relPredicate.trim() && relTarget)
      ? [{ predicate: relPredicate.trim(), target_id: relTarget }]
      : []

    setBusy(true)
    try {
      const res = await api.createEntity({
        tenant, canonical_type: type, actor: 'ui',
        properties, relationships,
      })
      toast.ok('Asset created', `${name} (${res.label})`)
      onCreated()
      onClose()
    } catch (e) {
      // 422 from the gate carries { error, violations }
      if (e.detail && typeof e.detail === 'object') {
        setViolations(e.detail.violations || [e.detail.error])
        toast.err('Rejected by the ontology gate', e.detail.error || 'Validation failed')
      } else {
        toast.err('Create failed', e.message)
      }
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal title="Add Asset"
           subtitle="Created through the validated write path — the SHACL gate must pass."
           onClose={onClose}>
      <div className="field">
        <label>Entity type</label>
        <select className="select" value={type} onChange={(e) => setType(e.target.value)}>
          <option value="">Select a type…</option>
          {Object.entries(grouped).map(([cat, list]) => (
            <optgroup key={cat} label={cat}>
              {list.map((t) => (
                <option key={t.iri} value={t.iri}>{t.label} ({t.id})</option>
              ))}
            </optgroup>
          ))}
        </select>
      </div>

      <div className="field">
        <label>Display name</label>
        <input className="input" autoFocus value={name}
               placeholder="e.g. AHU-02" onChange={(e) => setName(e.target.value)} />
      </div>

      <div style={{ display: 'flex', gap: 10 }}>
        <div className="field" style={{ flex: 1 }}>
          <label>Status (optional)</label>
          <input className="input" value={status}
                 placeholder="running / idle" onChange={(e) => setStatus(e.target.value)} />
        </div>
        {isAsset && (
          <div className="field" style={{ flex: 1 }}>
            <label>Setpoint °C (optional)</label>
            <input className="input" type="number" value={setpoint}
                   placeholder="22" onChange={(e) => setSetpoint(e.target.value)} />
          </div>
        )}
      </div>

      <div className="field">
        <label>Relationship (optional)</label>
        <div style={{ display: 'flex', gap: 8 }}>
          <input className="input" style={{ flex: 1 }} value={relPredicate}
                 placeholder="predicate e.g. hvac:servesSpace"
                 onChange={(e) => setRelPredicate(e.target.value)} />
          <select className="select" style={{ flex: 1 }} value={relTarget}
                  onChange={(e) => setRelTarget(e.target.value)}>
            <option value="">target entity…</option>
            {existing.map((n) => (
              <option key={n.id} value={n.id}>
                {n.displayName || localName(n.canonicalType)} ({n._label})
              </option>
            ))}
          </select>
        </div>
      </div>

      {violations && (
        <div className="error-box" style={{ textAlign: 'left', marginTop: 4 }}>
          <b><i className="ti ti-shield-x" /> Ontology gate rejected this entity</b>
          <ul style={{ margin: '6px 0 0 18px', fontSize: 11 }}>
            {violations.map((v, i) => <li key={i} style={{ marginBottom: 3 }}>{String(v)}</li>)}
          </ul>
        </div>
      )}

      <div className="modal-actions">
        <button className="btn" onClick={onClose} disabled={busy}>Cancel</button>
        <button className="btn btn-primary" onClick={submit} disabled={busy}>
          {busy ? <><span className="spinner" /> &nbsp;Validating…</> : <><i className="ti ti-plus" /> Create</>}
        </button>
      </div>
    </Modal>
  )
}
