// The backend stores and serializes timestamps in UTC but *naive* (no "Z"
// suffix); the browser would parse those as local time. Treat any
// timezone-less ISO string as UTC. Strings that carry an offset (e.g.
// APScheduler's next-run times) parse as-is.
const HAS_TIMEZONE = /(?:Z|[+-]\d{2}:?\d{2})$/i

export function parseApiDate(iso: string | null | undefined): Date | null {
  if (!iso) return null
  const date = new Date(HAS_TIMEZONE.test(iso) ? iso : `${iso}Z`)
  return Number.isNaN(date.getTime()) ? null : date
}
