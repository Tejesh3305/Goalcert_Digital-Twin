/**
 * SimTwinNotice.jsx — what a graph-backed panel shows for a SIMULATED twin.
 *
 * Helix Data Center and Forge Plant 7 are client-side simulations: a synthetic
 * `sim:<domain>` tenant, no registry row, no graph, no change log. The Dashboard
 * knows that and renders the simulation instead — but every other panel took the
 * active tenant at face value and polled the API with it, and the server did the
 * only correct thing with a tenant nobody owns: 403. Every three seconds, from
 * whichever panel was open, for as long as the twin stayed selected.
 *
 * Two failures in one. The visible one is a panel that renders as permanently
 * empty with no explanation. The invisible one is worse: the log fills with
 * authorization warnings that look exactly like a real access-control problem, so
 * a genuine 403 has nowhere to stand out.
 *
 * So panels that need server-side state say so and STOP ASKING. `what` names the
 * thing that does not exist, because "not available" without a reason reads as a
 * bug and this is a property of the twin.
 */

import { PanelHeader } from './ui/Card'
import { simLabel } from '../lib/simTwins'

export default function SimTwinNotice({ tenant, title, what, icon = 'ti-flask' }) {
  return (
    <div className="panel">
      <PanelHeader title={title} subtitle={`${simLabel(tenant)} · simulated twin`} />
      <div className="empty" style={{ marginTop: 40 }}>
        <i className={`ti ${icon}`}
           style={{ fontSize: 30, display: 'block', marginBottom: 10 }} />
        <div style={{ fontSize: 14, color: 'var(--text)', marginBottom: 6 }}>
          {what} needs a provisioned twin
        </div>
        <div style={{ maxWidth: 460, margin: '0 auto' }}>
          This one is a client-side simulation, used to demonstrate the 3-D view
          and fault injection. Its signals never reach the graph, so there is no
          history here to read. Switch to any other twin in the library to see
          this panel against live data.
        </div>
      </div>
    </div>
  )
}
