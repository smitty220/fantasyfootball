import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { getTeamLineup, getTeams } from '../api/endpoints'
import type { LineupPlayer, Team, TeamLineupResponse } from '../api/types'
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
          {player.injury_status && <Badge tone="warning">{player.injury_status}</Badge>}
          <span className="matchup-cell-value">{fmtPts(player.week_points)}</span>
        </>
      ) : (
        <span className="matchup-cell-value">—</span>
      )}
    </td>
  )
}

export function MatchupPage() {
  const { leagueKey = '' } = useParams<{ leagueKey: string }>()
  const { showError } = useToast()

  const [teams, setTeams] = useState<Team[] | null>(null)
  const [teamAId, setTeamAId] = useState<number | null>(null)
  const [teamBId, setTeamBId] = useState<number | null>(null)
  const [lineupA, setLineupA] = useState<TeamLineupResponse | null>(null)
  const [lineupB, setLineupB] = useState<TeamLineupResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [sources, setSources] = useState<string[]>([])

  useEffect(() => {
    getTeams(leagueKey)
      .then((data) => {
        setTeams(data)
        const mine = data.find((t) => t.is_my_team)
        if (mine) setTeamAId(mine.id)
      })
      .catch((err: unknown) => {
        setTeams([])
        showError(err instanceof ApiError ? err.message : 'Failed to load teams')
      })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [leagueKey])

  useEffect(() => {
    if (teamAId == null || teamBId == null) {
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
  }, [leagueKey, teamAId, teamBId, sources])

  if (teams === null) {
    return (
      <div className="page loading-row">
        <Spinner /> Loading teams…
      </div>
    )
  }

  const teamA = teams.find((t) => t.id === teamAId) ?? null
  const teamB = teams.find((t) => t.id === teamBId) ?? null

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
          <p className="field-hint">Current-week projection based on each team's current lineup.</p>
        </div>
        <Link to={`/leagues/${encodeURIComponent(leagueKey)}`}>
          <Button>Back to league</Button>
        </Link>
      </div>

      <SourcePicker onChange={setSources} />

      <div className="field-row">
        <label className="field">
          <span className="field-label">Team A</span>
          <select
            className="field-input"
            value={teamAId ?? ''}
            onChange={(e) => setTeamAId(e.target.value ? Number(e.target.value) : null)}
          >
            <option value="">Select a team…</option>
            {teams.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
                {t.is_my_team ? ' (me)' : ''}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span className="field-label">Team B</span>
          <select
            className="field-input"
            value={teamBId ?? ''}
            onChange={(e) => setTeamBId(e.target.value ? Number(e.target.value) : null)}
          >
            <option value="">Select a team…</option>
            {teams.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
                {t.is_my_team ? ' (me)' : ''}
              </option>
            ))}
          </select>
        </label>
      </div>

      {loading && (
        <div className="loading-row">
          <Spinner /> Loading lineups…
        </div>
      )}

      {!loading && (teamAId == null || teamBId == null) && <EmptyState>Select two teams to compare.</EmptyState>}

      {!loading && haveBoth && (
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
