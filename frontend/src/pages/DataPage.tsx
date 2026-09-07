import { useEffect, useState } from 'react'
import { getDataSchedule, getDataStatus, refreshDataSource } from '../api/endpoints'
import type { DataScheduleRow, DataSourceId, DataStatusRow } from '../api/types'
import { ApiError } from '../api/client'
import { Badge, Button, Card } from '../components/ui'
import { useToast } from '../components/toastContext'

const SOURCES: { id: DataSourceId; name: string; description: string }[] = [
  {
    id: 'crosswalk',
    name: 'Player ID crosswalk',
    description: 'Maps player identities across Yahoo, Sleeper, and FantasyCalc so data can be joined together.',
  },
  {
    id: 'sleeper_players',
    name: 'Sleeper players',
    description: 'Player metadata: names, positions, NFL teams, and injury status.',
  },
  {
    id: 'sleeper_trending',
    name: 'Sleeper trending',
    description: 'Trending add/drop activity across Sleeper leagues.',
  },
  {
    id: 'fantasycalc',
    name: 'FantasyCalc values',
    description: 'Dynasty and redraft trade values for players and picks.',
  },
  {
    id: 'espn_projections',
    name: 'ESPN projections',
    description: 'Full-season player projections from ESPN (free, no key required).',
  },
  {
    id: 'espn_week_projections',
    name: 'ESPN weekly projections',
    description: 'Current-week player projections from ESPN.',
  },
  {
    id: 'fantasypros_projections',
    name: 'FantasyPros projections',
    description: 'Full-season expert-consensus projections (requires API key).',
  },
  {
    id: 'fantasypros_week_projections',
    name: 'FantasyPros weekly projections',
    description: 'Current-week expert-consensus projections (requires API key).',
  },
]

// Human interval label per source, matching the trigger cadence configured
// in backend/app/services/scheduler.py. Kept here (rather than returned by
// the API) since it's a fixed, display-only fact about the schedule.
const REFRESH_INTERVAL_LABEL: Record<DataSourceId, string> = {
  crosswalk: 'week',
  sleeper_players: 'day',
  sleeper_trending: '6h',
  fantasycalc: 'day',
  espn_projections: '12h',
  espn_week_projections: '12h',
  fantasypros_projections: '12h',
  fantasypros_week_projections: '12h',
}

function formatCountdown(nextRunAt: string | null | undefined): string | null {
  if (!nextRunAt) return null
  const target = new Date(nextRunAt).getTime()
  if (Number.isNaN(target)) return null
  const diffMs = target - Date.now()
  if (diffMs <= 0) return 'due now'
  const totalMinutes = Math.round(diffMs / 60000)
  const days = Math.floor(totalMinutes / (60 * 24))
  const hours = Math.floor((totalMinutes % (60 * 24)) / 60)
  const minutes = totalMinutes % 60
  if (days > 0) return `${days}d ${hours}h`
  if (hours > 0) return `${hours}h ${minutes}m`
  return `${minutes}m`
}

function toneForStatus(status: string | undefined): 'neutral' | 'success' | 'warning' | 'accent' {
  if (!status) return 'neutral'
  const s = status.toLowerCase()
  if (s.includes('run') || s.includes('progress') || s.includes('pending')) return 'accent'
  if (s.includes('error') || s.includes('fail')) return 'warning'
  if (s.includes('ok') || s.includes('success') || s.includes('done') || s.includes('complete')) return 'success'
  return 'neutral'
}

function formatTime(value: string | null | undefined): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString()
}

export function DataPage() {
  const [statusByResource, setStatusByResource] = useState<Record<string, DataStatusRow>>({})
  const [scheduleBySource, setScheduleBySource] = useState<Record<string, DataScheduleRow>>({})
  const [running, setRunning] = useState<Record<string, boolean>>({})
  const [resultMessage, setResultMessage] = useState<Record<string, string>>({})
  const { showError, showSuccess } = useToast()

  function loadStatus() {
    getDataStatus()
      .then((rows) => {
        const map: Record<string, DataStatusRow> = {}
        for (const row of rows) map[row.resource] = row
        setStatusByResource(map)
      })
      .catch(() => {
        // Non-fatal: rows just show as "unknown" until a refresh is run.
      })
  }

  function loadSchedule() {
    getDataSchedule()
      .then((rows) => {
        const map: Record<string, DataScheduleRow> = {}
        for (const row of rows) map[row.source] = row
        setScheduleBySource(map)
      })
      .catch(() => {
        // Non-fatal: auto-refresh line just shows as "off" if this fails.
      })
  }

  useEffect(() => {
    loadStatus()
    loadSchedule()
  }, [])

  async function handleRefresh(id: DataSourceId) {
    setRunning((prev) => ({ ...prev, [id]: true }))
    setResultMessage((prev) => ({ ...prev, [id]: '' }))
    try {
      const result = await refreshDataSource(id)
      setResultMessage((prev) => ({ ...prev, [id]: result.message || result.status }))
      showSuccess(`${id} refresh finished`)
      loadStatus()
    } catch (err) {
      const message = err instanceof ApiError ? err.message : 'Refresh failed'
      setResultMessage((prev) => ({ ...prev, [id]: message }))
      showError(`${id} refresh failed: ${message}`)
    } finally {
      setRunning((prev) => ({ ...prev, [id]: false }))
    }
  }

  return (
    <div className="page">
      <div className="page-header">
        <h1>Data sources</h1>
      </div>
      <p className="field-hint">
        Recommended refresh order: crosswalk → Sleeper players → FantasyCalc. Refreshes can take up to a couple of
        minutes.
      </p>

      <div className="data-source-list">
        {SOURCES.map((source) => {
          const status = statusByResource[source.id]
          const schedule = scheduleBySource[source.id]
          const isRunning = running[source.id] || status?.status?.toLowerCase().includes('run')
          const message = resultMessage[source.id] || status?.message
          const intervalLabel = REFRESH_INTERVAL_LABEL[source.id]
          const countdown = schedule?.scheduled ? formatCountdown(schedule.next_run_at) : null
          const autoRefreshText = schedule?.scheduled
            ? `Auto-refresh: every ${intervalLabel}${countdown ? ` · next run in ${countdown}` : ''}`
            : 'Auto-refresh: off'
          return (
            <Card key={source.id} className="data-source-card">
              <div className="data-source-header">
                <div>
                  <h2>{source.name}</h2>
                  <p className="field-hint">{source.description}</p>
                </div>
                <Button variant="primary" busy={isRunning} onClick={() => handleRefresh(source.id)}>
                  Refresh
                </Button>
              </div>
              <div className="data-source-meta">
                <Badge tone={toneForStatus(status?.status)}>{status?.status || 'unknown'}</Badge>
                <span className="field-hint">Last finished: {formatTime(status?.finished_at)}</span>
              </div>
              <div className="field-hint">{autoRefreshText}</div>
              {message && <div className="data-source-message">{message}</div>}
            </Card>
          )
        })}
      </div>
    </div>
  )
}
