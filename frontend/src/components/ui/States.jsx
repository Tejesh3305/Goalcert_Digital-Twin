/** Shared loading / empty / error states + a tiny inline spinner. */

export function Spinner() {
  return <span className="spinner" aria-label="loading" />
}

export function Loading({ label = 'Loading…' }) {
  return <div className="loading"><Spinner /> &nbsp;{label}</div>
}

export function Empty({ label = 'Nothing here yet.', icon = 'ti-inbox' }) {
  return (
    <div className="empty">
      <i className={`ti ${icon}`} style={{ fontSize: 22, display: 'block', marginBottom: 6 }} />
      {label}
    </div>
  )
}

export function ErrorBox({ error, hint }) {
  const msg = error?.message || String(error || 'Something went wrong')
  return (
    <div className="error-box">
      <i className="ti ti-alert-triangle" /> {msg}
      {hint && <div style={{ marginTop: 6, fontSize: 11, opacity: 0.8 }}>{hint}</div>}
    </div>
  )
}
