import { useCallback, useEffect, useState, type CSSProperties } from 'react'
import { api } from './api/client'
import type { HotelInfo } from './api/types'
import { Composer } from './components/Composer'
import { Home } from './components/Home'
import { Landing } from './components/Landing'
import { LanguagePicker } from './components/LanguagePicker'
import { MessageList } from './components/MessageList'
import { IconBack } from './components/icons'
import { formatShortDate, monogram, partyLabel, toISODate } from './format'
import { useChat } from './hooks/useChat'
import { useOnlineStatus } from './hooks/useOnlineStatus'
import { useI18n } from './i18n/context'
import { SUPPORTED_LOCALES } from './i18n/messages'

const DEFAULT_HOTEL_NAME = 'The Palm Grove Resort'

/**
 * Two screens, addressed by hash so the browser's back button and a shared link both work:
 * the landing page at `/`, the conversation at `/#chat`. A hash keeps this to a few lines of state
 * instead of pulling in a router for one route.
 */
type View = 'home' | 'chat'

const viewFromHash = (): View => (window.location.hash === '#chat' ? 'chat' : 'home')

export default function App() {
  const { t, locale, dateLocale } = useI18n()
  const [hotel, setHotel] = useState<HotelInfo | null>(null)
  const [view, setView] = useState<View>(viewFromHash)
  const chat = useChat(locale)
  const online = useOnlineStatus()

  useEffect(() => {
    // The app still works if this fails; the screens fall back to defaults.
    api.hotel().then(setHotel).catch(() => undefined)
  }, [])

  useEffect(() => {
    const onHashChange = () => setView(viewFromHash())
    window.addEventListener('hashchange', onHashChange)
    return () => window.removeEventListener('hashchange', onHashChange)
  }, [])

  const openChat = useCallback(() => {
    window.location.hash = '#chat'
    setView('chat')
  }, [])

  const goHome = useCallback(() => {
    window.location.hash = ''
    setView('home')
  }, [])

  const hotelName = hotel?.hotel.name ?? DEFAULT_HOTEL_NAME
  const today = hotel?.today ?? toISODate(new Date())
  const languages = SUPPORTED_LOCALES.filter((l) => (hotel?.hotel.languages ?? ['en']).includes(l))
  const brandStyle = hotel ? ({ '--primary': hotel.hotel.brand.primary_color } as CSSProperties) : undefined
  const openBookingForm = () => chat.openBookingForm(t('form.prompt'))

  if (view === 'home') {
    return (
      <div className="app app--home" style={brandStyle}>
        <Home
          hotel={hotel}
          hotelName={hotelName}
          languages={languages}
          onOpenChat={openChat}
          onAsk={(question) => {
            openChat()
            chat.send(question)
          }}
          onCheckAvailability={() => {
            openChat()
            openBookingForm()
          }}
        />
      </div>
    )
  }

  return (
    <div className="app" style={brandStyle}>
      <main className="chat" aria-label={t('app.label', { hotel: hotelName })}>
        <header className="chat__header">
          <button type="button" className="icon-btn chat__back" onClick={goHome} aria-label={t('home.back')} title={t('home.back')}>
            <IconBack />
          </button>
          <span className="chat__mark" aria-hidden="true">
            {monogram(hotelName)}
          </span>
          <div className="chat__title">
            <h1>{hotelName}</h1>
            <p>{t('header.purpose')}</p>
          </div>
          <div className="chat__tools">
            {chat.mode && (
              <span className={`status status--${chat.mode}`} title={chat.mode === 'ai' ? t('status.aiTitle') : t('status.offlineTitle')}>
                <span className="status__dot" aria-hidden="true" />
                {chat.mode === 'ai' ? t('status.ai') : t('status.offline')}
              </span>
            )}
            <LanguagePicker languages={languages} />
          </div>
        </header>

        {!online && (
          <p className="connection-banner" role="status">
            {t('connection.offline')}
          </p>
        )}

        {chat.messages.length === 0 && !chat.pending ? (
          <Landing hotel={hotel} onAsk={chat.send} onOpenBookingForm={openBookingForm} />
        ) : (
          <MessageList
            messages={chat.messages}
            pending={chat.pending}
            hotel={hotel}
            today={today}
            onSuggestion={chat.send}
            onRetry={chat.retry}
            onOpenBookingForm={openBookingForm}
            onCheckAvailability={(formId, details) => {
              const summary = `${formatShortDate(details.check_in, dateLocale)} – ${formatShortDate(details.check_out, dateLocale)} · ${partyLabel(t, details.adults, details.children)}`
              return chat.checkAvailability(formId, details, summary, t('form.userSummary', { summary }))
            }}
          />
        )}

        <Composer pending={chat.pending} onSend={chat.send} onOpenBookingForm={openBookingForm} />
        <p className="disclaimer">{t('disclaimer')}</p>
      </main>
    </div>
  )
}
