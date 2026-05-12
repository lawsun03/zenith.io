export function fmtMoney(s: string | number | null | undefined, opts: { signed?: boolean } = {}): string {
  if (s === null || s === undefined) return '-'
  const n = Number(s)
  const sign = opts.signed && n > 0 ? '+' : ''
  const abs = Math.abs(n)
  return sign + (n < 0 ? '-$' : '$') + abs.toLocaleString('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
}

// All display timestamps are rendered in Pacific Time with 12-hour clock.
const PT = 'America/Los_Angeles'

export function fmtTime(iso: string | Date | null | undefined): string {
  if (!iso) return ''
  return new Date(iso as string).toLocaleTimeString('en-US', {
    timeZone: PT,
    hour: 'numeric',
    minute: '2-digit',
    second: '2-digit',
    hour12: true,
  })
}

// For bar timestamps: show MM/DD h:MM AM/PM so historical replays are legible
export function fmtBarTs(iso: string | null | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  const date = d.toLocaleDateString('en-US', {
    timeZone: PT,
    month: '2-digit',
    day: '2-digit',
  })
  const time = d.toLocaleTimeString('en-US', {
    timeZone: PT,
    hour: 'numeric',
    minute: '2-digit',
    hour12: true,
  })
  return `${date} ${time}`
}
