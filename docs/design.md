# Gridiron HQ — Design

Self-hosted fantasy football helper. Pulls two Yahoo leagues (one keeper, one redraft),
joins them with projection/trade-value data from external sources, and serves
evaluation tools (free-agent evaluator, trade evaluator, later lineup optimizer etc.)
to the owner and leaguemates from a Raspberry Pi.

## Data flow

```
Yahoo Fantasy API ──┐
FantasyPros API ────┤
ESPN (espn-api) ────┼──► sync services ──► SQLite ──► evaluators ──► FastAPI ──► React UI
Sleeper API ────────┤        (APScheduler + manual trigger)
FantasyCalc API ────┤
nflverse crosswalk ─┘
```

All external data lands in SQLite first; evaluators only read from the DB. This keeps
the UI fast on the Pi, makes us resilient to any one source being down, and caps how
often we hit rate-limited/paid APIs.

## Player identity

The `players` table is the canonical spine. Every row carries the per-platform IDs
(yahoo/sleeper/espn/fantasypros/gsis) sourced from the nflverse `ff_playerids`
crosswalk, refreshed weekly. All source ingesters resolve to a canonical player row
via their platform ID first, falling back to normalized name+position+team matching
only for players missing from the crosswalk (e.g. fresh rookies). Unresolvable rows go
to a review queue rather than being silently dropped.

## Scoring

League scoring rules come from Yahoo league settings (stat_id → modifier). Projections
are stored as raw stat lines (JSON) per source/week. A scoring engine converts a stat
line to points under a specific league's rules, so the same projection scores
differently in the keeper league vs the redraft league. Yahoo stat_id → canonical stat
name mapping lives in code (it is static per sport).

## Yahoo auth

OAuth2 authorization-code flow, implemented in-house (small surface, and we must own
token storage): the owner clicks "Connect Yahoo" in the UI, opens Yahoo's authorize
URL, and pastes the resulting code back into the app (out-of-band flow — most reliable
with Yahoo's installed-app registration). Tokens live in the `oauth_tokens` table.
Yahoo rotates the refresh token on every refresh, so every refresh persists the new
pair transactionally before the old one is discarded. Parsing of Yahoo responses is
delegated to the `yahoo_fantasy_api` library via a thin session adapter, because
Yahoo's raw JSON has unstable array shapes.

## Schema (v1)

- `leagues` — league_key (uniq), game_key, name, season, is_keeper, num_teams,
  scoring_type, current_week, settings_json (raw Yahoo settings), synced_at
- `teams` — team_key (uniq), league_id→leagues, name, manager_name, is_my_team,
  wins/losses/ties, rank, points_for/against, logo_url
- `players` — canonical: full_name, position, nfl_team, injury_status, bye_week,
  yahoo_id, sleeper_id, espn_id, fantasypros_id, gsis_id (all indexed), updated_at
- `league_players` — per-league ownership state: league_id, player_id, status
  (FA/W/T/K), percent_owned, on_team_id (nullable→teams), synced_at. This *is* the
  free-agent list when status is FA/W.
- `roster_slots` — team_id, week, player_id, selected_position (QB/WR/FLEX/BN/IR…)
- `matchups` — league_id, week, home_team_id, away_team_id, home/away_points,
  is_playoffs, status
- `transactions` — league_id, yahoo_transaction_key (uniq), type, timestamp, data_json
- `draft_picks` — league_id, round, pick, team_id, player_id, cost (nullable)
- `projections` — player_id, source, season, week (NULL = rest-of-season/full-season),
  stat_json, fetched_at; unique on (player_id, source, season, week)
- `trade_values` — player_id, source, format (redraft/dynasty), value, trend_30d,
  fetched_at; unique on (player_id, source, format)
- `oauth_tokens` — provider (uniq), access_token, refresh_token, expires_at, updated_at
- `sync_log` — resource, league_id (nullable), started_at, finished_at, status, message
- `app_users` — leaguemate logins (phase-1 tail): username (uniq), password_hash, role

No migration framework yet: `Base.metadata.create_all` at startup until the schema
stabilizes; we'll adopt Alembic before other users' data matters.

## Phases

1. Yahoo sync → projections pipeline → free-agent evaluator → trade evaluator →
   auth + Pi deploy (Docker + Tailscale Funnel)
2. Lineup optimizer, matchup win probability
3. Playoff odds (Monte Carlo), strength of schedule, power rankings
