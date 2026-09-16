/** Parse YYYY-MM-DD as a local calendar date (avoids UTC off-by-one shifts). */
export function parseISODate(value: string): Date {
  const [y, m, d] = value.split('-').map(Number)
  return new Date(y, m - 1, d)
}

export function toISODate(date: Date): string {
  const y = date.getFullYear()
  const m = String(date.getMonth() + 1).padStart(2, '0')
  const d = String(date.getDate()).padStart(2, '0')
  return `${y}-${m}-${d}`
}

export function addDays(iso: string, days: number): string {
  const date = parseISODate(iso)
  date.setDate(date.getDate() + days)
  return toISODate(date)
}

export function nightsBetween(checkIn: string, checkOut: string): number {
  return Math.round((parseISODate(checkOut).getTime() - parseISODate(checkIn).getTime()) / 86_400_000)
}

export function formatDate(iso: string): string {
  return parseISODate(iso).toLocaleDateString('en-IN', { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' })
}

export function formatShortDate(iso: string): string {
  return parseISODate(iso).toLocaleDateString('en-IN', { day: 'numeric', month: 'short' })
}

export function formatMoney(amount: number, currency: string): string {
  return new Intl.NumberFormat('en-IN', { style: 'currency', currency, maximumFractionDigits: 0 }).format(amount)
}

export function plural(count: number, singular: string, pluralForm = `${singular}s`): string {
  return `${count} ${count === 1 ? singular : pluralForm}`
}

export function partyLabel(adults: number, children: number): string {
  return children > 0 ? `${plural(adults, 'adult')}, ${plural(children, 'child', 'children')}` : plural(adults, 'adult')
}
