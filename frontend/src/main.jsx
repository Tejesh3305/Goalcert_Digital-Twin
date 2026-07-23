import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import './styles/theme.css'
import './styles/app.css'
import App from './App.jsx'
import { TwinProvider } from './context/TwinContext.jsx'
import { ToastProvider } from './context/ToastContext.jsx'

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <BrowserRouter>
      <ToastProvider>
        <TwinProvider>
          <App />
        </TwinProvider>
      </ToastProvider>
    </BrowserRouter>
  </React.StrictMode>,
)
