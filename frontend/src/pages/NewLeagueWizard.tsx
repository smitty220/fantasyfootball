import { useMemo, useState } from 'react'
import type { FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { createManualLeague } from '../api/endpoints'
import type { ScoringPreset, ScoringRules } from '../api/types'
import { ApiError } from '../api/client'
import { Button, Card, SelectField, TextField } from '../components/ui'
import { useToast } from '../components/toastContext'
import { SCORING_PRESET_DEFAULTS, SCORING_PRESET_LABELS, humanizeStatKey } from '../scoringPresets'

const DEFAULT_ROSTER_SLOTS: Record<string, number> = {
  QB: 1,
  RB: 2,
  WR: 2,
  TE: 1,
  FLEX: 1,
  K: 1,
  DEF: 1,
  BN: 6,
}

const ROSTER_SLOT_KEYS = Object.keys(DEFAULT_ROSTER_SLOTS)

type Step = 1 | 2 | 3

export function NewLeagueWizard() {
  const navigate = useNavigate()
  const { showError, showSuccess } = useToast()
  const [step, setStep] = useState<Step>(1)
  const [submitting, setSubmitting] = useState(false)

  // Step 1
  const [name, setName] = useState('')
  const [season, setSeason] = useState(2026)
  const [numTeams, setNumTeams] = useState(10)
  const [isKeeper, setIsKeeper] = useState(false)

  // Step 2
  const [preset, setPreset] = useState<ScoringPreset>('half_ppr')
  const [customizing, setCustomizing] = useState(false)
  const [customRules, setCustomRules] = useState<ScoringRules>(() => cloneRules(SCORING_PRESET_DEFAULTS.half_ppr))
  const [ruleTouched, setRuleTouched] = useState(false)

  // Step 3
  const [rosterSlots, setRosterSlots] = useState<Record<string, number>>(DEFAULT_ROSTER_SLOTS)

  const perStatEntries = useMemo(() => Object.entries(customRules.per_stat), [customRules])

  function cloneRules(rules: ScoringRules): ScoringRules {
    return { ...rules, per_stat: { ...rules.per_stat } }
  }

  function handlePresetChange(next: ScoringPreset) {
    setPreset(next)
    if (!ruleTouched) {
      setCustomRules(cloneRules(SCORING_PRESET_DEFAULTS[next]))
    }
  }

  function handleStatChange(key: string, value: string) {
    const num = value === '' ? 0 : Number(value)
    setRuleTouched(true)
    setCustomRules((prev) => ({ ...prev, per_stat: { ...prev.per_stat, [key]: num } }))
  }

  function handleRosterChange(key: string, value: string) {
    const num = value === '' ? 0 : Math.max(0, Math.floor(Number(value)))
    setRosterSlots((prev) => ({ ...prev, [key]: num }))
  }

  function canProceedFromStep1() {
    return name.trim().length > 0 && season > 0 && numTeams >= 2
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setSubmitting(true)
    try {
      const league = await createManualLeague({
        name: name.trim(),
        season,
        is_keeper: isKeeper,
        num_teams: numTeams,
        ...(customizing || ruleTouched ? { scoring_rules: customRules } : { scoring_preset: preset }),
        roster_slots: rosterSlots,
      })
      showSuccess(`League "${league.name}" created`)
      navigate(`/leagues/${encodeURIComponent(league.league_key)}`)
    } catch (err) {
      showError(err instanceof ApiError ? err.message : 'Failed to create league')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="page page-narrow">
      <h1>New manual league</h1>
      <div className="wizard-steps">
        <StepDot active={step === 1} done={step > 1} label="Basics" />
        <StepDot active={step === 2} done={step > 2} label="Scoring" />
        <StepDot active={step === 3} done={false} label="Roster" />
      </div>

      <Card>
        {step === 1 && (
          <div className="wizard-step">
            <TextField label="League name" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. The Gridiron Guild" />
            <div className="field-row">
              <TextField
                label="Season"
                type="number"
                value={season}
                onChange={(e) => setSeason(Number(e.target.value))}
              />
              <TextField
                label="Number of teams"
                type="number"
                min={2}
                max={32}
                value={numTeams}
                onChange={(e) => setNumTeams(Number(e.target.value))}
              />
            </div>
            <label className="checkbox-field">
              <input type="checkbox" checked={isKeeper} onChange={(e) => setIsKeeper(e.target.checked)} />
              <span>Keeper league</span>
            </label>
            <div className="wizard-actions">
              <Button variant="primary" disabled={!canProceedFromStep1()} onClick={() => setStep(2)}>
                Next: Scoring
              </Button>
            </div>
          </div>
        )}

        {step === 2 && (
          <div className="wizard-step">
            <SelectField
              label="Scoring preset"
              value={preset}
              onChange={(e) => handlePresetChange(e.target.value as ScoringPreset)}
            >
              {(Object.keys(SCORING_PRESET_LABELS) as ScoringPreset[]).map((key) => (
                <option key={key} value={key}>
                  {SCORING_PRESET_LABELS[key]}
                </option>
              ))}
            </SelectField>

            <button type="button" className="link-button" onClick={() => setCustomizing((v) => !v)}>
              {customizing ? 'Hide customization' : 'Customize scoring'}
            </button>

            {customizing && (
              <div className="scoring-grid">
                {perStatEntries.map(([key, value]) => (
                  <label className="field" key={key}>
                    <span className="field-label">{humanizeStatKey(key)}</span>
                    <input
                      className="field-input"
                      type="number"
                      step="0.01"
                      value={value}
                      onChange={(e) => handleStatChange(key, e.target.value)}
                    />
                  </label>
                ))}
              </div>
            )}

            <div className="wizard-actions">
              <Button onClick={() => setStep(1)}>Back</Button>
              <Button variant="primary" onClick={() => setStep(3)}>
                Next: Roster
              </Button>
            </div>
          </div>
        )}

        {step === 3 && (
          <form className="wizard-step" onSubmit={handleSubmit}>
            <p className="field-hint">Roster slot counts</p>
            <div className="scoring-grid">
              {ROSTER_SLOT_KEYS.map((key) => (
                <label className="field" key={key}>
                  <span className="field-label">{key}</span>
                  <input
                    className="field-input"
                    type="number"
                    min={0}
                    value={rosterSlots[key]}
                    onChange={(e) => handleRosterChange(key, e.target.value)}
                  />
                </label>
              ))}
            </div>
            <div className="wizard-actions">
              <Button type="button" onClick={() => setStep(2)}>
                Back
              </Button>
              <Button type="submit" variant="primary" busy={submitting}>
                Create league
              </Button>
            </div>
          </form>
        )}
      </Card>
    </div>
  )
}

function StepDot({ active, done, label }: { active: boolean; done: boolean; label: string }) {
  return (
    <div className={`step-dot${active ? ' step-dot-active' : ''}${done ? ' step-dot-done' : ''}`}>
      <span className="step-dot-circle" />
      <span className="step-dot-label">{label}</span>
    </div>
  )
}
