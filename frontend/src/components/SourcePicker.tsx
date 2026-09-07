import { useEffect, useState } from 'react'
import { parseApiDate } from '../api/dates'
import { getProjectionSources } from '../api/endpoints'
import type { ProjectionSourceId, ProjectionSourceInfo } from '../api/types'

const STORAGE_KEY = 'projection_sources'

const SOURCE_LABELS: Record<ProjectionSourceId, string> = {
  fantasypros: 'FantasyPros (expert consensus)',
  espn: 'ESPN',
}

function sourceLabel(id: string): string {
  return SOURCE_LABELS[id as ProjectionSourceId] || id
}

function relativeTime(iso: string | null): string {
  const date = parseApiDate(iso)
  if (!date) return 'never'
  const diffMs = Date.now() - date.getTime()
  const diffMin = diffMs / 60000
  if (diffMin < 60) return `${Math.max(0, Math.round(diffMin))}m ago`
  const diffHr = diffMin / 60
  if (diffHr < 48) return `${Math.round(diffHr)}h ago`
  const diffDay = diffHr / 24
  return `${Math.round(diffDay)}d ago`
}

function newerOf(a: string | null, b: string | null): string | null {
  if (!a) return b
  if (!b) return a
  return (parseApiDate(a)?.getTime() ?? 0) >= (parseApiDate(b)?.getTime() ?? 0) ? a : b
}

function loadStoredSelection(): string[] | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return null
    const parsed: unknown = JSON.parse(raw)
    if (!Array.isArray(parsed)) return null
    const strings = parsed.filter((v): v is string => typeof v === 'string')
    return strings.length > 0 ? strings : null
  } catch {
    return null
  }
}

function saveStoredSelection(sources: string[]) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(sources))
  } catch {
    // ignore write failures (e.g. private browsing / storage disabled)
  }
}

export function SourcePicker({ onChange }: { onChange: (sources: string[]) => void }) {
  const [sources, setSources] = useState<ProjectionSourceInfo[] | null>(null)
  const [week, setWeek] = useState<number | null>(null)
  const [selected, setSelected] = useState<string[]>([])

  useEffect(() => {
    let cancelled = false
    getProjectionSources()
      .then((data) => {
        if (cancelled) return
        setSources(data.sources)
        setWeek(data.week ?? null)
        const validIds = data.sources.map((s) => s.source)
        const stored = loadStoredSelection()
        const validStored = stored ? stored.filter((s) => validIds.includes(s as ProjectionSourceId)) : null
        const initial = validStored && validStored.length > 0 ? validStored : validIds
        setSelected(initial)
        onChange(initial)
      })
      .catch(() => {
        if (!cancelled) setSources([])
      })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  function toggle(id: string) {
    setSelected((prev) => {
      const checked = prev.includes(id)
      if (checked && prev.length <= 1) return prev
      const next = checked ? prev.filter((s) => s !== id) : [...prev, id]
      saveStoredSelection(next)
      onChange(next)
      return next
    })
  }

  if (!sources || sources.length === 0) return null

  return (
    <div className="source-picker">
      <span className="source-picker-label">Projections:</span>
      {sources.map((s) => {
        const checked = selected.includes(s.source)
        const isLast = checked && selected.length <= 1
        const seasonRel = relativeTime(s.season_updated_at)
        const weekRel = relativeTime(s.week_updated_at)
        const weekName = week != null ? `Week ${week}` : 'Week'
        const freshness = relativeTime(newerOf(s.season_updated_at, s.week_updated_at))
        const title = isLast
          ? 'At least one source required'
          : `Season: ${seasonRel} · ${weekName}: ${weekRel}`
        return (
          <label
            key={s.source}
            className={`source-chip${checked ? ' source-chip-checked' : ''}`}
            title={title}
          >
            <input type="checkbox" checked={checked} onChange={() => toggle(s.source)} />
            <span className="source-chip-name">{sourceLabel(s.source)}</span>
            <span className="source-chip-freshness">{freshness}</span>
          </label>
        )
      })}
    </div>
  )
}
