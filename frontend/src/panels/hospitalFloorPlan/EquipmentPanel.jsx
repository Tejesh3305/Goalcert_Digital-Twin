/**
 * EquipmentPanel.jsx — the click-to-inspect panel for a machine in the
 * floor-plan scene: live status + metrics, its injectable fault list,
 * inject / clear, Repair-with-AI launch and copilot work-order generation.
 */
import { useMemo, useState } from 'react'
import { WorkOrderView } from '../../components/copilot/Structured'

const STATUS_PILL = {
  ok: 'hfp-pill-ok', warning: 'hfp-pill-warn', critical: 'hfp-pill-crit', unknown: '',
}

export default function EquipmentPanel({
  unit, faultInfo, activeFault, busy, offline,
  onInject, onClear, onRepair, onWorkOrder, workOrder, onClose,
}) {
  const faults = useMemo(() => Object.entries(faultInfo || {})
    .filter(([, f]) => f.target === unit?.id), [faultInfo, unit?.id])
  const [pick, setPick] = useState('')
  if (!unit) return null

  const isFaulted = !!unit.fault
  const chosen = pick || faults[0]?.[0] || ''

  return (
    <div className="hfp-panel">
      <div className="hfp-panel-head">
        <div>
          <div className="hfp-panel-title">{unit.label}</div>
          <div className="hfp-panel-sub">{unit.id} · {unit.subsystem}</div>
        </div>
        <span className={`hfp-pill ${STATUS_PILL[unit.status] || ''}`}>{unit.status}</span>
        <button className="hfp-x" onClick={onClose}><i className="ti ti-x" /></button>
      </div>

      <div className="hfp-health">
        <div className="hfp-health-bar">
          <div className="hfp-health-fill" style={{
            width: `${Math.round((unit.health ?? 1) * 100)}%`,
            background: unit.status === 'critical' ? '#f43f5e' : unit.status === 'warning' ? '#f59e0b' : '#34d399',
          }} />
        </div>
        <span>{Math.round((unit.health ?? 1) * 100)}%</span>
      </div>

      <div className="hfp-metrics">
        {(unit.metrics || []).map((m) => (
          <div key={m.label} className="hfp-metric">
            <span className="hfp-metric-label">{m.label}</span>
            <span className="hfp-metric-value">{m.value}{m.unit ? ` ${m.unit}` : ''}</span>
          </div>
        ))}
      </div>

      {isFaulted && (
        <div className="hfp-faultbox">
          <div className="hfp-faultbox-title"><i className="ti ti-alert-triangle" /> {unit.fault_label}</div>
          {faultInfo?.[unit.fault]?.description && (
            <div className="hfp-faultbox-desc">{faultInfo[unit.fault].description}</div>
          )}
        </div>
      )}

      {offline ? (
        <div className="hfp-offline">Live twin offline — start the backend (start.ps1) to
          run physics, faults and repair.</div>
      ) : (
        <>
          {faults.length > 0 && !isFaulted && (
            <div className="hfp-row">
              <select className="hfp-select" value={chosen} onChange={(e) => setPick(e.target.value)}>
                {faults.map(([id, f]) => <option key={id} value={id}>{f.label}</option>)}
              </select>
              <button className="hfp-btn hfp-btn-danger" disabled={busy || !chosen}
                      onClick={() => onInject(chosen)}>
                <i className="ti ti-bolt" /> Inject fault
              </button>
            </div>
          )}
          {faults.length === 0 && !isFaulted && (
            <div className="hfp-note">No injectable faults for this unit.</div>
          )}

          <div className="hfp-actions">
            {activeFault && (
              <button className="hfp-btn" disabled={busy} onClick={onClear}>
                <i className="ti ti-checkbox" /> Mark repaired (clear)
              </button>
            )}
            <button className="hfp-btn hfp-btn-primary" onClick={onRepair}>
              <i className="ti ti-robot" /> Repair with AI
            </button>
            <button className="hfp-btn" disabled={workOrder.loading} onClick={onWorkOrder}>
              <i className="ti ti-clipboard-text" />
              {workOrder.loading ? ` Generating… ${workOrder.elapsed ?? ''}` : ' Generate work order'}
            </button>
          </div>

          {workOrder.error && <div className="hfp-note hfp-err">Work order failed: {workOrder.error}</div>}
          {workOrder.data?.work_order && (
            <div className="hfp-wo">
              <div className="hfp-wo-src">
                source: {workOrder.data.source} · ai: {workOrder.data.ai?.backend || '—'}
              </div>
              <WorkOrderView wo={workOrder.data.work_order} />
            </div>
          )}
        </>
      )}
    </div>
  )
}
