import { Routes, Route } from 'react-router-dom'

import Dashboard from './panels/Dashboard'
import BuildTwin from './panels/BuildTwin'
import Twins from './panels/Twins'
import Predict from './panels/Predict'
import Copilot from './panels/Copilot'
import Changelog from './panels/Changelog'
import Marketplace from './panels/Marketplace'

// The twin platform's routed content, WITHOUT the app chrome (Topbar/Sidebar).
// Standalone: App.jsx renders <Topbar/><Sidebar/><TwinRoutes/>.
// In the hub: the federated remote exposes <TwinRoutes/> and the hub mounts it
// inside its own shell (see Goalcert_Hub hub/web/src/hub/remotes/TwinRemoteHost).
// Keeping the routes here lets the panels' internal navigate('/') keep working.
export default function TwinRoutes() {
  return (
    <Routes>
      <Route path="/" element={<Dashboard />} />
      <Route path="/build" element={<BuildTwin />} />
      <Route path="/twins" element={<Twins />} />
      <Route path="/predict" element={<Predict />} />
      <Route path="/copilot" element={<Copilot />} />
      <Route path="/changelog" element={<Changelog />} />
      <Route path="/marketplace" element={<Marketplace />} />
      <Route path="*" element={<Dashboard />} />
    </Routes>
  )
}
