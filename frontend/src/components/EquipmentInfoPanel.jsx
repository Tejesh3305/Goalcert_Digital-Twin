/**
 * EquipmentInfoPanel — the click-an-asset detail panel for the 3-D twin.
 *
 * Three sections:
 *   • Health   — status + condition index + active findings (from /topology)
 *   • Live sensors — physics-computed telemetry from GET /entities/{id}/telemetry
 *                    (always available, no feed required; evolves as it polls)
 *   • Asset info — manufacturer / model / serial / install + warranty / runtime
 *
 * Metadata + condition come from api.getEntity; the live sensor values come from
 * api.entityTelemetry (the on-demand per-tenant dynamics engine).
 */
import { useEffect, useState } from 'react'
import AssetPreview from '../three/AssetPreview'
import api from '../api/client'

// signal short-name → [label, unit, binaryLabels?]. binaryLabels "true|false"
// renders 0/1 signals as words instead of numbers.
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
  temperature: ['Temperature', '°C'],
  nurseCall: ['Nurse Call', '', 'active|idle'],
  bedOccupied: ['Occupancy', '', 'occupied|vacant'],
  backrestAngle: ['Backrest Angle', '°'],
  patientWeight: ['Patient Weight', 'kg'],
  brakeEngaged: ['Castor Brake', '', 'engaged|released'],
  bedExitRisk: ['Bed-Exit Alarm', '', 'ALARM|clear'],
  // generic electrical / thermal / hvac signals the shared archetypes emit
  supplyAirTemp: ['Supply Air Temp', '°C'],
  filterDeltaP: ['Filter ΔP', 'Pa'],
  coolingDemandKW: ['Cooling Demand', 'kW'],
  chillerCOP: ['Coeff. of Performance', ''],
  chwSupplyTemp: ['Chilled-Water Supply', '°C'],
  fuelLevel: ['Fuel Level', '%'],
  frequency: ['Frequency', 'Hz'],
  runHours: ['Run Hours', 'h'],
  cpuLoad: ['CPU Load', '%'],
  voltage: ['Voltage', 'V'],
  electricCurrent: ['Current', 'A'],
  oilTemperature: ['Oil Temperature', '°C'],
  powerFactor: ['Power Factor', ''],
  upsSoC: ['State of Charge', '%'],
  available: ['Available', '', 'yes|no'],
  // flowRate unit depends on asset (see flowRateUnit below); label only here
  flowRate: ['Flow Rate', ''],
}

// per-asset unit override for signals whose unit is context-dependent.
function flowRateUnit(modelKey) {
  return modelKey === 'infusionpump' ? 'mL/h' : 'L/s'
}

// derived/coupling/liveness signals not worth showing as a sensor tile.
const HIDE = new Set(['heatOutputW', 'heartbeat'])

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

