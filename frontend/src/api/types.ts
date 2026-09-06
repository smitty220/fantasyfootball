// Shared API types, matching the backend contract in frontend/README (see task spec).

export type LeagueSource = 'manual' | 'yahoo'
export type ScoringPreset = 'standard' | 'half_ppr' | 'full_ppr'

export interface ScoringRules {
  per_stat: Record<string, number>
  [key: string]: unknown
}

export type RosterSlots = Record<string, number>

export interface League {
  league_key: string
  name: string
  season: number
  is_keeper: boolean
  num_teams: number
  source: LeagueSource
  current_week?: number | null
}

export interface LeagueDetail extends League {
  scoring_rules?: ScoringRules
  roster_slots?: RosterSlots
}

export interface CreateManualLeaguePayload {
  name: string
  season: number
  is_keeper: boolean
  num_teams: number
  scoring_preset?: ScoringPreset
  scoring_rules?: ScoringRules
  roster_slots?: RosterSlots
}

export interface UpdateManualLeaguePayload {
  name?: string
  is_keeper?: boolean
  num_teams?: number
  scoring_rules?: ScoringRules
  roster_slots?: RosterSlots
}

export interface Team {
  id: number
  team_key: string
  name: string
  manager_name?: string | null
  is_my_team: boolean
  wins: number
  losses: number
  ties: number
  rank?: number | null
}

export interface CreateTeamPayload {
  name: string
  manager_name?: string
  is_my_team?: boolean
}

export interface UpdateTeamPayload {
  name?: string
  manager_name?: string
  is_my_team?: boolean
}

export type InjuryStatus = string | null

export interface RosterPlayer {
  player_id: string
  full_name: string
  position: string
  nfl_team: string | null
  injury_status: InjuryStatus
}

export interface PlayerSearchResult {
  id: string
  full_name: string
  position: string
  nfl_team: string | null
  injury_status: InjuryStatus
  trade_value?: number | null
}

export interface FreeAgent {
  player_id: string
  full_name: string
  position: string
  nfl_team: string | null
  injury_status: InjuryStatus
  [key: string]: unknown
}

export type DataSourceId =
  | 'crosswalk'
  | 'sleeper_players'
  | 'sleeper_trending'
  | 'fantasycalc'
  | 'espn_projections'

export interface DataRefreshResult {
  resource: string
  status: string
  message?: string
  [key: string]: unknown
}

export interface DataStatusRow {
  resource: string
  status: string
  started_at?: string | null
  finished_at?: string | null
  message?: string | null
}

export interface YahooStatus {
  connected: boolean
  expires_at?: string | null
}

export interface HealthResponse {
  status: string
  version: string
}
