import { useEffect, useRef, useState } from 'react'
import type { MouseEvent, ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { getRoster, getTeamLineup } from '../api/endpoints'
import type {
  LineupMode,
  LineupPlayer,
  LineupSlotEntry,
  RosterPlayer,
  TeamLineupResponse,
} from '../api/types'
import { ApiError } from '../api/client'
import { Badge, EmptyState, Spinner } from './ui'
import { useToast } from './toastContext'
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

function isEligibleForSlot(position: string, slot: string): boolean {
  if (slot === 'FLEX') return FLEX_ELIGIBLE.includes(position)
  if (slot === 'SUPERFLEX') return position === 'QB' || FLEX_ELIGIBLE.includes(position)
  if (slot === 'DEF') return DEF_ALIASES.includes(position)
  return position === slot
}

// Finds the best bench player eligible for a filled starting slot whose
// week_points strictly beats the starter's — informational only, so it runs
// in read-only mode too. Bench players with no week projection, and Yahoo
// IR/NA players (who can't start without a roster move), are ignored.
function findBenchUpgrade(slot: string, occupantWeekPoints: number | null, bench: LineupPlayer[]): LineupPlayer | null {
  const occupant = occupantWeekPoints ?? -Infinity
  let best: LineupPlayer | null = null
  for (const p of bench) {
    if (p.week_points == null) continue
    if (p.reserve) continue
    if (!isEligibleForSlot(p.position, slot)) continue
    if (p.week_points > occupant && (best == null || p.week_points > (best.week_points as number))) {
      best = p
    }
  }
  return best
}


export function RosterEditor({
  leagueKey,
  teamId,
  teamName,
  isYahoo = false,
}: {
  leagueKey: string
  teamId: number
  teamName: string
  /** Yahoo league: default to the lineup set in Yahoo, with an Optimal toggle. */
  isYahoo?: boolean
}) {
  const { showError } = useToast()
  const navigate = useNavigate()
  const [roster, setRoster] = useState<RosterPlayer[] | null>(null)
  const [lineup, setLineup] = useState<TeamLineupResponse | null>(null)
  const [sources, setSources] = useState<string[]>([])
  // Yahoo leagues only; client-side, defaults back to Yahoo's lineup on remount.
  const [lineupMode, setLineupMode] = useState<LineupMode>('actual')
  const lineupRequest = useRef(0)

  function loadRoster() {
    getRoster(teamId)
      .then(setRoster)
      .catch((err: unknown) => {
        setRoster([])
        showError(err instanceof ApiError ? err.message : 'Failed to load roster')
      })
  }

  function loadLineup(): Promise<void> {
    // Only the latest request may land, so a quick toggle can't be overwritten
    // by a slower earlier response.
    const requestId = ++lineupRequest.current
    return getTeamLineup(leagueKey, teamId, sources, isYahoo ? lineupMode : undefined)
      .then((resp) => {
        if (requestId === lineupRequest.current) setLineup(resp)
      })
      .catch(() => {
        // Non-fatal: fall back to a plain roster listing without slot badges.
        if (requestId === lineupRequest.current) setLineup(null)
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
    loadLineup()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sources, lineupMode])



  function handleFaFlagClick(e: MouseEvent, player: LineupPlayer, sort: 'week' | 'ros') {
    e.stopPropagation()
    navigate(
      `/leagues/${encodeURIComponent(leagueKey)}?tab=free-agents&position=${encodeURIComponent(player.position)}&sort=${sort}`,
    )
  }

  function renderFaChips(player: LineupPlayer) {
    const chips: ReactNode[] = []
    if (player.better_fa_week_points != null) {
      const value = player.better_fa_week_points
      chips.push(
        <button
          key="week"
          type="button"
          className="fa-upgrade-chip"
          title={`A free agent ${player.position} projects ${value.toFixed(1)} pts this week — click to view`}
          draggable={false}
          onMouseDown={(e) => e.stopPropagation()}
          onDragStart={(e) => e.stopPropagation()}
          onClick={(e) => handleFaFlagClick(e, player, 'week')}
        >
          FA Wk
        </button>,
      )
    }
    if (player.better_fa_ros_points != null) {
      const value = player.better_fa_ros_points
      chips.push(
        <button
          key="ros"
          type="button"
          className="fa-upgrade-chip"
          title={`A free agent ${player.position} projects ${value.toFixed(1)} rest-of-season pts — click to view`}
          draggable={false}
          onMouseDown={(e) => e.stopPropagation()}
          onDragStart={(e) => e.stopPropagation()}
          onClick={(e) => handleFaFlagClick(e, player, 'ros')}
        >
          FA ROS
        </button>,
      )
    }
    if (chips.length === 0) return null
    return <span className="fa-chip-group">{chips}</span>
  }

  function renderPlayerCells(player: LineupPlayer, benchUpgrade?: LineupPlayer | null) {
    const faChips = renderFaChips(player)
    const hasFlags = Boolean(player.injury_status || benchUpgrade || faChips)
    return (
      <>
        <span className="lineup-row-pos">{player.position}</span>
        <span className="lineup-row-name">
          <span className="lineup-row-name-text" title={player.full_name}>
            {player.full_name}
          </span>
          {/* Flags sit on their own line so they never squeeze the name. */}
          {hasFlags && (
            <span className="lineup-row-flags">
              {player.injury_status && (
                <Badge tone="warning">{player.injury_status}</Badge>
              )}
              {benchUpgrade && (
                <span
                  className="bench-alert-chip"
                  title={`Bench player ${benchUpgrade.full_name} projects ${benchUpgrade.week_points!.toFixed(
                    1,
                  )} pts this week — consider swapping`}
                >
                  ▼ bench
                </span>
              )}
              {faChips}
            </span>
          )}
        </span>
        <span className="lineup-row-team">{player.nfl_team || 'FA'}</span>
        <span className="lineup-row-opp">
          {player.on_bye ? <Badge tone="danger">BYE</Badge> : player.opponent ?? '—'}
        </span>
        <span className={`lineup-row-stat${player.on_bye ? ' bye-text' : ''}`}>
          {player.on_bye ? 'BYE' : fmtPts(player.week_points)}
        </span>
        <span className="lineup-row-stat">{fmtPts(player.ros_points)}</span>
        <span className="lineup-row-stat lineup-row-pct">{fmtPct(player.percent_owned)}</span>
        <span className="lineup-row-stat lineup-row-pct">{fmtPct(player.percent_started)}</span>
      </>
    )
  }

  function renderSlot(entry: LineupSlotEntry, index: number) {
    const player = entry.player
    const benchUpgrade = player ? findBenchUpgrade(entry.slot, player.week_points, lineup?.bench ?? []) : null
    return (
      <div
        key={index}
        className={`lineup-row${player ? '' : ' lineup-row-empty'}${
          benchUpgrade ? ' lineup-row-bench-alert' : ''
        }${player?.on_bye ? ' lineup-row-bye' : ''}`}
      >
        <span className="lineup-row-chip">{slotDisplayLabel(entry.slot)}</span>
        {player ? (
          renderPlayerCells(player, benchUpgrade)
        ) : (
          <span className="lineup-row-empty-label">Empty</span>
        )}
      </div>
    )
  }

  function renderBenchCard(player: LineupPlayer) {
    return (
      <li key={player.player_id} className="lineup-bench-item">
        <div className={`lineup-row${player.on_bye ? ' lineup-row-bye' : ''}`}>
          <span
            className="lineup-row-chip lineup-row-chip-bench"
            title={player.reserve ? 'In an IR/NA slot in Yahoo' : undefined}
          >
            {player.reserve ? 'IR' : 'BN'}
          </span>
          {renderPlayerCells(player)}
        </div>
      </li>
    )
  }

  const showingYahoo = lineup?.source === 'yahoo'
  let caption: string
  if (showingYahoo) {
    caption = 'Starters are the lineup set in Yahoo. ▼ bench marks a starter a bench player outprojects this week.'
  } else if (isYahoo && lineupMode === 'actual' && lineup) {
    caption = 'No Yahoo lineup synced for this week yet — showing the projected-optimal lineup.'
  } else {
    caption = 'Starters reflect the projected-optimal lineup.'
  }

  let sourceLabel = 'auto (optimal)'
  if (lineup?.source === 'manual') sourceLabel = 'manual'
  else if (showingYahoo) sourceLabel = 'Yahoo (as set)'

  return (
    <div className="roster-editor">
      <h3>{teamName} roster</h3>
      <p className="field-hint roster-caption">{caption}</p>

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
            <Badge tone={lineup.source === 'auto' ? 'neutral' : 'accent'}>Lineup: {sourceLabel}</Badge>
            {isYahoo && (
              <div
                className="lineup-mode-toggle"
                role="group"
                aria-label="Lineup to show"
                style={{ display: 'inline-flex', gap: 4 }}
              >
                {(
                  [
                    ['actual', 'Yahoo lineup'],
                    ['optimal', 'Optimal'],
                  ] as const
                ).map(([mode, label]) => (
                  <button
                    key={mode}
                    type="button"
                    className={`source-chip${lineupMode === mode ? ' source-chip-checked' : ''}`}
                    aria-pressed={lineupMode === mode}
                    onClick={() => setLineupMode(mode)}
                  >
                    {label}
                  </button>
                ))}
              </div>
            )}
            {lineup.week != null && <span className="field-hint">Week {lineup.week}</span>}
          </div>

          <div className="lineup-columns-header" aria-hidden="true">
            <span className="lineup-col" />
            <span className="lineup-col">Pos</span>
            <span className="lineup-col">Player</span>
            <span className="lineup-col">Team</span>
            <span className="lineup-col lineup-col-opp">Opp</span>
            <span className="lineup-col">Wk</span>
            <span className="lineup-col">ROS</span>
            <span className="lineup-col lineup-col-pct">%Own</span>
            <span className="lineup-col lineup-col-pct">%Start</span>
          </div>

          <div className="lineup-slots">{lineup.slots.map((entry, index) => renderSlot(entry, index))}</div>

          <div className="lineup-bench-section">
            <div className="roster-group-title lineup-bench-title">Bench</div>
            <ul className="lineup-bench">
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
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}

    </div>
  )
}
