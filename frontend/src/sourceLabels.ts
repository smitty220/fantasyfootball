import type { ProjectionSourceId } from './api/types'

export const SOURCE_LABELS: Record<ProjectionSourceId, string> = {
  fantasypros: 'FantasyPros (expert consensus)',
  espn: 'ESPN',
}

export function sourceLabel(id: string): string {
  return SOURCE_LABELS[id as ProjectionSourceId] || id
}
