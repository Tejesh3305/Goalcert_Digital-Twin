/**
 * AuthContext.jsx — who is signed in, for the whole app.
 *
 * THE STARTUP SEQUENCE IS THE INTERESTING PART. On mount this does NOT assume
 * the user is signed out. It calls `bootstrap()`, which trades the HttpOnly
 * refresh cookie for a fresh access token, and only then decides. Until that
 * settles, `loading` is true and `RequireAuth` renders nothing.
 *
 * That ordering matters: rendering the login page first and correcting once the
 * refresh lands means every reload flashes a login form at an already-signed-in
 * user, and any redirect logic underneath it fires on a state that was never
 * true. One `loading` gate is cheaper than reasoning about that.
 *
 * DEV-MODE OPEN BACKEND. When the server runs with authentication disabled
 * (NXR_DEV_MODE), there is no session to restore and never will be — but the API
 * answers anyway. `openBackend` detects that by asking whether an unauthenticated
 * call succeeds, so local development does not require signing in, while a real
 * deployment (where that call 401s) does.
 */

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import * as authApi from '../api/auth'
import { bootstrap, getToken, subscribe } from '../api/session'

const AuthContext = createContext(null)

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null)
  const [org, setOrg] = useState(null)
  const [orgs, setOrgs] = useState([])
  const [role, setRole] = useState('read')
  const [tenants, setTenants] = useState(null)   // null = unrestricted
  const [loading, setLoading] = useState(true)
  const [openBackend, setOpenBackend] = useState(false)

  const applySession = useCallback((data) => {
    if (!data) return
    setUser(data.user || null)
    setRole(data.role || 'read')
    setOrgs(data.orgs || [])
    const active = (data.orgs || []).find((o) => o.org_id === data.org_id)
    setOrg(active || (data.org_id ? { org_id: data.org_id, name: data.org_id } : null))
  }, [])

  /** Pull the authoritative view from /auth/me — including reachable tenants. */
  const reload = useCallback(async () => {
    const token = getToken()
    if (!token) return null
    try {
      const info = await authApi.me(token)
      setUser(info.user)
      setOrg(info.org)
      setOrgs(info.orgs || [])
      setRole(info.role || 'read')
      setTenants(info.tenants)          // null means every tenant
      return info
    } catch {
      return null
    }
  }, [])

  useEffect(() => {
    let cancelled = false

    ;(async () => {
      const restored = await bootstrap()
      if (cancelled) return

      if (restored) {
        applySession(restored)
        await reload()
      } else {
        // No session. Is the backend actually demanding one? A dev server with
        // auth disabled answers this without a credential, and forcing a login
        // page in front of it would make local development require an account
        // that the open backend cannot even issue a token for.
        try {
          const base = (typeof window !== 'undefined' && window.__NXR_API_BASE__)
            || (import.meta.env && import.meta.env.VITE_API_BASE) || '/api/v1'
          const probe = await fetch(`${base}/twins`, { credentials: 'include' })
          if (!cancelled) setOpenBackend(probe.status !== 401)
        } catch {
          if (!cancelled) setOpenBackend(false)
        }
      }
      if (!cancelled) setLoading(false)
    })()

    // A refresh that fails anywhere in the app (session revoked, password
    // changed elsewhere) clears the token; this is how the UI finds out and
    // drops back to the login page without every component checking.
    const unsubscribe = subscribe((event) => {
      if (event === 'signed-out') {
        setUser(null)
        setOrg(null)
        setOrgs([])
        setTenants(null)
      }
    })

    return () => { cancelled = true; unsubscribe() }
  }, [applySession, reload])

  const login = useCallback(async (email, password) => {
    const data = await authApi.login({ email, password })
    applySession(data)
    await reload()
    return data
  }, [applySession, reload])

  const signup = useCallback(async (fields) => {
    const data = await authApi.signup(fields)
    applySession(data)
    await reload()
    return data
  }, [applySession, reload])

  const logout = useCallback(async () => {
    await authApi.logout()
    setUser(null)
    setOrg(null)
    setOrgs([])
    setTenants(null)
  }, [])

  const switchOrg = useCallback(async (orgId) => {
    const data = await authApi.switchOrg(orgId, getToken())
    applySession(data)
    await reload()
    return data
  }, [applySession, reload])

  const value = useMemo(() => ({
    user, org, orgs, role, tenants, loading, openBackend,
    // `openBackend` counts as authenticated so the app renders normally against
    // a dev server. It is NOT the same as being signed in — `user` is still
    // null, so anything that needs an identity can tell the difference.
    isAuthenticated: Boolean(user) || openBackend,
    isSignedIn: Boolean(user),
    canWrite: openBackend || ['owner', 'admin', 'write'].includes(role),
    canAdminister: openBackend || ['owner', 'admin'].includes(role),
    isOwner: role === 'owner',
    login, signup, logout, switchOrg, reload,
  }), [user, org, orgs, role, tenants, loading, openBackend,
       login, signup, logout, switchOrg, reload])

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used inside <AuthProvider>')
  return ctx
}

export default AuthContext
