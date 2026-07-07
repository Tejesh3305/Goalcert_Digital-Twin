/**
 * MachineTwinLive — the live surface for a machine-domain twin (turbine / EDM /
 * tram fleet). Renders health, per-subsystem condition, the live sensor grid,
 * recent findings and (for the fleet domain) the network map — all polled from
 * the machine-twin runtime. Includes a live fault injector so the physics can be
 * perturbed and watched. LiveOps renders this in place of the HVAC/CFP feed when
 * the active twin is a machine domain.
 */
import { useMemo } from 'react'
import { Card } from './ui/Card'
import { Empty } from './ui/States'
import NetworkMap from './NetworkMap'
import { usePolling, useApi } from '../hooks/useApi'
import { statusColor, healthBand } from '../lib/machine'
import { localName } from '../lib/format'
import api from '../api/client'

const sevClass = { critical: 'ev-crit', warning: 'ev-warn', info: 'ev-info', ok: 'ev-ok' }

function fmt(v) {
  if (typeof v !== 'number') return v ?? '—'
  return Number.isInteger(v) ? v : v.toFixed(1)
}

export default function MachineTwinLive({ tenant, domain }) {
  const { data: state, refetch: refetchState } =
    usePolling(() => api.twinRuntimeState(tenant), 1500, [tenant], { skip: !tenant })
  const { data: diag } =
    usePolling(() => api.twinDiagnostics(tenant), 3000, [tenant], { skip: !tenant })
  const { data: net } =
    usePolling(() => api.twinNetwork(tenant).catch(() => null), 2000, [tenant],
      { skip: !tenant || domain !== 'tram-network' })
  const { data: domains } = useApi(() => api.machineDomains(), [])

  const faults = useMemo(() => {
    const d = (domains?.domains || []).find((x) => x.key === domain)
    return d?.faults || []
  }, [domains, domain])

  const health = state?.health
  const band = healthBand(health)
  const running = state?.running
  const findings = state?.findings || []
  const components = diag?.components || []
  const sensors = diag?.sensors || []

  const inject = async (fault) => {
    try { await api.twinSimulate(tenant, { fault, severity: 0.9 }); refetchState() } catch { /* ignore */ }
  }
  const toggleRunning = async () => {
    try { await api.twinRunning(tenant, !running); refetchState() } catch { /* ignore */ }
  }

  return (
    <>
      {/* Health + controls */}
      <div className="grid-2 section-gap" style={{ alignItems: 'stretch' }}>
        <Card title={<><i className="ti ti-heart-rate-monitor" /> Machine Health</>}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 18 }}>
            <div style={{ fontFamily: 'var(--display)', fontSize: 46, fontWeight: 600,
              color: band.color, lineHeight: 1 }}>
              {health != null ? Math.round(health * 100) : '—'}<span style={{ fontSize: 20 }}>%</span>
            </div>
            <div>
              <div style={{ fontWeight: 600, color: band.color }}>{band.label}</div>
              <div className="muted" style={{ fontSize: 12 }}>
                {state?.frames ?? 0} frames · {findings.length} active findings
              </div>
              <button className="btn" style={{ marginTop: 8 }} onClick={toggleRunning}>
                <i className={`ti ${running ? 'ti-player-pause' : 'ti-player-play'}`} />
                {running ? 'Pause ticker' : 'Resume ticker'}
              </button>
            </div>
          </div>
        </Card>

        <Card title={<><i className="ti ti-flask" /> Inject Fault (live what-if)</>}>
          <div className="chat-quick" style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
            {faults.map((f) => (
              <button key={f} className="quick-chip" onClick={() => inject(f)}>
                {localName(f).replace(/_/g, ' ')}
              </button>
            ))}
            <button className="quick-chip" style={{ borderColor: 'var(--ok)', color: 'var(--ok)' }}
              onClick={() => inject('none')}>
              <i className="ti ti-refresh" /> clear
            </button>
          </div>
          <div className="muted" style={{ fontSize: 11.5, marginTop: 8 }}>
            Perturbs the live physics — watch health, findings and the map respond.
          </div>
        </Card>
      </div>

      {/* Network map (fleet only) */}
      {domain === 'tram-network' && net && (
        <Card title={<><i className="ti ti-map-2" /> Live Network Map</>}
          action={net.blocked?.length
            ? <span className="pill pill-red">{net.blocked.length} route blocked</span>
            : <span className="pill pill-green">● all routes running</span>}
          style={{ marginBottom: 16 }}>
          <NetworkMap net={net} />
        </Card>
      )}

      {/* Subsystem condition */}
      <Card title={<><i className="ti ti-subtask" /> Subsystem Condition</>} style={{ marginBottom: 16 }}>
        {components.length === 0 ? <Empty label="Warming up…" icon="ti-loader" /> : (
          <div className="grid-2">
            {components.map((c) => {
              const pct = c.health != null ? Math.round(c.health * 100) : 0
              return (
                <div key={c.name} style={{ marginBottom: 6 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12.5, marginBottom: 5 }}>
                    <span>{c.name}</span>
                    <b style={{ fontFamily: 'var(--mono)', color: statusColor(c.status) }}>{pct}%</b>
                  </div>
                  <div style={{ height: 7, borderRadius: 999, background: 'var(--surface2)', overflow: 'hidden' }}>
                    <div style={{ width: `${pct}%`, height: '100%', borderRadius: 999,
                      background: statusColor(c.status), transition: 'width .5s ease' }} />
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </Card>

      {/* Live telemetry */}
      <Card title={<><i className="ti ti-activity" /> Live Telemetry</>}
        action={<span className="pill pill-green">● STREAMING</span>} style={{ marginBottom: 16 }}>
        <div className="sensor-grid">
          {sensors.map((s) => {
            const cls = s.status === 'critical' ? 'sensor-crit' : s.status === 'warning' ? 'sensor-warn' : ''
            return (
              <div key={s.signal} className={`sensor-card ${cls}`}>
                <div className="live-indicator" />
                <div className="sensor-label">{s.name}</div>
                <div className="sensor-value">{fmt(s.value)}
                  {s.unit && <span className="sensor-unit">{s.unit}</span>}</div>
              </div>
            )
          })}
        </div>
      </Card>

      {/* Findings */}
      <Card title={<><i className="ti ti-alert-triangle" /> Active Findings</>}
        action={<span className="pill pill-amber">{findings.length}</span>}>
        {findings.length === 0
          ? <Empty label="No active findings — the machine is within limits." icon="ti-shield-check" />
          : (
            <div className="event-list" style={{ maxHeight: 320, overflowY: 'auto' }}>
              {findings.map((f, i) => (
                <div key={i} className="event-item">
                  <div className={`event-icon ${sevClass[f.severity] || 'ev-info'}`}>
                    <i className="ti ti-alert-triangle" />
                  </div>
                  <div className="event-body">
                    <div className="event-title">{f.message}</div>
                    <div className="event-meta">Tier {f.tier} · {f.behaviorId}</div>
                  </div>
                  <span className="event-time" style={{ color: statusColor(f.severity) }}>{f.severity}</span>
                </div>
              ))}
            </div>
          )}
      </Card>
    </>
  )
}
