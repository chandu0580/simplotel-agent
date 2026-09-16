import { createContext, useContext } from 'react'
import { translate, type Translate } from './core'
import type { Locale } from './messages'

export interface I18nValue {
  locale: Locale
  setLocale: (locale: Locale) => void
  t: Translate
  dateLocale: string
}

export const I18nContext = createContext<I18nValue>({
  locale: 'en',
  setLocale: () => undefined,
  t: (key, vars) => translate('en', key, vars),
  dateLocale: 'en-IN',
})

export function useI18n(): I18nValue {
  return useContext(I18nContext)
}
