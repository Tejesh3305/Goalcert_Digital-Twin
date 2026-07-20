/**
 * EquipmentInfoPanel — the click-an-asset detail panel for the 3-D twin.
 *
 * Shown (instead of BimViewer's generic property Drawer) when the picked scene
 * node is a piece of equipment. Three sections:
 *   • Health   — status + condition index + active findings (from /topology)
 *   • Live sensors — the per-instance signal values the dynamics engine persists
 *                    onto the node (fridge temp, gas pressure, ventilator O₂ flow…)
 *   • Asset info — manufacturer / model / serial / install + warranty / runtime
 *
 * All fields come from the SAME api.getEntity(id) call BimViewer's Drawer used —
 * no new endpoint. The live-sensor values update as the panel re-fetches while
 * the dynamics feed runs.
 */
import { useEffect, useState } from 'react'
import api from '../api/client'

// short node-property name → (label, unit). Matches the fragments the dynamics
// engine writes (DynamicsEngine._signal_prop) for the hospital equipment models.
const SENSOR_META = {
  coldChainTemp: ['Internal Temperature', '°C'],
  gasPressure: ['Line Pressure', 'bar'],
  gasLevel: ['Reserve Level', '%'],
  imagingCoolantTemp: ['Magnet Coolant', '°C'],
  heliumLevel: ['Helium Level', '%'],
  o2Flow: ['O₂ Flow', 'L/min'],
  tidalVolume: ['Tidal Volume', 'mL'],
  fiO2: ['FiO₂', '%'],
  peep: ['PEEP', 'cmH₂O'],
  batteryLevel: ['Battery', '%'],
  flowRate: ['Flow Rate', 'mL/h'],
  volumeInfused: ['Volume Infused', 'mL'],
  occlusionPressure: ['Occlusion Pressure', 'kPa'],
  chamberTemp: ['Chamber Temperature', '°C'],
  chamberPressure: ['Chamber Pressure', 'bar'],
  cycleF0: ['Sterilisation F₀', 'min'],
  activePower: ['Active Power', 'kW'],
  heatOutputW: ['Heat Output', 'W'],
  nurseCall: ['Nurse Call', ''],
  temperature: ['Temperature', '°C'],
}

// asset-management fields (static) → label.
const ASSET_FIELDS = [
  ['manufacturer', 'Manufacturer'],
  ['modelNumber', 'Model'],
  ['serialNumber', 'Serial No.'],
  ['installDate', 'Installed'],
  ['warrantyExpiry', 'Warranty until'],
  ['runtimeHours', 'Runtime (h)'],
  ['criticality', 'Criticality'],
]

const STATUS = {
  crit: { color: '#f43f5e', label: 'Critical' },
  warn: { color: '#f59e0b', label: 'Warning' },
  ok: { color: '#5fd08a', label: 'Healthy' },
}

