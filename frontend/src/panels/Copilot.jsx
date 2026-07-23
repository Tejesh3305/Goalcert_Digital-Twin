/**
 * Copilot — the embedded agent console.
 *
 * Every agent here runs INSIDE the twin process against the live machine-twin
 * runtime (see nextxr-ontology/copilot/). They read the same physics state the
 * 3-D scene renders, which is why no telemetry is posted from the browser — the
 * tenant is enough.
 *
 * Grouped the way the work actually flows: understand the twin → decide what to
 * do → produce the paperwork → talk it through → remember the outcome.
 *
 * (This replaced a rule-based mock chat. Nothing here is canned; if an answer
 * came from the deterministic fallback rather than Claude, <AiBadge> says so.)
 */
import { useMemo, useState } from 'react'
import { PanelHeader, Card } from '../components/ui/Card'
import { Empty, ErrorBox, Loading } from '../components/ui/States'
import NoTwin from '../components/NoTwin'
import { useTwin } from '../context/TwinContext'
import { useApi } from '../hooks/useApi'
import AgentCard from '../components/copilot/AgentCard'
import useAgent from '../components/copilot/useAgent'
import Markdown from '../components/copilot/Markdown'
import CopilotChat from '../components/copilot/CopilotChat'
import NarrationStrip from '../components/copilot/NarrationStrip'
import {
  WorkOrderView, ProcurementView, IncidentReportView, ProcedureView,
} from '../components/copilot/Structured'
import api from '../api/client'

const HORIZONS = ['1 hour', '2 hours', '6 hours', '12 hours', '24 hours', '3 days', '1 week']

const TABS = [
  { id: 'assess', label: 'Assess', icon: 'ti-stethoscope' },
  { id: 'act', label: 'Act', icon: 'ti-tool' },
  { id: 'train', label: 'Train', icon: 'ti-school' },
  { id: 'ask', label: 'Ask', icon: 'ti-message-chatbot' },
]

export default function Copilot() {
  const { activeTenant, activeTwin } = useTwin()
  const [tab, setTab] = useState('assess')
  const [horizon, setHorizon] = useState('6 hours')

  const { data: health } = useApi(() => api.copilot.health(), [])

  const ctx = useMemo(() => ({
    tenant: activeTenant,
    machine: activeTwin?.name || '',
    domain: activeTwin?.domain || '',
    horizon_label: horizon,
  }), [activeTenant, activeTwin, horizon])

  if (!activeTenant) return <NoTwin />

  const stubMode = health?.copilot?.mode === 'stub'

  return (
    <div className="panel">
      <PanelHeader
        title="Twin Copilot"
        subtitle={`${activeTwin?.name || activeTenant} · agents run in-process against the live twin`}
      >
        {health?.copilot?.claude_enabled
          ? <span className="pill pill-green"><i className="ti ti-sparkles" /> {health.copilot.model}</span>
          : <span className="pill pill-amber"><i className="ti ti-alert-triangle" /> no API key</span>}
      </PanelHeader>

      {stubMode && (
        <div className="stub-warning section-gap">
          <i className="ti ti-alert-triangle" />
          <div>
            <strong>Every agent is returning its deterministic fallback.</strong>
            <div className="stub-warning-why">
              {health?.copilot?.hint || 'Set ANTHROPIC_API_KEY and restart the server.'}
            </div>
          </div>
        </div>
      )}

      <NarrationStrip tenant={activeTenant} machine={ctx.machine} horizon={horizon} />

      <div className="copilot-tabs">
        {TABS.map((t) => (
          <button key={t.id}
                  className={`copilot-tab ${tab === t.id ? 'is-active' : ''}`}
                  onClick={() => setTab(t.id)}>
            <i className={`ti ${t.icon}`} /> {t.label}
          </button>
        ))}
        <span className="copilot-tabs-spacer" />
        {(tab === 'assess' || tab === 'act') && (
          <label className="copilot-horizon">
            Horizon
            <select className="input" value={horizon} onChange={(e) => setHorizon(e.target.value)}>
              {HORIZONS.map((h) => <option key={h} value={h}>{h}</option>)}
            </select>
          </label>
        )}
      </div>

      {tab === 'assess' && <AssessTab ctx={ctx} />}
      {tab === 'act' && <ActTab ctx={ctx} />}
      {tab === 'train' && <TrainTab ctx={ctx} />}
      {tab === 'ask' && <AskTab ctx={ctx} />}
    </div>
  )
}

