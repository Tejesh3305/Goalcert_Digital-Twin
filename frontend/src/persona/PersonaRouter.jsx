/**
 * PersonaRouter.jsx — role in, application out.
 *
 * The single place that turns "who is this" into "which product do they see".
 * The shell, the navigation, the landing page and which deep links are reachable
 * are all derived from `workspaceFor(persona)`, so adding a third persona later
 * is a data change in `persona/nav.js` rather than a hunt through the tree.
 *
 * ONE SHELL, DIFFERENT NAVIGATION — and that is deliberate.
 * ---------------------------------------------------------
 * An earlier version gave the operator a separate phone-shaped shell: no
 * sidebar, a bottom tab bar, its own header. It was a worse product. The
 * operator lost the navigation everyone else has, the chrome no longer matched
 * the rest of the platform, and every style in it had to be written twice —
 * which is exactly how it ended up with colours that did not exist in the
 * theme. The roles genuinely differ in WHAT they can reach, not in what a
 * window should look like, so they now share `AppShell` and differ only in the
 * nav list it renders.
 *
 * THE FIRST-PAINT PROBLEM, AND WHY THIS GATES
 * -------------------------------------------
 * The persona arrives from `GET /work/me`, so for a few hundred milliseconds
 * after sign-in the app does not know which navigation to draw. Painting a guess
 * means half your users watch the wrong menu appear and then change, which reads
 * as a bug in the first second of every session — so this renders a boot state
 * until it resolves. Same call `RequireAuth` already makes for the session.
 *
 * `/` IS NOT GUARDED. Every role has its own dashboard there (RoleDashboard),
 * so there is nothing to redirect. An earlier version sent `/` to the role's
 * work page, which silently made the sidebar's "Dashboard" item unclickable —
 * it navigated and bounced back in the same tick.
 *
 * ROUTE GUARDING IS A REDIRECT, NOT A REFUSAL
 * -------------------------------------------
 * An operator who opens a supervisor's deep link lands on their own home rather
 * than a 403 panel. The refusal panels still exist inside the pages as defence,
 * and the SERVER is what actually enforces this — these redirects are a
 * courtesy, not a security boundary.
 */

import { Navigate, useLocation } from 'react-router-dom'
import Logo from '../components/ui/Logo'
import { useWork } from '../context/WorkContext'
import AppShell from './AppShell'
import { ownerOf, workspaceFor } from './nav'
import '../styles/persona.css'

/** Shown while `/work/me` is in flight. See the note above on why this gates. */
function Booting() {
  return (
    <div className="persona-boot">
      <Logo size={38} />
      <div className="persona-boot-label">Opening your workspace</div>
    </div>
  )
}

export default function PersonaRouter({ children }) {
  const { persona, loading } = useWork()
  const { pathname } = useLocation()

  if (loading) return <Booting />

  const workspace = workspaceFor(persona)

  // A path that belongs to the OTHER persona goes home instead of refusing.
  // Only applies once a persona is known — a no-persona session (dev mode) gets
  // the full nav and reaches everything, exactly as it did before personas.
  if (persona) {
    const owner = ownerOf(pathname)
    if (owner && owner !== persona) {
      return <Navigate to={workspace.home} replace />
    }
  }

  return <AppShell workspace={workspace}>{children}</AppShell>
}
