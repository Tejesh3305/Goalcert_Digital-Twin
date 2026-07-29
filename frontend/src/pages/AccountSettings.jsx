/**
 * AccountSettings.jsx — the org administration surface.
 *
 * Four tabs, matching the four things an administrator actually has to do:
 * change their own password, manage who is in the organisation, issue and
 * revoke machine credentials, and read the audit trail.
 *
 * THE API-KEY FLOW IS THE ONE TO BE CAREFUL WITH. The server returns the secret
 * exactly once, at creation, and never stores it in a recoverable form. So this
 * page has to make that unmissable — the key is shown in a panel that says so,
 * and it disappears on any navigation. A UI that displays it like an ordinary
 * field teaches users it can be looked up again, and the support ticket that
 * follows cannot be answered.
 */

import { useCallback, useEffect, useState } from 'react'
import * as authApi from '../api/auth'
import { getToken } from '../api/session'
import { useAuth } from '../context/AuthContext'
import { RequireRole } from '../components/RequireAuth'

const TABS = [
  { id: 'profile', label: 'Profile' },
  { id: 'members', label: 'Members', admin: true },
  { id: 'keys', label: 'API keys', admin: true },
  { id: 'sessions', label: 'Sessions' },
  { id: 'audit', label: 'Audit log', admin: true },
]

export default function AccountSettings() {
  const { org, canAdminister, openBackend } = useAuth()
  const [tab, setTab] = useState('profile')

  if (openBackend) {
    return (
      <div className="settings-page">
        <h1>Account</h1>
        <div className="auth-note">
          This server is running with authentication disabled
          (<code>NXR_DEV_MODE</code>), so there are no accounts to manage. Start it
          without that flag to use the identity system.
        </div>
      </div>
    )
  }

  const visible = TABS.filter((t) => !t.admin || canAdminister)

  return (
    <div className="settings-page">
      <header className="settings-head">
        <h1>Account</h1>
        {org && <div className="settings-org">{org.name} <span>({org.org_id})</span></div>}
      </header>

      <nav className="settings-tabs">
        {visible.map((t) => (
          <button key={t.id}
                  className={tab === t.id ? 'active' : ''}
                  onClick={() => setTab(t.id)}>
            {t.label}
          </button>
        ))}
      </nav>

      <div className="settings-body">
        {tab === 'profile' && <ProfileTab />}
        {tab === 'members' && <RequireRole role="admin"><MembersTab /></RequireRole>}
        {tab === 'keys' && <RequireRole role="admin"><KeysTab /></RequireRole>}
        {tab === 'sessions' && <SessionsTab />}
        {tab === 'audit' && <RequireRole role="admin"><AuditTab /></RequireRole>}
      </div>
    </div>
  )
}


