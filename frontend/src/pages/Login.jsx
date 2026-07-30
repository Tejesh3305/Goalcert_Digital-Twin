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
 *
 * THE SIGNUP LINK IS CONDITIONAL, because `NXR_ALLOW_SIGNUP` can be closed. An
 * unconditional "Create an account" link on a deployment with signup disabled is
 * a link to a form that always 403s, which reads as a broken product rather than
 * a deliberate policy. `/auth/posture` is what makes that knowable before anyone
 * signs in.
 */

import { useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import { AlertIcon, EyeIcon, EyeOffIcon } from '../components/ui/AuthIcons'
import AuthShell from './AuthShell'

export default function Login() {
  const { login, signupOpen } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()

  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [reveal, setReveal] = useState(false)
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
    <AuthShell
      title="Sign in"
      subtitle="Access your organisation's digital twins."
      footer={signupOpen
        ? <>New here? <Link to="/signup">Create an account</Link></>
        : <>Need access? Ask an administrator of your organisation for an invitation.</>}
    >
      <form onSubmit={onSubmit} className="auth-form">
        {error && (
          <div className="auth-error" role="alert">
            <AlertIcon size={16} />
            <span>{error}</span>
          </div>
        )}

        <label className="auth-field">
          <span>Email</span>
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="you@company.com"
            autoComplete="username"
            required
            autoFocus
          />
        </label>

        {/* A DIV with an explicit `htmlFor`, not a wrapping <label> like the field
            above. A <label> may not contain a second labelable control, and the
            reveal toggle is a <button> — nesting it would leave which control the
            label names ambiguous, and a click on the toggle would also be a click
            on the label. */}
        <div className="auth-field">
          <label htmlFor="login-password">Password</label>
          <div className="auth-password">
            <input
              id="login-password"
              type={reveal ? 'text' : 'password'}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              required
            />
            {/* A real button, so it is reachable by keyboard — `tabIndex={-1}` is
                deliberately NOT set: someone who cannot see what they are typing
                is exactly who needs this control. */}
            <button
              type="button"
              className="auth-reveal"
              onClick={() => setReveal((on) => !on)}
              aria-label={reveal ? 'Hide password' : 'Show password'}
              aria-pressed={reveal}
            >
              {reveal ? <EyeOffIcon size={17} /> : <EyeIcon size={17} />}
            </button>
          </div>
        </div>

        <div className="auth-links">
          <Link to="/forgot-password">Forgot your password?</Link>
        </div>

        <button type="submit" className="auth-submit" disabled={busy}>
          {busy ? 'Signing in…' : 'Sign in'}
        </button>
      </form>
    </AuthShell>
  )
}
