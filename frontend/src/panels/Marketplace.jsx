import { useState } from 'react'
import { PanelHeader } from '../components/ui/Card'
import MockBanner from '../components/ui/MockBanner'
import { useToast } from '../context/ToastContext'

/** Marketplace — MOCK. Domain packs / capability modules. The platform already
 *  has a real pack mechanism (packs/hvac is loaded); a real marketplace would
 *  install/unload packs at runtime via the bundle loader (see PLACEHOLDERS.md
 *  → "Pack marketplace"). */
const INSTALLED = [
  { icon: 'ti-wind', name: 'HVAC Pack', desc: 'Air handlers, chillers, temperature sensors + Tier A/B/C behaviours. Loaded in the core today.' },
]
const AVAILABLE = [
  { icon: 'ti-anchor', name: 'Maritime Pack', desc: 'Vessels, berths, AIS-driven movement. Maps to the same backbone.' },
  { icon: 'ti-bolt', name: 'Energy Pack', desc: 'Meters, circuits, load optimisation behaviours.' },
  { icon: 'ti-shield', name: 'Compliance Automation', desc: 'Standards as Document entities with clause→evidence mapping.' },
]

export default function Marketplace() {
  const toast = useToast()
  const [installed, setInstalled] = useState([])
  return (
    <div className="panel">
      <PanelHeader title="Pack Marketplace" subtitle="Install domain packs and capability modules" />
      <MockBanner what="Install buttons are mocked; the real pack loader already powers HVAC — runtime load/unload would extend it to any pack." />

      <div className="card-title">Installed</div>
      <div className="grid-3 section-gap">
        {INSTALLED.map((m) => (
          <div key={m.name} className="card" style={{ borderColor: 'rgba(75,139,245,.35)' }}>
            <div style={{ fontSize: 22 }}><i className={`ti ${m.icon}`} style={{ color: 'var(--accent-blue)' }} /></div>
            <div style={{ fontWeight: 600, margin: '6px 0 4px' }}>{m.name}</div>
            <div style={{ fontSize: 11, color: 'var(--muted)', lineHeight: 1.5 }}>{m.desc}</div>
            <span className="pill pill-blue" style={{ marginTop: 8, alignSelf: 'flex-start' }}>ACTIVE</span>
          </div>
        ))}
      </div>

      <div className="card-title">Available</div>
      <div className="grid-3">
        {AVAILABLE.map((m) => {
          const isIn = installed.includes(m.name)
          return (
            <div key={m.name} className="card">
              <div style={{ fontSize: 22 }}><i className={`ti ${m.icon}`} style={{ color: 'var(--accent-teal)' }} /></div>
              <div style={{ fontWeight: 600, margin: '6px 0 4px' }}>{m.name}</div>
              <div style={{ fontSize: 11, color: 'var(--muted)', lineHeight: 1.5, minHeight: 48 }}>{m.desc}</div>
              <button className="btn btn-primary" style={{ marginTop: 8 }}
                      disabled={isIn}
                      onClick={() => { setInstalled((x) => [...x, m.name]); toast.info('Demo only', `${m.name} install is mocked`) }}>
                {isIn ? 'Installed' : 'Install'}
              </button>
            </div>
          )
        })}
      </div>
    </div>
  )
}
