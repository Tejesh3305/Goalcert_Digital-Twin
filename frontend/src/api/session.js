/**
 * session.js — where the access token lives, and how it gets renewed.
 *
 * THE TOKEN IS HELD IN A MODULE VARIABLE, NOT IN localStorage.
 *
 * That is the whole point of this file. A token in localStorage is readable by
 * any JavaScript on the page, which means any successful XSS — in our code or in
 * any dependency we ship — exfiltrates a working credential that keeps working
 * after the user closes the tab. A module variable dies with the page, and is
 * not reachable through `window`.
 *
 * The cost is that a refresh (F5) loses it. That is what the REFRESH TOKEN is
 * for: it lives in an HttpOnly cookie the browser will not let script read at
 * all, scoped to /api/v1/auth, and `bootstrap()` trades it for a new access
 * token on page load. So a reload keeps you signed in without ever putting a
 * long-lived credential somewhere script can reach.
 *
 * WHY THE REFRESH IS A SINGLE-FLIGHT PROMISE
 * ------------------------------------------
 * A dashboard fires a dozen requests at once. When the token expires they all
 * 401 at once, and a naive implementation then sends a dozen refreshes. Because
 * the server ROTATES refresh tokens and treats a reused one as theft
 * (identity/service.py), that would revoke the user's whole session family and
 * log them out — a self-inflicted logout on every token expiry. `_inflight`
 * makes concurrent callers await ONE refresh.
 */

let accessToken = ''
let expiresAt = 0
let inflight = null

// Notified whenever the session changes, so AuthContext can re-render.
const listeners = new Set()

export function subscribe(fn) {
  listeners.add(fn)
  return () => listeners.delete(fn)
}

function notify(event) {
  for (const fn of listeners) {
    try { fn(event) } catch { /* a bad listener must not break the session */ }
  }
}

export function getToken() {
  return accessToken
}

export function setSession({ access_token, expires_in }) {
  accessToken = access_token || ''
  // Renew a little early. Refreshing at the exact expiry loses the race against
  // clock skew and network latency, and the failure mode is a spurious 401 the
  // user sees as a random logout.
  expiresAt = access_token ? Date.now() + Math.max(0, (expires_in || 900) - 30) * 1000 : 0
  notify(access_token ? 'signed-in' : 'signed-out')
}

export function clearSession() {
  accessToken = ''
  expiresAt = 0
  inflight = null
  notify('signed-out')
}

export function isExpired() {
  return !accessToken || Date.now() >= expiresAt
}

/** The base the app talks to — mirrors client.js so both agree in the hub. */
function apiBase() {
  if (typeof window !== 'undefined' && window.__NXR_API_BASE__) return window.__NXR_API_BASE__
  return (import.meta.env && import.meta.env.VITE_API_BASE) || '/api/v1'
}

/**
 * Exchange the refresh cookie for a new access token.
 *
 * `credentials: 'include'` is required — the cookie is HttpOnly and same-site,
 * and fetch does not send cookies by default on cross-origin requests. Without
 * it the refresh silently 401s and the user is bounced to the login page on
 * every reload.
 */
export async function refresh() {
  if (inflight) return inflight

  inflight = (async () => {
    try {
      const res = await fetch(`${apiBase()}/auth/refresh`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: '{}',
      })
      if (!res.ok) {
        clearSession()
        return null
      }
      const data = await res.json()
      setSession(data)
      return data
    } catch {
      // A network failure is NOT a sign-out. Clearing the session here would log
      // a user out every time their wifi hiccups; the token they hold may still
      // be valid, and the next request will find out.
      return null
    } finally {
      inflight = null
    }
  })()

  return inflight
}

/**
 * Called once at startup: restore a session from the refresh cookie.
 * Returns the session payload, or null when nobody is signed in.
 */
export async function bootstrap() {
  return refresh()
}

/** A valid token, refreshing first if the current one is stale. */
export async function ensureToken() {
  if (!isExpired()) return accessToken
  const data = await refresh()
  return data ? data.access_token : ''
}
