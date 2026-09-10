import { useEffect, useState } from 'react'
import { NavLink, Navigate, Route, Routes } from 'react-router-dom'
import './App.css'
import { getHealth } from './api/endpoints'
import { LeaguesPage } from './pages/LeaguesPage'
import { NewLeagueWizard } from './pages/NewLeagueWizard'
import { LeagueDetailPage } from './pages/LeagueDetailPage'
import { TradeAnalyzerPage } from './pages/TradeAnalyzerPage'
import { MatchupPage } from './pages/MatchupPage'
import { AccuracyPage } from './pages/AccuracyPage'
import { StandingsPage } from './pages/StandingsPage'
import { DataPage } from './pages/DataPage'
import { useSession } from './components/sessionContext'
import { Badge } from './components/ui'

type HealthState = 'checking' | 'ok' | 'error'

function App() {
  const [health, setHealth] = useState<HealthState>('checking')
  const { role, canEdit, logout } = useSession()

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
          {role && (
            <div className="app-session">
              <Badge tone={role === 'owner' ? 'accent' : 'neutral'}>{role}</Badge>
              <button type="button" className="nav-link app-logout" onClick={logout}>
                Log out
              </button>
            </div>
          )}
        </div>
      </header>

      <main className="app-main">
        <Routes>
          <Route path="/" element={<LeaguesPage />} />
          {/* Nothing links here for a viewer; this catches a typed-in URL. */}
          <Route path="/leagues/new" element={canEdit ? <NewLeagueWizard /> : <Navigate to="/" replace />} />
          <Route path="/leagues/:leagueKey" element={<LeagueDetailPage />} />
          <Route path="/leagues/:leagueKey/trade" element={<TradeAnalyzerPage />} />
          <Route path="/leagues/:leagueKey/matchup" element={<MatchupPage />} />
          <Route path="/leagues/:leagueKey/standings" element={<StandingsPage />} />
          <Route path="/leagues/:leagueKey/accuracy" element={<AccuracyPage />} />
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
