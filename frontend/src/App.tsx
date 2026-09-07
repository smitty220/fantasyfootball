import { useEffect, useState } from 'react'
import { NavLink, Route, Routes } from 'react-router-dom'
import './App.css'
import { getHealth } from './api/endpoints'
import { LeaguesPage } from './pages/LeaguesPage'
import { NewLeagueWizard } from './pages/NewLeagueWizard'
import { LeagueDetailPage } from './pages/LeagueDetailPage'
import { TradeAnalyzerPage } from './pages/TradeAnalyzerPage'
import { MatchupPage } from './pages/MatchupPage'
import { DataPage } from './pages/DataPage'

type HealthState = 'checking' | 'ok' | 'error'

function App() {
  const [health, setHealth] = useState<HealthState>('checking')

  useEffect(() => {
    getHealth()
      .then(() => setHealth('ok'))
      .catch(() => setHealth('error'))
  }, [])

  return (
    <div className="app-shell">
      <header className="app-header">
        <div className="app-header-inner">
          <span className="app-title">Gridiron HQ</span>
          <nav className="app-nav">
            <NavLink to="/" end className={({ isActive }) => (isActive ? 'nav-link nav-link-active' : 'nav-link')}>
              Leagues
            </NavLink>
            <NavLink to="/data" className={({ isActive }) => (isActive ? 'nav-link nav-link-active' : 'nav-link')}>
              Data
            </NavLink>
          </nav>
        </div>
      </header>

      <main className="app-main">
        <Routes>
          <Route path="/" element={<LeaguesPage />} />
          <Route path="/leagues/new" element={<NewLeagueWizard />} />
          <Route path="/leagues/:leagueKey" element={<LeagueDetailPage />} />
          <Route path="/leagues/:leagueKey/trade" element={<TradeAnalyzerPage />} />
          <Route path="/leagues/:leagueKey/matchup" element={<MatchupPage />} />
          <Route path="/data" element={<DataPage />} />
        </Routes>
      </main>

      <footer className="app-footer">
        <span className={`health-dot health-dot-${health}`} />
        API: {health === 'checking' ? 'checking…' : health}
      </footer>
    </div>
  )
}

export default App
