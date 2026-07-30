import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import './styles/theme.css'
import './styles/app.css'
import './styles/auth.css'
import App from './App.jsx'
import { AuthProvider } from './context/AuthContext.jsx'
import { ToastProvider } from './context/ToastContext.jsx'

// TwinProvider is NOT here. It used to be, wrapped by AuthProvider so that the
// tenant list loaded after the session — but nesting only orders the two
// providers' construction, not their fetches: TwinProvider mounted on EVERY
// route, including /login, and its `refreshTwins()` effect fired immediately.
// So an unauthenticated visitor's first page load sent `GET /twins` (twice,
// under StrictMode's double-mount) and got 401s behind the login form, which is
// exactly what App.jsx's route split and AuthShell's comment both claim does not
// happen.
//
// It now lives inside <RequireAuth> in App.jsx, so it mounts only once there is
// a session to scope it to. See the note there.
ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <BrowserRouter>
      <ToastProvider>
        <AuthProvider>
          <App />
        </AuthProvider>
      </ToastProvider>
    </BrowserRouter>
  </React.StrictMode>,
)
