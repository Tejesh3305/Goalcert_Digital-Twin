/**
 * DefenceBaseViews — the military-base live surface: the NATO APP-6 tactical map
 * and the Kanban mission board, fed by the base twin's network payload. Rendered
 * by LiveOps + the dashboard when the active twin is the defence-base domain.
 */
import { Card } from './ui/Card'
import { Empty } from './ui/States'
import DefenceTacticalMap from './DefenceTacticalMap'
import DefenceMissionBoard from './DefenceMissionBoard'

export default function DefenceBaseViews({ net }) {
  if (!net) return <Empty label="Loading tactical picture…" icon="ti-loader" />
  const hostiles = (net.assets || []).filter((a) => a.affiliation === 'hostile').length

  return (
    <>
      <Card title={<><i className="ti ti-map-pin" /> Tactical Map (NATO APP-6)</>} className="section-gap"
        action={hostiles ? <span className="pill pill-red">{hostiles} hostile track</span>
          : <span className="pill pill-green">● area secure</span>}>
        <DefenceTacticalMap net={net} />
      </Card>
      <Card title={<><i className="ti ti-layout-kanban" /> Mission Board</>} className="section-gap"
        action={<span className="pill pill-surface">C4ISR</span>}>
        <DefenceMissionBoard missions={net.missions} />
      </Card>
    </>
  )
}
