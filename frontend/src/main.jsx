import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import './styles/theme.css'
import './styles/app.css'
import App from './App.jsx'
import ApiKeyGate from './components/ApiKeyGate.jsx'
import { TwinProvider } from './context/TwinContext.jsx'
import { ToastProvider } from './context/ToastContext.jsx'

// ApiKeyGate wraps the providers, not just <App/>: TwinProvider fetches on mount, so
// gating inside it would fire those calls (and their 401s) before a key exists.
// Standalone only — the hub enters via mount.jsx and authenticates at its gateway.
ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <BrowserRouter>
      <ApiKeyGate>
        <ToastProvider>
          <TwinProvider>
            <App />
          </TwinProvider>
        </ToastProvider>
      </ApiKeyGate>
    </BrowserRouter>
  </React.StrictMode>,
)
