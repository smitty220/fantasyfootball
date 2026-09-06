import { useEffect, useState } from 'react'
import { addRosterPlayer, getRoster, getTeamLineup, removeRosterPlayer, searchPlayers } from '../api/endpoints'
import type { LineupPlayer, PlayerSearchResult, RosterPlayer, TeamLineupResponse } from '../api/types'
import { ApiError } from '../api/client'
import { Badge, Button, EmptyState, Spinner, TextField } from './ui'
import { useToast } from './toastContext'
import { useDebouncedValue } from '../useDebouncedValue'

const POSITION_ORDER = ['QB', 'RB', 'WR', 'TE', 'FLEX', 'K', 'DEF', 'DST', 'BN', 'OTHER']

function groupByPosition(roster: RosterPlayer[]): [string, RosterPlayer[]][] {
  const groups = new Map<string, RosterPlayer[]>()
  for (const player of roster) {
    const pos = player.position || 'OTHER'
    if (!groups.has(pos)) groups.set(pos, [])
    groups.get(pos)!.push(player)
  }
  const entries = Array.from(groups.entries())
  entries.sort((a, b) => {
    const ai = POSITION_ORDER.indexOf(a[0])
    const bi = POSITION_ORDER.indexOf(b[0])
    return (ai === -1 ? 99 : ai) - (bi === -1 ? 99 : bi)
  })
  return entries
}

type SlottedPlayer = { player: RosterPlayer; slot: string | null; isStarter: boolean }

function toRosterPlayer(lp: LineupPlayer): RosterPlayer {
  return {
    player_id: lp.player_id,
    full_name: lp.full_name,
    position: lp.position,
    nfl_team: lp.nfl_team,
    injury_status: null,
  }
}

function buildSlottedRoster(roster: RosterPlayer[], lineup: TeamLineupResponse): {
  starters: SlottedPlayer[]
  bench: SlottedPlayer[]
} {
  const rosterById = new Map(roster.map((p) => [p.player_id, p]))
  const seen = new Set<string>()
  const starters: SlottedPlayer[] = lineup.starters.map((s) => {
    seen.add(s.player_id)
    return { player: rosterById.get(s.player_id) ?? toRosterPlayer(s), slot: s.slot ?? null, isStarter: true }
  })
  const bench: SlottedPlayer[] = lineup.bench.map((b) => {
    seen.add(b.player_id)
    return { player: rosterById.get(b.player_id) ?? toRosterPlayer(b), slot: null, isStarter: false }
  })
  for (const p of roster) {
    if (!seen.has(p.player_id)) bench.push({ player: p, slot: null, isStarter: false })
  }
  return { starters, bench }
}

