import { apiDelete, apiGet, apiPost, apiPut, buildQuery } from './client'
import type {
  CreateManualLeaguePayload,
  CreateTeamPayload,
  DataRefreshResult,
  DataScheduleRow,
  DataSourceId,
  DataStatusRow,
  FreeAgent,
  FreeAgentsEvalResponse,
  HealthResponse,
  ImportRosterPasteResponse,
  League,
  LeagueDetail,
  LineupAssignment,
  PlayerSearchResult,
  ProjectionSourcesResponse,
  RosterPlayer,
  SessionLoginResponse,
  SessionMe,
  Team,
  TeamLineupResponse,
  TradeEvaluatePayload,
  TradeEvaluateResponse,
  UpdateManualLeaguePayload,
  UpdateTeamPayload,
  YahooStatus,
} from './types'

export function getHealth(): Promise<HealthResponse> {
  return apiGet('/api/health')
}

export function getSession(): Promise<SessionMe> {
  return apiGet('/api/session/me')
}

export function sessionLogin(password: string): Promise<SessionLoginResponse> {
  return apiPost('/api/session/login', { password })
}

export function sessionLogout(): Promise<void> {
  return apiPost('/api/session/logout')
}

export function getYahooStatus(): Promise<YahooStatus> {
  return apiGet('/api/auth/yahoo/status')
}

export function getLeagues(): Promise<League[]> {
  return apiGet('/api/leagues')
}

export function createManualLeague(payload: CreateManualLeaguePayload): Promise<LeagueDetail> {
  return apiPost('/api/manual/leagues', payload)
}

export function updateManualLeague(leagueKey: string, payload: UpdateManualLeaguePayload): Promise<LeagueDetail> {
  return apiPut(`/api/manual/leagues/${encodeURIComponent(leagueKey)}`, payload)
}

export function deleteManualLeague(leagueKey: string): Promise<void> {
  return apiDelete(`/api/manual/leagues/${encodeURIComponent(leagueKey)}`)
}

export function getTeams(leagueKey: string): Promise<Team[]> {
  return apiGet(`/api/leagues/${encodeURIComponent(leagueKey)}/teams`)
}

export function importRosterPaste(leagueKey: string, text: string): Promise<ImportRosterPasteResponse> {
  return apiPost(`/api/manual/leagues/${encodeURIComponent(leagueKey)}/import-roster-paste`, { text })
}

export function createTeam(leagueKey: string, payload: CreateTeamPayload): Promise<Team> {
  return apiPost(`/api/manual/leagues/${encodeURIComponent(leagueKey)}/teams`, payload)
}

export function updateTeam(teamId: number, payload: UpdateTeamPayload): Promise<Team> {
  return apiPut(`/api/manual/teams/${teamId}`, payload)
}

export function deleteTeam(teamId: number): Promise<void> {
  return apiDelete(`/api/manual/teams/${teamId}`)
}

export function getRoster(teamId: number): Promise<RosterPlayer[]> {
  return apiGet(`/api/manual/teams/${teamId}/roster`)
}

export function addRosterPlayer(teamId: number, playerId: string): Promise<RosterPlayer> {
  return apiPost(`/api/manual/teams/${teamId}/roster`, { player_id: playerId })
}

export function removeRosterPlayer(teamId: number, playerId: string): Promise<void> {
  return apiDelete(`/api/manual/teams/${teamId}/roster/${encodeURIComponent(playerId)}`)
}

export function searchPlayers(q: string, position?: string, limit = 20): Promise<PlayerSearchResult[]> {
  return apiGet(`/api/players/search${buildQuery({ q, position, limit })}`)
}

export function getFreeAgents(leagueKey: string, position?: string, limit = 50): Promise<FreeAgent[]> {
  return apiGet(`/api/leagues/${encodeURIComponent(leagueKey)}/free-agents${buildQuery({ position, limit })}`)
}

export function getFreeAgentsEval(
  leagueKey: string,
  position?: string,
  limit = 50,
  sources?: string[],
): Promise<FreeAgentsEvalResponse> {
  return apiGet(
    `/api/leagues/${encodeURIComponent(leagueKey)}/evaluate/free-agents${buildQuery({
      position,
      limit,
      sources: sources && sources.length > 0 ? sources.join(',') : undefined,
    })}`,
  )
}

export function getTeamLineup(
  leagueKey: string,
  teamId: number,
  sources?: string[],
): Promise<TeamLineupResponse> {
  return apiGet(
    `/api/leagues/${encodeURIComponent(leagueKey)}/evaluate/teams/${teamId}/lineup${buildQuery({
      sources: sources && sources.length > 0 ? sources.join(',') : undefined,
    })}`,
  )
}

export function putLineup(teamId: number, assignments: LineupAssignment[]): Promise<TeamLineupResponse> {
  return apiPut(`/api/manual/teams/${teamId}/lineup`, { assignments })
}

export function clearLineup(teamId: number): Promise<void> {
  return apiDelete(`/api/manual/teams/${teamId}/lineup`)
}

export function evaluateTrade(leagueKey: string, payload: TradeEvaluatePayload): Promise<TradeEvaluateResponse> {
  return apiPost(`/api/leagues/${encodeURIComponent(leagueKey)}/evaluate/trade`, payload)
}

export function refreshDataSource(source: DataSourceId): Promise<DataRefreshResult> {
  return apiPost(`/api/data/refresh/${source}`)
}

export function getDataStatus(): Promise<DataStatusRow[]> {
  return apiGet('/api/data/status')
}

export function getDataSchedule(): Promise<DataScheduleRow[]> {
  return apiGet('/api/data/schedule')
}

export function getProjectionSources(): Promise<ProjectionSourcesResponse> {
  return apiGet('/api/projections/sources')
}
