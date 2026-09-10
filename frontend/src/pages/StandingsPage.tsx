import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import {
  deleteManualMatchups,
  getLeagues,
  getManualMatchups,
  getPlayoffOdds,
  getStandings,
  getTeams,
  putManualMatchups,
} from '../api/endpoints'
import type {
  League,
  ManualMatchup,
  PlayoffOddsResponse,
  PutManualMatchupsPayload,
  StandingsRow,
  Team,
} from '../api/types'
import { ApiError } from '../api/client'
import { Badge, Button, Card, EmptyState, InlineError, Spinner } from '../components/ui'
import { useToast } from '../components/toastContext'
import { useSession } from '../components/sessionContext'
import { SourcePicker } from '../components/SourcePicker'

function fmt1(n: number): string {
  return n.toFixed(1)
}

function fmtRecord(row: { wins: number; losses: number; ties: number }): string {
  return `${row.wins}-${row.losses}${row.ties ? `-${row.ties}` : ''}`
}

interface EditRow {
  home_team_id: number | ''
  away_team_id: number | ''
  home_points: string
  away_points: string
}

function matchupToRow(m: ManualMatchup): EditRow {
  return {
    home_team_id: m.home_team_id,
    away_team_id: m.away_team_id,
    home_points: m.home_points == null ? '' : String(m.home_points),
    away_points: m.away_points == null ? '' : String(m.away_points),
  }
}

function emptyRow(): EditRow {
  return { home_team_id: '', away_team_id: '', home_points: '', away_points: '' }
}

/** The first week (1-based) not yet in `completedWeeks`, or 1 if the whole
 *  regular season is already complete (or nothing is complete yet). */
function firstIncompleteWeek(regularSeasonWeeks: number, completedWeeks: number[]): number {
  const completed = new Set(completedWeeks)
  for (let w = 1; w <= regularSeasonWeeks; w++) {
    if (!completed.has(w)) return w
  }
  return 1
}

