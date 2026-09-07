import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { getLeagues, getYahooStatus } from '../api/endpoints'
import type { League } from '../api/types'
import { ApiError } from '../api/client'
import { Badge, Banner, Button, Card, EmptyState, Spinner } from '../components/ui'
import { useToast } from '../components/toastContext'
import { useSession } from '../components/sessionContext'

export function LeaguesPage() {
  const { canEdit } = useSession()
  const [leagues, setLeagues] = useState<League[] | null>(null)
  const [yahooConnected, setYahooConnected] = useState<boolean | null>(null)
  const [bannerDismissed, setBannerDismissed] = useState(false)
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

  return (
    <div className="page">
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

      {leagues === null && (
        <div className="loading-row">
          <Spinner /> Loading leagues…
        </div>
      )}

      {leagues !== null && leagues.length === 0 && (
        <EmptyState>
          {canEdit ? 'No leagues yet. Create a manual league to get started.' : 'No leagues yet.'}
        </EmptyState>
      )}

      {leagues !== null && leagues.length > 0 && (
        <div className="card-grid">
          {leagues.map((league) => (
            <Link key={league.league_key} to={`/leagues/${encodeURIComponent(league.league_key)}`} className="card-link">
              <Card className="league-card">
                <div className="league-card-title">
                  <h2>{league.name}</h2>
                </div>
                <div className="badge-row">
                  <Badge tone="neutral">{league.season}</Badge>
                  <Badge tone={league.source === 'manual' ? 'accent' : 'success'}>{league.source}</Badge>
                  {league.is_keeper && <Badge tone="warning">Keeper</Badge>}
                </div>
                <div className="league-card-meta">
                  {league.num_teams} teams
                  {league.current_week != null && <> · Week {league.current_week}</>}
                </div>
              </Card>
            </Link>
          ))}
        </div>
      )}
    </div>
  )
}
