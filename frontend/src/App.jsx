import { Routes, Route } from 'react-router-dom'
import Topbar from './components/layout/Topbar'
import Sidebar from './components/layout/Sidebar'

import Dashboard from './panels/Dashboard'
import Concierge from './panels/Concierge'
import BundleAuthor from './panels/BundleAuthor'
import Twins from './panels/Twins'
import Assets from './panels/Assets'
import LiveOps from './panels/LiveOps'
import Agents from './panels/Agents'
import Predict from './panels/Predict'
import Copilot from './panels/Copilot'
import Changelog from './panels/Changelog'
import Compliance from './panels/Compliance'
import Simulation from './panels/Simulation'
import TwinHealth from './panels/TwinHealth'
import Marketplace from './panels/Marketplace'

export default function App() {
  return (
    <div className="app-root">
      <Topbar />
      <div className="body">
        <Sidebar />
        <div className="content">
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/build" element={<Concierge />} />
            <Route path="/bundle-author" element={<BundleAuthor />} />
            <Route path="/twins" element={<Twins />} />
            <Route path="/assets" element={<Assets />} />
            <Route path="/live" element={<LiveOps />} />
            <Route path="/agents" element={<Agents />} />
            <Route path="/predict" element={<Predict />} />
            <Route path="/copilot" element={<Copilot />} />
            <Route path="/changelog" element={<Changelog />} />
            <Route path="/compliance" element={<Compliance />} />
            <Route path="/simulation" element={<Simulation />} />
            <Route path="/health" element={<TwinHealth />} />
            <Route path="/marketplace" element={<Marketplace />} />
            <Route path="*" element={<Dashboard />} />
          </Routes>
        </div>
      </div>
    </div>
  )
}
