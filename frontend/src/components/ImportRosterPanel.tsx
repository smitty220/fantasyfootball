import { useState } from 'react'
import { importRosterPaste } from '../api/endpoints'
import type { ImportRosterPasteResponse, ImportRosterTeamResult } from '../api/types'
import { ApiError } from '../api/client'
import { Badge, Button, Card, InlineError } from './ui'
import { useToast } from './toastContext'

function TeamReportRow({ result }: { result: ImportRosterTeamResult }) {
  return (
    <>
      <tr>
        <td>{result.team}</td>
        <td>
          <Badge tone={result.created ? 'accent' : 'neutral'}>{result.created ? 'Created' : 'Updated'}</Badge>
        </td>
        <td className="import-report-counts">
          <span className="import-count-added">+{result.added}</span>
          <span className="import-count-removed">−{result.removed}</span>
          <span className="import-count-kept">{result.kept} kept</span>
        </td>
        <td className="import-report-lineup">{result.lineup_set ? '✓' : '—'}</td>
      </tr>
      {result.unmatched.length > 0 && (
        <tr className="import-report-unmatched-row">
          <td colSpan={4}>
            <span className="import-report-unmatched-label">Unmatched:</span>
            {result.unmatched.map((name, i) => (
              <Badge key={`${name}-${i}`} tone="warning">
                {name}
              </Badge>
            ))}
          </td>
        </tr>
      )}
    </>
  )
}

export function ImportRosterPanel({
  leagueKey,
  onImported,
  onClose,
}: {
  leagueKey: string
  onImported: () => void
  onClose: () => void
}) {
  const { showError } = useToast()
  const [text, setText] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [report, setReport] = useState<ImportRosterPasteResponse | null>(null)

  async function handleImport() {
    if (!text.trim() || loading) return
    setLoading(true)
    setError(null)
    try {
      const result = await importRosterPaste(leagueKey, text)
      setReport(result)
      setText('')
      onImported()
    } catch (err) {
      if (err instanceof ApiError && err.status === 400) {
        setError(err.message)
      } else {
        showError(err instanceof ApiError ? err.message : 'Failed to import rosters')
      }
    } finally {
      setLoading(false)
    }
  }

  return (
    <Card className="import-roster-panel">
      <div className="panel-header">
        <h3>Import rosters from Yahoo</h3>
        <Button onClick={onClose}>Close</Button>
      </div>

      <p className="field-hint">
        On Yahoo, open your league's Rosters page, select all (Cmd+A), copy, and paste below. Teams and lineups
        will be created or updated to match.
      </p>

      <textarea
        className="field-input import-roster-textarea"
        rows={14}
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="Paste the copied Yahoo Rosters page text here…"
        disabled={loading}
      />

      {error && <InlineError>{error}</InlineError>}

      <div className="import-roster-actions">
        <Button variant="primary" onClick={handleImport} disabled={!text.trim()} busy={loading}>
          Import
        </Button>
      </div>

      {report && (
        <div className="import-report">
          <table className="import-report-table">
            <thead>
              <tr>
                <th>Team</th>
                <th></th>
                <th>Roster</th>
                <th>Lineup</th>
              </tr>
            </thead>
            <tbody>
              {report.teams.map((t, i) => (
                <TeamReportRow key={`${t.team}-${i}`} result={t} />
              ))}
            </tbody>
          </table>
          {report.total_unmatched > 0 && (
            <p className="import-report-summary">
              {report.total_unmatched} player{report.total_unmatched === 1 ? '' : 's'} couldn't be matched — add
              {report.total_unmatched === 1 ? ' it' : ' them'} manually via search.
            </p>
          )}
        </div>
      )}
    </Card>
  )
}
