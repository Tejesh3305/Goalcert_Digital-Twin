import { Routes, Route } from 'react-router-dom'
import Topbar from './components/layout/Topbar'
import Sidebar from './components/layout/Sidebar'

import Dashboard from './panels/Dashboard'
import BuildTwin from './panels/BuildTwin'
import Twins from './panels/Twins'
import Predict from './panels/Predict'
import Agents from './panels/Agents'
import Changelog from './panels/Changelog'
import BundleAuthor from './panels/BundleAuthor'
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
            <Route path="/build" element={<BuildTwin />} />
            <Route path="/twins" element={<Twins />} />
            <Route path="/predict" element={<Predict />} />
            <Route path="/agents" element={<Agents />} />
            <Route path="/changelog" element={<Changelog />} />
            <Route path="/bundle-author" element={<BundleAuthor />} />
            <Route path="/marketplace" element={<Marketplace />} />
            <Route path="*" element={<Dashboard />} />
          </Routes>
        </div>
      </div>
    </div>
  )
}
