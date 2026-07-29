/**
 * ResetPassword.jsx — set a new password from a reset (or invitation) token.
 *
 * Serves BOTH flows deliberately. An invited user gets an account with a random
 * password they have never seen and a long-lived reset token; that is the same
 * operation as "I forgot mine", so it is the same page rather than a second one
 * to keep in step.
 *
 * On success this does NOT sign the user in. The server revokes every session
 * for the account on reset (`complete_password_reset`), which is the correct
 * response to "someone may have had access", and auto-signing-in here would
 * quietly undo half of that. The user types the password they just chose, once.
 */

import { useMemo, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { resetPassword } from '../api/auth'
import AuthShell from './AuthShell'

const MIN_LENGTH = 12

export default function ResetPassword() {
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const token = params.get('token') || ''

  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState(false)

  const problems = useMemo(() => {
    const out = []
    if (password && password.length < MIN_LENGTH) out.push(`At least ${MIN_LENGTH} characters`)
    if (password && new Set(password).size < 5) out.push('Too repetitive')
    if (confirm && password !== confirm) out.push('The two passwords do not match')
    return out
  }, [password, confirm])

  const valid = password.length >= MIN_LENGTH && password === confirm && !problems.length

  async function onSubmit(e) {
    e.preventDefault()
    setError('')
    setBusy(true)
    try {
      await resetPassword(token, password)
      setDone(true)
      setTimeout(() => navigate('/login', { replace: true }), 2500)
    } catch (err) {
      setError(err.message || 'Could not reset the password.')
    } finally {
      setBusy(false)
    }
  }

  if (!token) {
    return (
      <AuthShell title="Invalid link"
                 subtitle="This reset link is missing its token."
                 footer={<Link to="/forgot-password">Request a new link</Link>} />
    )
  }

  if (done) {
    return (
      <AuthShell title="Password set"
                 subtitle="Every other session has been signed out. Redirecting to sign in…"
                 footer={<Link to="/login">Sign in now</Link>} />
    )
  }

  return (
    <AuthShell title="Choose a new password"
               footer={<Link to="/login">Back to sign in</Link>}>
      <form onSubmit={onSubmit} className="auth-form">
        {error && <div className="auth-error" role="alert">{error}</div>}

        <label className="auth-field">
          <span>New password</span>
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)}
                 autoComplete="new-password" required autoFocus />
        </label>

        <label className="auth-field">
          <span>Confirm password</span>
          <input type="password" value={confirm} onChange={(e) => setConfirm(e.target.value)}
                 autoComplete="new-password" required />
        </label>

        {problems.length > 0 && (
          <ul className="auth-rules">
            {problems.map((p) => <li key={p}><span aria-hidden="true">○</span> {p}</li>)}
          </ul>
        )}

        <button type="submit" className="auth-submit" disabled={busy || !valid}>
          {busy ? 'Saving…' : 'Set password'}
        </button>
      </form>
    </AuthShell>
  )
}
