/**
 * AuthShell.jsx — the frame every unauthenticated page renders inside.
 *
 * Exists so login, signup, forgot-password and reset-password cannot drift
 * apart visually, and so the app chrome (sidebar, twin picker, command palette)
 * is never mounted for someone who has no session — those components fetch
 * tenant data on mount, which would fire a burst of 401s behind the login form.
 *
 * WHY IT IS A TWO-PANEL LAYOUT AND NOT A CARD ON A BACKDROP
 * ---------------------------------------------------------
 * The sign-in page is the first screen anyone sees, and it was the only surface
 * in the product that did not look like the product: a dark 26rem card, a square
 * "NX" placeholder where the logo goes, and the old NextXR name — sitting in
 * front of a light Goalcert application. The mismatch read as an unfinished
 * build, and on the demo deployment it is the first impression.
 *
 * So it is built from the SAME tokens as the app shell (styles/theme.css) and the
 * same brand lockup component the sidebar uses (components/ui/Logo). The brand
 * panel carries the product gradient; the form sits on `--surface` at the app's
 * card radius, with the app's own input, focus-ring and pill-button treatments.
 * Change a token and both move together.
 *
 * The panel is decorative, so it is `aria-hidden` and the form is what a screen
 * reader lands on. Below 920px it collapses to a compact header — a marketing
 * column on a phone would push the actual form below the fold.
 */

import Logo from '../components/ui/Logo'
import { CubeIcon, GraphIcon, PulseIcon } from '../components/ui/AuthIcons'

// Three lines about what the platform IS, for the person deciding whether they
// are on the right page. Deliberately capabilities rather than adjectives.
const HIGHLIGHTS = [
  {
    Icon: GraphIcon,
    title: 'One live graph',
    text: 'Assets, telemetry and the relationships between them in a single model.',
  },
  {
    Icon: PulseIcon,
    title: 'Physics, not dashboards',
    text: 'Simulation and failure prediction running against the real signals.',
  },
  {
    Icon: CubeIcon,
    title: 'Walk the site in 3-D',
    text: 'Inspect any asset, from a building floor plan down to one machine.',
  },
]

export default function AuthShell({ title, subtitle, children, footer }) {
  return (
    <div className="auth-page">
      <aside className="auth-brand-panel" aria-hidden="true">
        <div className="auth-brand-top">
          <span className="auth-brand-chip">
            <img src="/goalcert-mark.png" alt="" width="34" height="34" />
          </span>
          <span className="auth-brand-word">
            <span className="auth-brand-name">Goalcert</span>
            <span className="auth-brand-tag">Workforce Intelligence</span>
          </span>
        </div>

        <div className="auth-brand-body">
          <h2 className="auth-brand-headline">
            Digital twins of the estate you actually operate.
          </h2>
          <ul className="auth-highlights">
            {HIGHLIGHTS.map(({ Icon, title, text }) => (
              <li key={title}>
                <span className="auth-highlight-icon"><Icon size={18} /></span>
                <div>
                  <strong>{title}</strong>
                  <span>{text}</span>
                </div>
              </li>
            ))}
          </ul>
        </div>

        <p className="auth-brand-foot">
          Every session is scoped to your organisation.
        </p>
      </aside>

      <main className="auth-form-panel">
        <div className="auth-card">
          {/* Shown only when the brand panel is collapsed, so the page still
              says whose product this is on a phone. */}
          <div className="auth-card-brand">
            <Logo variant="full" size={34} />
          </div>

          <h1 className="auth-title">{title}</h1>
          {subtitle && <p className="auth-subtitle">{subtitle}</p>}

          {children}

          {footer && <div className="auth-footer">{footer}</div>}
        </div>
      </main>
    </div>
  )
}
