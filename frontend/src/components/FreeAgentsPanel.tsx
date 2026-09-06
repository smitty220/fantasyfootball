import { useEffect, useState } from 'react'
import { getFreeAgents } from '../api/endpoints'
import type { FreeAgent } from '../api/types'
import { ApiError } from '../api/client'
import { Badge, EmptyState, Spinner } from './ui'
import { useToast } from './toastContext'

const POSITIONS = ['', 'QB', 'RB', 'WR', 'TE', 'K', 'DEF']

export function FreeAgentsPanel({ leagueKey }: { leagueKey: string }) {
  const [position, setPosition] = useState('')
  const [agents, setAgents] = useState<FreeAgent[] | null>(null)
  const [loading, setLoading] = useState(false)
  const { showError } = useToast()

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    getFreeAgents(leagueKey, position || undefined, 100)
      .then((data) => {
        if (!cancelled) setAgents(data)
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setAgents([])
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
  }, [leagueKey, position])

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
      </div>

      <p className="field-hint">Projections columns coming later.</p>

      {loading && (
        <div className="loading-row">
          <Spinner /> Loading free agents…
        </div>
      )}

      {!loading && agents !== null && agents.length === 0 && <EmptyState>No free agents found.</EmptyState>}

      {!loading && agents !== null && agents.length > 0 && (
        <div className="table-scroll">
          <table className="data-table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Pos</th>
                <th>Team</th>
                <th>Injury</th>
              </tr>
            </thead>
            <tbody>
              {agents.map((agent) => (
                <tr key={agent.player_id}>
                  <td>{agent.full_name}</td>
                  <td>{agent.position}</td>
                  <td>{agent.nfl_team || '—'}</td>
                  <td>{agent.injury_status ? <Badge tone="warning">{agent.injury_status}</Badge> : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
