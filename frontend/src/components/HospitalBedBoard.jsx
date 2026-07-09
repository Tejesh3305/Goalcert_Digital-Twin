/**
 * HospitalBedBoard — real-time bed board (P3-023). Ward occupancy by bed status
 * (occupied / available / cleaning / blocked) from the campus twin's RTLS/bed
 * stream. Pure render of `net.beds`.
 */
const STATUS = {
  occupied: { label: 'Occupied', bg: 'rgba(37,99,235,.14)', fg: 'var(--accent-blue)', icon: 'ti-user' },
  available: { label: 'Available', bg: 'rgba(22,163,74,.14)', fg: 'var(--ok)', icon: 'ti-bed' },
  cleaning: { label: 'Cleaning', bg: 'rgba(217,119,6,.14)', fg: 'var(--accent-amber)', icon: 'ti-spray' },
  blocked: { label: 'Blocked', bg: 'rgba(225,29,72,.14)', fg: 'var(--accent-red)', icon: 'ti-ban' },
}

export default function HospitalBedBoard({ beds }) {
  if (!beds || !beds.length) return null
  const wards = [...new Set(beds.map((b) => b.ward))]
  const counts = Object.keys(STATUS).reduce((a, s) => ({ ...a, [s]: beds.filter((b) => b.status === s).length }), {})

  return (
    <div>
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 12 }}>
        {Object.entries(STATUS).map(([s, c]) => (
          <span key={s} className="pill" style={{ background: c.bg, color: c.fg, border: 'none' }}>
            <i className={`ti ${c.icon}`} /> {counts[s]} {c.label.toLowerCase()}
          </span>
        ))}
      </div>
      <div style={{ display: 'grid', gap: 14 }}>
        {wards.map((ward) => {
          const wb = beds.filter((b) => b.ward === ward)
          const occ = Math.round(100 * wb.filter((b) => b.status === 'occupied').length / wb.length)
          return (
            <div key={ward}>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
                <b style={{ fontSize: 12.5 }}>{ward}</b>
                <span className="muted" style={{ fontSize: 11 }}>{occ}% occupied · {wb.length} beds</span>
              </div>
              <div style={{ display: 'grid', gap: 5, gridTemplateColumns: 'repeat(auto-fill, minmax(78px, 1fr))' }}>
                {wb.map((b) => {
                  const c = STATUS[b.status] || STATUS.available
                  return (
                    <div key={b.id} title={b.patient ? `${b.patient} · ${b.los_h}h` : c.label}
                      style={{ background: c.bg, border: '1px solid var(--border)', borderRadius: 8,
                        padding: '6px 8px', cursor: 'grab' }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                        <span style={{ fontFamily: 'var(--mono)', fontSize: 10, color: 'var(--muted)' }}>#{b.bed}</span>
                        <i className={`ti ${c.icon}`} style={{ color: c.fg, fontSize: 12 }} />
                      </div>
                      <div style={{ fontSize: 10.5, fontWeight: 600, color: c.fg }}>{c.label}</div>
                      {b.patient && <div style={{ fontSize: 9.5, color: 'var(--muted)', fontFamily: 'var(--mono)' }}>{b.patient}</div>}
                    </div>
                  )
                })}
              </div>
            </div>
          )
        })}
      </div>
      <div className="muted" style={{ fontSize: 10.5, marginTop: 10 }}>
        <i className="ti ti-hand-move" /> Live from the RTLS/bed stream — drag-to-reassign wires to the graph write path.
      </div>
    </div>
  )
}
