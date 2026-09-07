import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import './index.css'
import App from './App.tsx'
import { ToastProvider } from './components/ToastProvider'
import { SessionGate } from './components/SessionGate'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <ToastProvider>
        <SessionGate>
          <App />
        </SessionGate>
      </ToastProvider>
    </BrowserRouter>
  </StrictMode>,
)
