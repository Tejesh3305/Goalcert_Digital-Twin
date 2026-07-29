import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import './styles/theme.css'
import './styles/app.css'
import './styles/auth.css'
import App from './App.jsx'
import { AuthProvider } from './context/AuthContext.jsx'
import { TwinProvider } from './context/TwinContext.jsx'
import { ToastProvider } from './context/ToastContext.jsx'

// AuthProvider wraps TwinProvider deliberately: the twin context loads the
// tenant list on mount, and which tenants exist depends on who is signed in.
// The other order would fetch twins before there is a session and get a 401.
ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <BrowserRouter>
      <ToastProvider>
        <AuthProvider>
          <TwinProvider>
            <App />
          </TwinProvider>
        </AuthProvider>
      </ToastProvider>
    </BrowserRouter>
  </React.StrictMode>,
)
