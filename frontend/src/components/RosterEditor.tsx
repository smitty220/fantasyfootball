import { useEffect, useState } from 'react'
import type { DragEvent, KeyboardEvent, ReactNode } from 'react'
import {
  addRosterPlayer,
  clearLineup,
  getRoster,
  getTeamLineup,
  putLineup,
  removeRosterPlayer,
  searchPlayers,
} from '../api/endpoints'
import type {
  LineupAssignment,
  LineupPlayer,
  LineupSlotEntry,
  PlayerSearchResult,
  RosterPlayer,
  TeamLineupResponse,
} from '../api/types'
import { ApiError } from '../api/client'
import { Badge, Button, EmptyState, Spinner, TextField } from './ui'
import { useToast } from './toastContext'
import { useDebouncedValue } from '../useDebouncedValue'

const POSITION_ORDER = ['QB', 'RB', 'WR', 'TE', 'FLEX', 'K', 'DEF', 'DST', 'BN', 'OTHER']
const FLEX_ELIGIBLE = ['RB', 'WR', 'TE']
const DEF_ALIASES = ['DEF', 'DST']

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

function toRosterPlayer(lp: LineupPlayer): RosterPlayer {
  return {
    player_id: lp.player_id,
    full_name: lp.full_name,
    position: lp.position,
    nfl_team: lp.nfl_team,
    injury_status: null,
  }
}

function slotDisplayLabel(slot: string): string {
  return slot === 'FLEX' ? 'W/R/T' : slot
}

function slotEligibleHint(slot: string): string {
  if (slot === 'FLEX') return 'RB/WR/TE'
  return slot
}

function isEligibleForSlot(position: string, slot: string): boolean {
  if (slot === 'FLEX') return FLEX_ELIGIBLE.includes(position)
  if (slot === 'DEF') return DEF_ALIASES.includes(position)
  return position === slot
}

type MoveSource = { type: 'bench'; player: LineupPlayer } | { type: 'slot'; index: number; player: LineupPlayer }
type MoveTarget = { type: 'bench' } | { type: 'slot'; index: number }