export default function EquipmentInfoPanel({ node, tenant, info, onClose, propKey, embedded,
                                             unitCount, typeDesc }) {
  const [entity, setEntity] = useState(null)
  const [tele, setTele] = useState(null)

  useEffect(() => {
    if (!node.entityId || !tenant) { setEntity(null); setTele(null); return }
    let alive = true
    const loadMeta = () => api.getEntity(node.entityId, tenant)
      .then((e) => { if (alive) setEntity(e) }).catch(() => {})
    const loadTele = () => api.entityTelemetry(node.entityId, tenant)
      .then((t) => { if (alive) setTele(t) }).catch(() => { if (alive) setTele({ available: false }) })
    loadMeta(); loadTele()
    const t1 = setInterval(loadTele, 3000)  // live telemetry
    const t2 = setInterval(loadMeta, 8000)  // condition / metadata
    return () => { alive = false; clearInterval(t1); clearInterval(t2) }
  }, [node.entityId, tenant])

  const props = entity?.node || {}
  const typeShort = (node.type || props.canonicalType || '').split('#').pop()

  const fc = info?.findings
  let sev = 'ok'
  if (fc?.critical > 0 || info?.severity === 'critical') sev = 'crit'
  else if (fc?.warning > 0 || info?.severity === 'warning') sev = 'warn'
  // reflect the live telemetry status too, so health matches the sensor readings
  // even when the findings feed isn't running
  if (sev === 'ok' && tele?.status === 'fault') sev = 'crit'
  else if (sev === 'ok' && tele?.status === 'degraded') sev = 'warn'
  const tracked = !!node.entityId
  const cond = num(props.conditionIndex)

  const modelKey = propKey || node.geometry?.prop

  // live sensors from the telemetry endpoint
  const sensors = (tele?.signals || [])
    .filter((s) => !HIDE.has(s.name) && s.value != null)
    .map((s) => {
      const m = SENSOR_META[s.name]
      return {
        key: s.name,
        label: m ? m[0] : prettyType(s.name),
        unit: s.name === 'flowRate' ? flowRateUnit(modelKey) : (m ? m[1] : ''),
        bin: m ? m[2] : undefined,
        value: s.value,
      }
    })

  const assetRows = ASSET_FIELDS
    .filter(([k]) => props[k] != null && props[k] !== '')
    .map(([k, label]) => ({ key: k, label, value: props[k] }))

  const teleLoading = tele == null

  return (
    <div className={embedded ? 'equip-panel' : 'bim-drawer'}>
      {onClose && <i className="ti ti-x close" onClick={onClose} />}
      {modelKey && (
        <div style={{ marginBottom: 12, height: 200, borderRadius: 10, overflow: 'hidden', background: '#0c0f16' }}>
          <AssetPreview assetKey={modelKey} interactive showHuman showGrid background="#0c0f16" />
        </div>
      )}
      <h4>{node.label || typeShort}</h4>
      <div className="sub">{prettyType(typeShort)}{node.sector ? ` · ${node.sector}` : ''}</div>
      {typeDesc && <p style={{ fontSize: 11.5, color: '#9aa3d6', margin: '8px 0 0', lineHeight: 1.4 }}>{typeDesc}</p>}
      {unitCount > 1 && (
        <div style={{ fontSize: 11, color: '#9aa3d6', marginTop: 8, display: 'flex', alignItems: 'center', gap: 6 }}>
          <i className="ti ti-stack-2" /> {unitCount} units in this hospital · showing the least-healthy
        </div>
      )}

      {/* Health */}
      {tracked && (
        <div style={{ margin: '12px 0' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: cond == null ? 0 : 8 }}>
            <span style={{ width: 9, height: 9, borderRadius: '50%', background: STATUS[sev].color }} />
            <span style={{ fontSize: 12, color: STATUS[sev].color }}>{STATUS[sev].label}</span>
            {fc?.critical > 0 && <span style={{ fontSize: 11, color: '#f43f5e' }}>· {fc.critical} critical</span>}
            {fc?.warning > 0 && <span style={{ fontSize: 11, color: '#f59e0b' }}>· {fc.warning} warning</span>}
          </div>
          {cond != null && <HealthBar value={cond} />}
        </div>
      )}

      {/* Live sensors */}
      <Section title="Live sensors">
        {sensors.length > 0 ? (
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6 }}>
            {sensors.map((s) => (
              <div key={s.key} style={{ background: 'rgba(255,255,255,0.04)', borderRadius: 8, padding: '7px 9px' }}>
                <div style={{ fontSize: 10.5, opacity: 0.65 }}>{s.label}</div>
                <div style={{ fontSize: 15, fontWeight: 600 }}>
                  {s.bin
                    ? binLabel(s.value, s.bin)
                    : <>{fmt(s.value)}<span style={{ fontSize: 10, opacity: 0.6, marginLeft: 3 }}>{s.unit}</span></>}
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="sub" style={{ fontSize: 11 }}>
            {teleLoading ? 'Reading sensors…' : 'This asset type has no sensors.'}
          </div>
        )}
      </Section>

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

function binLabel(v, bin) {
  const [t, f] = bin.split('|')
  const on = Number(v) >= 0.5
  return <span style={{ color: on && /alarm/i.test(t) ? '#f43f5e' : undefined }}>{on ? t : f}</span>
}

function prettyType(t) {
  return (t || 'Equipment').replace(/([a-z])([A-Z])/g, '$1 $2').replace(/^./, (c) => c.toUpperCase())
}

function warrantyTag(dateStr) {
  const d = Date.parse(dateStr)
  if (isNaN(d)) return ''
  return d < Date.now() ? '  ⚠ expired' : ''
}
