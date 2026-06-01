/** Modal — overlay dialog used by Create-Twin and Add-Asset flows. */
import { useEffect } from 'react'

export function Modal({ title, subtitle, onClose, children, width }) {
  useEffect(() => {
    const onKey = (e) => e.key === 'Escape' && onClose?.()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" style={width ? { width } : undefined}
           onClick={(e) => e.stopPropagation()}>
        {title && <div className="modal-title">{title}</div>}
        {subtitle && <div className="modal-sub">{subtitle}</div>}
        {children}
      </div>
    </div>
  )
}

export function DemoNote({ children }) {
  // A small inline tag marking UI that is mocked / not yet backed by the core.
  return <span className="demo-note"><i className="ti ti-flask" /> {children}</span>
}

export function SeverityPill({ severity }) {
  const map = {
    critical: ['ev-crit', 'CRITICAL'],
    warning: ['ev-warn', 'WARNING'],
    info: ['ev-info', 'INFO'],
  }
  const [cls, label] = map[severity] || ['ev-info', (severity || 'INFO').toUpperCase()]
  const color = {
    'ev-crit': 'var(--accent-red)', 'ev-warn': 'var(--accent-amber)',
    'ev-info': 'var(--accent-blue)',
  }[cls]
  return (
    <span className="pill" style={{ background: `${color}22`, color }}>{label}</span>
  )
}
