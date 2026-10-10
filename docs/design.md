# Gridiron HQ — Design

Self-hosted fantasy football helper. Pulls two Yahoo leagues (one keeper, one redraft),
joins them with projection/trade-value data from external sources, and serves
evaluation tools (lineups, free-agent and trade evaluators, matchup preview, standings
and playoff odds) to the owner and leaguemates from the homelab server (see
`deploy/server-setup.md`).

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
the UI fast, makes us resilient to any one source being down, and caps how often we
hit rate-limited/paid APIs.

Schedule (APScheduler, cron times in `SCHEDULER_TIMEZONE`, default America/Chicago;
every job also has a Run button on the Data page):

- Yahoo league sync every 15 min — settings (roster positions, scoring), teams and
  standings, rosters, this week's matchups; free agents/waivers and the draft about
  hourly. Roughly 15 Yahoo requests per league per run, ~55 when free agents are due.
- Daily: full-season Yahoo schedule (all weeks' matchups) + prune of unreferenced
  players, Yahoo league discovery, Sleeper players, FantasyCalc values, DB backup.
- FantasyPros projections hourly, ESPN every 6h, Sleeper trending every 6h, crosswalk
  weekly, NFL schedule and actual stats weekly.

## Player identity

The Yahoo pull is the source of truth for which players exist: only the Yahoo sync
creates `players` rows, from league rosters, free-agent/waiver lists and draft results.
Every other source only enriches existing rows — Sleeper (sleeper_id for trending, plus
missing espn/gsis IDs), the nflverse `ff_playerids` crosswalk (all platform IDs), and the
projection/value ingesters (FantasyPros, ESPN, FantasyCalc), which attach to rows and
skip players we don't have. Yahoo owns name, position, team and injury status.

Resolution is by platform ID first, then the shared unambiguous fallback in
`services/matching.py`: normalized name + position (suffixes like Jr./III ignored), or
position + team for team defenses. An ambiguous match is never guessed.

Departures are handled by the sync: a player on no Yahoo roster after a complete
roster pull leaves his team, free-agent entries Yahoo stops listing are removed after
a complete pull, and the daily schedule job deletes players nothing references any
more. `python -m app.services.player_merge [--prune] [--apply]` is the manual tool
for folding duplicate rows and pruning (dry run by default).

## Scoring and roster shape

Each Yahoo league's roster positions (`lg.positions()`, e.g. QB/RB×2/WR×2/TE/W/R/T/
Q/W/R/T/BN/IR) and scoring rules are stored on every sync in `settings_json` as
`roster_slots` and `scoring_rules`. `scoring.rules_from_yahoo` translates Yahoo's
`stat_modifiers` (stat_id → points) into canonical per-stat values plus
points/yards-allowed tiers; stats no source projects (TFL, three-and-outs, DEF return
yards) are dropped. Projections are stored as canonical stat lines (JSON) per
source/week and scored at read time, so one projection scores differently per league.

Lineups: Yahoo leagues default to the lineup as set in Yahoo (`roster_slots` table rows for
the current week); the "Optimal" toggle computes this week's best lineup from weekly
projections, with bye, Out and IR players last.

Trade values: FantasyCalc is fetched per league profile (teams, QBs — 2 for superflex
— and PPR), stored with `format` = `"<redraft|dynasty>:<teams>:<qbs>:<ppr>"`, and
each league reads its own profile.

## Yahoo auth

OAuth2 authorization-code flow, implemented in-house (small surface, and we must own
token storage): the owner clicks "Connect Yahoo" in the UI and opens Yahoo's authorize
URL. Yahoo redirects to the registered `YAHOO_REDIRECT_URI` (a real https URI —
Yahoo removed the out-of-band flow for apps created after ~Oct 2025); when that URI
isn't reachable locally, the owner pastes the `code` from the address bar into the
app instead. Yahoo also requires per-app approval for Fantasy Sports data
(sports.yahoo.com/developer/access/, submit the app's Client ID); ours was approved
2026-10-10. Tokens live in the `oauth_tokens` table.
Yahoo rotates the refresh token on every refresh, so every refresh persists the new
pair transactionally before the old one is discarded. Parsing of Yahoo responses is
delegated to the `yahoo_fantasy_api` library via a thin httpx session adapter, because
Yahoo's raw JSON has unstable array shapes. (The adapter merges request params into
any query string already on the URL — httpx would otherwise drop it.)

## Schema (v1)

- `leagues` — league_key (uniq), game_key, name, season, is_keeper, num_teams,
  scoring_type, current_week, settings_json (raw Yahoo settings plus derived
  `roster_slots` and `scoring_rules`), source (yahoo/manual), synced_at
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
  (no longer synced; nothing read it)
- `draft_picks` — league_id, round, pick, team_id, player_id, cost (nullable)
- `projections` — player_id, source, season, week (NULL = rest-of-season/full-season),
  stat_json, fetched_at; unique on (player_id, source, season, week)
- `trade_values` — player_id, source, format (`<redraft|dynasty>:<teams>:<qbs>:<ppr>`),
  value, trend_30d, fetched_at; unique on (player_id, source, format)
- `oauth_tokens` — provider (uniq), access_token, refresh_token, expires_at, updated_at
- `sync_log` — resource, league_id (nullable), started_at, finished_at, status, message
- `app_users` — leaguemate logins (phase-1 tail): username (uniq), password_hash, role

Alembic migrations (`backend/alembic/`) run at app startup (`app/migrations.py`). The
app also takes a consistent SQLite backup into `data/backups/` on start and daily,
keeping the newest 14.

Manual leagues (typed-in leagues from before Yahoo access) are still supported by the
backend (`routers/manual.py`) but no longer have UI.

## Phases

1. Done: Yahoo sync → projections pipeline → free-agent evaluator → trade evaluator,
   lineups (Yahoo as set / optimal), matchup preview, standings, Monte Carlo playoff
   odds on the real schedule, trade finder.
2. Open: leaguemate access (public hostname + password gate — the site is LAN-only and
   unauthenticated today), strength of schedule, power rankings.
