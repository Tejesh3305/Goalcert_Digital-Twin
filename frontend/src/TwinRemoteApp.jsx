import { useEffect } from 'react'
import { MemoryRouter, useLocation, useNavigate } from 'react-router-dom'
import { ToastProvider } from './context/ToastContext'
import { TwinProvider, useTwin } from './context/TwinContext'
import { WorkProvider } from './context/WorkContext'
import TwinRoutes from './TwinRoutes'
import Topbar from './components/layout/Topbar'
import Sidebar from './components/layout/Sidebar'

// Self-contained mount for the hub host. The remote wraps ITSELF in its own
// providers + router — using its OWN module instances — so the host renders just
// this one federated component. (Composing providers across the federation
// boundary in the host caused the "invalid element type" #130 error.)
//
// CHROME — this is load-bearing, not cosmetic.
// The standalone shell (App.jsx) is:
//     .app-root > Topbar + (.body > Sidebar + .content > TwinRoutes)
// and essentially every rule in styles.css hangs off `.app-root` / `.body` /
// `.content`. Rendering bare <TwinRoutes/> — which this component used to do —
// dropped all three wrappers, so NONE of the app's own styling applied: the twin
// came out unstyled and dark inside the hub while looking correct standalone.
//
// `chrome` decides how much of the shell comes along:
//   'full'    Topbar + Sidebar + routes — byte-for-byte the standalone app. This
//             is what the hub uses: it mounts the twin as a FULL-CANVAS takeover,
//             so our sidebar is the only one on screen and nothing is duplicated.
//   'bare'    the wrappers (so styling works) but no Topbar/Sidebar, for hosts
//             that supply their own navigation around a single page.
// Either way the wrappers are always present — that was the actual bug.

function RouterBridge({ path, onNavigate }) {
  const loc = useLocation()
  const navigate = useNavigate()

  useEffect(() => {
    if (path && path !== loc.pathname) navigate(path)
  }, [path]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (onNavigate) onNavigate(loc.pathname)
  }, [loc.pathname]) // eslint-disable-line react-hooks/exhaustive-deps

  return null
}

// Reports the active twin up to the host so the hub's OWN surfaces (topbar health
// chip, overview, agentic layer) track whichever twin is opened in here.
function TwinBridge({ onTwinChange }) {
  const { activeTwin, activeTenant } = useTwin()
  useEffect(() => {
    if (!onTwinChange) return
    onTwinChange(activeTenant ? { ...(activeTwin || {}), tenant_id: activeTwin?.tenant_id || activeTenant } : null)
  }, [activeTenant, activeTwin?.tenant_id]) // eslint-disable-line react-hooks/exhaustive-deps
  return null
}

// `tenant` (alias initialTenant) deep-links to a specific twin. The hub sends it
// when the surrounding work names an asset — a work order, an assignment, a
// supervisor opening the asset on their line. Without it TwinContext restores
// whatever the browser last had open, which is the wrong asset more often than not.
export default function TwinRemoteApp({
  initialPath = '/twins', path, onNavigate, onTwinChange,
  initialTenant, tenant, chrome = 'full',
}) {
  const full = chrome !== 'bare'
  return (
    <ToastProvider>
      <TwinProvider initialTenant={tenant || initialTenant}>
        {/* WorkProvider here too, so anything shared with the standalone shell
            (the Topbar's persona badge, TwinRoutes' persona-aware root) can rely
            on the context existing. It has no AuthProvider to read — see
            useAuthOptional — and degrades to "no persona" if the host's
            credential does not resolve one. */}
        <WorkProvider>
        <TwinBridge onTwinChange={onTwinChange} />
        <MemoryRouter initialEntries={[initialPath]}>
          <RouterBridge path={path} onNavigate={onNavigate} />
          <div className="app-root">
            {full && <Topbar />}
            <div className="body">
              {full && <Sidebar />}
              <div className="content">
                <TwinRoutes />
              </div>
            </div>
          </div>
        </MemoryRouter>
        </WorkProvider>
      </TwinProvider>
    </ToastProvider>
  )
}
