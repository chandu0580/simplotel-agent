import { useI18n } from '../i18n/context'
import { LOCALE_NAMES, type Locale } from '../i18n/messages'

/** Shown on both screens, so the guest can switch language before or during a conversation. */
export function LanguagePicker({ languages }: { languages: Locale[] }) {
  const { t, locale, setLocale } = useI18n()
  if (languages.length < 2) return null
  return (
    <label className="language">
      <span className="visually-hidden">{t('language.label')}</span>
      <select value={locale} onChange={(e) => setLocale(e.target.value as Locale)}>
        {languages.map((l) => (
          <option key={l} value={l}>
            {LOCALE_NAMES[l]}
          </option>
        ))}
      </select>
    </label>
  )
}
