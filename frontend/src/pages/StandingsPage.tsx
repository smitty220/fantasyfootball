import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { getPlayoffOdds, getStandings } from '../api/endpoints'
import type { PlayoffOddsResponse, StandingsRow } from '../api/types'
import { ApiError } from '../api/client'
import { Button, Card, EmptyState, Spinner } from '../components/ui'
import { useToast } from '../components/toastContext'
import { SourcePicker } from '../components/SourcePicker'

function fmt1(n: number): string {
  return n.toFixed(1)
}

function fmtRecord(row: { wins: number; losses: number; ties: number }): string {
  return `${row.wins}-${row.losses}${row.ties ? `-${row.ties}` : ''}`
}

export function StandingsPage() {
  const { leagueKey = '' } = useParams<{ leagueKey: string }>()
  const { showError } = useToast()

  const [standings, setStandings] = useState<StandingsRow[] | null>(null)

  const [oddsSources, setOddsSources] = useState<string[]>([])
  const [odds, setOdds] = useState<PlayoffOddsResponse | null>(null)
  const [oddsLoading, setOddsLoading] = useState(true)

  function loadStandings() {
    getStandings(leagueKey)
      .then(setStandings)
      .catch((err: unknown) => {
        setStandings([])
        showError(err instanceof ApiError ? err.message : 'Failed to load standings')
      })
  }

  function loadOdds(sources: string[]) {
    setOddsLoading(true)
    getPlayoffOdds(leagueKey, sources)
      .then(setOdds)
      .catch((err: unknown) => {
        setOdds(null)
        showError(err instanceof ApiError ? err.message : 'Failed to load playoff odds')
      })
      .finally(() => setOddsLoading(false))
  }

  useEffect(() => {
    loadStandings()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [leagueKey])

  useEffect(() => {
    loadOdds(oddsSources)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [leagueKey, oddsSources])

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>Standings &amp; Odds</h1>
          <p className="field-hint">Season standings and Monte Carlo playoff odds, from the Yahoo schedule and results.</p>
        </div>
        <Link to={`/leagues/${encodeURIComponent(leagueKey)}`}>
          <Button>Back to league</Button>
        </Link>
      </div>

      <Card>
        <h2>Standings</h2>
        {standings === null && (
          <div className="loading-row">
            <Spinner /> Loading standings…
          </div>
        )}
        {standings !== null && standings.length === 0 && (
          <EmptyState>No completed matchups yet — enter weekly results below.</EmptyState>
        )}
        {standings !== null && standings.length > 0 && (
          <div className="table-scroll">
            <table className="data-table standings-table">
              <thead>
                <tr>
                  <th>Rank</th>
                  <th>Team</th>
                  <th>W-L-T</th>
                  <th>PF</th>
                  <th>PA</th>
                </tr>
              </thead>
              <tbody>
                {standings.map((row, i) => (
                  <tr key={row.team_id} className={row.is_my_team ? 'standings-row-mine' : undefined}>
                    <td>{i + 1}</td>
                    <td>
                      {row.is_my_team && <span className="standings-star">★</span>}
                      {row.name}
                    </td>
                    <td>{fmtRecord(row)}</td>
                    <td>{fmt1(row.points_for)}</td>
                    <td>{fmt1(row.points_against)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card>
        <h2>Playoff odds</h2>
        <SourcePicker onChange={setOddsSources} />

        {oddsLoading && !odds && (
          <div className="loading-row">
            <Spinner /> Running season simulations…
          </div>
        )}

        {odds && (
          <>
            <div className="table-scroll">
              <table className="data-table odds-table">
                <thead>
                  <tr>
                    <th>Team</th>
                    <th>Playoff %</th>
                    <th>Avg seed</th>
                    <th>#1 seed %</th>
                  </tr>
                </thead>
                <tbody>
                  {odds.teams.map((t) => {
                    const pct = t.playoff_prob * 100
                    return (
                      <tr key={t.team_id} className={t.is_my_team ? 'standings-row-mine' : undefined}>
                        <td>
                          {t.is_my_team && <span className="standings-star">★</span>}
                          {t.name}
                        </td>
                        <td>
                          <div
                            className="odds-bar-cell"
                            style={{
                              background: `linear-gradient(to right, var(--accent-bg) ${pct}%, transparent ${pct}%)`,
                            }}
                          >
                            {pct.toFixed(1)}%
                          </div>
                        </td>
                        <td>{t.avg_seed.toFixed(1)}</td>
                        <td>{(t.seed_1_prob * 100).toFixed(1)}%</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
            <p className="field-hint">
              Monte Carlo: {odds.sims.toLocaleString()} season simulations · top {odds.playoff_teams} make the
              playoffs · weeks 1–{odds.regular_season_weeks}
            </p>
          </>
        )}
      </Card>

    </div>
  )
}
