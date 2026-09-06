import { useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { evaluateTrade, getRoster, getTeams } from '../api/endpoints'
import type { RosterPlayer, Team, TradeEvaluateResponse, TradeSideResult } from '../api/types'
import { ApiError } from '../api/client'
import { Badge, Button, Card, EmptyState, InlineError, Spinner } from '../components/ui'
import { useToast } from '../components/toastContext'

function formatNum(n: number | null | undefined): string {
  if (n === null || n === undefined) return '—'
  return n.toFixed(1)
}

function TeamSidePicker({
  label,
  teams,
  teamId,
  onTeamChange,
  roster,
  rosterLoading,
  selected,
  onToggle,
}: {
  label: string
  teams: Team[]
  teamId: number | null
  onTeamChange: (id: number | null) => void
  roster: RosterPlayer[] | null
  rosterLoading: boolean
  selected: Set<string>
  onToggle: (playerId: string) => void
}) {
  return (
    <Card className="trade-side-card">
      <div className="panel-header">
        <h2>{label}</h2>
      </div>
      <label className="field">
        <span className="field-label">Team</span>
        <select
          className="field-input"
          value={teamId ?? ''}
          onChange={(e) => onTeamChange(e.target.value ? Number(e.target.value) : null)}
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

      {teamId != null && rosterLoading && (
        <div className="loading-row">
          <Spinner /> Loading roster…
        </div>
      )}

      {teamId != null && !rosterLoading && roster !== null && roster.length === 0 && (
        <EmptyState>No players rostered.</EmptyState>
      )}

      {teamId != null && !rosterLoading && roster !== null && roster.length > 0 && (
        <ul className="trade-roster-list">
          {roster.map((player) => (
            <li key={player.player_id} className="trade-roster-item">
              <label className="trade-roster-checkbox">
                <input
                  type="checkbox"
                  checked={selected.has(player.player_id)}
                  onChange={() => onToggle(player.player_id)}
                />
                <span className="roster-player-name">{player.full_name}</span>
                <span className="roster-player-meta">
                  {player.position} · {player.nfl_team || 'FA'}
                  {player.injury_status && <Badge tone="warning">{player.injury_status}</Badge>}
                </span>
              </label>
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}

function TradeSideResultCard({ label, side }: { label: string; side: TradeSideResult }) {
  const delta = side.lineup_delta
  return (
    <Card className="trade-side-card">
      <div className="panel-header">
        <h2>{label !== side.team_name ? `${label}: ${side.team_name}` : side.team_name}</h2>
      </div>
      <ul className="trade-roster-list">
        {side.players.map((p) => (
          <li key={p.player_id} className="trade-roster-item">
            <span className="roster-player-name">{p.full_name}</span>
            <span className="roster-player-meta">
              {p.position} · {formatNum(p.ros_points)} ROS · {formatNum(p.ppg)} PPG · Value {formatNum(p.value)}
            </span>
          </li>
        ))}
      </ul>
      <div className="trade-totals">
        <div className="trade-total-row">
          <span>Total ROS points</span>
          <strong>{formatNum(side.total_ros_points)}</strong>
        </div>
        <div className="trade-total-row">
          <span>Total value</span>
          <strong>{formatNum(side.total_value)}</strong>
        </div>
        <div className="trade-total-row">
          <span>Lineup points before → after</span>
          <strong>
            {formatNum(side.lineup_points_before)} → {formatNum(side.lineup_points_after)}
          </strong>
        </div>
        <div className="trade-total-row">
          <span>Lineup delta</span>
          <strong className={delta > 0 ? 'delta-positive' : delta < 0 ? 'delta-negative' : undefined}>
            {delta > 0 ? '+' : ''}
            {formatNum(delta)}
          </strong>
        </div>
      </div>
    </Card>
  )
}

export function TradeAnalyzerPage() {
  const { leagueKey = '' } = useParams<{ leagueKey: string }>()
  const { showError } = useToast()

  const [teams, setTeams] = useState<Team[] | null>(null)
  const [teamAId, setTeamAId] = useState<number | null>(null)
  const [teamBId, setTeamBId] = useState<number | null>(null)
  const [rosterA, setRosterA] = useState<RosterPlayer[] | null>(null)
  const [rosterB, setRosterB] = useState<RosterPlayer[] | null>(null)
  const [rosterALoading, setRosterALoading] = useState(false)
  const [rosterBLoading, setRosterBLoading] = useState(false)
  const [selectedA, setSelectedA] = useState<Set<string>>(new Set())
  const [selectedB, setSelectedB] = useState<Set<string>>(new Set())
  const [evaluating, setEvaluating] = useState(false)
  const [result, setResult] = useState<TradeEvaluateResponse | null>(null)
  const [evalError, setEvalError] = useState<string | null>(null)

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
    if (teamAId == null) {
      setRosterA(null)
      return
    }
    let cancelled = false
    setRosterALoading(true)
    setSelectedA(new Set())
    getRoster(teamAId)
      .then((data) => {
        if (!cancelled) setRosterA(data)
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setRosterA([])
          showError(err instanceof ApiError ? err.message : 'Failed to load roster')
        }
      })
      .finally(() => {
        if (!cancelled) setRosterALoading(false)
      })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [teamAId])

  useEffect(() => {
    if (teamBId == null) {
      setRosterB(null)
      return
    }
    let cancelled = false
    setRosterBLoading(true)
    setSelectedB(new Set())
    getRoster(teamBId)
      .then((data) => {
        if (!cancelled) setRosterB(data)
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setRosterB([])
          showError(err instanceof ApiError ? err.message : 'Failed to load roster')
        }
      })
      .finally(() => {
        if (!cancelled) setRosterBLoading(false)
      })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [teamBId])

  function toggleA(playerId: string) {
    setSelectedA((prev) => {
      const next = new Set(prev)
      if (next.has(playerId)) next.delete(playerId)
      else next.add(playerId)
      return next
    })
  }

  function toggleB(playerId: string) {
    setSelectedB((prev) => {
      const next = new Set(prev)
      if (next.has(playerId)) next.delete(playerId)
      else next.add(playerId)
      return next
    })
  }

  function handleReset() {
    setSelectedA(new Set())
    setSelectedB(new Set())
    setResult(null)
    setEvalError(null)
  }

  const canEvaluate = teamAId != null && teamBId != null && selectedA.size > 0 && selectedB.size > 0

  const myTeamName = useMemo(() => teams?.find((t) => t.id === teamAId)?.name, [teams, teamAId])

  async function handleEvaluate() {
    if (!canEvaluate || teamAId == null || teamBId == null) return
    setEvaluating(true)
    setEvalError(null)
    setResult(null)
    try {
      const res = await evaluateTrade(leagueKey, {
        side_a: { team_id: teamAId, player_ids: Array.from(selectedA).map((id) => Number(id)) },
        side_b: { team_id: teamBId, player_ids: Array.from(selectedB).map((id) => Number(id)) },
      })
      setResult(res)
    } catch (err) {
      setEvalError(err instanceof ApiError ? err.message : 'Failed to evaluate trade')
    } finally {
      setEvaluating(false)
    }
  }

  if (teams === null) {
    return (
      <div className="page loading-row">
        <Spinner /> Loading teams…
      </div>
    )
  }

  const verdictLabel =
    result &&
    (result.verdict === 'fair'
      ? 'Fair trade'
      : result.verdict === 'favors_a'
        ? `Favors ${result.sides.a.team_name}`
        : `Favors ${result.sides.b.team_name}`)

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>Trade analyzer</h1>
          {myTeamName && <p className="field-hint">Comparing trades for {myTeamName}</p>}
        </div>
        <Link to={`/leagues/${encodeURIComponent(leagueKey)}`}>
          <Button>Back to league</Button>
        </Link>
      </div>

      <div className="trade-grid">
        <TeamSidePicker
          label="Side A sends"
          teams={teams}
          teamId={teamAId}
          onTeamChange={setTeamAId}
          roster={rosterA}
          rosterLoading={rosterALoading}
          selected={selectedA}
          onToggle={toggleA}
        />
        <TeamSidePicker
          label="Side B sends"
          teams={teams}
          teamId={teamBId}
          onTeamChange={setTeamBId}
          roster={rosterB}
          rosterLoading={rosterBLoading}
          selected={selectedB}
          onToggle={toggleB}
        />
      </div>

      <div className="trade-actions">
        <Button variant="primary" busy={evaluating} disabled={!canEvaluate} onClick={handleEvaluate}>
          Evaluate trade
        </Button>
        <Button onClick={handleReset}>Reset</Button>
      </div>

      {evalError && <InlineError>{evalError}</InlineError>}

      {result && (
        <div className="trade-result">
          <div
            className={`trade-verdict-banner trade-verdict-${result.verdict === 'fair' ? 'fair' : 'lopsided'}`}
          >
            <span className="trade-verdict-label">{verdictLabel}</span>
            <span className="trade-verdict-margin">Margin: {formatNum(result.margin_pct)}%</span>
          </div>

          <div className="trade-grid">
            <TradeSideResultCard label="Side A" side={result.sides.a} />
            <TradeSideResultCard label="Side B" side={result.sides.b} />
          </div>

          {result.notes.length > 0 && (
            <Card>
              <h3>Notes</h3>
              <ul className="trade-notes">
                {result.notes.map((note, i) => (
                  <li key={i}>{note}</li>
                ))}
              </ul>
            </Card>
          )}
        </div>
      )}
    </div>
  )
}