export function StandingsPage() {
  const { leagueKey = '' } = useParams<{ leagueKey: string }>()
  const { showError, showSuccess } = useToast()
  const { canEdit } = useSession()

  const [league, setLeague] = useState<League | null | undefined>(undefined)
  const [teams, setTeams] = useState<Team[] | null>(null)

  const [standings, setStandings] = useState<StandingsRow[] | null>(null)

  const [oddsSources, setOddsSources] = useState<string[]>([])
  const [odds, setOdds] = useState<PlayoffOddsResponse | null>(null)
  const [oddsLoading, setOddsLoading] = useState(true)

  const [selectedWeek, setSelectedWeek] = useState<number | null>(null)
  const [matchups, setMatchups] = useState<ManualMatchup[] | null>(null)
  const [matchupsLoading, setMatchupsLoading] = useState(false)
  const [rows, setRows] = useState<EditRow[]>([])
  const [saveError, setSaveError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [clearing, setClearing] = useState(false)

  const isManualEditable = canEdit && league?.source === 'manual'

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
      .then((data) => {
        setOdds(data)
        setSelectedWeek((current) => current ?? firstIncompleteWeek(data.regular_season_weeks, data.completed_weeks))
      })
      .catch((err: unknown) => {
        setOdds(null)
        showError(err instanceof ApiError ? err.message : 'Failed to load playoff odds')
      })
      .finally(() => setOddsLoading(false))
  }

  useEffect(() => {
    getLeagues()
      .then((leagues) => setLeague(leagues.find((l) => l.league_key === leagueKey) ?? null))
      .catch((err: unknown) => {
        setLeague(null)
        showError(err instanceof ApiError ? err.message : 'Failed to load league')
      })
    getTeams(leagueKey)
      .then(setTeams)
      .catch((err: unknown) => {
        setTeams([])
        showError(err instanceof ApiError ? err.message : 'Failed to load teams')
      })
    loadStandings()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [leagueKey])

  useEffect(() => {
    loadOdds(oddsSources)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [leagueKey, oddsSources])

  useEffect(() => {
    if (!isManualEditable || selectedWeek == null) return
    let cancelled = false
    setMatchupsLoading(true)
    setSaveError(null)
    getManualMatchups(leagueKey, selectedWeek)
      .then((data) => {
        if (cancelled) return
        setMatchups(data)
        setRows(data.length > 0 ? data.map(matchupToRow) : [emptyRow()])
      })
      .catch((err: unknown) => {
        if (!cancelled) showError(err instanceof ApiError ? err.message : 'Failed to load matchups')
      })
      .finally(() => {
        if (!cancelled) setMatchupsLoading(false)
      })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [leagueKey, selectedWeek, isManualEditable])

  function updateRow(index: number, patch: Partial<EditRow>) {
    setRows((prev) => prev.map((r, i) => (i === index ? { ...r, ...patch } : r)))
  }

  function addRow() {
    setRows((prev) => [...prev, emptyRow()])
  }

  function removeRow(index: number) {
    setRows((prev) => prev.filter((_, i) => i !== index))
  }

  async function handleSave() {
    if (selectedWeek == null) return
    setSaveError(null)
    if (rows.some((r) => r.home_team_id === '' || r.away_team_id === '')) {
      setSaveError('Select both teams for every matchup row.')
      return
    }
    const payload: PutManualMatchupsPayload = {
      matchups: rows.map((r) => ({
        home_team_id: Number(r.home_team_id),
        away_team_id: Number(r.away_team_id),
        home_points: r.home_points.trim() === '' ? null : Number(r.home_points),
        away_points: r.away_points.trim() === '' ? null : Number(r.away_points),
      })),
    }
    setSaving(true)
    try {
      const result = await putManualMatchups(leagueKey, selectedWeek, payload)
      setMatchups(result)
      setRows(result.length > 0 ? result.map(matchupToRow) : [emptyRow()])
      showSuccess(`Week ${selectedWeek} saved`)
      loadStandings()
      loadOdds(oddsSources)
    } catch (err) {
      if (err instanceof ApiError && err.status === 400) {
        setSaveError(err.message)
      } else {
        showError(err instanceof ApiError ? err.message : 'Failed to save matchups')
      }
    } finally {
      setSaving(false)
    }
  }

  async function handleClearWeek() {
    if (selectedWeek == null) return
    if (!window.confirm(`Clear all matchups for week ${selectedWeek}? This cannot be undone.`)) return
    setClearing(true)
    try {
      await deleteManualMatchups(leagueKey, selectedWeek)
      setMatchups([])
      setRows([emptyRow()])
      setSaveError(null)
      showSuccess(`Week ${selectedWeek} cleared`)
      loadStandings()
      loadOdds(oddsSources)
    } catch (err) {
      showError(err instanceof ApiError ? err.message : 'Failed to clear week')
    } finally {
      setClearing(false)
    }
  }

  const maxRows = teams ? Math.floor(teams.length / 2) : 0

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>Standings &amp; Odds</h1>
          <p className="field-hint">Season standings, Monte Carlo playoff odds, and (for manual leagues) weekly results entry.</p>
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

      {isManualEditable && (
        <Card>
          <div className="panel-header">
            <h2>Weekly results</h2>
            <label className="field standings-week-field">
              <span className="field-label">Week</span>
              <select
                className="field-input"
                value={selectedWeek ?? ''}
                disabled={!odds}
                onChange={(e) => setSelectedWeek(Number(e.target.value))}
              >
                {odds &&
                  Array.from({ length: odds.regular_season_weeks }, (_, i) => i + 1).map((w) => (
                    <option key={w} value={w}>
                      Week {w}
                    </option>
                  ))}
              </select>
            </label>
          </div>

          <p className="field-hint">
            Enter pairings without scores to record the future schedule; odds use it.
          </p>

          {!odds && (
            <div className="loading-row">
              <Spinner /> Waiting for season info…
            </div>
          )}

          {odds && matchupsLoading && (
            <div className="loading-row">
              <Spinner /> Loading week {selectedWeek}…
            </div>
          )}

          {odds && !matchupsLoading && teams && (
            <>
              <div className="matchup-edit-rows">
                {rows.map((row, i) => (
                  <div className="matchup-edit-row" key={i}>
                    <select
                      className="field-input"
                      value={row.home_team_id}
                      onChange={(e) =>
                        updateRow(i, { home_team_id: e.target.value ? Number(e.target.value) : '' })
                      }
                    >
                      <option value="">Home team…</option>
                      {teams.map((t) => (
                        <option key={t.id} value={t.id}>
                          {t.name}
                        </option>
                      ))}
                    </select>
                    <input
                      className="field-input matchup-edit-score"
                      type="number"
                      step="0.1"
                      placeholder="—"
                      value={row.home_points}
                      onChange={(e) => updateRow(i, { home_points: e.target.value })}
                    />
                    <span className="matchup-edit-vs">vs</span>
                    <input
                      className="field-input matchup-edit-score"
                      type="number"
                      step="0.1"
                      placeholder="—"
                      value={row.away_points}
                      onChange={(e) => updateRow(i, { away_points: e.target.value })}
                    />
                    <select
                      className="field-input"
                      value={row.away_team_id}
                      onChange={(e) =>
                        updateRow(i, { away_team_id: e.target.value ? Number(e.target.value) : '' })
                      }
                    >
                      <option value="">Away team…</option>
                      {teams.map((t) => (
                        <option key={t.id} value={t.id}>
                          {t.name}
                        </option>
                      ))}
                    </select>
                    <Button
                      variant="danger"
                      className="matchup-edit-remove"
                      aria-label="Remove matchup"
                      onClick={() => removeRow(i)}
                    >
                      ×
                    </Button>
                  </div>
                ))}
                {rows.length === 0 && <EmptyState>No matchups for this week yet.</EmptyState>}
              </div>

              {saveError && <InlineError>{saveError}</InlineError>}

              <div className="panel-header-actions standings-actions">
                <Button onClick={addRow} disabled={rows.length >= maxRows}>
                  Add matchup
                </Button>
                <Button variant="danger" busy={clearing} onClick={handleClearWeek} disabled={!matchups || matchups.length === 0}>
                  Clear week
                </Button>
                <Button variant="primary" busy={saving} onClick={handleSave}>
                  Save
                </Button>
              </div>
            </>
          )}
        </Card>
      )}

      {!canEdit && league?.source === 'manual' && (
        <p className="field-hint">Sign in as the league owner to enter weekly results.</p>
      )}
      {league && league.source !== 'manual' && (
        <p className="field-hint">
          <Badge>{league.source}</Badge> leagues sync results automatically — weekly entry is only available for
          manual leagues.
        </p>
      )}
    </div>
  )
}
