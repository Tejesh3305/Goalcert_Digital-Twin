/**
 * MachineDashboard — the unified live dashboard for a machine-domain twin
 * (turbine / EDM / tram). One page, collins-demo style: 3D/network scene on top,
 * KPIs (health ring, risk, findings), the embedded AI co-pilot (narration,
 * predictive alert and live Q&A — real agents from copilot/, not canned
 * replies), live telemetry with sparklines, subsystem condition, active
 * findings and an agent-generated work order. Everything polls the
 * machine-twin runtime.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { Card } from './ui/Card'
import { Empty } from './ui/States'
import { HealthRing, Sparkline } from './ui/Viz'
import RailwayNetworkMap from './RailwayNetworkMap'
import RailwayDepotBoard from './RailwayDepotBoard'
import HospitalCampusViews from './HospitalCampusViews'
import EVNetworkViews from './EVNetworkViews'
import EVBatteryHeatmap from './EVBatteryHeatmap'
import DefenceBaseViews from './DefenceBaseViews'
import DefenceDamageControl from './DefenceDamageControl'
import TurbineModel from './TurbineModel'
import Scene3D from './Scene3D'
import GlbViewer from './GlbViewer'
import { usePolling, useApi } from '../hooks/useApi'
import {
  domainMeta, statusColor, healthBand, riskFromHealth, hColor, isNetworkDomain,
} from '../lib/machine'
import { localName } from '../lib/format'
import NarrationStrip from './copilot/NarrationStrip'
import CopilotChat from './copilot/CopilotChat'
import AgentCard from './copilot/AgentCard'
import useAgent from './copilot/useAgent'
import { WorkOrderView } from './copilot/Structured'
import Maintenance from '../collins/Maintenance'
import SignalHeatmap from '../collins/Heatmap'
import CascadeGraph from '../collins/CascadeGraph'
import EVWorld from '../collins/EVWorld'
import CollinsNetworkMap from '../collins/NetworkMap'
import { toCollinsDomain, toCollinsTwin, toCollinsLatest, maintSupported } from '../collins/adapter'
import api, { assetUrl } from '../api/client'

const sevClass = { critical: 'ev-crit', warning: 'ev-warn', info: 'ev-info', ok: 'ev-ok' }
const fmt = (v) => (typeof v !== 'number' ? (v ?? '—') : Number.isInteger(v) ? v : v.toFixed(1))

export default function MachineDashboard({ tenant, domain, name }) {
  const meta = domainMeta(domain)
  const [repair, setRepair] = useState(false)   // AI Maintenance Director overlay
  const { data: state, refetch } = usePolling(() => api.twinRuntimeState(tenant), 1500, [tenant], { skip: !tenant })
  const { data: diag } = usePolling(() => api.twinDiagnostics(tenant), 3000, [tenant], { skip: !tenant })
  const { data: net } = usePolling(() => api.twinNetwork(tenant).catch(() => null), 2000, [tenant],
    { skip: !tenant || !isNetworkDomain(domain) })
  const { data: domains } = useApi(() => api.machineDomains(), [])
  // If this twin was built from a photo, its reconstructed GLB is the model to
  // show (not the stock one). Cached as an object-scan scene keyed by tenant.
  const { data: sceneRes } = useApi(() => api.twinSceneByTenant(tenant).catch(() => null), [tenant])
  // assetUrl(): the backend returns '/api/v1/threed/...', which resolves against the
  // HUB's origin (404 -> black canvas) when this runs federated. Re-base it.
  const reconUrl = assetUrl(sceneRes?.scene_result?.model_url)

  const health = state?.health
  const running = state?.running
  const latest = state?.latest || {}
  const findings = state?.findings || []
  const components = diag?.components || []
  const sensors = diag?.sensors || []
  const risk = riskFromHealth(health)
  const faults = useMemo(
    () => (domains?.domains || []).find((d) => d.key === domain)?.faults || [], [domains, domain])

  // Collins-vocabulary view of the live frame, for the ported Collins widgets
  // (signal heatmap, and the cinematic Repair-with-AI overlay).
  const collinsLive = useMemo(() => toCollinsLatest(domain, latest), [domain, latest])
  const collinsSignals = useMemo(
    () => Object.keys(toCollinsLatest(domain, latest)),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [domain, Object.keys(latest).length])
  const canRepair = maintSupported(domain)

  // rolling history for sparklines
  const hist = useRef({})
  useEffect(() => {
    for (const [k, v] of Object.entries(latest)) {
      if (typeof v !== 'number') continue
      ;(hist.current[k] ||= []).push(v)
      if (hist.current[k].length > 30) hist.current[k].shift()
    }
  }, [latest])

  const inject = async (f) => { try { await api.twinSimulate(tenant, { fault: f, severity: 0.9 }); refetch() } catch { /* */ } }
  const toggleRunning = async () => { try { await api.twinRunning(tenant, !running); refetch() } catch { /* */ } }

  return (
    <div className="panel">
      <div className="panel-header">
        <div>
          <div className="panel-title">{name || meta.label}</div>
          <div className="panel-subtitle">{meta.tag} · live physics twin · streaming telemetry</div>
        </div>
        <div className="panel-actions">
          <button className="btn" onClick={toggleRunning}>
            <i className={`ti ${running ? 'ti-player-pause' : 'ti-player-play'}`} /> {running ? 'Stop twin' : 'Start twin'}
          </button>
          {canRepair && (
            <button className="btn btn-primary repair-cta" onClick={() => setRepair(true)}
              title="Enter the AI Maintenance Director — cinematic guided repair">
              <i className="ti ti-robot" /> Repair with AI
            </button>
          )}
          {faults.length > 0 && (
            <select className="select" style={{ width: 'auto', minWidth: 150 }} value=""
              onChange={(e) => e.target.value && inject(e.target.value)}>
              <option value="">Inject fault…</option>
              <option value="none">✓ Healthy (clear)</option>
              {faults.map((f) => <option key={f} value={f}>⚠ {localName(f).replace(/_/g, ' ')}</option>)}
            </select>
          )}
        </div>
      </div>

      {/* KPI row */}
      <div className="grid-4 section-gap">
        <div className="card kpi">
          <div className="card-label">Twin Health</div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <HealthRing value={health} size={54} />
            <div>
              <div className="card-value" style={{ color: hColor(health), fontSize: 20 }}>{healthBand(health).label}</div>
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
          <div className="card-change">{state?.incidents?.length || 0} incident(s)</div>
        </div>
        <div className="card kpi">
          <div className="card-label">{sensors[0]?.name || 'Signal'}</div>
          <div className="card-value" style={{ fontSize: 22 }}>
            {fmt(sensors[0]?.value)}<span style={{ fontSize: 13 }}>{sensors[0]?.unit ? ' ' + sensors[0].unit : ''}</span>
          </div>
          <div className="card-change">{state?.frames ?? 0} frames</div>
        </div>
      </div>

      {/* Hospital campus: the five clinical views (own cards) */}
      {domain === 'hospital-campus' ? (
        <div className="section-gap"><HospitalCampusViews net={net} tenant={tenant} /></div>
      ) : domain === 'ev-charging-network' ? (
        <>
          {/* The live 3-D energy-site world (Collins) sits above the network views. */}
          <Card title={<><i className="ti ti-charging-pile" /> Live Energy Site</>}
            action={<span className="pill pill-green">● live</span>}
            className="section-gap" style={{ padding: 0, overflow: 'hidden' }}>
            <EVWorld live={collinsLive} machine={name || meta.label} height={440} />
          </Card>
          <div className="section-gap"><EVNetworkViews net={net} /></div>
        </>
      ) : domain === 'tram-network' ? (
        <Card title={<><i className="ti ti-train" /> Live Tram Network</>}
          action={net?.blocked?.length
            ? <span className="pill pill-red">{net.blocked.length} route blocked</span>
            : <span className="pill pill-green">● live</span>}
          className="section-gap" style={{ padding: 0, overflow: 'hidden' }}>
          <CollinsNetworkMap tenant={tenant} running={running} height={460} />
        </Card>
      ) : domain === 'ev-battery-pack' ? (
        <Card title={<><i className="ti ti-grid-dots" /> Battery Cell Heatmap</>}
          action={<span className="pill pill-green">● live</span>} className="section-gap">
          {net ? <EVBatteryHeatmap net={net} /> : <Empty label="Loading cells…" icon="ti-loader" />}
        </Card>
      ) : domain === 'defence-base' ? (
        <div className="section-gap"><DefenceBaseViews net={net} /></div>
      ) : domain === 'defence-warship' ? (
        <Card title={<><i className="ti ti-ship" /> Damage Control</>}
          action={net?.ship?.capsize_risk ? <span className="pill pill-red">capsize risk</span> : <span className="pill pill-green">● live</span>}
          className="section-gap">
          {net ? <DefenceDamageControl net={net} /> : <Empty label="Loading…" icon="ti-loader" />}
        </Card>
      ) : (
        /* 3D / network scene */
        <Card title={<><i className={`ti ${meta.icon}`} /> {domain === 'railway-metro' ? 'Live Metro Network' : '3-D Twin'}</>}
          action={isNetworkDomain(domain) && net?.blocked?.length
            ? <span className="pill pill-red">{net.blocked.length} {domain === 'railway-metro' ? 'line' : 'route'} blocked</span>
            : <span className="pill pill-green">● live</span>}
          className="section-gap">
          {domain === 'railway-metro'
            ? (net ? <RailwayNetworkMap net={net} /> : <Empty label="Loading network…" icon="ti-loader" />)
            : reconUrl
              ? <GlbViewer url={reconUrl} height={340} label="Reconstructed model · live twin" />
              : domain === 'turbine-engine'
                ? <TurbineModel latest={latest} health={health} height={340} />
                : domain === 'edm-machine'
                  ? <Scene3D domain="edm-machine" machine={name || meta.label} live={latest} height={340} />
                  : <MachineHero meta={meta} name={name || meta.label} health={health} latest={latest} />}
        </Card>
      )}

      {/* Depot board (metro only) */}
      {domain === 'railway-metro' && net?.depots && (
        <Card title={<><i className="ti ti-building-warehouse" /> Depot Board</>}
          action={<span className="pill pill-surface">predicted availability</span>} className="section-gap">
          <RailwayDepotBoard depots={net.depots} />
        </Card>
      )}

      {/* AI co-pilot — the embedded agent layer, reading this twin's live physics
          in-process. Narration + predictive alert on top, Q&A below. */}
      <div className="section-gap">
        <NarrationStrip tenant={tenant} machine={name || meta.label} />
      </div>
      <Card title={<><i className="ti ti-message-chatbot" /> AI Co-Pilot</>}
        action={<span className="pill pill-green" style={{ fontSize: 9 }}>● live agent</span>}
        className="section-gap">
        <CopilotChat tenant={tenant} machine={name || meta.label} domain={domain}
          mode="dashboard" height={260} />
      </Card>

      {/* Live telemetry */}
      <Card title={<><i className="ti ti-activity" /> Live Telemetry</>}
        action={<span className="pill pill-green">● streaming</span>} className="section-gap">
        {sensors.length === 0 ? <Empty label="Warming up…" icon="ti-loader" /> : (
          <div className="sensor-grid">
            {sensors.map((s) => {
              const cls = s.status === 'critical' ? 'sensor-crit' : s.status === 'warning' ? 'sensor-warn' : ''
              const col = s.status === 'critical' ? '#e11d48' : s.status === 'warning' ? '#d97706' : meta.accent
              return (
                <div key={s.signal} className={`sensor-card ${cls}`} style={{ position: 'relative' }}>
                  <span className="live-indicator" />
                  <div className="sensor-label">{s.name}</div>
                  <div><span className="sensor-value">{fmt(s.value)}</span><span className="sensor-unit">{s.unit}</span></div>
                  <Sparkline data={hist.current[s.signal]} color={col} />
                </div>
              )
            })}
          </div>
        )}
      </Card>

      {/* Signal anomaly heatmap (Collins) — last 60 s, coloured by severity */}
      {collinsSignals.length > 0 && (
        <Card title={<><i className="ti ti-grid-dots" /> Signal Heatmap</>}
          action={<span className="pill pill-surface">last 60 s</span>} className="section-gap">
          <SignalHeatmap signals={collinsSignals} live={collinsLive} />
        </Card>
      )}

      <div className="grid-2 section-gap">
        {/* Subsystem condition */}
        <Card title={<><i className="ti ti-subtask" /> Subsystem Condition</>}>
          {components.length === 0 ? <Empty label="Warming up…" icon="ti-loader" /> : components.map((c) => {
            const pct = c.health != null ? Math.round(c.health * 100) : 0
            return (
              <div key={c.name} className="bar-row">
                <div className="bar-label"><span>{c.name}</span><b style={{ color: statusColor(c.status) }}>{pct}%</b></div>
                <div className="bar-track"><div className="bar-fill" style={{ width: `${pct}%`, background: statusColor(c.status) }} /></div>
              </div>
            )
          })}
        </Card>

        {/* Active findings */}
        <Card title={<><i className="ti ti-alert-triangle" /> Active Findings</>}
          action={<span className={`pill ${findings.length ? 'pill-red' : 'pill-surface'}`}>{findings.length}</span>}>
          {findings.length === 0
            ? <Empty label="No findings — within limits." icon="ti-shield-check" />
            : (
              <div className="event-list" style={{ maxHeight: 280, overflowY: 'auto' }}>
                {findings.map((f, i) => (
                  <div key={i} className="event-item">
                    <div className={`event-icon ${sevClass[f.severity] || 'ev-info'}`}><i className="ti ti-alert-triangle" /></div>
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
      </div>

      {/* Maintenance work order — the real agent, grounded in this twin's
          diagnosis and the domain's compliance regime. */}
      <div className="section-gap">
        <DashboardWorkOrder tenant={tenant} machine={name || meta.label} domain={domain} />
      </div>

      {/* Cascade analysis — the real copilot agent, rendered as a graph. */}
      <div className="section-gap">
        <DashboardCascade tenant={tenant} machine={name || meta.label} domain={domain} findings={findings} />
      </div>

      <div className="hint" style={{ textAlign: 'center', paddingBottom: 4 }}>
        Diagnosis, analysis, cascade, procurement, incident reports and the repair trainer live in{' '}
        <Link to="/copilot"><i className="ti ti-sparkles" /> Twin Copilot</Link>.
      </div>

      {/* AI Maintenance Director — the cinematic guided-repair takeover (Collins),
          driven by this live twin's domain, health and findings. */}
      {repair && (
        <Maintenance
          domain={toCollinsDomain(domain)}
          machineName={name || meta.label}
          twin={toCollinsTwin(domain, state)}
          modelUrl={reconUrl || null}
          claudeOn={false}
          onExit={() => setRepair(false)}
        />
      )}
    </div>
  )
}

/** Cascade analysis from the real copilot agent, drawn as a propagation graph. */
function DashboardCascade({ tenant, machine, domain, findings }) {
  const cascade = useAgent(api.copilot.cascade)
  return (
    <AgentCard
      icon="ti-affiliate" title="Cascade Analysis" slow={35} cta="Run analysis"
      description="Reason about how degradation in one subsystem propagates to others over the forecast horizon."
      agent={cascade}
      onRun={() => cascade.run({ tenant, machine, domain })}
    >
      {(d) => <CascadeGraph text={d.cascade_analysis || d.analysis || d.report || ''} findings={findings} />}
    </AgentCard>
  )
}

/** Work order from the real agent (copilot/), replacing a hand-written template
 *  whose "parts required" were a hardcoded per-domain list. */
function DashboardWorkOrder({ tenant, machine, domain }) {
  const workOrder = useAgent(api.copilot.workOrder)
  return (
    <AgentCard
      icon="ti-file-certificate" title="Maintenance Work Order" slow={35}
      cta="Generate"
      description="Generated from the current diagnosis: ordered steps with acceptance criteria, safety warnings, parts and the required sign-off authority."
      agent={workOrder}
      onRun={() => workOrder.run({ tenant, machine, domain })}
    >
      {(d) => <WorkOrderView wo={d.work_order} />}
    </AgentCard>
  )
}

/** A lightweight animated hero for non-network machine twins (turbine / EDM). */
function MachineHero({ meta, name, health, latest }) {
  const keys = Object.keys(latest).slice(0, 3)
  return (
    <div style={{ position: 'relative', height: 300, borderRadius: 12, overflow: 'hidden',
      background: `radial-gradient(circle at 50% 30%, ${meta.accent}22, var(--surface2) 70%)`,
      border: '1px solid var(--border)', display: 'flex', flexDirection: 'column',
      alignItems: 'center', justifyContent: 'center', gap: 14 }}>
      <div style={{ position: 'absolute', inset: 0, opacity: 0.08,
        background: `repeating-linear-gradient(45deg, ${meta.accent}, ${meta.accent} 1px, transparent 1px, transparent 14px)` }} />
      <div style={{ width: 96, height: 96, borderRadius: '50%', display: 'flex', alignItems: 'center', justifyContent: 'center',
        fontSize: 46, color: '#fff', background: `linear-gradient(135deg, ${meta.accent}, ${meta.accent}aa)`,
        boxShadow: `0 10px 40px ${meta.accent}55`, animation: 'pulse 2.4s ease-in-out infinite' }}>
        <i className={`ti ${meta.icon}`} />
      </div>
      <div style={{ fontFamily: 'var(--display)', fontSize: 18, fontWeight: 600, zIndex: 1 }}>{name}</div>
      <div style={{ display: 'flex', gap: 10, zIndex: 1, flexWrap: 'wrap', justifyContent: 'center' }}>
        {keys.map((k) => (
          <span key={k} className="pill pill-surface" style={{ fontFamily: 'var(--mono)' }}>
            {localName(k)}: <b>{fmt(latest[k])}</b>
          </span>
        ))}
      </div>
    </div>
  )
}

