import type { Translate } from './i18n/core'

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

export function formatDate(iso: string, locale = 'en-IN'): string {
  return parseISODate(iso).toLocaleDateString(locale, { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' })
}

export function formatShortDate(iso: string, locale = 'en-IN'): string {
  return parseISODate(iso).toLocaleDateString(locale, { day: 'numeric', month: 'short' })
}

export function formatMoney(amount: number, currency: string): string {
  return new Intl.NumberFormat('en-IN', { style: 'currency', currency, maximumFractionDigits: 0 }).format(amount)
}

export function nightsLabel(t: Translate, count: number): string {
  return t(count === 1 ? 'unit.night' : 'unit.nights', { count })
}

export function partyLabel(t: Translate, adults: number, children: number): string {
  const a = t(adults === 1 ? 'unit.adult' : 'unit.adults', { count: adults })
  return children > 0 ? `${a}, ${t(children === 1 ? 'unit.child' : 'unit.children', { count: children })}` : a
}