function handleKeyActivate(e: KeyboardEvent, action: () => void) {
  if (e.key === 'Enter' || e.key === ' ') {
    e.preventDefault()
    action()
  }
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
  const [autoSetting, setAutoSetting] = useState(false)
  const [dragSource, setDragSource] = useState<MoveSource | null>(null)
  const [selectedSource, setSelectedSource] = useState<MoveSource | null>(null)
  const debouncedQuery = useDebouncedValue(query, 300)

  function loadRoster() {
    getRoster(teamId)
      .then(setRoster)
      .catch((err: unknown) => {
        setRoster([])
        showError(err instanceof ApiError ? err.message : 'Failed to load roster')
      })
  }

  function loadLineup(): Promise<void> {
    return getTeamLineup(leagueKey, teamId)
      .then(setLineup)
      .catch(() => {
        // Non-fatal: fall back to a plain roster listing without slot badges.
        setLineup(null)
      })
  }

  useEffect(() => {
    setRoster(null)
    setLineup(null)
    setSelectedSource(null)
    setDragSource(null)
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
    setSelectedSource(null)
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

  function commitLineup(newSlots: LineupSlotEntry[], newBench: LineupPlayer[]) {
    if (!lineup) return
    const prev = lineup
    setLineup({ ...lineup, slots: newSlots, bench: newBench })
    const assignments: LineupAssignment[] = newSlots
      .filter((s): s is LineupSlotEntry & { player: LineupPlayer } => s.player !== null)
      .map((s) => ({ player_id: Number(s.player.player_id), slot: s.slot }))
    putLineup(teamId, assignments)
      .then((resp) => setLineup(resp))
      .catch((err: unknown) => {
        setLineup(prev)
        showError(err instanceof ApiError ? err.message : 'Failed to update lineup')
      })
  }

  function performMove(source: MoveSource, target: MoveTarget) {
    if (!lineup) return
    if (source.type === 'slot' && target.type === 'slot' && source.index === target.index) return
    if (source.type === 'bench' && target.type === 'bench') return
    if (target.type === 'slot' && !isEligibleForSlot(source.player.position, lineup.slots[target.index].slot)) return

    const newSlots = lineup.slots.map((s) => ({ ...s }))
    let newBench = [...lineup.bench]

    if (source.type === 'bench') {
      newBench = newBench.filter((p) => p.player_id !== source.player.player_id)
    } else {
      newSlots[source.index] = { ...newSlots[source.index], player: null }
    }

    if (target.type === 'bench') {
      newBench.push(source.player)
    } else {
      const displaced = newSlots[target.index].player
      newSlots[target.index] = { ...newSlots[target.index], player: source.player }
      if (displaced) {
        if (source.type === 'slot' && isEligibleForSlot(displaced.position, newSlots[source.index].slot)) {
          newSlots[source.index] = { ...newSlots[source.index], player: displaced }
        } else {
          newBench.push(displaced)
        }
      }
    }

    commitLineup(newSlots, newBench)
  }

  async function handleAutoSet() {
    setAutoSetting(true)
    setSelectedSource(null)
    try {
      await clearLineup(teamId)
      await loadLineup()
      showSuccess('Lineup reset to auto-optimal')
    } catch (err) {
      showError(err instanceof ApiError ? err.message : 'Failed to reset lineup')
    } finally {
      setAutoSetting(false)
    }
  }

  function handleDragStart(source: MoveSource) {
    setSelectedSource(null)
    setDragSource(source)
  }

  function handleDragEnd() {
    setDragSource(null)
  }

  function handleSlotDragOver(e: DragEvent, index: number) {
    if (!lineup || !dragSource) return
    if (isEligibleForSlot(dragSource.player.position, lineup.slots[index].slot)) {
      e.preventDefault()
      e.dataTransfer.dropEffect = 'move'
    }
  }

  function handleSlotDrop(e: DragEvent, index: number) {
    e.preventDefault()
    if (dragSource) performMove(dragSource, { type: 'slot', index })
    setDragSource(null)
  }

  function handleBenchDragOver(e: DragEvent) {
    if (!dragSource) return
    e.preventDefault()
    e.dataTransfer.dropEffect = 'move'
  }

  function handleBenchDrop(e: DragEvent) {
    e.preventDefault()
    if (dragSource) performMove(dragSource, { type: 'bench' })
    setDragSource(null)
  }

  function handleSlotClick(index: number) {
    if (!lineup) return
    const slot = lineup.slots[index]
    if (selectedSource) {
      if (selectedSource.type === 'slot' && selectedSource.index === index) {
        setSelectedSource(null)
        return
      }
      if (isEligibleForSlot(selectedSource.player.position, slot.slot)) {
        performMove(selectedSource, { type: 'slot', index })
        setSelectedSource(null)
        return
      }
      if (slot.player) {
        setSelectedSource({ type: 'slot', index, player: slot.player })
      }
      return
    }
    if (slot.player) {
      setSelectedSource({ type: 'slot', index, player: slot.player })
    }
  }

  function handleBenchCardClick(player: LineupPlayer) {
    if (selectedSource && selectedSource.type === 'bench' && selectedSource.player.player_id === player.player_id) {
      setSelectedSource(null)
      return
    }
    setSelectedSource({ type: 'bench', player })
  }

  function handleBenchAreaTarget() {
    if (!selectedSource || selectedSource.type !== 'slot') return
    performMove(selectedSource, { type: 'bench' })
    setSelectedSource(null)
  }

  function renderCard(player: LineupPlayer, source: MoveSource, isSelected: boolean, actions?: ReactNode) {
    return (
      <div
        className={`lineup-card${isSelected ? ' lineup-card-selected' : ''}`}
        draggable={editable}
        onDragStart={editable ? () => handleDragStart(source) : undefined}
        onDragEnd={editable ? handleDragEnd : undefined}
      >
        <div className="lineup-card-main">
          <span className="lineup-card-name">{player.full_name}</span>
          <span className="lineup-card-meta">
            {player.position} · {player.nfl_team || 'FA'}
          </span>
        </div>
        <div className="lineup-card-points">
          <span className="lineup-card-stat">
            <span className="lineup-card-stat-label">Wk</span>
            {player.week_points != null ? player.week_points.toFixed(1) : '—'}
          </span>
          <span className="lineup-card-stat">
            <span className="lineup-card-stat-label">ROS</span>
            {player.ros_points.toFixed(1)}
          </span>
        </div>
        {actions && <div className="lineup-card-actions">{actions}</div>}
      </div>
    )
  }

  function removeAction(player: LineupPlayer) {
    if (!editable) return undefined
    return (
      <Button
        variant="danger"
        onClick={(e) => {
          e.stopPropagation()
          handleRemove(toRosterPlayer(player))
        }}
        busy={removingId === player.player_id}
      >
        Remove
      </Button>
    )
  }

  function renderSlot(entry: LineupSlotEntry, index: number) {
    const active = dragSource ?? selectedSource
    const isOwnSource = !!(active && active.type === 'slot' && active.index === index)
    let stateClass = ''
    if (editable && active) {
      stateClass = isOwnSource
        ? ' lineup-slot-active'
        : isEligibleForSlot(active.player.position, entry.slot)
          ? ' lineup-slot-eligible'
          : ' lineup-slot-ineligible'
    }
    const isSelectedCard = !!(selectedSource && selectedSource.type === 'slot' && selectedSource.index === index)
    return (
      <div
        key={index}
        className={`lineup-slot${stateClass}`}
        role={editable ? 'button' : undefined}
        tabIndex={editable ? 0 : undefined}
        onDragOver={editable ? (e) => handleSlotDragOver(e, index) : undefined}
        onDrop={editable ? (e) => handleSlotDrop(e, index) : undefined}
        onClick={editable ? () => handleSlotClick(index) : undefined}
        onKeyDown={editable ? (e) => handleKeyActivate(e, () => handleSlotClick(index)) : undefined}
      >
        <div className="lineup-slot-label">{slotDisplayLabel(entry.slot)}</div>
        {entry.player ? (
          renderCard(
            entry.player,
            { type: 'slot', index, player: entry.player },
            isSelectedCard,
            removeAction(entry.player),
          )
        ) : (
          <div className="lineup-slot-empty">Drop a {slotEligibleHint(entry.slot)} here</div>
        )}
      </div>
    )
  }

  function renderBenchCard(player: LineupPlayer) {
    const isSelected = !!(
      selectedSource &&
      selectedSource.type === 'bench' &&
      selectedSource.player.player_id === player.player_id
    )
    return (
      <li
        key={player.player_id}
        className="lineup-bench-item"
        role={editable ? 'button' : undefined}
        tabIndex={editable ? 0 : undefined}
        onClick={editable ? () => handleBenchCardClick(player) : undefined}
        onKeyDown={editable ? (e) => handleKeyActivate(e, () => handleBenchCardClick(player)) : undefined}
      >
        {renderCard(player, { type: 'bench', player }, isSelected, removeAction(player))}
      </li>
    )
  }

  return (
    <div className="roster-editor">
      <h3>{teamName} roster</h3>
      <p className="field-hint roster-caption">
        {editable
          ? 'Drag players into slots, or Auto-set for the projected-optimal lineup.'
          : 'Starters reflect the projected-optimal lineup.'}
      </p>

      {roster === null && (
        <div className="loading-row">
          <Spinner /> Loading roster…
        </div>
      )}

      {roster !== null && roster.length === 0 && <EmptyState>No players rostered yet.</EmptyState>}

      {roster !== null && roster.length > 0 && lineup && (
        <div className="lineup-editor">
          <div className="lineup-header">
            <Badge tone={lineup.source === 'manual' ? 'accent' : 'neutral'}>
              Lineup: {lineup.source === 'manual' ? 'manual' : 'auto (optimal)'}
            </Badge>
            {lineup.week != null && <span className="field-hint">Week {lineup.week}</span>}
            {editable && (
              <Button variant="secondary" onClick={handleAutoSet} busy={autoSetting}>
                Auto-set lineup
              </Button>
            )}
          </div>

          <div className="lineup-slots">{lineup.slots.map((entry, index) => renderSlot(entry, index))}</div>

          <div className="lineup-bench-section">
            <div
              className={`roster-group-title lineup-bench-title${
                editable && selectedSource?.type === 'slot' ? ' lineup-bench-title-target' : ''
              }`}
              onClick={editable ? handleBenchAreaTarget : undefined}
            >
              Bench
            </div>
            <ul
              className="lineup-bench"
              onDragOver={editable ? handleBenchDragOver : undefined}
              onDrop={editable ? handleBenchDrop : undefined}
            >
              {lineup.bench.length === 0 && <li className="lineup-bench-empty">No bench players.</li>}
              {lineup.bench.map((player) => renderBenchCard(player))}
            </ul>
          </div>
        </div>
      )}

      {roster !== null && roster.length > 0 && !lineup && (
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
