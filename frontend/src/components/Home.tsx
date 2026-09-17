import type { HotelInfo } from '../api/types'
import { formatMoney, formatTime, monogram } from '../format'
import { useI18n } from '../i18n/context'
import type { Locale } from '../i18n/messages'
import { IconBed, IconBreakfast, IconCalendar, IconChat, IconSend } from './icons'
import { LanguagePicker } from './LanguagePicker'

/**
 * The landing page: what a guest sees first.
 *
 * Everything on it comes from the hotel profile the API already serves - name, tagline, address,
 * check-in and check-out times, published room content and the suggested questions. Nothing is
 * invented, and rates are labelled as indicative because the price for real dates comes from a
 * live availability search, not from the knowledge base.
 *
 * The assistant is one click away: `onOpenChat` switches to the conversation, optionally sending a
 * question or opening the booking form straight away.
 */
interface Props {
  hotel: HotelInfo | null
  hotelName: string
  languages: Locale[]
  onOpenChat: () => void
  onAsk: (question: string) => void
  onCheckAvailability: () => void
}

export function Home({ hotel, hotelName, languages, onOpenChat, onAsk, onCheckAvailability }: Props) {
  const { t, locale } = useI18n()
  const profile = hotel?.hotel
  const rooms = hotel?.rooms ?? []
  const currency = profile?.currency ?? 'INR'

  return (
    <div className="home">
      <header className="home__nav">
        <span className="chat__mark" aria-hidden="true">
          {monogram(hotelName)}
        </span>
        <div className="home__brand">
          <p className="home__brand-name">{hotelName}</p>
          {profile?.city && <p className="home__brand-city">{profile.city}</p>}
        </div>
        <div className="home__nav-tools">
          <LanguagePicker languages={languages} />
          <button type="button" className="btn btn--primary home__nav-cta" onClick={onOpenChat}>
            {t('home.chat')}
          </button>
        </div>
      </header>

      <main className="home__main">
        <section className="home__hero">
          <div className="home__hero-text">
          {profile?.city && <p className="home__eyebrow">{profile.city}</p>}
          <h1 className="home__title">{hotelName}</h1>
          <p className="home__tagline">{profile?.tagline ?? t('landing.subtitle')}</p>

          <div className="home__cta">
            <button type="button" className="btn btn--primary home__cta-primary" onClick={onOpenChat}>
              <IconSend />
              {t('home.chat')}
            </button>
            <button type="button" className="btn btn--secondary home__cta-secondary" onClick={onCheckAvailability}>
              <IconCalendar />
              {t('action.availability')}
            </button>
          </div>

          <ul className="home__facts">
            {profile && (
              <>
                <li>{t('home.facts.checkIn', { time: formatTime(profile.check_in_time, locale) })}</li>
                <li>{t('home.facts.checkOut', { time: formatTime(profile.check_out_time, locale) })}</li>
              </>
            )}
            {hotel && <li>{t('home.facts.guests', { count: hotel.max_guests })}</li>}
          </ul>
          </div>

          {/* Beside the hero, not below it: the questions are the fastest way into the assistant. */}
          <aside className="home__ask-card" aria-labelledby="home-ask">
            <h2 id="home-ask" className="home__section-title">
              <IconChat />
              {t('home.ask.title')}
            </h2>
            <p className="home__ask-subtitle">{t('home.ask.subtitle')}</p>
            <ul className="home__questions">
              {(hotel?.suggested_questions ?? []).map((question) => (
                <li key={question}>
                  <button type="button" className="chip" onClick={() => onAsk(question)}>
                    {question}
                  </button>
                </li>
              ))}
            </ul>
          </aside>
        </section>

        {rooms.length > 0 && (
          <section className="home__section" aria-labelledby="home-rooms">
            <h2 id="home-rooms" className="home__section-title">
              <IconBed />
              {t('home.rooms.title')}
            </h2>
            <ul className="home__rooms">
              {rooms.map((room) => (
                <li key={room.id} className="home-room">
                  <div className="home-room__head">
                    <h3>{room.name}</h3>
                    <p className="home-room__price">
                      <span className="home-room__from">{t('home.rooms.from', { price: formatMoney(room.base_rate, currency) })}</span>
                      <span className="home-room__night">{t('home.rooms.perNight')}</span>
                    </p>
                  </div>
                  <p className="home-room__meta">
                    {[t('results.sleeps', { count: room.max_occupancy }), room.beds, room.size_sqm ? `${room.size_sqm} m²` : ''].filter(Boolean).join(' · ')}
                  </p>
                  <p className="home-room__description">{room.description}</p>
                  <div className="home-room__foot">
                    {room.breakfast_included && (
                      <span className="badge badge--good">
                        <IconBreakfast />
                        {t('results.breakfast')}
                      </span>
                    )}
                    <button type="button" className="btn btn--link" onClick={() => onAsk(`Tell me about the ${room.name}.`)}>
                      {t('home.rooms.ask')}
                    </button>
                  </div>
                </li>
              ))}
            </ul>
            <p className="home__note">{t('home.rooms.note')}</p>
          </section>
        )}
      </main>

      {profile && (
        <footer className="home__footer">
          <p className="home__footer-title">{t('home.contact.title')}</p>
          <p className="home__address">{profile.address}</p>
          <p className="contact-links">
            <a href={`tel:${profile.phone.replace(/\s/g, '')}`}>{t('contact.call')}</a>
            <a href={`https://wa.me/${profile.whatsapp.replace(/\D/g, '')}`} target="_blank" rel="noreferrer">
              {t('contact.whatsapp')}
            </a>
            <a href={`mailto:${profile.email}`}>{t('contact.email')}</a>
          </p>
        </footer>
      )}
    </div>
  )
}
