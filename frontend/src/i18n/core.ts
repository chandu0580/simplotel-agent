import { catalogs, en, SUPPORTED_LOCALES, type Locale, type MessageKey } from './messages'

export type Translate = (key: MessageKey, vars?: Record<string, string | number>) => string

export function translate(locale: Locale, key: MessageKey, vars: Record<string, string | number> = {}): string {
  const template = catalogs[locale][key] ?? en[key] // fall back to English for missing keys
  return template.replace(/\{(\w+)\}/g, (_, name: string) => String(vars[name] ?? `{${name}}`))
}

export function detectLocale(available: string[] = SUPPORTED_LOCALES): Locale {
  const preferred = (typeof navigator !== 'undefined' ? navigator.language : 'en').slice(0, 2)
  return (SUPPORTED_LOCALES.includes(preferred as Locale) && available.includes(preferred) ? preferred : 'en') as Locale
}
