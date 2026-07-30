/**
 * AuthIcons.jsx — the handful of icons the sign-in pages need, INLINE.
 *
 * The rest of the app draws icons from the Tabler webfont loaded off a CDN in
 * index.html (`<i class="ti ti-*">`), and that is fine for the app: it renders
 * behind a session, after everything else has already loaded.
 *
 * The auth pages are the one place it is not fine. They are the first request a
 * new visitor makes, and on a corporate network that blocks jsdelivr the webfont
 * simply never arrives — which for a glyph inside a text label is cosmetic, and
 * for the password REVEAL BUTTON means an invisible control with no text at all.
 * So these five are inline SVG and the sign-in surface has no external
 * dependency: it renders identically offline, on a locked-down network, and on a
 * cold cache.
 *
 * Paths are from the same Tabler set the webfont serves (MIT), so the two
 * treatments look like one another rather than like two icon libraries.
 */

function Icon({ children, size = 24, ...rest }) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      {...rest}
    >
      {children}
    </svg>
  )
}

/** Nodes joined by edges — the graph the platform is built on. */
export function GraphIcon(props) {
  return (
    <Icon {...props}>
      <circle cx="12" cy="5" r="2" />
      <circle cx="5" cy="19" r="2" />
      <circle cx="19" cy="19" r="2" />
      <path d="M6.5 17.3 10.7 6.9M17.5 17.3 13.3 6.9M7 19h10" />
    </Icon>
  )
}

/** A signal trace — live telemetry rather than a static report. */
export function PulseIcon(props) {
  return (
    <Icon {...props}>
      <path d="M3 12h4l3-8 4 16 3-8h4" />
    </Icon>
  )
}

/** An isometric cube — the 3-D twin. */
export function CubeIcon(props) {
  return (
    <Icon {...props}>
      <path d="M12 2.6 20 7.3 12 12 4 7.3Z" />
      <path d="M4 7.3v9.4l8 4.7 8-4.7V7.3" />
      <path d="M12 12v9.4" />
    </Icon>
  )
}

export function AlertIcon(props) {
  return (
    <Icon {...props}>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 8v4" />
      <path d="M12 16h.01" />
    </Icon>
  )
}

export function EyeIcon(props) {
  return (
    <Icon {...props}>
      <path d="M10 12a2 2 0 1 0 4 0a2 2 0 0 0 -4 0" />
      <path d="M21 12c-2.4 4-5.4 6-9 6s-6.6-2-9-6c2.4-4 5.4-6 9-6s6.6 2 9 6" />
    </Icon>
  )
}

export function EyeOffIcon(props) {
  return (
    <Icon {...props}>
      <path d="M10.585 10.587a2 2 0 0 0 2.829 2.828" />
      <path d="M16.681 16.673A8.717 8.717 0 0 1 12 18c-3.6 0-6.6-2-9-6 1.272-2.12 2.712-3.678 4.32-4.674m2.86-1.146A9.055 9.055 0 0 1 12 6c3.6 0 6.6 2 9 6a16.4 16.4 0 0 1-2.138 2.87" />
      <path d="m3 3 18 18" />
    </Icon>
  )
}
