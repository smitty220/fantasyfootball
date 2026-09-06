import type { ScoringPreset, ScoringRules } from './api/types'

// The API contract only returns scoring_rules from the create/update endpoints -
// there is no GET for preset defaults. To let the wizard show editable fields
// before a league exists, we mirror standard fantasy-football scoring defaults
// here and send them back as `scoring_rules` if the user customizes anything,
// or just the `scoring_preset` name if they don't.
export const SCORING_PRESET_LABELS: Record<ScoringPreset, string> = {
  standard: 'Standard (0 PPR)',
  half_ppr: 'Half PPR',
  full_ppr: 'Full PPR',
}

export const STAT_LABELS: Record<string, string> = {
  pass_yds: 'Passing yards',
  pass_td: 'Passing touchdowns',
  pass_int: 'Interceptions thrown',
  pass_2pt: 'Passing 2-point conversions',
  rush_yds: 'Rushing yards',
  rush_td: 'Rushing touchdowns',
  rush_2pt: 'Rushing 2-point conversions',
  rec: 'Receptions',
  rec_yds: 'Receiving yards',
  rec_td: 'Receiving touchdowns',
  rec_2pt: 'Receiving 2-point conversions',
  fumbles_lost: 'Fumbles lost',
  fg_made: 'Field goals made',
  fg_missed: 'Field goals missed',
  xp_made: 'Extra points made',
}

function presetRules(rec: number): ScoringRules {
  return {
    per_stat: {
      pass_yds: 0.04,
      pass_td: 4,
      pass_int: -2,
      rush_yds: 0.1,
      rush_td: 6,
      rec,
      rec_yds: 0.1,
      rec_td: 6,
      fumbles_lost: -2,
    },
  }
}

export const SCORING_PRESET_DEFAULTS: Record<ScoringPreset, ScoringRules> = {
  standard: presetRules(0),
  half_ppr: presetRules(0.5),
  full_ppr: presetRules(1),
}

export function humanizeStatKey(key: string): string {
  if (STAT_LABELS[key]) return STAT_LABELS[key]
  return key
    .split('_')
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ')
}
