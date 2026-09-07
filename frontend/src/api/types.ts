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

export interface FreeAgentEvalRow {
  player_id: string
  full_name: string
  position: string
  nfl_team: string | null
  injury_status: InjuryStatus
  has_projection: boolean
  ros_points: number
  ppg: number
  vor: number
  trade_value: number | null
  trending_add: number | null
  my_worst_starter_delta: number | null
  week_points: number | null
  week_delta: number | null
}

export interface MyPlayerEvalRow {
  player_id: string
  full_name: string
  position: string
  nfl_team: string | null
  injury_status: InjuryStatus
  ros_points: number
  ppg: number
  week_points: number | null
  is_starter: boolean
  starter_slot: string | null
}

export interface FreeAgentsEvalResponse {
  league_key: string
  season: number
  week: number | null
  my_players: MyPlayerEvalRow[]
  rows: FreeAgentEvalRow[]
}

export interface LineupPlayer {
  player_id: string
  full_name: string
  position: string
  nfl_team: string | null
  week_points: number | null
  ros_points: number
  injury_status: InjuryStatus
  percent_owned: number | null
  percent_started: number | null
  better_fa_week_points: number | null
}

export interface LineupSlotEntry {
  slot: string
  player: LineupPlayer | null
}

export type LineupSource = 'manual' | 'auto'

export interface TeamLineupResponse {
  week: number | null
  source: LineupSource
  slots: LineupSlotEntry[]
  bench: LineupPlayer[]
}

export interface LineupAssignment {
  player_id: number
  slot: string
}

export interface TradeSidePayload {
  team_id: number
  player_ids: number[]
}

export interface TradeEvaluatePayload {
  side_a: TradeSidePayload
  side_b: TradeSidePayload
  sources?: string[]
}

export interface TradePlayerResult {
  player_id: number
  full_name: string
  position: string
  ros_points: number
  ppg: number
  value: number
  dynasty_value: number
}

export interface TradeSideResult {
  team_id: number
  team_name: string
  total_ros_points: number
  total_value: number
  lineup_points_before: number
  lineup_points_after: number
  lineup_delta: number
  players: TradePlayerResult[]
}

export type TradeVerdict = 'fair' | 'favors_a' | 'favors_b'

export interface TradeEvaluateResponse {
  verdict: TradeVerdict
  margin_pct: number
  sides: {
    a: TradeSideResult
    b: TradeSideResult
  }
  notes: string[]
}

export type DataSourceId =
  | 'crosswalk'
  | 'sleeper_players'
  | 'sleeper_trending'
  | 'fantasycalc'
  | 'espn_projections'
  | 'espn_week_projections'
  | 'fantasypros_projections'
  | 'fantasypros_week_projections'

export type ProjectionSourceId = 'fantasypros' | 'espn'

export interface ProjectionSourceInfo {
  source: ProjectionSourceId
  season_updated_at: string | null
  week_updated_at: string | null
}

export interface ProjectionSourcesResponse {
  week: number | null
  sources: ProjectionSourceInfo[]
}

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
