/**
 * HospitalCampusViews — the hospital-campus live surface: the five clinical views
 * (bed board, OR utilisation calendar, patient-flow funnel, infection map,
 * medical-gas P&ID) laid out as cards, all fed by the campus twin's network
 * payload (GET /twins/{tenant}/network). Rendered by LiveOps + the dashboard when
 * the active twin is the hospital-campus domain.
 */
import { Card } from './ui/Card'
import { Empty } from './ui/States'
import HospitalBedBoard from './HospitalBedBoard'
import HospitalORCalendar from './HospitalORCalendar'
import HospitalPatientFlow from './HospitalPatientFlow'
import HospitalInfectionMap from './HospitalInfectionMap'
import HospitalMedicalGasSchematic from './HospitalMedicalGasSchematic'

export default function HospitalCampusViews({ net }) {
  if (!net) return <Empty label="Loading campus…" icon="ti-loader" />
  const gasCrit = (net.medical_gas?.zones || []).some((z) => z.status === 'critical')
  const closures = (net.zones || []).filter((z) => z.recommend_closure).length

  return (
    <>
      <div className="grid-2 section-gap" style={{ alignItems: 'start' }}>
        <Card title={<><i className="ti ti-map-2" /> Infection Spread Map</>}
          action={closures ? <span className="pill pill-red">{closures} closure</span> : <span className="pill pill-green">● contained</span>}>
          <HospitalInfectionMap zones={net.zones} />
        </Card>
        <Card title={<><i className="ti ti-topology-star-3" /> Medical Gas Schematic</>}
          action={gasCrit ? <span className="pill pill-red">low pressure</span> : <span className="pill pill-green">● nominal</span>}>
          <HospitalMedicalGasSchematic gas={net.medical_gas} />
        </Card>
      </div>

      <Card title={<><i className="ti ti-bed" /> Bed Board</>} className="section-gap"
        action={<span className="pill pill-surface">live · RTLS</span>}>
        <HospitalBedBoard beds={net.beds} />
      </Card>

      <div className="grid-2 section-gap" style={{ alignItems: 'start' }}>
        <Card title={<><i className="ti ti-calendar-stats" /> OR Utilisation Calendar</>}>
          <HospitalORCalendar schedule={net.or_schedule} />
        </Card>
        <Card title={<><i className="ti ti-filter" /> ED Patient-Flow Funnel</>}>
          <HospitalPatientFlow flow={net.patient_flow} />
        </Card>
      </div>
    </>
  )
}
