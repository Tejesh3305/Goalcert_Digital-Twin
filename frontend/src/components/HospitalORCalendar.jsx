/**
 * HospitalORCalendar — OR utilisation calendar (P3-024). Per theatre: booked vs
 * actual case times, turnover gaps and predicted utilisation, from the campus
 * twin's OR schedule. Pure render of `net.or_schedule`.
 */
const HOURS = 10          // an 08:00–18:00 list
const START = 8

const CASE_STATUS = {
  done: 'var(--muted)', in_progress: 'var(--accent-blue)', booked: 'var(--brand)',
}

export default function HospitalORCalendar({ schedule }) {
  if (!schedule || !schedule.length) return null
  const pct = (h) => `${(h / HOURS) * 100}%`

  return (
    <div>
      {/* hour axis */}
      <div style={{ display: 'flex', marginLeft: 96, marginBottom: 4 }}>
        {Array.from({ length: HOURS + 1 }).map((_, i) => (
          <div key={i} style={{ flex: 1, fontSize: 9, color: 'var(--muted)', fontFamily: 'var(--mono)' }}>
            {String(START + i).padStart(2, '0')}
          </div>
        ))}
      </div>
      {schedule.map((th) => {
        let cursor = 0
        return (
          <div key={th.id} style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
            <div style={{ width: 88, flexShrink: 0 }}>
              <div style={{ fontWeight: 700, fontSize: 12 }}>{th.theatre}</div>
              <div style={{ fontSize: 10, color: th.utilisation >= 85 ? 'var(--ok)' : th.utilisation >= 60 ? 'var(--accent-amber)' : 'var(--accent-red)' }}>
                {th.utilisation}% util
              </div>
            </div>
            <div style={{ position: 'relative', flex: 1, height: 34, background: 'var(--surface2)',
              borderRadius: 8, border: '1px solid var(--border)', overflow: 'hidden' }}>
              {(th.cases || []).map((c) => {
                const left = cursor
                const booked = c.duration_h
                const actual = c.actual_h
                cursor += actual + c.turnover_h
                const col = CASE_STATUS[c.status] || 'var(--brand)'
                const overrun = actual > booked
                return (
                  <div key={c.id} title={`${c.id} · booked ${booked}h · actual ${actual}h · turnover ${c.turnover_h}h`}
                    style={{ position: 'absolute', left: pct(left), width: pct(actual), top: 4, bottom: 4,
                      background: col, opacity: 0.85, borderRadius: 5,
                      border: overrun ? '1.5px solid var(--accent-red)' : 'none',
                      display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                    <span style={{ fontSize: 9, color: '#fff', fontWeight: 700 }}>{actual}h</span>
                    {/* booked outline (planned duration) */}
                    <span style={{ position: 'absolute', left: 0, width: pct(booked / actual * actual), top: 0, bottom: 0,
                      borderRight: '1px dashed rgba(255,255,255,.6)' }} />
                  </div>
                )
              })}
            </div>
            <div style={{ width: 60, flexShrink: 0, textAlign: 'right' }}>
              <div style={{ fontSize: 9.5, color: 'var(--muted)' }}>predicted</div>
              <div style={{ fontWeight: 700, fontSize: 12 }}>{th.predicted}%</div>
            </div>
          </div>
        )
      })}
      <div className="muted" style={{ fontSize: 10.5, marginTop: 4 }}>
        Bars = actual case time (red outline = overran the booked slot); gaps = turnover; predicted = next-48h utilisation.
      </div>
    </div>
  )
}
