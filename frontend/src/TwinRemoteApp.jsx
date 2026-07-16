import { useEffect } from 'react'
import { MemoryRouter, useLocation, useNavigate } from 'react-router-dom'
import { ToastProvider } from './context/ToastContext'
import { TwinProvider, useTwin } from './context/TwinContext'
import TwinRoutes from './TwinRoutes'

// Self-contained mount for the hub host. The remote wraps ITSELF in its own
// providers + router — using its OWN module instances — so the host renders just
// this one federated component. (Composing providers across the federation
// boundary in the host caused the "invalid element type" #130 error.)

// Keeps the host's sidebar and this remote's router in sync BOTH ways:
//  • host nav changed  → `path` prop → navigate here
//  • internal nav here (e.g. Twins → Open → navigate('/')) → report via onNavigate
//    so the host can highlight the right sidebar item. Without this the host stays
//    on "Twins" while the dashboard shows, and the user can't navigate back.
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

export default function TwinRemoteApp({ initialPath = '/twins', path, onNavigate, onTwinChange }) {
  return (
    <ToastProvider>
      <TwinProvider>
        <TwinBridge onTwinChange={onTwinChange} />
        <MemoryRouter initialEntries={[initialPath]}>
          <RouterBridge path={path} onNavigate={onNavigate} />
          <TwinRoutes />
        </MemoryRouter>
      </TwinProvider>
    </ToastProvider>
  )
}
