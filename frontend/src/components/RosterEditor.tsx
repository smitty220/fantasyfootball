import { useEffect, useState } from 'react'
import type { DragEvent, KeyboardEvent, MouseEvent } from 'react'
import { useNavigate } from 'react-router-dom'
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
import { SourcePicker } from './SourcePicker'

const POSITION_ORDER = ['QB', 'RB', 'WR', 'TE', 'FLEX', 'SUPERFLEX', 'K', 'DEF', 'DST', 'BN', 'OTHER']
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
    injury_status: lp.injury_status,
  }
}

function fmtPts(n: number | null | undefined): string {
  return n == null ? '—' : n.toFixed(1)
}

function fmtPct(n: number | null | undefined): string {
  return n == null ? '—' : `${n.toFixed(1)}%`
}

function slotDisplayLabel(slot: string): string {
  if (slot === 'FLEX') return 'W/R/T'
  if (slot === 'SUPERFLEX') return 'Q/W/R/T'
  return slot
}

function slotEligibleHint(slot: string): string {
  if (slot === 'FLEX') return 'RB/WR/TE'
  if (slot === 'SUPERFLEX') return 'QB/RB/WR/TE'
  return slot
}

function isEligibleForSlot(position: string, slot: string): boolean {
  if (slot === 'FLEX') return FLEX_ELIGIBLE.includes(position)
  if (slot === 'SUPERFLEX') return position === 'QB' || FLEX_ELIGIBLE.includes(position)
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
  const navigate = useNavigate()
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
  const [sources, setSources] = useState<string[]>([])
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
    return getTeamLineup(leagueKey, teamId, sources)
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
    loadLineup()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sources])

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

  function removeAction(player: LineupPlayer) {
    if (!editable) return undefined
    return (
      <Button
        variant="danger"
        className="lineup-row-remove"
        aria-label={`Remove ${player.full_name}`}
        onClick={(e) => {
          e.stopPropagation()
          handleRemove(toRosterPlayer(player))
        }}
        busy={removingId === player.player_id}
      >
        ×
      </Button>
    )
  }

  function handleFaFlagClick(e: MouseEvent, player: LineupPlayer) {
    e.stopPropagation()
    navigate(`/leagues/${encodeURIComponent(leagueKey)}?tab=free-agents&position=${encodeURIComponent(player.position)}`)
  }

  function renderFaFlag(player: LineupPlayer) {
    if (player.better_fa_week_points == null) return null
    const value = player.better_fa_week_points
    return (
      <button
        type="button"
        className="fa-upgrade-chip"
        title={`A free agent ${player.position} projects ${value.toFixed(1)} pts this week — click to view`}
        draggable={false}
        onMouseDown={(e) => e.stopPropagation()}
        onDragStart={(e) => e.stopPropagation()}
        onClick={(e) => handleFaFlagClick(e, player)}
      >
        FA Available
      </button>
    )
  }

  function renderPlayerCells(player: LineupPlayer) {
    return (
      <>
        <span className="lineup-row-pos">{player.position}</span>
        <span className="lineup-row-name">
          <span className="lineup-row-name-text">{player.full_name}</span>
          {player.injury_status && (
            <Badge tone="warning">{player.injury_status}</Badge>
          )}
          {renderFaFlag(player)}
        </span>
        <span className="lineup-row-team">{player.nfl_team || 'FA'}</span>
        <span className="lineup-row-stat">{fmtPts(player.week_points)}</span>
        <span className="lineup-row-stat">{fmtPts(player.ros_points)}</span>
        <span className="lineup-row-stat lineup-row-pct">{fmtPct(player.percent_owned)}</span>
        <span className="lineup-row-stat lineup-row-pct">{fmtPct(player.percent_started)}</span>
        <span className="lineup-row-actions">{removeAction(player)}</span>
      </>
    )
  }

  function renderSlot(entry: LineupSlotEntry, index: number) {
    const active = dragSource ?? selectedSource
    const isOwnSource = !!(active && active.type === 'slot' && active.index === index)
    let stateClass = ''
    if (editable && active) {
      stateClass = isOwnSource
        ? ' lineup-row-active'
        : isEligibleForSlot(active.player.position, entry.slot)
          ? ' lineup-row-eligible'
          : ' lineup-row-ineligible'
    }
    const isSelected = !!(selectedSource && selectedSource.type === 'slot' && selectedSource.index === index)
    const player = entry.player
    return (
      <div
        key={index}
        className={`lineup-row${stateClass}${isSelected ? ' lineup-row-selected' : ''}${
          player ? '' : ' lineup-row-empty'
        }`}
        role={editable ? 'button' : undefined}
        tabIndex={editable ? 0 : undefined}
        draggable={editable && !!player}
        onDragStart={editable && player ? () => handleDragStart({ type: 'slot', index, player }) : undefined}
        onDragEnd={editable && player ? handleDragEnd : undefined}
        onDragOver={editable ? (e) => handleSlotDragOver(e, index) : undefined}
        onDrop={editable ? (e) => handleSlotDrop(e, index) : undefined}
        onClick={editable ? () => handleSlotClick(index) : undefined}
        onKeyDown={editable ? (e) => handleKeyActivate(e, () => handleSlotClick(index)) : undefined}
      >
        <span className="lineup-row-chip">{slotDisplayLabel(entry.slot)}</span>
        {player ? (
          renderPlayerCells(player)
        ) : (
          <span className="lineup-row-empty-label">Drop a {slotEligibleHint(entry.slot)} here</span>
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
      <li key={player.player_id} className="lineup-bench-item">
        <div
          className={`lineup-row${isSelected ? ' lineup-row-selected' : ''}`}
          role={editable ? 'button' : undefined}
          tabIndex={editable ? 0 : undefined}
          draggable={editable}
          onDragStart={editable ? () => handleDragStart({ type: 'bench', player }) : undefined}
          onDragEnd={editable ? handleDragEnd : undefined}
          onClick={editable ? () => handleBenchCardClick(player) : undefined}
          onKeyDown={editable ? (e) => handleKeyActivate(e, () => handleBenchCardClick(player)) : undefined}
        >
          <span className="lineup-row-chip lineup-row-chip-bench">BN</span>
          {renderPlayerCells(player)}
        </div>
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

      <SourcePicker onChange={setSources} />

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

          <div className="lineup-columns-header" aria-hidden="true">
            <span className="lineup-col" />
            <span className="lineup-col">Pos</span>
            <span className="lineup-col">Player</span>
            <span className="lineup-col">Team</span>
            <span className="lineup-col">Wk</span>
            <span className="lineup-col">ROS</span>
            <span className="lineup-col lineup-col-pct">%Own</span>
            <span className="lineup-col lineup-col-pct">%Start</span>
            <span className="lineup-col" />
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