// ── Assess: what is happening, and what happens next ──────────────────────

function AssessTab({ ctx }) {
  const diagnosis = useAgent(api.copilot.diagnosis)
  const analysis = useAgent(api.copilot.analysis)
  const cascade = useAgent(api.copilot.cascade)

  return (
    <>
      <AgentCard
        icon="ti-stethoscope" title="Diagnosis" slow={20}
        description="Component health, out-of-band sensors, likely root cause and prioritised actions — grounded in the fault library and this domain's compliance regime."
        agent={diagnosis} onRun={() => diagnosis.run(ctx)}
      >
        {(d) => <Markdown>{d.report}</Markdown>}
      </AgentCard>

      <AgentCard
        icon="ti-chart-line" title="Analysis" slow={20}
        description={`Present state plus the projected assessment over the next ${ctx.horizon_label}, using the twin's own forward physics.`}
        agent={analysis} onRun={() => analysis.run(ctx)}
      >
        {(d) => (
          <>
            {d.forecast_basis === 'qualitative' && (
              <div className="doc-note"><i className="ti ti-info-circle" />
                No forward physics model for this twin — the outlook is qualitative.</div>
            )}
            <Markdown>{d.report}</Markdown>
            {(d.prediction?.rul || []).length > 0 && <RulTable rul={d.prediction.rul} />}
          </>
        )}
      </AgentCard>

      <AgentCard
        icon="ti-affiliate" title="Cascade analysis" slow={12}
        description="Whether degradation in one subsystem is likely to propagate into another, and how long that takes."
        agent={cascade} onRun={() => cascade.run(ctx)}
      >
        {(d) => <Markdown>{d.cascade_analysis}</Markdown>}
      </AgentCard>
    </>
  )
}

