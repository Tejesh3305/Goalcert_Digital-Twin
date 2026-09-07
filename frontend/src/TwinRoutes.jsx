import { Navigate, Route, Routes } from 'react-router-dom'


import Dashboard from './panels/Dashboard'
import RoleDashboard from './panels/RoleDashboard'
import Dispatch from './panels/Dispatch'
import Drill from './panels/Drill'
import FixIssue from './panels/FixIssue'
import RepairWithAI from './panels/RepairWithAI'
import Training from './panels/Training'
import WorkOrder from './panels/WorkOrder'
import BuildTwin from './panels/BuildTwin'
import Twins from './panels/Twins'
import Predict from './panels/Predict'
import Copilot from './panels/Copilot'
import Changelog from './panels/Changelog'
import Marketplace from './panels/Marketplace'
import HospitalFloorPlanScene from './panels/hospitalFloorPlan/HospitalFloorPlanScene'

// The twin platform's routed content, WITHOUT the app chrome (Topbar/Sidebar).
// Standalone: App.jsx renders <Topbar/><Sidebar/><TwinRoutes/>.
// In the hub: the federated remote exposes <TwinRoutes/> and the hub mounts it
// inside its own shell (see Goalcert_Hub hub/web/src/hub/remotes/TwinRemoteHost).
// Keeping the routes here lets the panels' internal navigate('/') keep working.
export default function TwinRoutes() {
  return (
    <Routes>
      {/* THREE DIFFERENT QUESTIONS, THREE PAGES.
          `/`             how is MY work going       — per role
          `/twin`         how is the PLANT           — 3-D, telemetry, findings
          `/twin-library` which twins exist

          `/` used to render the twin's operations overview AND be redirected
          away by the persona router, so the sidebar's "Dashboard" bounced
          straight back to the role's work page and could never be opened. */}
      <Route path="/" element={<RoleDashboard />} />
      <Route path="/twin" element={<Dashboard />} />
      <Route path="/twin-library" element={<Twins />} />
      {/* Old links keep working. */}
      <Route path="/twins" element={<Navigate to="/twin-library" replace />} />
      <Route path="/build" element={<BuildTwin />} />
      <Route path="/predict" element={<Predict />} />
      <Route path="/copilot" element={<Copilot />} />
      <Route path="/changelog" element={<Changelog />} />
      <Route path="/marketplace" element={<Marketplace />} />
      <Route path="/hospital-floorplan" element={<HospitalFloorPlanScene />} />

      {/* Every dispatch route stays REGISTERED for both roles, and
          <PersonaRouter> redirects the ones that belong to the other persona to
          that user's home. Registering them unconditionally is what keeps a
          shared deep link from 404-ing for a colleague — they land somewhere
          useful instead. The pages' own refusal panels remain underneath as
          defence, and the SERVER is what actually enforces any of this. */}
      <Route path="/dispatch" element={<Dispatch />} />
      {/* The operator's work lives on the dashboard now. Kept as a redirect
          so existing links and bookmarks still land somewhere right. */}
      <Route path="/my-work" element={<Navigate to="/" replace />} />
      {/* Progress was a page you had to remember to visit, so the numbers meant
          to motivate the work were never on screen while the work was being
          chosen. The XP ledger and run history live on the dashboard now, and
          old links land there rather than 404-ing. */}
      <Route path="/progress" element={<Navigate to="/" replace />} />
      <Route path="/fix/:taskId" element={<FixIssue />} />
      <Route path="/order/:taskId" element={<WorkOrder />} />
      <Route path="/training" element={<Training />} />
      {/* Static segment first: React Router ranks it above `/repair/:taskId`,
          so "procedure" is never mistaken for a task id. */}
      <Route path="/repair/procedure/:scenarioId" element={<RepairWithAI />} />
      <Route path="/repair/:taskId" element={<RepairWithAI />} />
      <Route path="/drill/:runId" element={<Drill />} />
      {/* An unknown path lands on the role dashboard, not on somebody else's
          plant view. */}
      <Route path="*" element={<RoleDashboard />} />
    </Routes>
  )
}
