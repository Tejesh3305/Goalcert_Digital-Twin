import { Link } from 'react-router-dom'

/** Shown by data panels when no twin is selected yet. Routes the user to the
 *  Twins panel to create their first digital twin. */
export default function NoTwin() {
  return (
    <div className="empty" style={{ marginTop: 40 }}>
      <i className="ti ti-stack-2" style={{ fontSize: 30, display: 'block', marginBottom: 10 }} />
      <div style={{ fontSize: 14, color: 'var(--text)', marginBottom: 6 }}>
        No digital twin yet
      </div>
      <div style={{ marginBottom: 14 }}>
        Create your first twin to bring this panel to life.
      </div>
      <Link to="/twins" className="btn btn-primary">
        <i className="ti ti-plus" /> Go to Twins
      </Link>
    </div>
  )
}
