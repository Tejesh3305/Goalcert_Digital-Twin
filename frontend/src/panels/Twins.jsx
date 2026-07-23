import { useNavigate } from 'react-router-dom'
import { PanelHeader } from '../components/ui/Card'
import { Loading, ErrorBox } from '../components/ui/States'
import { useTwin } from '../context/TwinContext'
import { useToast } from '../context/ToastContext'
import { domainMeta } from '../lib/machine'
import { dateOf } from '../lib/format'
import { SIM_TWINS, simTenantFor } from '../lib/simTwins'
import api from '../api/client'

/**
 * Twins — the library of twins that already exist. It is NOT a catalogue for
 * creating twins: new twins are built through "Build a Twin", which maps the
 * right components / sensors / physics / behaviour from the chosen domain's
 * ontology + pack. This page just lists what you've already built (the backend
 * twins) plus the two always-on built-in twins, and opens their dashboards.
 */
export default function Twins() {
  const nav = useNavigate()
  const toast = useToast()
  const { twins, loading, error, refreshTwins, activeTenant, setActiveTenant } = useTwin()

  const openInstance = (t) => { setActiveTenant(t.tenant_id); nav('/') }
  const openSim = (domain) => { setActiveTenant(simTenantFor(domain)); nav('/') }

  const remove = async (e, t) => {
    e.stopPropagation()
    if (!confirm(`Delete twin "${t.name}"? This removes its graph entities.`)) return
    try { await api.deleteTwin(t.tenant_id); toast.ok('Twin deleted', t.name); refreshTwins() }
    catch (err) { toast.err('Delete failed', err.message) }
  }

  return (
    <div className="panel">
      <PanelHeader title="Twins"
        subtitle="Your live digital twins. To create a new one, use Build a Twin.">
        <button className="btn btn-primary" onClick={() => nav('/build')}>
          <i className="ti ti-sparkles" /> Build a Twin
        </button>
      </PanelHeader>

      {error && <ErrorBox error={error} hint="Is the backend running? (python -m server.main)" />}

      {loading && !twins.length ? <Loading label="Loading your twins…" /> : (
        <div className="grid-3 section-gap">
          {/* Backend twins (built via Build a Twin, or seeded on first boot). */}
          {twins.map((t) => {
            const m = domainMeta(t.domain)
            return (
              <div key={t.tenant_id} className={`card twin-card ${t.tenant_id === activeTenant ? 'active' : ''}`}
                style={{ cursor: 'pointer', position: 'relative' }} onClick={() => openInstance(t)}>
                <div style={{ position: 'absolute', top: 0, left: 0, right: 0, height: 3, borderRadius: '16px 16px 0 0',
                  background: `linear-gradient(90deg, ${m.accent}, ${m.accent}88)` }} />
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10, marginTop: 4 }}>
                  <div className="agent-icon" style={{ background: `${m.accent}18`, color: m.accent }}><i className={`ti ${m.icon}`} /></div>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: 14, fontWeight: 700, fontFamily: 'var(--display)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{t.name}</div>
                    <div style={{ fontSize: 11, color: 'var(--muted)' }}>{m.label}</div>
                  </div>
                  {t.tenant_id === activeTenant && <span className="pill pill-green" style={{ fontSize: 9 }}>ACTIVE</span>}
                  <span title="Delete" onClick={(e) => remove(e, t)} style={{ cursor: 'pointer', color: 'var(--hint)', fontSize: 15 }}><i className="ti ti-trash" /></span>
                </div>
                <div className="twin-meta">
                  <span><i className="ti ti-cube" /> {t.summary?.total ?? 0} entities</span>
                  <span><i className="ti ti-calendar" /> {dateOf(t.created_at)}</span>
                </div>
                <button className="btn btn-primary" style={{ width: '100%', marginTop: 12 }} onClick={(e) => { e.stopPropagation(); openInstance(t) }}>
                  <i className="ti ti-bolt" /> Open dashboard
                </button>
              </div>
            )
          })}

          {/* Always-on built-in twins (datacenter / manufacturing). */}
          {SIM_TWINS.map((s) => {
            const active = activeTenant === simTenantFor(s.domain)
            return (
              <div key={s.domain} className={`card twin-card ${active ? 'active' : ''}`}
                style={{ cursor: 'pointer', position: 'relative' }} onClick={() => openSim(s.domain)}>
                <div style={{ position: 'absolute', top: 0, left: 0, right: 0, height: 3, borderRadius: '16px 16px 0 0',
                  background: `linear-gradient(90deg, ${s.accent}, ${s.accent}88)` }} />
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10, marginTop: 4 }}>
                  <div className="agent-icon" style={{ background: `${s.accent}18`, color: s.accent }}><i className={`ti ${s.icon}`} /></div>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: 14, fontWeight: 700, fontFamily: 'var(--display)' }}>{s.label}</div>
                    <div style={{ fontSize: 11, color: 'var(--muted)' }}>{s.tag}</div>
                  </div>
                  {active && <span className="pill pill-green" style={{ fontSize: 9 }}>ACTIVE</span>}
                </div>
                <div style={{ fontSize: 12, color: 'var(--muted)', lineHeight: 1.55, marginBottom: 12, minHeight: 44 }}>{s.blurb}</div>
                <button className="btn btn-primary" style={{ width: '100%', background: s.accent, borderColor: 'transparent', boxShadow: `0 4px 14px ${s.accent}33` }}
                  onClick={(e) => { e.stopPropagation(); openSim(s.domain) }}>
                  <i className="ti ti-bolt" /> Open dashboard
                </button>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
