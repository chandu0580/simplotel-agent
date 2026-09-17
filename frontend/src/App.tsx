import { useEffect, useState, type CSSProperties } from 'react'
import { api } from './api/client'
import type { HotelInfo } from './api/types'
import { Composer } from './components/Composer'
import { Landing } from './components/Landing'
import { MessageList } from './components/MessageList'
import { formatShortDate, partyLabel, toISODate } from './format'
import { useChat } from './hooks/useChat'
import { useOnlineStatus } from './hooks/useOnlineStatus'
import { useI18n } from './i18n/context'
import { LOCALE_NAMES, SUPPORTED_LOCALES, type Locale } from './i18n/messages'

const DEFAULT_HOTEL_NAME = 'The Palm Grove Resort'

export default function App() {
  const { t, locale, setLocale, dateLocale } = useI18n()
  const [hotel, setHotel] = useState<HotelInfo | null>(null)
  const chat = useChat(locale)
  const online = useOnlineStatus()

  useEffect(() => {
    // The chat still works if this fails; the header just uses defaults.
    api.hotel().then(setHotel).catch(() => undefined)
  }, [])

  const hotelName = hotel?.hotel.name ?? DEFAULT_HOTEL_NAME
  const today = hotel?.today ?? toISODate(new Date())
  const languages = SUPPORTED_LOCALES.filter((l) => (hotel?.hotel.languages ?? ['en']).includes(l))
  const brandStyle = hotel ? ({ '--primary': hotel.hotel.brand.primary_color } as CSSProperties) : undefined

  return (
    <div className="app" style={brandStyle}>
      <main className="chat" aria-label={t('app.label', { hotel: hotelName })}>
        <header className="chat__header">
          <div className="chat__avatar" aria-hidden="true">
            🌴
          </div>
          <div className="chat__title">
            <h1>{hotelName}</h1>
            <p>{t('header.purpose')}</p>
          </div>
          {languages.length > 1 && (
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
          )}
          {chat.mode && (
            <span className={`status status--${chat.mode}`} title={chat.mode === 'ai' ? t('status.aiTitle') : t('status.offlineTitle')}>
              {chat.mode === 'ai' ? t('status.ai') : t('status.offline')}
            </span>
          )}
        </header>

        {!online && (
          <p className="connection-banner" role="status">
            {t('connection.offline')}
          </p>
        )}

        {chat.messages.length === 0 && !chat.pending ? (
          <Landing hotel={hotel} onAsk={chat.send} onOpenBookingForm={() => chat.openBookingForm(t('form.prompt'))} />
        ) : (
        <MessageList
          messages={chat.messages}
          pending={chat.pending}
          hotel={hotel}
          today={today}
          onSuggestion={chat.send}
          onRetry={chat.retry}
          onOpenBookingForm={() => chat.openBookingForm(t('form.prompt'))}
          onCheckAvailability={(formId, details) => {
            const summary = `${formatShortDate(details.check_in, dateLocale)} – ${formatShortDate(details.check_out, dateLocale)} · ${partyLabel(t, details.adults, details.children)}`
            return chat.checkAvailability(formId, details, summary, t('form.userSummary', { summary }))
          }}
        />
        )}

        <Composer pending={chat.pending} onSend={chat.send} onOpenBookingForm={() => chat.openBookingForm(t('form.prompt'))} />
        <p className="disclaimer">{t('disclaimer')}</p>
      </main>
    </div>
  )
}
