/**
 * Signup.jsx — create an account and its organisation in one step.
 *
 * The password rules are shown BEFORE submission and checked live, rather than
 * only being reported by the server on failure. The server is still the
 * authority (identity/passwords.py `validate_strength` is what actually
 * decides); this mirrors it so a user is not told "too short" after typing a
 * password they already committed to.
 *
 * Only the rules the server enforces are shown. Inventing extra requirements
 * here — "needs a symbol" — would be a lie about what the system wants and
 * pushes people toward `Password1!`, which NIST SP 800-63B advises against and
 * the backend deliberately does not ask for.
 */

import { useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import AuthShell from './AuthShell'

const MIN_LENGTH = 12

export default function Signup() {
  const { signup } = useAuth()
  const navigate = useNavigate()

  const [name, setName] = useState('')
  const [orgName, setOrgName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  // Mirrors identity/passwords.py. Kept as data so the list and the submit gate
  // cannot disagree.
  const rules = useMemo(() => {
    const local = email.split('@')[0].trim().toLowerCase()
    const lowered = password.toLowerCase()
    return [
      { ok: password.length >= MIN_LENGTH, text: `At least ${MIN_LENGTH} characters` },
      { ok: password.length === 0 || new Set(password).size >= 5, text: 'Not a repeated character' },
      {
        ok: !(local.length >= 4 && lowered.includes(local)),
        text: 'Does not contain your email address',
      },
    ]
  }, [password, email])

  const valid = password.length > 0 && rules.every((r) => r.ok)

  async function onSubmit(e) {
    e.preventDefault()
    setError('')
    setBusy(true)
    try {
      await signup({ email: email.trim(), password, name: name.trim(), orgName: orgName.trim() })
      navigate('/', { replace: true })
    } catch (err) {
      setError(
        err.status === 403
          ? 'Self-service signup is disabled on this deployment. Ask an administrator for an invitation.'
          : err.message || 'Could not create the account.',
      )
    } finally {
      setBusy(false)
    }
  }

  return (
    <AuthShell
      title="Create your account"
      subtitle="You'll get an organisation to hold your twins"
      footer={<>Already have an account? <Link to="/login">Sign in</Link></>}
    >
      <form onSubmit={onSubmit} className="auth-form">
        {error && <div className="auth-error" role="alert">{error}</div>}

        <label className="auth-field">
          <span>Your name</span>
          <input value={name} onChange={(e) => setName(e.target.value)}
                 autoComplete="name" required autoFocus />
        </label>

        <label className="auth-field">
          <span>Organisation</span>
          <input value={orgName} onChange={(e) => setOrgName(e.target.value)}
                 autoComplete="organization" placeholder="Acme Energy" required />
        </label>

        <label className="auth-field">
          <span>Work email</span>
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)}
                 autoComplete="username" required />
        </label>

        <label className="auth-field">
          <span>Password</span>
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)}
                 autoComplete="new-password" required />
        </label>

        <ul className="auth-rules">
          {rules.map((rule) => (
            <li key={rule.text} className={rule.ok ? 'ok' : ''}>
              <span aria-hidden="true">{rule.ok ? '✓' : '○'}</span> {rule.text}
            </li>
          ))}
        </ul>

        <button type="submit" className="auth-submit" disabled={busy || !valid}>
          {busy ? 'Creating…' : 'Create account'}
        </button>
      </form>
    </AuthShell>
  )
}
