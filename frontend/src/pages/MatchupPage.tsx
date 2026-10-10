import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { getLeagues, getMatchups, getTeamLineup, getTeams } from '../api/endpoints'
import type { LineupPlayer, Matchup, Team, TeamLineupResponse } from '../api/types'
import { ApiError } from '../api/client'
import { Badge, Button, Card, EmptyState, Spinner } from '../components/ui'
import { useToast } from '../components/toastContext'
import { SourcePicker } from '../components/SourcePicker'

function fmtPts(n: number | null | undefined): string {
  return n == null ? '—' : n.toFixed(1)
}

function slotDisplayLabel(slot: string): string {
  if (slot === 'FLEX') return 'W/R/T'
  if (slot === 'SUPERFLEX') return 'Q/W/R/T'
  return slot
}

function slotValue(player: LineupPlayer | null): number | null {
  return player ? player.week_points ?? 0 : null
}

function MatchupCell({ player, lead }: { player: LineupPlayer | null; lead: boolean }) {
  return (
    <td className={`matchup-cell${lead ? ' matchup-cell-lead' : ''}`}>
      {player ? (
        <>
          <span className="matchup-cell-name">{player.full_name}</span>
          {player.on_bye ? (
            <Badge tone="danger">BYE</Badge>
          ) : (
            player.opponent && <span className="matchup-cell-opp">{player.opponent}</span>
          )}
          {player.injury_status && <Badge tone="warning">{player.injury_status}</Badge>}
          <span className="matchup-cell-value">{fmtPts(player.week_points)}</span>
        </>
      ) : (
        <span className="matchup-cell-value">—</span>
      )}
    </td>
  )
}

function gameHasScore(m: Matchup): boolean {
  return m.status === 'postevent' || m.status === 'midevent' || (m.home_points ?? 0) > 0 || (m.away_points ?? 0) > 0
}

