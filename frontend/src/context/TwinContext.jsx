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

const TwinContext = createContext(null)
const STORAGE_KEY = 'nxr_active_tenant'

export function TwinProvider({ children }) {
  const [twins, setTwins] = useState([])
  const [activeTenant, setActiveTenant] = useState(
    () => localStorage.getItem(STORAGE_KEY) || null,
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
      setActiveTenant((cur) => {
        if (cur && list.some((t) => t.tenant_id === cur)) return cur
        return list.length ? list[0].tenant_id : null
      })
    } catch (e) {
      setError(e)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { refreshTwins() }, [refreshTwins])

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
