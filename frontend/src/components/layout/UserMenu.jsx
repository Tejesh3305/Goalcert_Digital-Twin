/**
 * UserMenu.jsx — the signed-in identity in the top bar: who you are, which
 * organisation you are acting for, and the way out.
 *
 * THE ORG SWITCHER IS NOT COSMETIC. Switching re-issues the session for the
 * other organisation (a new token, a new scope) and revokes the old one, so the
 * twins you can see change with it. A user belonging to two customers is
 * genuinely acting as one or the other, never both — which is why this shows the
 * active org prominently rather than tucking it into a settings page.
 */

import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useAuth } from '../../context/AuthContext'

export default function UserMenu() {
  const { user, org, orgs, role, logout, switchOrg, openBackend } = useAuth()
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const ref = useRef(null)
  const navigate = useNavigate()

  // Close on an outside click. Without it the panel stays open behind whatever
  // the user clicks next, which on a dashboard is almost always a chart.
  useEffect(() => {
    if (!open) return
    function onDocClick(e) {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false)
    }
    function onEscape(e) { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', onDocClick)
    document.addEventListener('keydown', onEscape)
    return () => {
      document.removeEventListener('mousedown', onDocClick)
      document.removeEventListener('keydown', onEscape)
    }
  }, [open])

  if (openBackend && !user) {
    return <span className="user-menu-dev" title="NXR_DEV_MODE — authentication disabled">dev mode</span>
  }
  if (!user) return null

  const initials = (user.name || user.email || '?')
    .split(/[\s@.]+/).filter(Boolean).slice(0, 2)
    .map((p) => p[0].toUpperCase()).join('')

  async function onSwitch(orgId) {
    if (orgId === org?.org_id) return
    setBusy(true)
    try {
      await switchOrg(orgId)
      setOpen(false)
      // The active twin belonged to the previous org and is no longer reachable.
      // Landing on the dashboard is correct; staying put would show a 403.
      navigate('/', { replace: true })
    } finally {
      setBusy(false)
    }
  }

  async function onLogout() {
    await logout()
    navigate('/login', { replace: true })
  }

  return (
    <div className="user-menu" ref={ref}>
      <button className="user-menu-trigger" onClick={() => setOpen((v) => !v)}
              aria-expanded={open} aria-haspopup="menu">
        <span className="user-avatar" aria-hidden="true">{initials}</span>
        <span className="user-menu-labels">
          <span className="user-menu-name">{user.name || user.email}</span>
          {org && <span className="user-menu-org">{org.name}</span>}
        </span>
      </button>

      {open && (
        <div className="user-menu-panel" role="menu">
          <div className="user-menu-head">
            <div className="user-menu-email">{user.email}</div>
            <span className={`role-chip role-${role}`}>{role}</span>
          </div>

          {orgs.length > 1 && (
            <div className="user-menu-section">
              <div className="user-menu-section-title">Organisation</div>
              {orgs.map((o) => (
                <button key={o.org_id} role="menuitem" disabled={busy}
                        className={`user-menu-item ${o.org_id === org?.org_id ? 'active' : ''}`}
                        onClick={() => onSwitch(o.org_id)}>
                  <span>{o.name}</span>
                  <span className="user-menu-role">{o.role}</span>
                </button>
              ))}
            </div>
          )}

          <div className="user-menu-section">
            <Link to="/account" role="menuitem" className="user-menu-item"
                  onClick={() => setOpen(false)}>
              Account settings
            </Link>
            <button role="menuitem" className="user-menu-item user-menu-signout"
                    onClick={onLogout}>
              Sign out
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
