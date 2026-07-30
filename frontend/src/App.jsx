import { Route, Routes } from 'react-router-dom'

import Topbar from './components/layout/Topbar'
import Sidebar from './components/layout/Sidebar'
import TwinRoutes from './TwinRoutes'
import CommandPalette from './components/CommandPalette'
import RequireAuth from './components/RequireAuth'
import { TwinProvider } from './context/TwinContext'
import AccountSettings from './pages/AccountSettings'
import ForgotPassword from './pages/ForgotPassword'
import Login from './pages/Login'
import ResetPassword from './pages/ResetPassword'
import Signup from './pages/Signup'

/**
 * Standalone shell.
 *
 * TWO ROUTE GROUPS, and the split is the point. The auth pages render on their
 * OWN — no Topbar, no Sidebar, no CommandPalette — because that chrome fetches
 * twin data the moment it mounts. Rendering it behind a login form would fire a
 * burst of 401s for a user who has not signed in yet, and the twin switcher
 * would flash an empty list.
 *
 * Everything else sits behind <RequireAuth>, which renders nothing until the
 * refresh cookie has been exchanged for a token — so a reload does not flash the
 * login page at someone who is already signed in.
 *
 * <TwinProvider> IS INSIDE THAT GUARD, and that placement is the point rather
 * than a detail. It was mounted at the root (main.jsx) under the reasoning that
 * AuthProvider wrapping it made the session land first — but provider nesting
 * orders construction, not effects. It mounted on /login too and loaded the
 * tenant list straight away, so an unauthenticated visitor's first paint sent
 * `GET /twins` and collected 401s behind the login form: precisely the burst the
 * route split above exists to prevent. Inside the guard it cannot mount until
 * there is a session for its scope to mean something.
 *
 * The hub federates only <TwinRoutes/> and supplies its own chrome AND its own
 * identity, so none of this applies there — which is why the guard lives here
 * rather than inside TwinRoutes.
 */
export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/signup" element={<Signup />} />
      <Route path="/forgot-password" element={<ForgotPassword />} />
      <Route path="/reset-password" element={<ResetPassword />} />

      <Route
        path="*"
        element={
          <RequireAuth>
            <TwinProvider>
              <div className="app-root">
                <Topbar />
                <div className="body">
                  <Sidebar />
                  <div className="content">
                    <Routes>
                      <Route path="/account" element={<AccountSettings />} />
                      <Route path="*" element={<TwinRoutes />} />
                    </Routes>
                  </div>
                </div>
                <CommandPalette />
              </div>
            </TwinProvider>
          </RequireAuth>
        }
      />
    </Routes>
  )
}
