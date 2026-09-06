import { useEffect, useMemo, useState } from 'react'
import { getFreeAgentsEval } from '../api/endpoints'
import type { FreeAgentEvalRow } from '../api/types'
import { ApiError } from '../api/client'
import { Badge, EmptyState, Spinner } from './ui'
import { useToast } from './toastContext'

const POSITIONS = ['', 'QB', 'RB', 'WR', 'TE', 'K', 'DEF']

type SortKey = 'ros_points' | 'ppg' | 'vor' | 'trade_value'

const SORT_COLUMNS: { key: SortKey; label: string }[] = [
  { key: 'ros_points', label: 'ROS Pts' },
  { key: 'ppg', label: 'PPG' },
  { key: 'vor', label: 'VOR' },
  { key: 'trade_value', label: 'Value' },
]

function formatNum(n: number | null | undefined): string {
  if (n === null || n === undefined) return '—'
  return n.toFixed(1)
}

export function FreeAgentsPanel({ leagueKey }: { leagueKey: string }) {
  const [position, setPosition] = useState('')
  const [limit, setLimit] = useState(50)
  const [rows, setRows] = useState<FreeAgentEvalRow[] | null>(null)
  const [loading, setLoading] = useState(false)
  const [sortKey, setSortKey] = useState<SortKey>('vor')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc')
  const { showError } = useToast()

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    getFreeAgentsEval(leagueKey, position || undefined, limit)
      .then((data) => {
        if (!cancelled) setRows(data.rows)
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setRows([])
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
    withProjection.sort((a, b) => {
      const av = a[sortKey] ?? -Infinity
      const bv = b[sortKey] ?? -Infinity
      return (av - bv) * dir
    })
    return [...withProjection, ...withoutProjection]
  }, [rows, sortKey, sortDir])

  function handleSort(key: SortKey) {
    if (key === sortKey) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'))
    } else {
      setSortKey(key)
      setSortDir('desc')
    }
  }

  return (
    <div className="free-agents">
      <div className="field-row">
        <label className="field">
          <span className="field-label">Position</span>
          <select className="field-input" value={position} onChange={(e) => setPosition(e.target.value)}>
            {POSITIONS.map((p) => (
              <option key={p || 'all'} value={p}>
                {p || 'All'}
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
                <th>Player</th>
                <th>Pos</th>
                {SORT_COLUMNS.map((col) => (
                  <th key={col.key}>
                    <button
                      type="button"
                      className={`sort-header${sortKey === col.key ? ' sort-header-active' : ''}`}
                      onClick={() => handleSort(col.key)}
                    >
                      {col.label}
                      {sortKey === col.key && <span className="sort-arrow">{sortDir === 'asc' ? ' ▲' : ' ▼'}</span>}
                    </button>
                  </th>
                ))}
                <th>Trending</th>
                <th>vs my starters</th>
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
                  <td>{formatNum(row.ros_points)}</td>
                  <td>{formatNum(row.ppg)}</td>
                  <td>{formatNum(row.vor)}</td>
                  <td>{row.trade_value != null ? formatNum(row.trade_value) : '—'}</td>
                  <td>
                    {row.trending_add != null ? (
                      <Badge tone="accent">+{row.trending_add} adds</Badge>
                    ) : (
                      '—'
                    )}
                  </td>
                  <td>
                    {row.my_worst_starter_delta == null ? (
                      ''
                    ) : row.my_worst_starter_delta > 0 ? (
                      <span className="delta-positive">+{formatNum(row.my_worst_starter_delta)}</span>
                    ) : (
                      <span className="delta-muted">{formatNum(row.my_worst_starter_delta)}</span>
                    )}
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
