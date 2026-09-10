import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { getLeagueAccuracy } from '../api/endpoints'
import type { AccuracySource, AccuracyStat, LeagueAccuracyResponse } from '../api/types'
import { ApiError } from '../api/client'
import { Badge, Button, Card, EmptyState, Spinner } from '../components/ui'
import { sourceLabel } from '../sourceLabels'
import { useToast } from '../components/toastContext'

const POSITION_ORDER = ['QB', 'RB', 'WR', 'TE', 'K', 'DEF']

function fmt1(n: number): string {
  return n.toFixed(1)
}

function biasLine(stat: AccuracyStat): { text: string; className: string } | null {
  if (stat.n === 0) return null
  if (Math.abs(stat.bias) < 0.3) {
    return { text: 'unbiased', className: 'accuracy-bias-muted' }
  }
  if (stat.bias > 0) {
    return { text: `overprojects by +${fmt1(stat.bias)} pts avg`, className: 'accuracy-bias-warning' }
  }
  return { text: `underprojects by −${fmt1(Math.abs(stat.bias))} pts avg`, className: 'accuracy-bias-muted' }
}

/** The single source with the strictly-lowest overall MAE, or null if tied / any source ungraded. */
function findBestSource(sources: AccuracySource[]): string | null {
  if (sources.length === 0 || sources.some((s) => s.overall.n === 0)) return null
  let best = sources[0]
  let tied = false
  for (const s of sources.slice(1)) {
    if (s.overall.mae < best.overall.mae) {
      best = s
      tied = false
    } else if (s.overall.mae === best.overall.mae) {
      tied = true
    }
  }
  return tied ? null : best.source
}

function ScoreboardCard({ source, isBest }: { source: AccuracySource; isBest: boolean }) {
  const bias = biasLine(source.overall)
  return (
    <Card className={`accuracy-card${isBest ? ' accuracy-card-best' : ''}`}>
      <div className="accuracy-card-header">
        <h2>{sourceLabel(source.source)}</h2>
        {isBest && <Badge tone="success">Most accurate</Badge>}
      </div>
      <div className="accuracy-card-mae">
        <strong>{source.overall.n > 0 ? fmt1(source.overall.mae) : '—'}</strong>
        <span className="field-hint">avg miss (pts/player/wk)</span>
      </div>
      {bias ? (
        <p className={`accuracy-bias ${bias.className}`}>{bias.text}</p>
      ) : (
        <p className="accuracy-bias accuracy-bias-muted">{'—'}</p>
      )}
      <p className="accuracy-card-n">{source.overall.n.toLocaleString()} player-weeks</p>
    </Card>
  )
}

function BreakdownTable({
  title,
  rowLabel,
  rows,
  sources,
  getStat,
}: {
  title: string
  rowLabel: string
  rows: (number | string)[]
  sources: AccuracySource[]
  getStat: (source: AccuracySource, row: number | string) => AccuracyStat | undefined
}) {
  return (
    <Card>
      <h2>{title}</h2>
      <div className="table-scroll">
        <table className="data-table">
          <thead>
            <tr>
              <th>{rowLabel}</th>
              {sources.map((s) => (
                <th key={s.source}>{sourceLabel(s.source)} MAE</th>
              ))}
              <th>N</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const stats = sources.map((s) => getStat(s, row))
              const gradedMaes = stats.filter((st): st is AccuracyStat => !!st && st.n > 0).map((st) => st.mae)
              const minMae = gradedMaes.length > 1 ? Math.min(...gradedMaes) : null
              return (
                <tr key={row}>
                  <td>{typeof row === 'number' ? `Week ${row}` : row}</td>
                  {stats.map((stat, i) => {
                    const isBestCell = !!stat && stat.n > 0 && minMae !== null && stat.mae === minMae
                    return (
                      <td key={sources[i].source} className={isBestCell ? 'accuracy-cell-best' : undefined}>
                        {stat && stat.n > 0 ? fmt1(stat.mae) : '—'}
                      </td>
                    )
                  })}
                  <td className="accuracy-n-cell">
                    {stats.map((stat, i) => `${sourceLabel(sources[i].source).split(' ')[0]} ${stat?.n ?? 0}`).join(' · ')}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </Card>
  )
}

export function AccuracyPage() {
  const { leagueKey = '' } = useParams<{ leagueKey: string }>()
  const { showError } = useToast()

  const [data, setData] = useState<LeagueAccuracyResponse | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    getLeagueAccuracy(leagueKey)
      .then((res) => {
        if (!cancelled) setData(res)
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setData(null)
          showError(err instanceof ApiError ? err.message : 'Failed to load accuracy data')
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [leagueKey])

  const isEmpty =
    !data || data.weeks.length === 0 || data.sources.every((s) => s.overall.n === 0)

  const bestSource = data ? findBestSource(data.sources) : null

  const positions = data
    ? POSITION_ORDER.filter((p) => data.sources.some((s) => s.by_position.some((bp) => bp.position === p)))
    : []

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>Accuracy</h1>
          <p className="field-hint">
            How each projection source's weekly predictions compared to actual results, scored under this league's
            rules.
          </p>
        </div>
        <Link to={`/leagues/${encodeURIComponent(leagueKey)}`}>
          <Button>Back to league</Button>
        </Link>
      </div>

      {loading && (
        <div className="loading-row">
          <Spinner /> Loading accuracy…
        </div>
      )}

      {!loading && isEmpty && (
        <EmptyState>
          No completed weeks graded yet — accuracy appears after the first week's actual stats arrive.
        </EmptyState>
      )}

      {!loading && data && !isEmpty && (
        <>
          <div className="accuracy-scoreboard">
            {data.sources.map((s) => (
              <ScoreboardCard key={s.source} source={s} isBest={bestSource === s.source} />
            ))}
          </div>

          <BreakdownTable
            title="By week"
            rowLabel="Week"
            rows={data.weeks}
            sources={data.sources}
            getStat={(source, row) => source.by_week.find((w) => w.week === row)}
          />

          <BreakdownTable
            title="By position"
            rowLabel="Position"
            rows={positions}
            sources={data.sources}
            getStat={(source, row) => source.by_position.find((p) => p.position === row)}
          />
        </>
      )}
    </div>
  )
}
