import { apiGet, apiPost, apiPut, buildQuery } from './client'
import type {
  DashboardResponse,
  DataRefreshResult,
  DataScheduleRow,
  DataSourceId,
  DataStatusRow,
  FreeAgent,
  FreeAgentsEvalResponse,
  HealthResponse,
  League,
  LeagueAccuracyResponse,
  LineupMode,
  Matchup,
  PlayoffOddsResponse,
  ProjectionSourcesResponse,
  RosterPlayer,
  SessionLoginResponse,
  SessionMe,
  StandingsRow,
  Team,
  TeamLineupResponse,
  TradeEvaluatePayload,
  TradeEvaluateResponse,
  TradeFinderResponse,
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

export function getDashboard(sources?: string[]): Promise<DashboardResponse> {
  return apiGet(
    `/api/dashboard${buildQuery({ sources: sources && sources.length > 0 ? sources.join(',') : undefined })}`,
  )
}

export function setLeagueKeeper(leagueKey: string, isKeeper: boolean): Promise<League> {
  return apiPut(`/api/leagues/${encodeURIComponent(leagueKey)}`, { is_keeper: isKeeper })
}

export function getMatchups(leagueKey: string, week: number): Promise<Matchup[]> {
  return apiGet(`/api/leagues/${encodeURIComponent(leagueKey)}/matchups${buildQuery({ week })}`)
}

export function getTeams(leagueKey: string): Promise<Team[]> {
  return apiGet(`/api/leagues/${encodeURIComponent(leagueKey)}/teams`)
}

export function getRoster(teamId: number): Promise<RosterPlayer[]> {
  return apiGet(`/api/manual/teams/${teamId}/roster`)
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
  lineup?: LineupMode,
): Promise<TeamLineupResponse> {
  return apiGet(
    `/api/leagues/${encodeURIComponent(leagueKey)}/evaluate/teams/${teamId}/lineup${buildQuery({
      sources: sources && sources.length > 0 ? sources.join(',') : undefined,
      lineup: lineup === 'optimal' ? 'optimal' : undefined,
    })}`,
  )
}

export function evaluateTrade(leagueKey: string, payload: TradeEvaluatePayload): Promise<TradeEvaluateResponse> {
  return apiPost(`/api/leagues/${encodeURIComponent(leagueKey)}/evaluate/trade`, payload)
}

export function getTradeFinder(
  leagueKey: string,
  sources?: string[],
  limit?: number,
): Promise<TradeFinderResponse> {
  return apiGet(
    `/api/leagues/${encodeURIComponent(leagueKey)}/evaluate/trade-finder${buildQuery({
      sources: sources && sources.length > 0 ? sources.join(',') : undefined,
      limit,
    })}`,
  )
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

export function getLeagueAccuracy(leagueKey: string): Promise<LeagueAccuracyResponse> {
  return apiGet(`/api/leagues/${encodeURIComponent(leagueKey)}/accuracy`)
}

export function getStandings(leagueKey: string): Promise<StandingsRow[]> {
  return apiGet(`/api/leagues/${encodeURIComponent(leagueKey)}/standings`)
}

export function getPlayoffOdds(leagueKey: string, sources?: string[]): Promise<PlayoffOddsResponse> {
  return apiGet(
    `/api/leagues/${encodeURIComponent(leagueKey)}/playoff-odds${buildQuery({
      sources: sources && sources.length > 0 ? sources.join(',') : undefined,
    })}`,
  )
}

