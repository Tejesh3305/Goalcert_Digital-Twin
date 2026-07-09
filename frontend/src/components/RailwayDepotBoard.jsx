/**
 * RailwayDepotBoard — the depot board UI (P1-025). Shows, per depot, each
 * stabling/maintenance berth's occupancy, the rolling-stock unit on it, its
 * status (in service / stabled / under maintenance) and the predicted
 * ready-for-service time. Pure render of the `net.depots` payload from
 * GET /twins/{tenant}/network.
 */
const CHIP = {
  in_service: { label: 'In service', bg: 'rgba(22,163,74,.12)', fg: 'var(--ok)', icon: 'ti-arrow-up-right' },
  stabled: { label: 'Stabled', bg: 'var(--surface2)', fg: 'var(--muted)', icon: 'ti-parking' },
  maintenance: { label: 'Maintenance', bg: 'rgba(217,119,6,.12)', fg: 'var(--accent-amber)', icon: 'ti-tools' },
}

export default function RailwayDepotBoard({ depots }) {
  if (!depots || !depots.length) return null
  return (
    <div style={{ display: 'grid', gap: 16, gridTemplateColumns: depots.length > 1 ? 'repeat(auto-fit, minmax(280px, 1fr))' : '1fr' }}>
      {depots.map((d) => (
        <div key={d.id}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
            <div style={{ fontWeight: 700, fontFamily: 'var(--display)', fontSize: 13.5 }}>
              <i className="ti ti-building-warehouse" style={{ marginRight: 6, color: 'var(--muted)' }} />{d.name}
            </div>
            <span className="pill pill-surface" style={{ fontSize: 10 }}>
              {d.available}/{d.berth_count} available
            </span>
          </div>
          <div style={{ display: 'grid', gap: 6, gridTemplateColumns: 'repeat(auto-fill, minmax(122px, 1fr))' }}>
            {d.berths.map((b) => {
              const c = CHIP[b.status] || CHIP.stabled
              return (
                <div key={b.berth} style={{ background: c.bg, border: '1px solid var(--border)',
                  borderRadius: 10, padding: '8px 10px' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <span style={{ fontFamily: 'var(--mono)', fontSize: 10.5, color: 'var(--muted)' }}>{b.berth}</span>
                    <i className={`ti ${c.icon}`} style={{ color: c.fg, fontSize: 13 }} />
                  </div>
                  <div style={{ fontWeight: 700, fontSize: 12.5, marginTop: 2 }}>{b.unit || '—'}</div>
                  <div style={{ fontSize: 10.5, color: c.fg, fontWeight: 600 }}>{c.label}</div>
                  {b.ready_in_h != null && (
                    <div style={{ fontSize: 10, color: 'var(--muted)', marginTop: 2 }}>
                      <i className="ti ti-clock" style={{ fontSize: 11 }} /> ready in ~{b.ready_in_h} h
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      ))}
    </div>
  )
}
