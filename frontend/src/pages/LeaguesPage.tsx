import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { getDashboard, getLeagues, getYahooStatus } from '../api/endpoints'
import type { DashboardLeague, League } from '../api/types'
import { ApiError } from '../api/client'
import { Badge, Banner, Button, Card, EmptyState, Spinner } from '../components/ui'
import { SourcePicker } from '../components/SourcePicker'
import { useToast } from '../components/toastContext'
import { useSession } from '../components/sessionContext'

function fmtPts(n: number | null | undefined): string {
  return n == null ? '—' : n.toFixed(1)
}

export function LeaguesPage() {
  const { canEdit } = useSession()
  const [leagues, setLeagues] = useState<League[] | null>(null)
  const [yahooConnected, setYahooConnected] = useState<boolean | null>(null)
  const [bannerDismissed, setBannerDismissed] = useState(false)
  const [dashboardLeagues, setDashboardLeagues] = useState<DashboardLeague[] | null>(null)
  const [dashboardFailed, setDashboardFailed] = useState(false)
  const [sources, setSources] = useState<string[]>([])
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

  useEffect(() => {
    let cancelled = false
    getDashboard(sources)
      .then((data) => {
        if (!cancelled) {
          setDashboardLeagues(data.leagues)
          setDashboardFailed(false)
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setDashboardLeagues(null)
          setDashboardFailed(true)
          showError(err instanceof ApiError ? err.message : 'Failed to load dashboard')
        }
      })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sources])

  const loading = leagues === null || (dashboardLeagues === null && !dashboardFailed)
  const useFallback = dashboardFailed
  const leagueByKey = new Map((leagues ?? []).map((l) => [l.league_key, l]))

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

      <SourcePicker onChange={setSources} />

      {loading && (
        <div className="loading-row">
          <Spinner /> Loading leagues…
        </div>
      )}

      {!loading && !useFallback && (dashboardLeagues?.length ?? 0) === 0 && (
        <EmptyState>
          {canEdit ? 'No leagues yet. Create a manual league to get started.' : 'No leagues yet.'}
        </EmptyState>
      )}

      {!loading && !useFallback && dashboardLeagues !== null && dashboardLeagues.length > 0 && (
        <div className="dashboard-grid">
          {dashboardLeagues.map((entry) => {
            const listMeta = leagueByKey.get(entry.league_key)
            const alerts = entry.alerts
            const hasBye = (alerts?.bye_starters.length ?? 0) > 0
            const hasInjured = (alerts?.injured_starters.length ?? 0) > 0
            const hasBenchBeats = (alerts?.bench_beats_starter.length ?? 0) > 0
            const hasFaFlags = (alerts?.fa_week_flags ?? 0) > 0 || (alerts?.fa_ros_flags ?? 0) > 0
            const hasAnyAlert = hasBye || hasInjured || hasBenchBeats || hasFaFlags
            const leagueHref = `/leagues/${encodeURIComponent(entry.league_key)}`
            const faHref = `${leagueHref}?tab=free-agents`

            return (
              <Card key={entry.league_key} className="dash-card">
                <Link to={leagueHref} className="dash-card-header">
                  <h2>{entry.name}</h2>
                  <div className="badge-row">
                    {listMeta && <Badge tone="neutral">{listMeta.season}</Badge>}
                    {listMeta && (
                      <Badge tone={listMeta.source === 'manual' ? 'accent' : 'success'}>{listMeta.source}</Badge>
                    )}
                    {entry.is_keeper && <Badge tone="warning">Keeper</Badge>}
                  </div>
                </Link>

                {entry.my_team ? (
                  <div className="dash-my-team">
                    My team: <strong>{entry.my_team.name}</strong>
                    {entry.my_lineup_week_points != null && (
                      <> · projected {fmtPts(entry.my_lineup_week_points)} pts this week</>
                    )}
                  </div>
                ) : (
                  <div className="dash-hint">Star your team to see action items.</div>
                )}

                {entry.my_team && (
                  <div className="dash-section">
                    <div className="dash-section-title">Action items</div>
                    <div className="dash-rows">
                      {alerts?.bye_starters.map((p) => (
                        <Link
                          key={`bye-${p.name}-${p.slot}`}
                          to={leagueHref}
                          className="dash-row dash-row-link dash-row-danger"
                        >
                          <span>
                            🚫 {p.name} ({p.slot}) is on BYE
                          </span>
                        </Link>
                      ))}
                      {alerts?.injured_starters.map((p) => (
                        <div key={`inj-${p.name}-${p.slot}`} className="dash-row dash-row-warning">
                          <span>
                            {p.name} ({p.slot}) — {p.status}
                          </span>
                        </div>
                      ))}
                      {alerts?.bench_beats_starter.map((b) => (
                        <div key={`bb-${b.starter}-${b.slot}-${b.bench}`} className="dash-row">
                          <span>
                            ▼ {b.starter} ({b.slot}) — bench {b.bench} projects {fmtPts(b.bench_week_points)} pts
                          </span>
                        </div>
                      ))}
                      {hasFaFlags && (
                        <Link to={faHref} className="dash-row dash-row-link">
                          <span>
                            FA upgrades available: {alerts?.fa_week_flags ?? 0} this week ·{' '}
                            {alerts?.fa_ros_flags ?? 0} rest-of-season
                          </span>
                        </Link>
                      )}
                      {!hasAnyAlert && <div className="dash-row dash-row-success">✓ No action items</div>}
                    </div>
                  </div>
                )}

                {entry.top_free_agents.length > 0 && (
                  <div className="dash-section">
                    <div className="dash-section-title">Top free agents</div>
                    <div className="dash-rows">
                      {entry.top_free_agents.slice(0, 3).map((fa) => (
                        <Link key={`${fa.name}-${fa.position}`} to={faHref} className="dash-row dash-row-link">
                          <span>
                            {fa.name} ({fa.position}) — VOR {fa.vor.toFixed(1)}
                          </span>
                          <span className="dash-row-tags">
                            {fa.week_delta != null && fa.week_delta > 0 && (
                              <span className="delta-positive">+{fa.week_delta.toFixed(1)} wk</span>
                            )}
                            {fa.trending_add != null && <Badge tone="accent">{fa.trending_add} adds</Badge>}
                          </span>
                        </Link>
                      ))}
                    </div>
                  </div>
                )}
              </Card>
            )
          })}
        </div>
      )}

      {!loading && useFallback && leagues !== null && leagues.length === 0 && (
        <EmptyState>
          {canEdit ? 'No leagues yet. Create a manual league to get started.' : 'No leagues yet.'}
        </EmptyState>
      )}

      {!loading && useFallback && leagues !== null && leagues.length > 0 && (
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
