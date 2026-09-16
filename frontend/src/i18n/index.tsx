import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { I18nContext, type I18nValue } from './context'
import { translate } from './core'
import type { Locale } from './messages'

export function I18nProvider({ initialLocale = 'en', children }: { initialLocale?: Locale; children: ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>(initialLocale)
  const setLocale = useCallback((next: Locale) => setLocaleState(next), [])

  useEffect(() => {
    document.documentElement.lang = locale // screen readers pick the right voice
  }, [locale])

  const value = useMemo<I18nValue>(
    () => ({ locale, setLocale, t: (key, vars) => translate(locale, key, vars), dateLocale: locale === 'hi' ? 'hi-IN' : 'en-IN' }),
    [locale, setLocale],
  )
  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>
}
