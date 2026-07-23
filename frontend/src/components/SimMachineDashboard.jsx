/**
 * SimMachineDashboard — the dashboard for a *simulated* twin (Helix Data Center,
 * Forge Plant 7). These domains have no backend physics pack; they stream a
 * frontend simulation (collins/lib.jsx `simTwin`), exactly as in the Collins
 * demo. Everything else — 3-D scene, live telemetry, signal heatmap, findings,
 * fault injection and the cinematic Repair-with-AI — is the same Collins UI the
 * live machine twins use, so a sim twin is indistinguishable in look and feel.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { Card, PanelHeader } from './ui/Card'
import { HealthRing, Sparkline } from './ui/Viz'
import Scene3D from '../collins/Scene3D'
import SignalHeatmap from '../collins/Heatmap'
import Maintenance from '../collins/Maintenance'
import {
  simTwin, domainMeta, SIG, sevClass, fmt, tilesFor, FAULT_FX,
} from '../collins/lib'
import { hColor, riskFromHealth } from '../lib/machine'

const localLabel = (f) => f.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())

export default function SimMachineDashboard({ domain }) {
  const meta = domainMeta(domain)
  const [running, setRunning] = useState(true)
  const [fault, setFault] = useState('')
  const [repair, setRepair] = useState(false)
  const [twin, setTwin] = useState(() => simTwin(domain, 0))

  const phase = useRef(0)
  const faultMag = useRef(0)
  const faultRef = useRef('')
  faultRef.current = fault

  // Reset when the domain changes.
  useEffect(() => {
    phase.current = 0; faultMag.current = 0
    setFault(''); setTwin(simTwin(domain, 0))
  }, [domain])

  // Local simulation ticker (frozen when stopped).
  useEffect(() => {
    if (!running) return
    const tick = () => {
      phase.current = Math.min(1, phase.current + 0.02)
      faultMag.current = Math.max(0, Math.min(1, faultMag.current + (faultRef.current ? 0.15 : -0.3)))
      setTwin(simTwin(domain, phase.current, faultRef.current || null, faultMag.current))
    }
    tick()
    const t = setInterval(tick, 1500)
    return () => clearInterval(t)
  }, [domain, running])

  const latest = twin.latest || {}
  const health = twin.health
  const findings = twin.findings || []
  const tiles = tilesFor(domain)
  const risk = riskFromHealth(health)
  const faults = useMemo(() => Object.keys(FAULT_FX[domain] || {}), [domain])
  const headline = SIG[tiles[0]]

  // rolling history for sparklines
  const hist = useRef({})
  useEffect(() => {
    for (const [k, v] of Object.entries(latest)) {
      if (typeof v !== 'number') continue
      ;(hist.current[k] ||= []).push(v)
      if (hist.current[k].length > 30) hist.current[k].shift()
    }
  }, [latest])

  return (
    <div className="panel">
      <PanelHeader title={meta.label} subtitle={`${meta.tag} · simulated twin · streaming telemetry`}>
        <button className="btn" onClick={() => setRunning((r) => !r)}>
          <i className={`ti ${running ? 'ti-player-pause' : 'ti-player-play'}`} /> {running ? 'Stop twin' : 'Start twin'}
        </button>
        <button className="btn btn-primary repair-cta" onClick={() => setRepair(true)}
          title="Enter the AI Maintenance Director — cinematic guided repair">
          <i className="ti ti-robot" /> Repair with AI
        </button>
        {faults.length > 0 && (
          <select className="select" style={{ width: 'auto', minWidth: 160 }}
            value={fault} onChange={(e) => setFault(e.target.value)}>
            <option value="">Inject fault…</option>
            <option value="">✓ Healthy (clear)</option>
            {faults.map((f) => <option key={f} value={f}>⚠ {localLabel(f)}</option>)}
          </select>
        )}
      </PanelHeader>

      {/* KPI row */}
      <div className="grid-4 section-gap">
        <div className="card kpi">
          <div className="card-label">Twin Health</div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <HealthRing value={health} size={54} />
            <div>
              <div className="card-value" style={{ color: hColor(health), fontSize: 20 }}>{Math.round((health ?? 0) * 100)}%</div>
              <div className="card-change">physics index</div>
            </div>
          </div>
        </div>
        <div className="card kpi">
          <div className="card-label">Risk Score</div>
          <div className="card-value">{risk.score ?? '—'}</div>
          <div className="card-change" style={{ color: risk.color, fontWeight: 600 }}>{risk.label}</div>
        </div>
        <div className="card kpi">
          <div className="card-label">Active Findings</div>
          <div className="card-value" style={{ color: findings.length ? 'var(--accent-red)' : 'var(--accent-green)' }}>{findings.length}</div>
          <div className="card-change">{(twin.incidents || []).length} incident(s)</div>
        </div>
        {headline && (
          <div className="card kpi">
            <div className="card-label">{headline.label}</div>
            <div className="card-value" style={{ fontSize: 22 }}>
              {fmt(latest[tiles[0]])}<span style={{ fontSize: 13 }}>{headline.unit ? ' ' + headline.unit : ''}</span>
            </div>
            <div className="card-change">live</div>
          </div>
        )}
      </div>

      {/* 3-D scene */}
      <Card title={<><i className={`ti ${meta.icon}`} /> 3-D Twin</>}
        action={<span className="pill pill-green">● live</span>}
        className="section-gap" style={{ padding: 0, overflow: 'hidden' }}>
        <Scene3D domain={domain} machine={meta.label} live={latest} height={360} />
      </Card>

      {/* Live telemetry */}
      <Card title={<><i className="ti ti-activity" /> Live Telemetry</>}
        action={<span className="pill pill-green">● streaming</span>} className="section-gap">
        <div className="sensor-grid">
          {tiles.filter((s) => latest[s] != null).map((s) => {
            const sev = sevClass(s, latest[s])
            const cls = sev === 'crit' ? 'sensor-crit' : sev === 'warn' ? 'sensor-warn' : ''
            const col = sev === 'crit' ? '#e11d48' : sev === 'warn' ? '#d97706' : meta.accent
            return (
              <div key={s} className={`sensor-card ${cls}`} style={{ position: 'relative' }}>
                <span className="live-indicator" />
                <div className="sensor-label">{SIG[s]?.label || s}</div>
                <div><span className="sensor-value">{fmt(latest[s])}</span><span className="sensor-unit">{SIG[s]?.unit}</span></div>
                <Sparkline data={hist.current[s]} color={col} />
              </div>
            )
          })}
        </div>
      </Card>

      {/* Signal heatmap */}
      <Card title={<><i className="ti ti-grid-dots" /> Signal Heatmap</>}
        action={<span className="pill pill-surface">last 60 s</span>} className="section-gap">
        <SignalHeatmap signals={tiles} live={latest} />
      </Card>

      {/* Findings */}
      <Card title={<><i className="ti ti-alert-triangle" /> Active Findings</>}
        action={<span className={`pill ${findings.length ? 'pill-red' : 'pill-surface'}`}>{findings.length}</span>}
        className="section-gap">
        {findings.length === 0
          ? <div className="empty">No findings — within limits.</div>
          : (
            <div className="event-list">
              {findings.slice(0, 8).map((f, i) => (
                <div key={i} className="event-item">
                  <div className={`event-icon ${f.severity === 'critical' ? 'ev-crit' : 'ev-warn'}`}><i className="ti ti-alert-triangle" /></div>
                  <div className="event-body">
                    <div className="event-title">{f.displayName}</div>
                    <div className="event-meta">{f.message}</div>
                  </div>
                </div>
              ))}
            </div>
          )}
      </Card>

      {/* AI Maintenance Director — cinematic guided repair (same as live twins). */}
      {repair && (
        <Maintenance domain={domain} machineName={meta.label} twin={twin}
          modelUrl={null} claudeOn={false} onExit={() => setRepair(false)} />
      )}
    </div>
  )
}
