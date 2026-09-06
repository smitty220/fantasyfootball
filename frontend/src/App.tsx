import { useEffect, useState } from 'react'
import './App.css'

interface HealthResponse {
  status: string
  version: string
}

function App() {
  const [health, setHealth] = useState<HealthResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    fetch('/api/health')
      .then((res) => {
        if (!res.ok) {
          throw new Error(`Request failed with status ${res.status}`)
        }
        return res.json() as Promise<HealthResponse>
      })
      .then(setHealth)
      .catch((err: Error) => setError(err.message))
  }, [])

  return (
    <main className="app">
      <h1>Gridiron HQ</h1>
      <p className="tagline">Your self-hosted fantasy football helper</p>
      <div className="status">
        {error && <span className="status-error">API error: {error}</span>}
        {!error && !health && <span className="status-pending">Checking API status…</span>}
        {!error && health && (
          <span className="status-ok">
            API status: {health.status} (v{health.version})
          </span>
        )}
      </div>
    </main>
  )
}

export default App
