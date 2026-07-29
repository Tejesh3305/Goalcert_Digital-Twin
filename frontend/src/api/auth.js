/**
 * auth.js — the account endpoints, kept apart from `client.js`.
 *
 * Separate because these calls have different rules from every other API call:
 * they must NOT carry a Bearer token that may be expired (login and refresh are
 * how you GET one), they must NOT trigger the 401-refresh-retry in `client.js`
 * (that would recurse), and the session-bearing ones must send the refresh
 * cookie with `credentials: 'include'`.
 */

import { clearSession, setSession } from './session'

function apiBase() {
  if (typeof window !== 'undefined' && window.__NXR_API_BASE__) return window.__NXR_API_BASE__
  return (import.meta.env && import.meta.env.VITE_API_BASE) || '/api/v1'
}

async function call(path, { method = 'POST', body, token } = {}) {
  const headers = { 'Content-Type': 'application/json' }
  if (token) headers['Authorization'] = `Bearer ${token}`

  const res = await fetch(`${apiBase()}/auth${path}`, {
    method,
    credentials: 'include',
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  })

  let payload = null
  try { payload = await res.json() } catch { payload = null }

  if (!res.ok) {
    const detail = payload && payload.detail
    const err = new Error(
      typeof detail === 'string' ? detail :
      detail ? JSON.stringify(detail) : `Request failed (${res.status})`,
    )
    err.status = res.status
    // The rate limiter's advice, surfaced so the form can say "try again in 42s"
    // instead of an unexplained failure. Login is limited per IP, so a shared
    // office address can legitimately hit it.
    err.retryAfter = Number(res.headers.get('Retry-After') || (payload && payload.retry_after_seconds) || 0)
    throw err
  }
  return payload
}

/** Sign in. Stores the access token in memory and returns the full session. */
export async function login({ email, password, orgId = '' }) {
  const data = await call('/login', { body: { email, password, org_id: orgId } })
  setSession(data)
  return data
}

export async function signup({ email, password, name = '', orgName = '' }) {
  const data = await call('/signup', {
    body: { email, password, name, org_name: orgName },
  })
  setSession(data)
  return data
}

export async function logout() {
  try {
    await call('/logout', { body: {} })
  } finally {
    // Clear locally whatever the server said. A failed logout call must still
    // sign the user out of THIS browser — the alternative is a user who clicked
    // "sign out", saw an error, and is still signed in.
    clearSession()
  }
}

export const me = (token) => call('/me', { method: 'GET', token })

export async function switchOrg(orgId, token) {
  const data = await call('/switch-org', { body: { org_id: orgId }, token })
  setSession(data)
  return data
}

export const changePassword = (currentPassword, newPassword, token) =>
  call('/password/change', {
    body: { current_password: currentPassword, new_password: newPassword },
    token,
  })

export const forgotPassword = (email) => call('/password/forgot', { body: { email } })

export const resetPassword = (token, newPassword) =>
  call('/password/reset', { body: { token, new_password: newPassword } })

export const verifyEmail = (token) => call('/verify-email', { body: { token } })

// ── Sessions ──
export const listSessions = (token) => call('/sessions', { method: 'GET', token })
export const revokeSession = (id, token) => call(`/sessions/${id}`, { method: 'DELETE', token })
export const revokeOtherSessions = (token) => call('/sessions/revoke-others', { body: {}, token })

// ── Organisation administration ──
export const listMembers = (orgId, token) =>
  call(`/orgs/${orgId}/members`, { method: 'GET', token })

export const inviteMember = (orgId, { email, role, name = '' }, token) =>
  call(`/orgs/${orgId}/members`, { body: { email, role, name }, token })

export const setMemberRole = (orgId, userId, role, token) =>
  call(`/orgs/${orgId}/members/${userId}`, { method: 'PATCH', body: { role }, token })

export const removeMember = (orgId, userId, token) =>
  call(`/orgs/${orgId}/members/${userId}`, { method: 'DELETE', token })

export const listKeys = (orgId, token) => call(`/orgs/${orgId}/keys`, { method: 'GET', token })

export const createKey = (orgId, { name, role, tenants = [], expiresInDays = null }, token) =>
  call(`/orgs/${orgId}/keys`, {
    body: { name, role, tenants, expires_in_days: expiresInDays },
    token,
  })

export const revokeKey = (orgId, keyId, token) =>
  call(`/orgs/${orgId}/keys/${keyId}`, { method: 'DELETE', token })

export const orgAudit = (orgId, token, limit = 100) =>
  call(`/orgs/${orgId}/audit?limit=${limit}`, { method: 'GET', token })

export const orgTenants = (orgId, token) =>
  call(`/orgs/${orgId}/tenants`, { method: 'GET', token })
