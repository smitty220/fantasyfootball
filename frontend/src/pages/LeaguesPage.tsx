import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { getDashboard, getLeagues, getYahooStatus } from '../api/endpoints'
import type { DashboardLeague, League } from '../api/types'
import { ApiError } from '../api/client'
import { Badge, Banner, Button, Card, EmptyState, Spinner } from '../components/ui'
import { RosterEditor } from '../components/RosterEditor'
import { useToast } from '../components/toastContext'
import { useSession } from '../components/sessionContext'

export function LeaguesPage() {
  const { canEdit } = useSession()
  const [leagues, setLeagues] = useState<League[] | null>(null)
  const [yahooConnected, setYahooConnected] = useState<boolean | null>(null)
  const [bannerDismissed, setBannerDismissed] = useState(false)
  const [dashboardLeagues, setDashboardLeagues] = useState<DashboardLeague[] | null>(null)
  const { showError } = useToast()

  useEffect(() => {
    let cancelled = false
    getLeagues()
      .then((data) => {
        if (!cancelled) setLeagues(data)
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setLeagues([])
          showError(err instanceof ApiError ? err.message : 'Failed to load leagues')
        }
      })
    // The dashboard payload is only used to locate my team in each league.
    getDashboard()
      .then((data) => {
        if (!cancelled) setDashboardLeagues(data.leagues)
      })
      .catch(() => {
        if (!cancelled) setDashboardLeagues([])
      })
    getYahooStatus()
      .then((status) => {
        if (!cancelled) setYahooConnected(status.connected)
      })
      .catch(() => {
        if (!cancelled) setYahooConnected(null)
      })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const loading = leagues === null || dashboardLeagues === null
  const myTeamByKey = new Map(
    (dashboardLeagues ?? []).map((entry) => [entry.league_key, entry.my_team]),
  )

  return (
    <div className="page page-wide">
      <div className="page-header">
        <h1>Leagues</h1>
        {canEdit && (
          <Link to="/leagues/new">
            <Button variant="primary">New league (manual)</Button>
          </Link>
        )}
      </div>

      {/* Only the owner can do anything about a missing Yahoo connection. */}
      {canEdit && yahooConnected === false && !bannerDismissed && (
        <Banner onDismiss={() => setBannerDismissed(true)}>
          Yahoo not connected — manual leagues only for now.
        </Banner>
      )}

      {loading && (
        <div className="loading-row">
          <Spinner /> Loading leagues…
        </div>
      )}

      {!loading && leagues !== null && leagues.length === 0 && (
        <EmptyState>
          {canEdit ? 'No leagues yet. Create a manual league to get started.' : 'No leagues yet.'}
        </EmptyState>
      )}

      {!loading && leagues !== null && leagues.length > 0 && (
        <div className="home-roster-grid">
          {leagues.map((league) => {
            const myTeam = myTeamByKey.get(league.league_key) ?? null
            return (
              <Card key={league.league_key} className="home-roster-card">
                <Link
                  to={`/leagues/${encodeURIComponent(league.league_key)}`}
                  className="dash-card-header"
                >
                  <h2>{league.name}</h2>
                  <div className="badge-row">
                    <Badge tone="neutral">{league.season}</Badge>
                    <Badge tone={league.source === 'manual' ? 'accent' : 'success'}>
                      {league.source}
                    </Badge>
                    {league.is_keeper && <Badge tone="warning">Keeper</Badge>}
                  </div>
                </Link>
                {myTeam ? (
                  <RosterEditor
                    leagueKey={league.league_key}
                    teamId={myTeam.id}
                    teamName={myTeam.name}
                    editable={canEdit && league.source === 'manual'}
                  />
                ) : (
                  <EmptyState>Star your team in this league to see its roster here.</EmptyState>
                )}
              </Card>
            )
          })}
        </div>
      )}
    </div>
  )
}