export default function EquipmentInfoPanel({ node, tenant, info, onClose }) {
  const [entity, setEntity] = useState(null)
  useEffect(() => {
    if (!node.entityId || !tenant) { setEntity(null); return }
    let alive = true
    const load = () => api.getEntity(node.entityId, tenant)
      .then((e) => { if (alive) setEntity(e) }).catch(() => {})
    load()
    const id = setInterval(load, 3000) // refresh live sensor values while the feed runs
    return () => { alive = false; clearInterval(id) }
  }, [node.entityId, tenant])

  const props = entity?.node || {}
  const typeShort = (node.type || props.canonicalType || '').split('#').pop()

  // health status from findings (falls back to healthy when tracked, unknown otherwise)
  const fc = info?.findings
  let sev = 'ok'
  if (fc?.critical > 0 || info?.severity === 'critical') sev = 'crit'
  else if (fc?.warning > 0 || info?.severity === 'warning') sev = 'warn'
  const tracked = !!node.entityId
  const cond = num(props.conditionIndex)

  // live sensor rows: any known sensor property present on the node
  const sensors = Object.entries(props)
    .filter(([k, v]) => SENSOR_META[k] && v != null && typeof v !== 'object')
    .map(([k, v]) => ({ key: k, label: SENSOR_META[k][0], unit: SENSOR_META[k][1], value: v }))

  const assetRows = ASSET_FIELDS
    .filter(([k]) => props[k] != null && props[k] !== '')
    .map(([k, label]) => ({ key: k, label, value: props[k] }))

  return (
    <div className="bim-drawer">
      <i className="ti ti-x close" onClick={onClose} />
      <h4>{node.label || typeShort}</h4>
      <div className="sub">{prettyType(typeShort)}{node.sector ? ` · ${node.sector}` : ''}</div>

      {/* Health */}
      {tracked ? (
        <div style={{ margin: '12px 0' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: cond == null ? 0 : 8 }}>
            <span style={{ width: 9, height: 9, borderRadius: '50%', background: STATUS[sev].color }} />
            <span style={{ fontSize: 12, color: STATUS[sev].color }}>{STATUS[sev].label}</span>
            {fc?.critical > 0 && <span style={{ fontSize: 11, color: '#f43f5e' }}>· {fc.critical} critical</span>}
            {fc?.warning > 0 && <span style={{ fontSize: 11, color: '#f59e0b' }}>· {fc.warning} warning</span>}
          </div>
          {cond != null && <HealthBar value={cond} />}
        </div>
      ) : (
        <div className="sub" style={{ margin: '12px 0' }}>Structural element — not a tracked asset.</div>
      )}

      {/* Live sensors */}
      {sensors.length > 0 && (
        <Section title="Live sensors">
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6 }}>
            {sensors.map((s) => (
              <div key={s.key} style={{ background: 'rgba(255,255,255,0.04)', borderRadius: 8, padding: '7px 9px' }}>
                <div style={{ fontSize: 10.5, opacity: 0.65 }}>{s.label}</div>
                <div style={{ fontSize: 15, fontWeight: 600 }}>
                  {fmt(s.value)}<span style={{ fontSize: 10, opacity: 0.6, marginLeft: 3 }}>{s.unit}</span>
                </div>
              </div>
            ))}
          </div>
        </Section>
      )}
      {tracked && sensors.length === 0 && (
        <div className="sub" style={{ marginTop: 8, fontSize: 11 }}>
          No live sensor stream — start the dynamics feed to see readings.
        </div>
      )}

      {/* Asset info */}
      {assetRows.length > 0 && (
        <Section title="Asset information">
          {assetRows.map((r) => (
            <div className="kv" key={r.key}>
              <span className="k">{r.label}</span>
              <span>{String(r.value)}{r.key === 'warrantyExpiry' ? warrantyTag(r.value) : ''}</span>
            </div>
          ))}
          {cond != null && (
            <div className="kv"><span className="k">Condition index</span><span>{Math.round(cond * 100)}%</span></div>
          )}
        </Section>
      )}
    </div>
  )
}

function Section({ title, children }) {
  return (
    <div style={{ marginTop: 14 }}>
      <div style={{ fontSize: 10.5, textTransform: 'uppercase', letterSpacing: 0.6, opacity: 0.55, marginBottom: 7 }}>{title}</div>
      {children}
    </div>
  )
}

function HealthBar({ value }) {
  const pct = Math.max(0, Math.min(100, Math.round(value * 100)))
  const color = pct < 40 ? '#f43f5e' : pct < 72 ? '#f59e0b' : '#5fd08a'
  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10.5, opacity: 0.6, marginBottom: 3 }}>
        <span>Condition</span><span>{pct}%</span>
      </div>
      <div style={{ height: 6, borderRadius: 4, background: 'rgba(255,255,255,0.08)', overflow: 'hidden' }}>
        <div style={{ width: `${pct}%`, height: '100%', background: color }} />
      </div>
    </div>
  )
}

const num = (v) => (v == null || v === '' || isNaN(Number(v)) ? null : Number(v))
const fmt = (v) => (typeof v === 'number' || !isNaN(Number(v))
  ? (Math.round(Number(v) * 100) / 100).toLocaleString() : String(v))

function prettyType(t) {
  return (t || 'Equipment').replace(/([a-z])([A-Z])/g, '$1 $2')
}

function warrantyTag(dateStr) {
  const d = Date.parse(dateStr)
  if (isNaN(d)) return ''
  return d < Date.now() ? '  ⚠ expired' : ''
}