export function RosterEditor({
  leagueKey,
  teamId,
  teamName,
  editable,
}: {
  leagueKey: string
  teamId: number
  teamName: string
  editable: boolean
}) {
  const { showError, showSuccess } = useToast()
  const [roster, setRoster] = useState<RosterPlayer[] | null>(null)
  const [lineup, setLineup] = useState<TeamLineupResponse | null>(null)
  const [query, setQuery] = useState('')
  const [positionFilter, setPositionFilter] = useState('')
  const [results, setResults] = useState<PlayerSearchResult[] | null>(null)
  const [searching, setSearching] = useState(false)
  const [addingId, setAddingId] = useState<string | null>(null)
  const [removingId, setRemovingId] = useState<string | null>(null)
  const debouncedQuery = useDebouncedValue(query, 300)

  function loadRoster() {
    getRoster(teamId)
      .then(setRoster)
      .catch((err: unknown) => {
        setRoster([])
        showError(err instanceof ApiError ? err.message : 'Failed to load roster')
      })
  }

  function loadLineup() {
    getTeamLineup(leagueKey, teamId)
      .then(setLineup)
      .catch(() => {
        // Non-fatal: fall back to a plain roster listing without slot badges.
        setLineup(null)
      })
  }

  useEffect(() => {
    setRoster(null)
    setLineup(null)
    loadRoster()
    loadLineup()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [teamId, leagueKey])

  useEffect(() => {
    if (!editable) return
    if (debouncedQuery.trim().length < 2) {
      setResults(null)
      return
    }
    let cancelled = false
    setSearching(true)
    searchPlayers(debouncedQuery.trim(), positionFilter || undefined, 15)
      .then((data) => {
        if (!cancelled) setResults(data)
      })
      .catch((err: unknown) => {
        if (!cancelled) showError(err instanceof ApiError ? err.message : 'Player search failed')
      })
      .finally(() => {
        if (!cancelled) setSearching(false)
      })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedQuery, positionFilter, editable])

  async function handleAdd(player: PlayerSearchResult) {
    setAddingId(player.id)
    try {
      await addRosterPlayer(teamId, player.id)
      showSuccess(`Added ${player.full_name}`)
      loadRoster()
      loadLineup()
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        showError(`${player.full_name} is already rostered in this league`)
      } else {
        showError(err instanceof ApiError ? err.message : 'Failed to add player')
      }
    } finally {
      setAddingId(null)
    }
  }

  async function handleRemove(player: RosterPlayer) {
    setRemovingId(player.player_id)
    try {
      await removeRosterPlayer(teamId, player.player_id)
      showSuccess(`Removed ${player.full_name}`)
      loadRoster()
      loadLineup()
    } catch (err) {
      showError(err instanceof ApiError ? err.message : 'Failed to remove player')
    } finally {
      setRemovingId(null)
    }
  }

  const slotted = roster !== null && roster.length > 0 && lineup ? buildSlottedRoster(roster, lineup) : null

  function renderPlayerRow({ player, slot, isStarter }: SlottedPlayer) {
    return (
      <li key={player.player_id} className="roster-list-item">
        <Badge tone={isStarter ? 'accent' : 'neutral'}>{isStarter ? slot || player.position : 'BN'}</Badge>
        <span className="roster-player-name">{player.full_name}</span>
        <span className="roster-player-meta">
          {player.nfl_team || 'FA'}
          {player.injury_status && <Badge tone="warning">{player.injury_status}</Badge>}
        </span>
        {editable && (
          <Button variant="danger" onClick={() => handleRemove(player)} busy={removingId === player.player_id}>
            Remove
          </Button>
        )}
      </li>
    )
  }

  return (
    <div className="roster-editor">
      <h3>{teamName} roster</h3>
      <p className="field-hint roster-caption">Starters = optimal projected lineup; syncs with Yahoo later.</p>

      {roster === null && (
        <div className="loading-row">
          <Spinner /> Loading roster…
        </div>
      )}

      {roster !== null && roster.length === 0 && <EmptyState>No players rostered yet.</EmptyState>}

      {roster !== null && roster.length > 0 && slotted && (
        <div className="roster-groups">
          <div className="roster-group">
            <div className="roster-group-title">Starters</div>
            <ul className="roster-list">{slotted.starters.map((entry) => renderPlayerRow(entry))}</ul>
          </div>
          <div className="roster-group">
            <div className="roster-group-title">Bench</div>
            <ul className="roster-list">{slotted.bench.map((entry) => renderPlayerRow(entry))}</ul>
          </div>
        </div>
      )}

      {roster !== null && roster.length > 0 && !slotted && (
        <div className="roster-groups">
          {groupByPosition(roster).map(([pos, players]) => (
            <div className="roster-group" key={pos}>
              <div className="roster-group-title">{pos}</div>
              <ul className="roster-list">
                {players.map((player) => (
                  <li key={player.player_id} className="roster-list-item">
                    <span className="roster-player-name">{player.full_name}</span>
                    <span className="roster-player-meta">
                      {player.nfl_team || 'FA'}
                      {player.injury_status && <Badge tone="warning">{player.injury_status}</Badge>}
                    </span>
                    {editable && (
                      <Button
                        variant="danger"
                        onClick={() => handleRemove(player)}
                        busy={removingId === player.player_id}
                      >
                        Remove
                      </Button>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}

      {editable && (
        <div className="player-search">
          <h4>Add a player</h4>
          <div className="field-row">
            <TextField
              label="Search players"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Player name…"
            />
            <label className="field">
              <span className="field-label">Position</span>
              <select className="field-input" value={positionFilter} onChange={(e) => setPositionFilter(e.target.value)}>
                <option value="">All</option>
                <option value="QB">QB</option>
                <option value="RB">RB</option>
                <option value="WR">WR</option>
                <option value="TE">TE</option>
                <option value="K">K</option>
                <option value="DEF">DEF</option>
              </select>
            </label>
          </div>

          {searching && (
            <div className="loading-row">
              <Spinner /> Searching…
            </div>
          )}

          {results !== null && results.length === 0 && !searching && <EmptyState>No matching players.</EmptyState>}

          {results !== null && results.length > 0 && (
            <ul className="search-results">
              {results.map((player) => (
                <li key={player.id} className="search-result-item" onClick={() => handleAdd(player)}>
                  <span className="roster-player-name">{player.full_name}</span>
                  <span className="roster-player-meta">
                    {player.position} · {player.nfl_team || 'FA'}
                    {player.injury_status && <Badge tone="warning">{player.injury_status}</Badge>}
                    {player.trade_value != null && <Badge tone="accent">Value {player.trade_value}</Badge>}
                  </span>
                  {addingId === player.id ? <Spinner size={14} /> : <span className="add-hint">Add</span>}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  )
}
