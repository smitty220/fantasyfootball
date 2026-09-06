import { useEffect, useMemo, useState } from 'react'
import { getFreeAgentsEval } from '../api/endpoints'
import type { FreeAgentEvalRow, MyPlayerEvalRow } from '../api/types'
import { ApiError } from '../api/client'
import { Badge, EmptyState, Spinner } from './ui'
import { useToast } from './toastContext'

const POSITION_OPTIONS: { value: string; label: string }[] = [
  { value: '', label: 'All' },
  { value: 'QB', label: 'QB' },
  { value: 'RB', label: 'RB' },
  { value: 'WR', label: 'WR' },
  { value: 'TE', label: 'TE' },
  { value: 'FLEX', label: 'W/R/T (Flex)' },
  { value: 'K', label: 'K' },
  { value: 'DEF', label: 'DEF' },
]

type SortKey =
  | 'full_name'
  | 'position'
  | 'week_points'
  | 'ros_points'
  | 'ppg'
  | 'vor'
  | 'trade_value'
  | 'week_delta'
  | 'my_worst_starter_delta'
  | 'trending_add'

const STRING_KEYS: SortKey[] = ['full_name', 'position']

function formatNum(n: number | null | undefined): string {
  if (n === null || n === undefined) return '—'
  return n.toFixed(1)
}

function renderDelta(value: number | null | undefined) {
  if (value === null || value === undefined) return ''
  return value > 0 ? (
    <span className="delta-positive">+{formatNum(value)}</span>
  ) : (
    <span className="delta-muted">{formatNum(value)}</span>
  )
}

export function FreeAgentsPanel({ leagueKey }: { leagueKey: string }) {
  const [position, setPosition] = useState('')
  const [limit, setLimit] = useState(50)
  const [rows, setRows] = useState<FreeAgentEvalRow[] | null>(null)
  const [myPlayers, setMyPlayers] = useState<MyPlayerEvalRow[]>([])
  const [week, setWeek] = useState<number | null>(null)
  const [loading, setLoading] = useState(false)
  const [sortKey, setSortKey] = useState<SortKey>('vor')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc')
  const { showError } = useToast()

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    getFreeAgentsEval(leagueKey, position || undefined, limit)
      .then((data) => {
        if (!cancelled) {
          setRows(data.rows)
          setMyPlayers(data.my_players || [])
          setWeek(data.week ?? null)
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setRows([])
          setMyPlayers([])
          setWeek(null)
          showError(err instanceof ApiError ? err.message : 'Failed to load free agents')
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [leagueKey, position, limit])

  const sorted = useMemo(() => {
    if (!rows) return null
    const withProjection = rows.filter((r) => r.has_projection)
    const withoutProjection = rows.filter((r) => !r.has_projection)
    const dir = sortDir === 'asc' ? 1 : -1
    const compare = (a: typeof withProjection[number], b: typeof withProjection[number]) => {
      if (STRING_KEYS.includes(sortKey)) {
        const av = String(a[sortKey] ?? '')
        const bv = String(b[sortKey] ?? '')
        return av.localeCompare(bv) * dir
      }
      const av = (a[sortKey] as number | null) ?? -Infinity
      const bv = (b[sortKey] as number | null) ?? -Infinity
      return (av - bv) * dir
    }
    withProjection.sort(compare)
    withoutProjection.sort(compare)
    return [...withProjection, ...withoutProjection]
  }, [rows, sortKey, sortDir])

  function handleSort(key: SortKey) {
    if (key === sortKey) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'))
    } else {
      setSortKey(key)
      setSortDir(STRING_KEYS.includes(key) ? 'asc' : 'desc')
    }
  }

  function sortHeader(key: SortKey, label: string) {
    return (
      <button
        type="button"
        className={`sort-header${sortKey === key ? ' sort-header-active' : ''}`}
        onClick={() => handleSort(key)}
      >
        {label}
        {sortKey === key && <span className="sort-arrow">{sortDir === 'asc' ? ' ▲' : ' ▼'}</span>}
      </button>
    )
  }

  const weekPtsLabel = week != null ? `Wk ${week} Pts` : 'Wk Pts'

  return (
    <div className="free-agents">
      <div className="field-row">
        <label className="field">
          <span className="field-label">Position</span>
          <select className="field-input" value={position} onChange={(e) => setPosition(e.target.value)}>
            {POSITION_OPTIONS.map((opt) => (
              <option key={opt.value || 'all'} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span className="field-label">Limit</span>
          <select className="field-input" value={limit} onChange={(e) => setLimit(Number(e.target.value))}>
            {[25, 50, 100, 200].map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        </label>
      </div>

      <p className="field-hint">
        VOR is points above a replacement-level player at the position over the rest of season.
      </p>

      {myPlayers.length > 0 && (
        <div className="my-players-strip">
          {myPlayers.map((p) => (
            <div
              key={p.player_id}
              className={`my-player-card${p.is_starter ? ' my-player-card-starter' : ' my-player-card-bench'}`}
            >
              <div className="my-player-card-top">
                <span className="roster-player-name">{p.full_name}</span>
                <Badge tone={p.is_starter ? 'accent' : 'neutral'}>
                  {p.is_starter ? p.starter_slot || p.position : 'Bench'}
                </Badge>
              </div>
              <div className="my-player-card-meta">
                <span>{p.position}</span>
                <span>Wk {formatNum(p.week_points)}</span>
                <span>ROS {formatNum(p.ros_points)}</span>
              </div>
            </div>
          ))}
        </div>
      )}

      {loading && (
        <div className="loading-row">
          <Spinner /> Loading free agents…
        </div>
      )}

      {!loading && sorted !== null && sorted.length === 0 && <EmptyState>No free agents found.</EmptyState>}

      {!loading && sorted !== null && sorted.length > 0 && (
        <div className="table-scroll">
          <table className="data-table">
            <thead>
              <tr>
                <th>{sortHeader('full_name', 'Player')}</th>
                <th>{sortHeader('position', 'Pos')}</th>
                <th>{sortHeader('week_points', weekPtsLabel)}</th>
                <th>{sortHeader('ros_points', 'ROS Pts')}</th>
                <th>{sortHeader('ppg', 'PPG')}</th>
                <th>{sortHeader('vor', 'VOR')}</th>
                <th>{sortHeader('week_delta', 'Wk vs starters')}</th>
                <th>{sortHeader('my_worst_starter_delta', 'ROS vs starters')}</th>
                <th>{sortHeader('trade_value', 'Value')}</th>
                <th>{sortHeader('trending_add', 'Trending')}</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((row) => (
                <tr key={row.player_id} className={row.has_projection ? undefined : 'row-dimmed'}>
                  <td>
                    <div className="player-cell">
                      <span className="roster-player-name">{row.full_name}</span>
                      <span className="roster-player-meta">
                        {row.nfl_team || 'FA'}
                        {row.injury_status && <Badge tone="warning">{row.injury_status}</Badge>}
                        {!row.has_projection && <Badge>no projection</Badge>}
                      </span>
                    </div>
                  </td>
                  <td>{row.position}</td>
                  <td>{formatNum(row.week_points)}</td>
                  <td>{formatNum(row.ros_points)}</td>
                  <td>{formatNum(row.ppg)}</td>
                  <td>{formatNum(row.vor)}</td>
                  <td>{renderDelta(row.week_delta)}</td>
                  <td>{renderDelta(row.my_worst_starter_delta)}</td>
                  <td>{row.trade_value != null ? formatNum(row.trade_value) : '—'}</td>
                  <td>
                    {row.trending_add != null ? <Badge tone="accent">+{row.trending_add} adds</Badge> : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