function ProfileTab() {
  const { user, org, role } = useAuth()
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  async function onSubmit(e) {
    e.preventDefault()
    setMessage(''); setError(''); setBusy(true)
    try {
      const res = await authApi.changePassword(current, next, getToken())
      setMessage(res.message || 'Password changed.')
      setCurrent(''); setNext('')
    } catch (err) {
      setError(err.message || 'Could not change the password.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="settings-section">
      <dl className="settings-facts">
        <dt>Name</dt><dd>{user?.name || '—'}</dd>
        <dt>Email</dt><dd>{user?.email || '—'}</dd>
        <dt>Organisation</dt><dd>{org?.name || '—'}</dd>
        <dt>Role</dt><dd><span className={`role-chip role-${role}`}>{role}</span></dd>
      </dl>

      <h2>Change password</h2>
      <form onSubmit={onSubmit} className="settings-form">
        {error && <div className="auth-error" role="alert">{error}</div>}
        {message && <div className="auth-success" role="status">{message}</div>}
        <label className="auth-field">
          <span>Current password</span>
          <input type="password" value={current} onChange={(e) => setCurrent(e.target.value)}
                 autoComplete="current-password" required />
        </label>
        <label className="auth-field">
          <span>New password</span>
          <input type="password" value={next} onChange={(e) => setNext(e.target.value)}
                 autoComplete="new-password" required />
        </label>
        <p className="settings-hint">
          Changing your password signs out every other device.
        </p>
        <button type="submit" className="auth-submit" disabled={busy}>
          {busy ? 'Saving…' : 'Change password'}
        </button>
      </form>
    </div>
  )
}


function MembersTab() {
  const { org, role: myRole } = useAuth()
  const [members, setMembers] = useState([])
  const [email, setEmail] = useState('')
  const [role, setRole] = useState('read')
  const [invite, setInvite] = useState(null)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    if (!org) return
    try {
      const data = await authApi.listMembers(org.org_id, getToken())
      setMembers(data.members || [])
    } catch (err) { setError(err.message) }
  }, [org])

  useEffect(() => { load() }, [load])

  async function onInvite(e) {
    e.preventDefault()
    setError(''); setInvite(null)
    try {
      const result = await authApi.inviteMember(org.org_id, { email: email.trim(), role }, getToken())
      setInvite(result)
      setEmail('')
      load()
    } catch (err) { setError(err.message) }
  }

  async function onRoleChange(userId, newRole) {
    setError('')
    try {
      await authApi.setMemberRole(org.org_id, userId, newRole, getToken())
      load()
    } catch (err) { setError(err.message) }
  }

  async function onRemove(userId, memberEmail) {
    if (!window.confirm(`Remove ${memberEmail} from ${org.name}? They will be signed out.`)) return
    setError('')
    try {
      await authApi.removeMember(org.org_id, userId, getToken())
      load()
    } catch (err) { setError(err.message) }
  }

  // An admin cannot grant owner — the server refuses it, and offering the option
  // only to have it rejected is a worse experience than not offering it.
  const grantable = myRole === 'owner'
    ? ['owner', 'admin', 'write', 'read']
    : ['admin', 'write', 'read']

  return (
    <div className="settings-section">
      {error && <div className="auth-error" role="alert">{error}</div>}

      {invite?.invite_token && (
        <div className="auth-note">
          <strong>No mailer is configured.</strong> Send {invite.user.email} this
          link so they can set a password:
          <code className="settings-token">
            {`${window.location.origin}/reset-password?token=${invite.invite_token}`}
          </code>
        </div>
      )}

      <h2>Invite someone</h2>
      <form onSubmit={onInvite} className="settings-inline-form">
        <input type="email" value={email} placeholder="name@company.com"
               onChange={(e) => setEmail(e.target.value)} required />
        <select value={role} onChange={(e) => setRole(e.target.value)}>
          {grantable.map((r) => <option key={r} value={r}>{r}</option>)}
        </select>
        <button type="submit">Invite</button>
      </form>

      <h2>Members</h2>
      <table className="settings-table">
        <thead>
          <tr><th>Name</th><th>Email</th><th>Role</th><th>Last sign-in</th><th /></tr>
        </thead>
        <tbody>
          {members.map((m) => (
            <tr key={m.user_id}>
              <td>{m.name || '—'}</td>
              <td>{m.email}</td>
              <td>
                <select value={m.role} onChange={(e) => onRoleChange(m.user_id, e.target.value)}>
                  {grantable.map((r) => <option key={r} value={r}>{r}</option>)}
                </select>
              </td>
              <td>{m.last_login_at ? new Date(m.last_login_at).toLocaleString() : 'never'}</td>
              <td>
                <button className="settings-danger"
                        onClick={() => onRemove(m.user_id, m.email)}>Remove</button>
              </td>
            </tr>
          ))}
          {!members.length && <tr><td colSpan="5">No members yet.</td></tr>}
        </tbody>
      </table>
    </div>
  )
}


