/**
 * EVV2GTradingView — V2G arbitrage view (P2-023). Spot electricity price curve vs
 * recommended V2G export windows, with the arbitrage agent's recommendation and a
 * human approval action. Self-contained SVG. Pure render of `net.v2g`.
 */
import { useState } from 'react'

export default function EVV2GTradingView({ v2g }) {
  const [approved, setApproved] = useState(false)
  if (!v2g || !v2g.price_curve) return null
  const curve = v2g.price_curve
  const windows = v2g.export_windows || []
  const W = 640, H = 180, padL = 40, padB = 20, padT = 10, padR = 10
  const maxP = Math.max(...curve.map((p) => p.price)) * 1.1
  const x = (h) => padL + (h / 23) * (W - padL - padR)
  const y = (v) => padT + (1 - v / maxP) * (H - padT - padB)
  const line = curve.map((p) => `${x(p.hour)},${y(p.price)}`).join(' ')
  const exportSet = new Set()
  windows.forEach((w) => { for (let h = w.start; h < w.end; h++) exportSet.add(h) })

  return (
    <div>
      <div style={{ display: 'flex', gap: 12, marginBottom: 12, flexWrap: 'wrap', alignItems: 'center' }}>
        <div style={{ background: 'var(--surface2)', borderRadius: 8, padding: '7px 12px' }}>
          <div className="card-label" style={{ fontSize: 9.5 }}>Spot price</div>
          <div style={{ fontWeight: 700, fontFamily: 'var(--mono)' }}>${v2g.spot_price}<span style={{ fontSize: 10 }}>/MWh</span></div>
        </div>
        <div style={{ background: 'var(--surface2)', borderRadius: 8, padding: '7px 12px' }}>
          <div className="card-label" style={{ fontSize: 9.5 }}>Est. daily V2G revenue</div>
          <div style={{ fontWeight: 700, fontFamily: 'var(--mono)', color: 'var(--ok)' }}>${v2g.total_revenue}</div>
        </div>
        <div style={{ marginLeft: 'auto', textAlign: 'right' }}>
          <div style={{ fontSize: 12, fontWeight: 600, color: v2g.pending_approval ? 'var(--accent-amber)' : 'var(--muted)' }}>
            {v2g.recommendation}
          </div>
          {v2g.pending_approval && (
            approved
              ? <span className="pill pill-green" style={{ marginTop: 4 }}><i className="ti ti-check" /> export approved</span>
              : <button className="btn btn-primary" style={{ marginTop: 4 }} onClick={() => setApproved(true)}>
                  <i className="ti ti-bolt" /> Approve export
                </button>
          )}
        </div>
      </div>

      <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', height: 'auto', display: 'block' }}>
        {/* export windows */}
        {[...exportSet].map((h) => (
          <rect key={'e' + h} x={x(h) - 6} y={padT} width="13" height={H - padT - padB}
            fill="var(--ok)" opacity="0.12" />
        ))}
        {/* export threshold */}
        <line x1={padL} y1={y(130)} x2={W - padR} y2={y(130)} stroke="var(--ok)"
          strokeWidth="1.2" strokeDasharray="5 4" />
        <text x={W - padR} y={y(130) - 3} fontSize="10" textAnchor="end" fill="var(--ok)">export threshold</text>
        {/* price curve */}
        <polyline points={line} fill="none" stroke="var(--brand)" strokeWidth="2.2" strokeLinejoin="round" />
        {curve.map((p) => exportSet.has(p.hour) && (
          <circle key={'d' + p.hour} cx={x(p.hour)} cy={y(p.price)} r="2.2" fill="var(--ok)" />
        ))}
        <line x1={padL} y1={H - padB} x2={W - padR} y2={H - padB} stroke="var(--border)" />
        {[0, 6, 12, 18, 23].map((h) => (
          <text key={h} x={x(h)} y={H - padB + 12} fontSize="10" textAnchor="middle" fill="var(--muted)"
            fontFamily="var(--mono)">{String(h).padStart(2, '0')}:00</text>
        ))}
      </svg>

      <div style={{ marginTop: 8 }}>
        <div className="card-label" style={{ marginBottom: 6 }}>Recommended export windows</div>
        {windows.length === 0
          ? <div className="muted" style={{ fontSize: 12 }}>No profitable export windows today — hold and charge.</div>
          : (
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              {windows.map((wn, i) => (
                <span key={i} className="pill" style={{ background: wn.recommended ? 'rgba(22,163,74,.12)' : 'var(--surface2)',
                  color: wn.recommended ? 'var(--ok)' : 'var(--text)', border: '1px solid var(--border)' }}>
                  {String(wn.start).padStart(2, '0')}:00–{String(wn.end).padStart(2, '0')}:00 · ${wn.price} · {wn.export_kw}kW · +${wn.revenue}
                </span>
              ))}
            </div>
          )}
      </div>
    </div>
  )
}