function RulTable({ rul }) {
  return (
    <div className="doc-table-wrap" style={{ marginTop: 10 }}>
      <table className="doc-table">
        <thead><tr><th>Limit</th><th className="num">Time to limit</th><th>Within horizon</th></tr></thead>
        <tbody>
          {rul.map((r, i) => (
            <tr key={i}>
              <td>{r.mode}</td>
              <td className="num">
                {typeof r.time_to_limit_min === 'number' ? `${Math.round(r.time_to_limit_min)} min` : '—'}
              </td>
              <td>
                {r.within_horizon
                  ? <span className="pill pill-red" style={{ fontSize: 10 }}>yes</span>
                  : <span className="pill pill-surface" style={{ fontSize: 10 }}>no</span>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ── Act: the paperwork a technician and a buyer actually need ─────────────

function ActTab({ ctx }) {
  const workOrder = useAgent(api.copilot.workOrder)
  const procurement = useAgent(api.copilot.procurement)
  const incident = useAgent(api.copilot.incidentReport)

  return (
    <>
      <AgentCard
        icon="ti-clipboard-text" title="Work order" slow={35}
        description="A compliant maintenance work order from the current diagnosis: ordered steps, acceptance criteria, safety warnings, parts and sign-off authority."
        agent={workOrder} onRun={() => workOrder.run(ctx)}
      >
        {(d) => <WorkOrderView wo={d.work_order} />}
      </AgentCard>

      <AgentCard
        icon="ti-package" title="Parts procurement" slow={45}
        description="Turns the work order into part numbers, quantities, unit costs, lead times and sources. Runs the work-order agent first, so it takes longer."
        agent={procurement} onRun={() => procurement.run(ctx)}
      >
        {(d) => <ProcurementView list={d.procurement} />}
      </AgentCard>

      <AgentCard
        icon="ti-file-report" title="Incident report" slow={30}
        description="A formal report with classification, symptoms, physics evidence, probable cause, regulatory closure references and return-to-service criteria."
        agent={incident} onRun={() => incident.run(ctx)}
      >
        {(d) => <IncidentReportView report={d.report} />}
      </AgentCard>
    </>
  )
}

// ── Train: the interactive repair procedure ───────────────────────────────

function TrainTab({ ctx }) {
  const [fault, setFault] = useState('')
  const [checked, setChecked] = useState(new Set())
  const procedure = useAgent(api.copilot.procedure)

  const { data: domains } = useApi(() => api.machineDomains(), [])
  const faults = useMemo(() => {
    const d = (domains?.domains || []).find((x) => x.key === ctx.domain)
    return d?.faults || []
  }, [domains, ctx.domain])

  const toggle = (id) => setChecked((s) => {
    const n = new Set(s)
    if (n.has(id)) n.delete(id); else n.add(id)
    return n
  })

  const run = () => {
    setChecked(new Set())
    procedure.run({ machine: ctx.machine, domain: ctx.domain, fault: fault || 'none' })
  }

  return (
    <>
      <Card title="Repair procedure" className="section-gap">
        <div className="hint" style={{ marginBottom: 10 }}>
          Generates the correctly-ordered repair procedure for a fault on this machine.
          Every step carries what goes wrong if you skip it, and what goes wrong if you do it
          before its prerequisites — tick steps off to see the ordering enforced.
        </div>
        <div className="field" style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <select className="input" value={fault} onChange={(e) => setFault(e.target.value)}
                  style={{ maxWidth: 320 }}>
            <option value="">Select a fault…</option>
            {faults.map((f) => {
              const id = typeof f === 'string' ? f : f.id
              return <option key={id} value={id}>{typeof f === 'string' ? f : (f.label || f.id)}</option>
            })}
          </select>
          <button className="btn btn-primary" disabled={!fault || procedure.loading} onClick={run}>
            {procedure.loading
              ? <><span className="spinner" /> {procedure.elapsed.toFixed(0)}s</>
              : <><i className="ti ti-player-play" /> Build procedure</>}
          </button>
          {procedure.loading && (
            <span className="hint">The trainer is the slowest agent — around 70s.</span>
          )}
        </div>
      </Card>

      {procedure.error && <ErrorBox error={procedure.error} />}
      {procedure.data && !procedure.loading && (
        <Card>
          <ProcedureView procedure={procedure.data.procedure} checked={checked} onToggle={toggle} />
        </Card>
      )}
      {!procedure.data && !procedure.loading && !procedure.error && (
        <Card><Empty label="Pick a fault above to generate its training procedure." icon="ti-school" /></Card>
      )}
    </>
  )
}

// ── Ask: the two conversational agents ────────────────────────────────────

function AskTab({ ctx }) {
  return (
    <div className="grid-2">
      <Card title={<><i className="ti ti-message-chatbot" /> Dashboard copilot</>}>
        <div className="hint" style={{ marginBottom: 8 }}>
          Answers from this twin's current telemetry and findings.
        </div>
        <CopilotChat tenant={ctx.tenant} machine={ctx.machine} domain={ctx.domain} mode="dashboard" />
      </Card>
      <Card title={<><i className="ti ti-tool" /> AI mechanic</>}>
        <div className="hint" style={{ marginBottom: 8 }}>
          Multi-turn troubleshooting — it asks you diagnostic questions back and tracks a hypothesis.
        </div>
        <CopilotChat tenant={ctx.tenant} machine={ctx.machine} domain={ctx.domain} mode="troubleshoot" />
      </Card>
    </div>
  )
}

