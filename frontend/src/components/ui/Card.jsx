/** Card + KpiCard + PanelHeader — the layout primitives reused everywhere. */

export function Card({ title, action, children, style, className = '' }) {
  return (
    <div className={`card ${className}`} style={style}>
      {(title || action) && (
        <div className="card-title">
          <span style={{ flex: 1 }}>{title}</span>
          {action}
        </div>
      )}
      {children}
    </div>
  )
}

export function KpiCard({ label, value, change, changeDir, valueColor }) {
  return (
    <div className="card">
      <div className="card-label">{label}</div>
      <div className="card-value" style={valueColor ? { color: valueColor } : undefined}>
        {value}
      </div>
      {change && (
        <div className={`card-change ${changeDir || ''}`}>{change}</div>
      )}
    </div>
  )
}

export function PanelHeader({ title, subtitle, children }) {
  return (
    <div className="panel-header">
      <div>
        <div className="panel-title">{title}</div>
        {subtitle && <div className="panel-subtitle">{subtitle}</div>}
      </div>
      {children && <div className="panel-actions">{children}</div>}
    </div>
  )
}