export function MatchupPage() {
  const { leagueKey = '' } = useParams<{ leagueKey: string }>()
  const { showError } = useToast()

  const [teams, setTeams] = useState<Team[] | null>(null)
  const [currentWeek, setCurrentWeek] = useState<number | null>(null)
  const [week, setWeek] = useState<number | null>(null)
  const [matchups, setMatchups] = useState<Matchup[] | null>(null)
  const [gameId, setGameId] = useState<number | null>(null)
  const [lineupA, setLineupA] = useState<TeamLineupResponse | null>(null)
  const [lineupB, setLineupB] = useState<TeamLineupResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [sources, setSources] = useState<string[]>([])

  // Initial load: teams + the league's current week (the default selected week).
  useEffect(() => {
    let cancelled = false
    Promise.all([getTeams(leagueKey), getLeagues()])
      .then(([t, leagues]) => {
        if (cancelled) return
        const cw = leagues.find((l) => l.league_key === leagueKey)?.current_week ?? null
        setTeams(t)
        setCurrentWeek(cw)
        setWeek(cw ?? 1)
      })
      .catch((err: unknown) => {
        if (cancelled) return
        setTeams([])
        setWeek(1)
        showError(err instanceof ApiError ? err.message : 'Failed to load teams')
      })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [leagueKey])

  // Load the selected week's games; default to my game, else the first one.
  useEffect(() => {
    if (week == null || teams === null) return
    let cancelled = false
    setMatchups(null)
    getMatchups(leagueKey, week)
      .then((data) => {
        if (cancelled) return
        const myIds = new Set(teams.filter((t) => t.is_my_team).map((t) => t.id))
        const mine = data.find((m) => myIds.has(m.home_team_id) || myIds.has(m.away_team_id))
        setMatchups(data)
        setGameId((mine ?? data[0])?.id ?? null)
      })
      .catch((err: unknown) => {
        if (cancelled) return
        setMatchups([])
        setGameId(null)
        showError(err instanceof ApiError ? err.message : 'Failed to load matchups')
      })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [leagueKey, week, teams])

  const game = matchups?.find((m) => m.id === gameId) ?? null
  const isCurrent = week != null && week === currentWeek
  const teamAId = game?.home_team_id ?? null
  const teamBId = game?.away_team_id ?? null

  // Projections only exist for the current week.
  useEffect(() => {
    if (!isCurrent || teamAId == null || teamBId == null) {
      setLineupA(null)
      setLineupB(null)
      return
    }
    let cancelled = false
    setLoading(true)
    Promise.all([getTeamLineup(leagueKey, teamAId, sources), getTeamLineup(leagueKey, teamBId, sources)])
      .then(([a, b]) => {
        if (!cancelled) {
          setLineupA(a)
          setLineupB(b)
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setLineupA(null)
          setLineupB(null)
          showError(err instanceof ApiError ? err.message : 'Failed to load lineups')
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [leagueKey, isCurrent, teamAId, teamBId, sources])

  if (teams === null || week == null) {
    return (
      <div className="page loading-row">
        <Spinner /> Loading matchups…
      </div>
    )
  }

  // Weeks 1..max(17, current_week): a regular NFL fantasy season is at most 17 weeks.
  const maxWeek = Math.max(17, currentWeek ?? 0)
  const teamName = (id: number) => teams.find((t) => t.id === id)?.name ?? `Team ${id}`
  const isMine = (id: number) => teams.some((t) => t.id === id && t.is_my_team)
  const teamA = teamAId != null ? teams.find((t) => t.id === teamAId) ?? null : null
  const teamB = teamBId != null ? teams.find((t) => t.id === teamBId) ?? null : null

  const rows =
    lineupA && lineupB
      ? lineupA.slots.map((entry, i) => ({
          slot: entry.slot,
          playerA: entry.player,
          playerB: lineupB.slots[i]?.player ?? null,
        }))
      : []

  let totalA = 0
  let totalB = 0
  let hadNull = false
  for (const row of rows) {
    if (row.playerA) {
      totalA += row.playerA.week_points ?? 0
      if (row.playerA.week_points == null) hadNull = true
    }
    if (row.playerB) {
      totalB += row.playerB.week_points ?? 0
      if (row.playerB.week_points == null) hadNull = true
    }
  }

  const haveBoth = lineupA !== null && lineupB !== null
  const winner = !haveBoth ? null : totalA === totalB ? 'even' : totalA > totalB ? teamA?.name : teamB?.name

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>Matchup preview</h1>
          <p className="field-hint">Pick a week and a game. Player projections are available for the current week.</p>
        </div>
        <Link to={`/leagues/${encodeURIComponent(leagueKey)}`}>
          <Button>Back to league</Button>
        </Link>
      </div>

      <SourcePicker onChange={setSources} />

      <label className="field">
        <span className="field-label">Week</span>
        <select className="field-input" value={week} onChange={(e) => setWeek(Number(e.target.value))}>
          {Array.from({ length: maxWeek }, (_, i) => i + 1).map((w) => (
            <option key={w} value={w}>
              Week {w}
              {w === currentWeek ? ' (current)' : ''}
            </option>
          ))}
        </select>
      </label>

      {matchups === null && (
        <div className="loading-row">
          <Spinner /> Loading games…
        </div>
      )}

      {matchups !== null && matchups.length === 0 && <EmptyState>No schedule for week {week} yet.</EmptyState>}

      {matchups !== null && matchups.length > 0 && (
        <Card>
          <ul className="game-list">
            {matchups.map((m) => {
              const scored = gameHasScore(m)
              return (
                <li key={m.id}>
                  <button
                    type="button"
                    className={`game-item${m.id === gameId ? ' game-item-active' : ''}`}
                    onClick={() => setGameId(m.id)}
                  >
                    <span className="game-item-team">
                      {teamName(m.home_team_id)}
                      {isMine(m.home_team_id) && ' (me)'}
                    </span>
                    <span className="game-item-score">
                      {scored ? `${fmtPts(m.home_points)} – ${fmtPts(m.away_points)}` : 'vs'}
                    </span>
                    <span className="game-item-team game-item-team-away">
                      {teamName(m.away_team_id)}
                      {isMine(m.away_team_id) && ' (me)'}
                    </span>
                  </button>
                </li>
              )
            })}
          </ul>
        </Card>
      )}

      {game && !isCurrent && (
        <Card>
          <div className="matchup-totals">
            <div className={`matchup-total-side${(game.home_points ?? 0) > (game.away_points ?? 0) ? ' matchup-total-lead' : ''}`}>
              <span>{teamName(game.home_team_id)}</span>
              <strong>{gameHasScore(game) ? fmtPts(game.home_points) : '—'}</strong>
            </div>
            <div className={`matchup-total-side${(game.away_points ?? 0) > (game.home_points ?? 0) ? ' matchup-total-lead' : ''}`}>
              <span>{teamName(game.away_team_id)}</span>
              <strong>{gameHasScore(game) ? fmtPts(game.away_points) : '—'}</strong>
            </div>
          </div>
          <p className="field-hint">
            {currentWeek != null && week < currentWeek
              ? 'Final team scores from the matchup record.'
              : 'Player projections are only available for the current week.'}
          </p>
        </Card>
      )}

      {game && isCurrent && loading && (
        <div className="loading-row">
          <Spinner /> Loading lineups…
        </div>
      )}

      {game && isCurrent && !loading && haveBoth && (
        <Card>
          <div className="table-scroll">
            <table className="data-table matchup-table">
              <tbody>
                {rows.map((row, i) => {
                  const valA = slotValue(row.playerA)
                  const valB = slotValue(row.playerB)
                  const leadA = valA != null && (valB == null || valA > valB)
                  const leadB = valB != null && (valA == null || valB > valA)
                  return (
                    <tr key={i}>
                      <MatchupCell player={row.playerA} lead={leadA} />
                      <td className="matchup-slot">{slotDisplayLabel(row.slot)}</td>
                      <MatchupCell player={row.playerB} lead={leadB} />
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>

          <div className="matchup-totals">
            <div className={`matchup-total-side${totalA > totalB ? ' matchup-total-lead' : ''}`}>
              <span>{teamA?.name}</span>
              <strong>{totalA.toFixed(1)}</strong>
            </div>
            <div className={`matchup-total-side${totalB > totalA ? ' matchup-total-lead' : ''}`}>
              <span>{teamB?.name}</span>
              <strong>{totalB.toFixed(1)}</strong>
            </div>
          </div>
          <p className="matchup-winner">
            {winner === 'even' ? 'Even matchup' : `Projected winner: ${winner}`}
          </p>
          {hadNull && <p className="field-hint">— = no weekly projection (counts 0)</p>}
        </Card>
      )}
    </div>
  )
}
