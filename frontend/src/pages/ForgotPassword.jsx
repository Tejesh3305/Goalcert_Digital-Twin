/**
 * ForgotPassword.jsx — request a reset link.
 *
 * ALWAYS SHOWS THE SAME CONFIRMATION, whether or not the address has an account.
 * The server behaves identically (it returns 200 either way — see
 * `identity/service.request_password_reset`), and the UI has to match, because a
 * page that says "no such account" for one address and "check your email" for
 * another is a bulk account-existence oracle that needs no credential to use.
 *
 * The `reset_token` branch is the local-development path: with no mailer
 * configured the server hands the token back in the response rather than
 * emailing it, so the flow is completable offline. It is absent in any
 * deployment with NXR_MAIL_FROM set.
 */

import { useState } from 'react'
import { Link } from 'react-router-dom'
import { forgotPassword } from '../api/auth'
import AuthShell from './AuthShell'

export default function ForgotPassword() {
  const [email, setEmail] = useState('')
  const [sent, setSent] = useState(false)
  const [devToken, setDevToken] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  async function onSubmit(e) {
    e.preventDefault()
    setError('')
    setBusy(true)
    try {
      const result = await forgotPassword(email.trim())
      setSent(true)
      if (result?.reset_token) setDevToken(result.reset_token)
    } catch (err) {
      setError(
        err.status === 429 && err.retryAfter
          ? `Too many requests. Try again in ${err.retryAfter} seconds.`
          : err.message || 'Could not start the reset.',
      )
    } finally {
      setBusy(false)
    }
  }

  if (sent) {
    return (
      <AuthShell
        title="Check your email"
        subtitle="If that address has an account, a reset link is on its way."
        footer={<Link to="/login">Back to sign in</Link>}
      >
        {devToken && (
          <div className="auth-note">
            <strong>No mailer configured.</strong> This deployment returned the
            token directly, so you can continue without email:
            <Link className="auth-devlink" to={`/reset-password?token=${encodeURIComponent(devToken)}`}>
              Set a new password
            </Link>
          </div>
        )}
      </AuthShell>
    )
  }

  return (
    <AuthShell
      title="Reset your password"
      subtitle="We'll send a link to your email"
      footer={<Link to="/login">Back to sign in</Link>}
    >
      <form onSubmit={onSubmit} className="auth-form">
        {error && <div className="auth-error" role="alert">{error}</div>}
        <label className="auth-field">
          <span>Email</span>
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)}
                 autoComplete="username" required autoFocus />
        </label>
        <button type="submit" className="auth-submit" disabled={busy}>
          {busy ? 'Sending…' : 'Send reset link'}
        </button>
      </form>
    </AuthShell>
  )
}
