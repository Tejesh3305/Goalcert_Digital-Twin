/**
 * Login.jsx — the sign-in page, and the shared shell the other auth pages use.
 *
 * ONE THING WORTH NOTING ABOUT THE ERROR HANDLING: whatever the server says is
 * shown verbatim. The backend deliberately returns an identical message for
 * "unknown email" and "wrong password" (identity/service.py explains why), so
 * the UI must not try to be more helpful by guessing which it was — that would
 * hand back the user-enumeration oracle the server just spent effort removing.
 *
 * The 429 case is different and IS worth expanding: the login endpoint is rate
 * limited per IP, so a shared office address can hit it legitimately. "Too many
 * requests" with no number reads as a bug; "try again in 42 seconds" reads as a
 * rule.
 */

import { useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import AuthShell from './AuthShell'

export default function Login() {
  const { login } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()

  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  // Where the user was headed before the guard bounced them here. Sending them
  // to the dashboard instead would silently discard a deep link — someone
  // opening a shared link to a specific twin should land on that twin.
  const destination = location.state?.from?.pathname || '/'

  async function onSubmit(e) {
    e.preventDefault()
    setError('')
    setBusy(true)
    try {
      await login(email.trim(), password)
      navigate(destination, { replace: true })
    } catch (err) {
      setError(
        err.status === 429 && err.retryAfter
          ? `Too many sign-in attempts. Try again in ${err.retryAfter} seconds.`
          : err.message || 'Sign-in failed.',
      )
    } finally {
      setBusy(false)
    }
  }

  return (
    <AuthShell title="Sign in" subtitle="Access your digital twins">
      <form onSubmit={onSubmit} className="auth-form">
        {error && <div className="auth-error" role="alert">{error}</div>}

        <label className="auth-field">
          <span>Email</span>
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            autoComplete="username"
            required
            autoFocus
          />
        </label>

        <label className="auth-field">
          <span>Password</span>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            required
          />
        </label>

        <button type="submit" className="auth-submit" disabled={busy}>
          {busy ? 'Signing in…' : 'Sign in'}
        </button>

        <div className="auth-links">
          <Link to="/forgot-password">Forgot your password?</Link>
          <Link to="/signup">Create an account</Link>
        </div>
      </form>
    </AuthShell>
  )
}
