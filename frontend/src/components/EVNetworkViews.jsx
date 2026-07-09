/**
 * EVNetworkViews — the EV charging-network live surface: the charging geo map,
 * the grid load curve and the V2G trading view, all fed by the network twin's
 * payload (GET /twins/{tenant}/network). Rendered by LiveOps + the dashboard when
 * the active twin is the ev-charging-network domain.
 */
import { Card } from './ui/Card'
import { Empty } from './ui/States'
import EVChargingMap from './EVChargingMap'
import EVGridLoadCurve from './EVGridLoadCurve'
import EVV2GTradingView from './EVV2GTradingView'

export default function EVNetworkViews({ net }) {
  if (!net) return <Empty label="Loading network…" icon="ti-loader" />
  const faultedStations = (net.stations || []).filter((s) => s.status === 'critical').length

  return (
    <>
      <Card title={<><i className="ti ti-map-2" /> Charging Network Map</>} className="section-gap"
        action={faultedStations ? <span className="pill pill-red">{faultedStations} station fault</span>
          : <span className="pill pill-green">● online</span>}>
        <EVChargingMap net={net} />
      </Card>
      <Card title={<><i className="ti ti-chart-area-line" /> Grid Load Curve</>} className="section-gap"
        action={<span className="pill pill-surface">24h · demand vs capacity</span>}>
        <EVGridLoadCurve curve={net.load_curve} />
      </Card>
      <Card title={<><i className="ti ti-arrows-exchange" /> V2G Arbitrage</>} className="section-gap"
        action={net.v2g?.pending_approval ? <span className="pill pill-amber">export window open</span>
          : <span className="pill pill-surface">spot vs export</span>}>
        <EVV2GTradingView v2g={net.v2g} />
      </Card>
    </>
  )
}
