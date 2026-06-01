import { PanelHeader, Card } from '../components/ui/Card'
import MockBanner from '../components/ui/MockBanner'
import NoTwin from '../components/NoTwin'
import { useTwin } from '../context/TwinContext'

/** Compliance — MOCK. Illustrative standard coverage + gaps. A real version
 *  models standards/clauses as Document entities and maps Asset→SOP→Clause→
 *  Evidence in the graph (see PLACEHOLDERS.md → "Compliance twin"). */
const STANDARDS = [
  { name: 'ISO 55001 Asset Management', pct: 82, color: 'var(--accent-blue)' },
  { name: 'ISO 27001 Information Security', pct: 68, color: 'var(--accent-amber)' },
  { name: 'Safe Work — Plant Safety', pct: 91, color: 'var(--accent-green)' },
  { name: 'Local Building Code', pct: 77, color: 'var(--accent-blue)' },
]
const GAPS = [
  { sev: 'red', t: 'ISO 55001 §8.1', d: 'Asset lifecycle plan missing for AHU-01' },
  { sev: 'amber', t: 'ISO 27001 A.12.6', d: 'Vulnerability scan overdue by 14 days' },
]

export default function Compliance() {
  const { activeTenant } = useTwin()
  if (!activeTenant) return <NoTwin />
  return (
    <div className="panel">
      <PanelHeader title="Compliance Twin" subtitle="Standard coverage · gap detection · evidence mapping" />
      <MockBanner what="Coverage and gaps are illustrative; real compliance models clauses as Document entities and links Asset→SOP→Clause→Evidence in the graph." />
      <div className="grid-2">
        <Card title="Standard Coverage">
          {STANDARDS.map((s) => (
            <div key={s.name} className="bar-row">
              <div className="bar-label"><span>{s.name}</span><b style={{ color: s.color }}>{s.pct}%</b></div>
              <div className="bar-track"><div className="bar-fill" style={{ width: `${s.pct}%`, background: s.color }} /></div>
            </div>
          ))}
        </Card>
        <Card title={<>AI-Identified Gaps <span className="pill" style={{ background: 'rgba(226,86,78,.12)', color: 'var(--accent-red)' }}>{GAPS.length} open</span></>}>
          {GAPS.map((g) => (
            <div key={g.t} style={{ display: 'flex', gap: 8, padding: 8, marginBottom: 7, borderRadius: 6,
              background: g.sev === 'red' ? 'rgba(226,86,78,.05)' : 'rgba(224,150,47,.05)',
              border: `1px solid ${g.sev === 'red' ? 'rgba(226,86,78,.15)' : 'rgba(224,150,47,.15)'}` }}>
              <i className={`ti ti-alert-triangle`} style={{ color: g.sev === 'red' ? 'var(--accent-red)' : 'var(--accent-amber)', marginTop: 1 }} />
              <div style={{ fontSize: 11 }}><b>{g.t}</b><br /><span className="muted">{g.d}</span></div>
            </div>
          ))}
        </Card>
      </div>
    </div>
  )
}
