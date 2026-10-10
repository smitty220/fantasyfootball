import { useEffect, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { getLeagues, getTeams, setLeagueKeeper } from '../api/endpoints'
import type { League, Team } from '../api/types'
import { ApiError } from '../api/client'
import { Badge, Card, EmptyState, Spinner } from '../components/ui'
import { useToast } from '../components/toastContext'
import { useSession } from '../components/sessionContext'
import { RosterEditor } from '../components/RosterEditor'
import { FreeAgentsPanel } from '../components/FreeAgentsPanel'

type Tab = 'teams' | 'free-agents'
type FaSort = 'week' | 'ros'

function parseFaSort(value: string | null): FaSort | undefined {
  return value === 'week' || value === 'ros' ? value : undefined
}

export function LeagueDetailPage() {
  const { leagueKey = '' } = useParams<{ leagueKey: string }>()
  const [searchParams] = useSearchParams()
  const { showError, showSuccess } = useToast()
  const { canEdit } = useSession()

  const [league, setLeague] = useState<League | null | undefined>(undefined)
  const [teams, setTeams] = useState<Team[] | null>(null)
  const [selectedTeamId, setSelectedTeamId] = useState<number | null>(null)
  const [tab, setTab] = useState<Tab>(() => (searchParams.get('tab') === 'free-agents' ? 'free-agents' : 'teams'))
  const [initialFaPosition, setInitialFaPosition] = useState<string | undefined>(
    () => searchParams.get('position') || undefined,
  )
  const [initialFaSort, setInitialFaSort] = useState<FaSort | undefined>(() => parseFaSort(searchParams.get('sort')))

  // The FA-upgrade chips navigate here with ?tab=&position=&sort= while this
  // page is already mounted, so honor param changes after mount too.
  useEffect(() => {
    if (searchParams.get('tab') === 'free-agents') {
      setTab('free-agents')
      setInitialFaPosition(searchParams.get('position') || undefined)
      setInitialFaSort(parseFaSort(searchParams.get('sort')))
    }
  }, [searchParams])
  function loadTeams() {
    getTeams(leagueKey)
      .then((loaded) => {
        setTeams(loaded)
        // Default the roster view to the owner's starred team.
        setSelectedTeamId((current) => {
          if (current !== null && loaded.some((t) => t.id === current)) return current
          return loaded.find((t) => t.is_my_team)?.id ?? null
        })
      })
      .catch((err: unknown) => {
        setTeams([])
        showError(err instanceof ApiError ? err.message : 'Failed to load teams')
      })
  }

  useEffect(() => {
    getLeagues()
      .then((leagues) => {
        setLeague(leagues.find((l) => l.league_key === leagueKey) ?? null)
      })
      .catch((err: unknown) => {
        setLeague(null)
        showError(err instanceof ApiError ? err.message : 'Failed to load league')
      })
    loadTeams()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [leagueKey])

  async function handleKeeperToggle(isKeeper: boolean) {
    if (!league) return
    try {
      setLeague(await setLeagueKeeper(league.league_key, isKeeper))
      showSuccess(isKeeper ? 'Marked as a keeper league' : 'No longer a keeper league')
    } catch (err) {
      showError(err instanceof ApiError ? err.message : 'Failed to update league')
    }
  }

  if (league === undefined) {
    return (
      <div className="page loading-row">
        <Spinner /> Loading league…
      </div>
    )
  }

  if (league === null) {
    return (
      <div className="page">
        <EmptyState>League not found.</EmptyState>
        <Link to="/">Back to leagues</Link>
      </div>
    )
  }

  const selectedTeam = teams?.find((t) => t.id === selectedTeamId) ?? null

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>{league.name}</h1>
          <div className="badge-row">
            <Badge>{league.season}</Badge>
            <Badge tone={league.source === 'manual' ? 'accent' : 'success'}>{league.source}</Badge>
            {league.is_keeper && <Badge tone="warning">Keeper</Badge>}
          </div>
          {canEdit && (
            <label className="checkbox-field">
              <input
                type="checkbox"
                checked={league.is_keeper}
                onChange={(e) => handleKeeperToggle(e.target.checked)}
              />
              <span>Keeper league</span>
            </label>
          )}
        </div>
      </div>

      <div className="tabs">
        <button className={`tab${tab === 'teams' ? ' tab-active' : ''}`} onClick={() => setTab('teams')} type="button">
          Teams
        </button>
        <button
          className={`tab${tab === 'free-agents' ? ' tab-active' : ''}`}
          onClick={() => setTab('free-agents')}
          type="button"
        >
          Free agents
        </button>
        <Link to={`/leagues/${encodeURIComponent(leagueKey)}/trade`} className="tab">
          Trade analyzer
        </Link>
        <Link to={`/leagues/${encodeURIComponent(leagueKey)}/matchup`} className="tab">
          Matchup preview
        </Link>
        <Link to={`/leagues/${encodeURIComponent(leagueKey)}/standings`} className="tab">
          Standings &amp; Odds
        </Link>
        <Link to={`/leagues/${encodeURIComponent(leagueKey)}/accuracy`} className="tab">
          Accuracy
        </Link>
      </div>

      {tab === 'teams' && (
        <div className="detail-grid">
          <Card>
            <div className="panel-header">
              <h2>Teams</h2>
            </div>

            {teams === null && (
              <div className="loading-row">
                <Spinner /> Loading teams…
              </div>
            )}

            {teams !== null && teams.length === 0 && <EmptyState>No teams yet.</EmptyState>}

            {teams !== null && teams.length > 0 && (
              <ul className="team-list">
                {teams.map((team) => (
                  <li
                    key={team.id}
                    className={`team-list-item${selectedTeamId === team.id ? ' team-list-item-active' : ''}`}
                  >
                    <span
                      className="star-toggle"
                      title={team.is_my_team ? 'My team' : undefined}
                      aria-label={team.is_my_team ? 'My team' : undefined}
                    >
                      {team.is_my_team ? '★' : '☆'}
                    </span>

                    <div className="team-list-main" onClick={() => setSelectedTeamId(team.id)}>
                      <span className="team-name">{team.name}</span>
                      {team.manager_name && <span className="team-manager">{team.manager_name}</span>}
                      <span className="team-record">
                        {team.wins}-{team.losses}
                        {team.ties ? `-${team.ties}` : ''}
                        {team.rank != null && ` · #${team.rank}`}
                      </span>
                    </div>

                  </li>
                ))}
              </ul>
            )}
          </Card>

          <Card>
            {selectedTeam ? (
              <RosterEditor
                key={selectedTeam.id}
                leagueKey={leagueKey}
                teamId={selectedTeam.id}
                teamName={selectedTeam.name}
                isYahoo={league?.source === 'yahoo'}
              />
            ) : (
              <EmptyState>Select a team to view its roster.</EmptyState>
            )}
          </Card>
        </div>
      )}

      {tab === 'free-agents' && (
        <Card>
          <FreeAgentsPanel leagueKey={leagueKey} initialPosition={initialFaPosition} initialSort={initialFaSort} />
        </Card>
      )}
    </div>
  )
}