function KeysTab() {
  const { org, tenants } = useAuth()
  const [keys, setKeys] = useState([])
  const [name, setName] = useState('')
  const [role, setRole] = useState('read')
  const [scoped, setScoped] = useState([])
  const [created, setCreated] = useState(null)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    if (!org) return
    try {
      const data = await authApi.listKeys(org.org_id, getToken())
      setKeys(data.keys || [])
    } catch (err) { setError(err.message) }
  }, [org])

  useEffect(() => { load() }, [load])

  async function onCreate(e) {
    e.preventDefault()
    setError(''); setCreated(null)
    try {
      const result = await authApi.createKey(
        org.org_id, { name: name.trim(), role, tenants: scoped }, getToken())
      setCreated(result)
      setName(''); setScoped([])
      load()
    } catch (err) { setError(err.message) }
  }

  async function onRevoke(keyId, keyName) {
    if (!window.confirm(`Revoke "${keyName}"? Anything using it stops working immediately.`)) return
    setError('')
    try {
      await authApi.revokeKey(org.org_id, keyId, getToken())
      load()
    } catch (err) { setError(err.message) }
  }

  return (
    <div className="settings-section">
      {error && <div className="auth-error" role="alert">{error}</div>}

      {created && (
        <div className="auth-note auth-note-strong">
          <strong>Copy this key now — it will never be shown again.</strong>
          <code className="settings-token">{created.secret}</code>
          <button onClick={() => navigator.clipboard?.writeText(created.secret)}>
            Copy to clipboard
          </button>
        </div>
      )}

      <h2>Issue a key</h2>
      <form onSubmit={onCreate} className="settings-inline-form">
        <input value={name} placeholder="CI pipeline"
               onChange={(e) => setName(e.target.value)} required />
        <select value={role} onChange={(e) => setRole(e.target.value)}>
          <option value="read">read</option>
          <option value="write">write</option>
          <option value="admin">admin</option>
        </select>
        <button type="submit">Create key</button>
      </form>

      {Array.isArray(tenants) && tenants.length > 0 && (
        <details className="settings-details">
          <summary>Restrict to specific twins (optional)</summary>
          <p className="settings-hint">
            Leave empty and the key reaches every twin this organisation owns,
            including ones created later.
          </p>
          {tenants.map((t) => (
            <label key={t} className="settings-check">
              <input type="checkbox" checked={scoped.includes(t)}
                     onChange={(e) => setScoped(e.target.checked
                       ? [...scoped, t]
                       : scoped.filter((x) => x !== t))} />
              {t}
            </label>
          ))}
        </details>
      )}

      <h2>Active keys</h2>
      <table className="settings-table">
        <thead>
          <tr><th>Name</th><th>Prefix</th><th>Role</th><th>Scope</th><th>Last used</th><th /></tr>
        </thead>
        <tbody>
          {keys.map((k) => (
            <tr key={k.key_id}>
              <td>{k.name || '—'}</td>
              <td><code>{k.prefix}…</code></td>
              <td><span className={`role-chip role-${k.role}`}>{k.role}</span></td>
              <td>{k.tenants.length ? k.tenants.join(', ') : 'all org twins'}</td>
              <td>{k.last_used_at ? new Date(k.last_used_at).toLocaleString() : 'never'}</td>
              <td>
                <button className="settings-danger"
                        onClick={() => onRevoke(k.key_id, k.name)}>Revoke</button>
              </td>
            </tr>
          ))}
          {!keys.length && <tr><td colSpan="6">No active keys.</td></tr>}
        </tbody>
      </table>
    </div>
  )
}


function SessionsTab() {
  const [sessions, setSessions] = useState([])
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    try {
      const data = await authApi.listSessions(getToken())
      setSessions(data.sessions || [])
    } catch (err) { setError(err.message) }
  }, [])

  useEffect(() => { load() }, [load])

  return (
    <div className="settings-section">
      {error && <div className="auth-error" role="alert">{error}</div>}
      <h2>Where you're signed in</h2>
      <table className="settings-table">
        <thead><tr><th>Started</th><th>Address</th><th>Browser</th><th /></tr></thead>
        <tbody>
          {sessions.map((s) => (
            <tr key={s.session_id}>
              <td>{new Date(s.issued_at).toLocaleString()}{s.current && ' (this one)'}</td>
              <td>{s.ip || '—'}</td>
              <td className="settings-ua">{s.user_agent || '—'}</td>
              <td>
                {!s.current && (
                  <button className="settings-danger" onClick={async () => {
                    await authApi.revokeSession(s.session_id, getToken()); load()
                  }}>Sign out</button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <button className="settings-danger" onClick={async () => {
        await authApi.revokeOtherSessions(getToken()); load()
      }}>Sign out everywhere else</button>
    </div>
  )
}


function AuditTab() {
  const { org } = useAuth()
  const [events, setEvents] = useState([])
  const [error, setError] = useState('')

  useEffect(() => {
    if (!org) return
    authApi.orgAudit(org.org_id, getToken())
      .then((d) => setEvents(d.events || []))
      .catch((err) => setError(err.message))
  }, [org])

  return (
    <div className="settings-section">
      {error && <div className="auth-error" role="alert">{error}</div>}
      <h2>Account activity</h2>
      <p className="settings-hint">
        Sign-ins, key issuance and revocation, and role changes. Changes to twin
        DATA are recorded separately in the tamper-evident change log.
      </p>
      <table className="settings-table">
        <thead><tr><th>When</th><th>Action</th><th>Target</th><th>Result</th><th>Address</th></tr></thead>
        <tbody>
          {events.map((e) => (
            <tr key={e.audit_id}>
              <td>{new Date(e.ts).toLocaleString()}</td>
              <td><code>{e.action}</code></td>
              <td>{e.target_id || '—'}</td>
              <td className={e.outcome === 'ok' ? '' : 'settings-bad'}>{e.outcome}</td>
              <td>{e.ip || '—'}</td>
            </tr>
          ))}
          {!events.length && <tr><td colSpan="5">Nothing recorded yet.</td></tr>}
        </tbody>
      </table>
    </div>
  )
}
