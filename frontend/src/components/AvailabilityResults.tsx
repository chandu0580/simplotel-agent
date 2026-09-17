import type { AvailabilityResult } from '../api/types'
import { formatDate, formatMoney, nightsLabel, partyLabel } from '../format'
import { useI18n } from '../i18n/context'

interface Props {
  result: AvailabilityResult
  contactPhone?: string
  onChangeDates: () => void
}

export function AvailabilityResults({ result, contactPhone, onChangeDates }: Props) {
  const { t, dateLocale } = useI18n()
  return (
    <section className="availability" aria-label={t('results.label')}>
      <header className="availability__summary">
        <div>
          <strong>
            {formatDate(result.check_in, dateLocale)} → {formatDate(result.check_out, dateLocale)}
          </strong>
          <span>
            {nightsLabel(t, result.nights)} · {partyLabel(t, result.adults, result.children)}
          </span>
        </div>
        <button type="button" className="btn btn--link" onClick={onChangeDates}>
          {t('results.change')}
        </button>
      </header>

      {result.season_label && <p className="availability__season">{t('results.season', { label: result.season_label })}</p>}

      {result.available ? (
        <ul className="room-list">
          {result.rooms.map((room) => (
            <li key={room.room_id} className="room-card">
              <div className="room-card__main">
                <h4>{room.name}</h4>
                <p className="room-card__meta">
                  {[t('results.sleeps', { count: room.max_occupancy }), room.beds, room.size_sqm ? `${room.size_sqm} m²` : '']
                    .filter(Boolean)
                    .join(' · ')}
                </p>
                <div className="room-card__badges">
                  {room.breakfast_included === null ? null : room.breakfast_included ? (
                    <span className="badge badge--good">{t('results.breakfast')}</span>
                  ) : (
                    <span className="badge">{t('results.roomOnly')}</span>
                  )}
                  {room.rooms_left <= 3 && <span className="badge badge--warn">{t('results.onlyLeft', { count: room.rooms_left })}</span>}
                </div>
              </div>
              <div className="room-card__price">
                <span className="room-card__total">{formatMoney(room.total_price, room.currency)}</span>
                <span className="room-card__nightly">{t('results.perNight', { price: formatMoney(room.nightly_rate, room.currency) })}</span>
              </div>
            </li>
          ))}
        </ul>
      ) : (
        <div className="availability__empty">
          <p>{result.message}</p>
          {result.sold_out_room_names.length > 0 && (
            <p className="muted">{t('results.soldOut', { names: result.sold_out_room_names.join(', ') })}</p>
          )}
          <button type="button" className="btn btn--secondary" onClick={onChangeDates}>
            {t('results.tryDates')}
          </button>
        </div>
      )}

      {result.available && (
        <p className="availability__footnote">
          {t('results.footnote')}
          {contactPhone && (
            <>
              {' '}
              {t('results.reserve')} <a href={`tel:${contactPhone.replace(/\s/g, '')}`}>{contactPhone}</a>.
            </>
          )}
        </p>
      )}
    </section>
  )
}
