import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { createTeam, deleteTeam, getLeagues, getTeams, updateTeam } from '../api/endpoints'
import type { League, Team } from '../api/types'
import { ApiError } from '../api/client'
import { Badge, Button, Card, EmptyState, Spinner, TextField } from '../components/ui'
import { useToast } from '../components/toastContext'
import { useSession } from '../components/sessionContext'
import { RosterEditor } from '../components/RosterEditor'
import { FreeAgentsPanel } from '../components/FreeAgentsPanel'
import { ImportRosterPanel } from '../components/ImportRosterPanel'

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
  const [editingTeamId, setEditingTeamId] = useState<number | null>(null)
  const [editName, setEditName] = useState('')
  const [showAddForm, setShowAddForm] = useState(false)
  const [newTeamName, setNewTeamName] = useState('')
  const [newManagerName, setNewManagerName] = useState('')
  const [busyTeamId, setBusyTeamId] = useState<number | null>(null)
  const [addingTeam, setAddingTeam] = useState(false)
  const [showImportPanel, setShowImportPanel] = useState(false)
  const [rosterRefreshKey, setRosterRefreshKey] = useState(0)

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

  // Team/roster editing needs both a manual league and edit rights: a viewer
  // sees exactly the read-only view a Yahoo league already gets.
  const isManual = league?.source === 'manual' && canEdit

  async function handleAddTeam(e: FormEvent) {
    e.preventDefault()
    if (!newTeamName.trim()) return
    setAddingTeam(true)
    try {
      await createTeam(leagueKey, { name: newTeamName.trim(), manager_name: newManagerName.trim() || undefined })
      showSuccess('Team added')
      setNewTeamName('')
      setNewManagerName('')
      setShowAddForm(false)
      loadTeams()
    } catch (err) {
      showError(err instanceof ApiError ? err.message : 'Failed to add team')
    } finally {
      setAddingTeam(false)
    }
  }

  async function handleRename(team: Team) {
    if (!editName.trim() || editName.trim() === team.name) {
      setEditingTeamId(null)
      return
    }
    setBusyTeamId(team.id)
    try {
      await updateTeam(team.id, { name: editName.trim() })
      showSuccess('Team renamed')
      setEditingTeamId(null)
      loadTeams()
    } catch (err) {
      showError(err instanceof ApiError ? err.message : 'Failed to rename team')
    } finally {
      setBusyTeamId(null)
    }
  }

  async function handleDelete(team: Team) {
    if (!window.confirm(`Delete team "${team.name}"? This cannot be undone.`)) return
    setBusyTeamId(team.id)
    try {
      await deleteTeam(team.id)
      showSuccess('Team deleted')
      if (selectedTeamId === team.id) setSelectedTeamId(null)
      loadTeams()
    } catch (err) {
      showError(err instanceof ApiError ? err.message : 'Failed to delete team')
    } finally {
      setBusyTeamId(null)
    }
  }

  function handleRosterImported() {
    loadTeams()
    setRosterRefreshKey((k) => k + 1)
  }

  async function handleToggleMyTeam(team: Team) {
    setBusyTeamId(team.id)
    try {
      await updateTeam(team.id, { is_my_team: !team.is_my_team })
      loadTeams()
    } catch (err) {
      showError(err instanceof ApiError ? err.message : 'Failed to update team')
    } finally {
      setBusyTeamId(null)
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
      </div>

      {tab === 'teams' && (
        <div className="detail-grid">
          <Card>
            <div className="panel-header">
              <h2>Teams</h2>
              {isManual && (
                <div className="panel-header-actions">
                  <Button onClick={() => setShowImportPanel((v) => !v)}>
                    {showImportPanel ? 'Hide import' : 'Import rosters from Yahoo'}
                  </Button>
                  <Button variant="primary" onClick={() => setShowAddForm((v) => !v)}>
                    {showAddForm ? 'Cancel' : 'Add team'}
                  </Button>
                </div>
              )}
            </div>

            {isManual && showImportPanel && (
              <ImportRosterPanel
                leagueKey={leagueKey}
                onImported={handleRosterImported}
                onClose={() => setShowImportPanel(false)}
              />
            )}

            {showAddForm && (
              <form className="inline-form" onSubmit={handleAddTeam}>
                <TextField label="Team name" value={newTeamName} onChange={(e) => setNewTeamName(e.target.value)} />
                <TextField
                  label="Manager (optional)"
                  value={newManagerName}
                  onChange={(e) => setNewManagerName(e.target.value)}
                />
                <Button type="submit" variant="primary" busy={addingTeam}>
                  Save
                </Button>
              </form>
            )}

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
                    <button
                      type="button"
                      className="star-toggle"
                      onClick={() => handleToggleMyTeam(team)}
                      disabled={!isManual || busyTeamId === team.id}
                      title={team.is_my_team ? 'My team' : 'Mark as my team'}
                      aria-label={team.is_my_team ? 'My team' : 'Mark as my team'}
                    >
                      {team.is_my_team ? '★' : '☆'}
                    </button>

                    <div className="team-list-main" onClick={() => setSelectedTeamId(team.id)}>
                      {editingTeamId === team.id ? (
                        <input
                          className="field-input"
                          autoFocus
                          value={editName}
                          onClick={(e) => e.stopPropagation()}
                          onChange={(e) => setEditName(e.target.value)}
                          onBlur={() => handleRename(team)}
                          onKeyDown={(e) => {
                            if (e.key === 'Enter') handleRename(team)
                            if (e.key === 'Escape') setEditingTeamId(null)
                          }}
                        />
                      ) : (
                        <>
                          <span className="team-name">{team.name}</span>
                          {team.manager_name && <span className="team-manager">{team.manager_name}</span>}
                          <span className="team-record">
                            {team.wins}-{team.losses}
                            {team.ties ? `-${team.ties}` : ''}
                            {team.rank != null && ` · #${team.rank}`}
                          </span>
                        </>
                      )}
                    </div>

                    {isManual && editingTeamId !== team.id && (
                      <div className="team-actions">
                        <Button
                          onClick={(e) => {
                            e.stopPropagation()
                            setEditingTeamId(team.id)
                            setEditName(team.name)
                          }}
                        >
                          Rename
                        </Button>
                        <Button
                          variant="danger"
                          busy={busyTeamId === team.id}
                          onClick={(e) => {
                            e.stopPropagation()
                            handleDelete(team)
                          }}
                        >
                          Delete
                        </Button>
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </Card>

          <Card>
            {selectedTeam ? (
              <RosterEditor
                key={`${selectedTeam.id}:${rosterRefreshKey}`}
                leagueKey={leagueKey}
                teamId={selectedTeam.id}
                teamName={selectedTeam.name}
                editable={isManual}
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
