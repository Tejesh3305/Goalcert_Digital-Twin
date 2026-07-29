/**
 * TwinContext — the currently-selected twin (tenant), shared app-wide.
 *
 * Every data panel is scoped to the active twin's tenant_id. The selection is
 * persisted to localStorage so a reload keeps you on the same twin. The twin
 * list itself is loaded here and exposed so the switcher and Twins panel stay
 * in sync after create/delete.
 */
import { createContext, useCallback, useContext, useEffect, useState } from 'react'
import api from '../api/client'
import { isSimTenant } from '../lib/simTwins'

const TwinContext = createContext(null)
const STORAGE_KEY = 'nxr_active_tenant'

/**
 * @param initialTenant  open THIS twin instead of restoring the last-opened one.
 *   The hub passes it when a work order or an assignment names a specific asset —
 *   without it the remote would restore whatever the browser last had open, and
 *   "show me the asset in this job" would show a different asset. Standalone it
 *   is undefined and the localStorage behaviour is unchanged.
 */
export function TwinProvider({ children, initialTenant }) {
  const [twins, setTwins] = useState([])
  const [activeTenant, setActiveTenant] = useState(
    () => initialTenant || localStorage.getItem(STORAGE_KEY) || null,
  )
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const refreshTwins = useCallback(async () => {
    setLoading(true)
    try {
      const data = await api.listTwins()
      const list = data.twins || []
      setTwins(list)
      setError(null)
      // Ensure the active tenant still exists; else pick the first twin.
      // Sim tenants ("sim:datacenter") have no backend record — keep them.
      setActiveTenant((cur) => {
        if (cur && (isSimTenant(cur) || list.some((t) => t.tenant_id === cur))) return cur
        return list.length ? list[0].tenant_id : null
      })
    } catch (e) {
      setError(e)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { refreshTwins() }, [refreshTwins])

  // The host can retarget an already-mounted remote (the operator moves from one
  // work order to the next without the panel being torn down).
  useEffect(() => {
    if (initialTenant) setActiveTenant(initialTenant)
  }, [initialTenant])

  useEffect(() => {
    if (activeTenant) localStorage.setItem(STORAGE_KEY, activeTenant)
  }, [activeTenant])

  const activeTwin = twins.find((t) => t.tenant_id === activeTenant) || null

  const value = {
    twins, loading, error,
    activeTenant, activeTwin,
    setActiveTenant, refreshTwins,
  }
  return <TwinContext.Provider value={value}>{children}</TwinContext.Provider>
}

export function useTwin() {
  const ctx = useContext(TwinContext)
  if (!ctx) throw new Error('useTwin must be used within TwinProvider')
  return ctx
}
