/**
 * AuthShell.jsx — the frame every unauthenticated page renders inside.
 *
 * Exists so login, signup, forgot-password and reset-password cannot drift
 * apart visually, and so the app chrome (sidebar, twin picker, command palette)
 * is never mounted for someone who has no session — those components fetch
 * tenant data on mount, which would fire a burst of 401s behind the login form.
 */

export default function AuthShell({ title, subtitle, children, footer }) {
  return (
    <div className="auth-page">
      <div className="auth-card">
        <div className="auth-brand">
          <div className="auth-logo" aria-hidden="true">NX</div>
          <div>
            <div className="auth-brand-name">NextXR</div>
            <div className="auth-brand-sub">Digital Twin Platform</div>
          </div>
        </div>

        <h1 className="auth-title">{title}</h1>
        {subtitle && <p className="auth-subtitle">{subtitle}</p>}

        {children}

        {footer && <div className="auth-footer">{footer}</div>}
      </div>
    </div>
  )
}
