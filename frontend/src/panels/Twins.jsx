import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { PanelHeader } from '../components/ui/Card'
import { Loading, ErrorBox } from '../components/ui/States'
import { useApi } from '../hooks/useApi'
import { useTwin } from '../context/TwinContext'
import { useToast } from '../context/ToastContext'
import { domainMeta } from '../lib/machine'
import { dateOf } from '../lib/format'
import { SIM_TWINS, simTenantFor } from '../lib/simTwins'
import api from '../api/client'

/**
 * Twins — the library. Shows the available domain templates as "open a twin"
 * cards (the 3 machine domains first, then facilities), plus "My Twins" for
 * already-created instances. Opening a domain reuses an existing twin of that
 * kind if present, else seeds a fresh one, then jumps to its dashboard.
 */
export default function Twins() {
  const nav = useNavigate()
  const toast = useToast()
  const { twins, loading, error, refreshTwins, activeTenant, setActiveTenant } = useTwin()
  const { data: tplData } = useApi(() => api.twinTemplates(), [])
  const [building, setBuilding] = useState(null)

  const templates = tplData?.templates || []
  // machine domains first, then facilities/blank
  const order = ['defence-base', 'defence-warship', 'ev-charging-network', 'ev-battery-pack',
    'hospital-campus', 'railway-metro', 'railway-trainset', 'turbine-engine', 'edm-machine',
    'generic-facility', 'hvac', 'blank']
  const sorted = [...templates].sort((a, b) => {
    const ia = order.indexOf(a.key); const ib = order.indexOf(b.key)
    return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib)
  })

  const openDomain = async (key) => {
    setBuilding(key)
    try {
      const existing = twins.find((t) => t.domain === key)
      if (existing) {
        setActiveTenant(existing.tenant_id)
      } else {
        const meta = domainMeta(key)
        const res = await api.createTwin({ name: meta.label, domain: key })
        await refreshTwins()
        setActiveTenant(res.twin.tenant_id)
        toast.ok('Twin created', `${res.twin.name} is live`)
      }
      nav('/')
    } catch (e) {
      toast.err('Could not open twin', e.message)
    } finally {
      setBuilding(null)
    }
  }

  const openInstance = (t) => { setActiveTenant(t.tenant_id); nav('/') }

  // Sim twins (datacenter / manufacturing) — no backend; open a client-side twin.
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
        subtitle="Open a live digital twin from the library, or build a new one from an image.">
        <button className="btn btn-primary" onClick={() => nav('/build')}>
          <i className="ti ti-sparkles" /> Build from image
        </button>
      </PanelHeader>

      {error && <ErrorBox error={error} hint="Is the backend running? (python -m server.main)" />}

      {/* My Twins — already-created instances */}
      {twins.length > 0 && (
        <div className="section-gap">
          <div className="card-label" style={{ marginBottom: 10 }}><i className="ti ti-device-floppy" /> My Twins</div>
          <div className="grid-3">
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
          </div>
        </div>
      )}

      {/* Domain library */}
      <div className="card-label" style={{ margin: '4px 0 10px' }}><i className="ti ti-stack-2" /> Twin Library</div>
      {loading && !templates.length ? <Loading label="Loading library…" /> : (
        <div className="grid-3 section-gap">
          {sorted.map((tpl) => {
            const m = domainMeta(tpl.key)
            const busy = building === tpl.key
            const hasInstance = twins.some((t) => t.domain === tpl.key)
            return (
              <div key={tpl.key} className="card twin-card" style={{ position: 'relative' }}>
                <div style={{ position: 'absolute', top: 0, left: 0, right: 0, height: 3, borderRadius: '16px 16px 0 0',
                  background: `linear-gradient(90deg, ${m.accent}, ${m.accent}88)` }} />
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10, marginTop: 4 }}>
                  <div className="agent-icon" style={{ background: `${m.accent}18`, color: m.accent }}><i className={`ti ${m.icon}`} /></div>
                  <div>
                    <div style={{ fontSize: 14, fontWeight: 700, fontFamily: 'var(--display)' }}>{m.label}</div>
                    <div style={{ fontSize: 11, color: 'var(--muted)' }}>{m.tag}</div>
                  </div>
                  {m.machine && <span className="pill pill-blue" style={{ marginLeft: 'auto', fontSize: 9 }}>PHYSICS</span>}
                </div>
                <div style={{ fontSize: 12, color: 'var(--muted)', lineHeight: 1.55, marginBottom: 12, minHeight: 44 }}>{m.blurb || tpl.description}</div>
                <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 14 }}>
                  <span className="pill pill-green">● live</span>
                  {m.signals ? <span className="pill pill-surface">{m.signals} signals</span> : null}
                  {m.network && <span className="pill pill-surface">network map</span>}
                </div>
                <button className="btn btn-primary" style={{ width: '100%', background: m.accent, borderColor: 'transparent', boxShadow: `0 4px 14px ${m.accent}33` }}
                  disabled={!!building} onClick={() => openDomain(tpl.key)}>
                  {busy ? <><span className="spinner" /> Opening…</> : <><i className="ti ti-bolt" /> {hasInstance ? 'Open twin' : 'Create & open'}</>}
                </button>
              </div>
            )
          })}

          {/* Simulated twins (datacenter / manufacturing) — no backend physics. */}
          {SIM_TWINS.map((s) => (
            <div key={s.domain} className="card twin-card" style={{ position: 'relative' }}>
              <div style={{ position: 'absolute', top: 0, left: 0, right: 0, height: 3, borderRadius: '16px 16px 0 0',
                background: `linear-gradient(90deg, ${s.accent}, ${s.accent}88)` }} />
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10, marginTop: 4 }}>
                <div className="agent-icon" style={{ background: `${s.accent}18`, color: s.accent }}><i className={`ti ${s.icon}`} /></div>
                <div>
                  <div style={{ fontSize: 14, fontWeight: 700, fontFamily: 'var(--display)' }}>{s.label}</div>
                  <div style={{ fontSize: 11, color: 'var(--muted)' }}>{s.tag}</div>
                </div>
                <span className="pill pill-surface" style={{ marginLeft: 'auto', fontSize: 9 }}>SIM</span>
              </div>
              <div style={{ fontSize: 12, color: 'var(--muted)', lineHeight: 1.55, marginBottom: 12, minHeight: 44 }}>{s.blurb}</div>
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 14 }}>
                <span className="pill pill-green">● live</span>
                <span className="pill pill-surface">simulated</span>
              </div>
              <button className="btn btn-primary" style={{ width: '100%', background: s.accent, borderColor: 'transparent', boxShadow: `0 4px 14px ${s.accent}33` }}
                onClick={() => openSim(s.domain)}>
                <i className="ti ti-bolt" /> Open twin
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
