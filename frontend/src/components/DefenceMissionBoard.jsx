/**
 * DefenceMissionBoard — a classified Kanban mission board (P4-018). Mission cards
 * grouped by status with assigned assets and a red/amber/green mission-health
 * indicator. Pure render of `net.missions`.
 */
const COLUMNS = ['Planning', 'Standing', 'Execution', 'Active']
const HEALTH = { green: 'var(--ok)', amber: 'var(--accent-amber)', red: 'var(--accent-red)' }

export default function DefenceMissionBoard({ missions }) {
  if (!missions || !missions.length) return null
  const cols = COLUMNS.filter((c) => missions.some((m) => m.status === c))
  const extra = missions.filter((m) => !COLUMNS.includes(m.status))
  const columns = extra.length ? [...cols, ...new Set(extra.map((m) => m.status))] : cols

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
        <span className="pill" style={{ background: '#1e293b', color: '#94a3b8', border: '1px solid #334155', fontSize: 9.5 }}>
          <i className="ti ti-lock" /> CLASSIFIED — UNCLASSIFIED (demo)
        </span>
        <span className="muted" style={{ fontSize: 11 }}>{missions.length} missions</span>
      </div>
      <div style={{ display: 'grid', gap: 10, gridTemplateColumns: `repeat(${columns.length}, minmax(150px, 1fr))` }}>
        {columns.map((col) => (
          <div key={col} style={{ background: 'var(--surface2)', borderRadius: 10, padding: 8 }}>
            <div style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase', color: 'var(--muted)', marginBottom: 8 }}>
              {col}
            </div>
            <div style={{ display: 'grid', gap: 8 }}>
              {missions.filter((m) => m.status === col).map((m) => (
                <div key={m.id} draggable style={{ background: 'var(--surface)', border: '1px solid var(--border)',
                  borderLeft: `3px solid ${HEALTH[m.health]}`, borderRadius: 8, padding: '8px 10px', cursor: 'grab' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <b style={{ fontSize: 12.5 }}>{m.name}</b>
                    <span style={{ width: 9, height: 9, borderRadius: 999, background: HEALTH[m.health] }} />
                  </div>
                  <div style={{ fontSize: 10, color: 'var(--muted)', fontFamily: 'var(--mono)', margin: '3px 0' }}>{m.id} · {m.phase}</div>
                  <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginTop: 4 }}>
                    {(m.assets || []).map((a) => (
                      <span key={a} className="pill pill-surface" style={{ fontSize: 9 }}>{a}</span>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
      <div className="muted" style={{ fontSize: 10.5, marginTop: 8 }}>
        <i className="ti ti-hand-move" /> Cards are drag-to-update; mission health is R/A/G from the base twin.
      </div>
    </div>
  )
}
