/**
 * HospitalPatientFlow — ED patient-flow funnel (P3-025). Triage → discharge
 * stages with counts + wait times and bottleneck highlighting, driven by the
 * Little's-Law patient-flow model. Pure render of `net.patient_flow`.
 */
export default function HospitalPatientFlow({ flow }) {
  if (!flow || !flow.stages) return null
  const stages = flow.stages
  const max = Math.max(1, ...stages.map((s) => s.count))

  return (
    <div>
      <div style={{ display: 'flex', gap: 14, marginBottom: 12, flexWrap: 'wrap' }}>
        <Kpi label="Arrival rate" value={`${flow.arrival_rate}/h`} />
        <Kpi label="In system" value={Math.round(flow.in_system ?? 0)} />
        <Kpi label="Avg wait" value={`${Math.round(flow.avg_wait_min ?? 0)} min`} />
        <Kpi label="Bottleneck" value={flow.bottleneck} color="var(--accent-amber)" />
      </div>
      <div style={{ display: 'grid', gap: 6 }}>
        {stages.map((s, i) => {
          const w = 30 + 70 * (s.count / max)
          const isBottleneck = s.name === flow.bottleneck
          const col = isBottleneck ? 'var(--accent-red)' : 'var(--brand)'
          return (
            <div key={s.name} style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <div style={{ width: 92, fontSize: 12, textAlign: 'right',
                fontWeight: isBottleneck ? 700 : 500, color: isBottleneck ? 'var(--accent-red)' : 'var(--text)' }}>
                {s.name}
              </div>
              <div style={{ flex: 1, display: 'flex', justifyContent: 'center' }}>
                <div style={{ width: `${w}%`, minWidth: 40, height: 30, borderRadius: 7,
                  background: `linear-gradient(90deg, ${col}, ${col}bb)`,
                  display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '0 10px',
                  boxShadow: isBottleneck ? '0 0 0 2px rgba(225,29,72,.25)' : 'none' }}>
                  <b style={{ color: '#fff', fontSize: 13 }}>{s.count}</b>
                  <span style={{ color: 'rgba(255,255,255,.85)', fontSize: 10 }}>~{s.wait_min}m</span>
                </div>
              </div>
              {isBottleneck && <span className="pill pill-red" style={{ fontSize: 9 }}>bottleneck</span>}
            </div>
          )
        })}
      </div>
    </div>
  )
}

function Kpi({ label, value, color }) {
  return (
    <div style={{ background: 'var(--surface2)', borderRadius: 8, padding: '7px 12px', minWidth: 84 }}>
      <div className="card-label" style={{ fontSize: 9.5 }}>{label}</div>
      <div style={{ fontWeight: 700, color: color || 'var(--text)', textTransform: 'capitalize' }}>{value}</div>
    </div>
  )
}
