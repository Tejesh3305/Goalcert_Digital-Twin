/**
 * RequireAuth.jsx — the route guard.
 *
 * Three states, and getting the order right is the whole job:
 *
 *   loading   the refresh cookie is still being exchanged for a token. Render
 *             NOTHING. Rendering the login page here is the classic bug: an
 *             already-signed-in user sees a login flash on every reload, and any
 *             redirect that fires underneath it acts on a state that was never
 *             true.
 *   signed in render the app.
 *   otherwise redirect to /login, REMEMBERING where they were going.
 *
 * That last part is why this passes `state={{ from: location }}`. Without it,
 * opening a shared deep link to a specific twin while signed out lands you on
 * the dashboard after signing in, and the link is silently lost.
 *
 * This is a CLIENT-SIDE guard, which means it is a user-experience control and
 * not a security control. It decides what to render, not what the API returns —
 * every route behind it is independently enforced by `server/auth.py` and
 * `server/tenancy.py`. Bypassing this in a browser console yields an app shell
 * that 401s on every call.
 */

import { Navigate, useLocation } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'

export default function RequireAuth({ children }) {
  const { isAuthenticated, loading } = useAuth()
  const location = useLocation()

  if (loading) {
    return (
      <div className="auth-loading" role="status" aria-live="polite">
        <div className="auth-spinner" aria-hidden="true" />
        <span>Restoring your session…</span>
      </div>
    )
  }

  if (!isAuthenticated) {
    return <Navigate to="/login" replace state={{ from: location }} />
  }

  return children
}

/**
 * Gate content on a ROLE rather than on being signed in.
 *
 * Used for the admin surfaces (members, API keys, audit). Renders `fallback`
 * rather than redirecting: a `read` member who reaches the settings page should
 * be told the section is not theirs, not bounced somewhere that looks like a
 * broken link.
 */
export function RequireRole({ role = 'admin', children, fallback = null }) {
  const { canWrite, canAdminister, isOwner } = useAuth()
  const allowed = role === 'owner' ? isOwner
    : role === 'admin' ? canAdminister
    : canWrite

  if (!allowed) {
    return fallback ?? (
      <div className="auth-denied">
        <h2>Not available to your role</h2>
        <p>This section needs the {role} role. Ask an administrator of your
           organisation if you need access.</p>
      </div>
    )
  }
  return children
}
