import Topbar from './components/layout/Topbar'
import Sidebar from './components/layout/Sidebar'
import TwinRoutes from './TwinRoutes'

// Standalone shell: chrome + the routed twin content. The hub federates only
// <TwinRoutes/> and supplies its own chrome, so the two stay in one source.
export default function App() {
  return (
    <div className="app-root">
      <Topbar />
      <div className="body">
        <Sidebar />
        <div className="content">
          <TwinRoutes />
        </div>
      </div>
    </div>
  )
}
