import type { AvailabilityResult } from '../api/types'
import { formatDate, formatMoney, partyLabel, plural } from '../format'

interface Props {
  result: AvailabilityResult
  contactPhone?: string
  onChangeDates: () => void
}

export function AvailabilityResults({ result, contactPhone, onChangeDates }: Props) {
  return (
    <section className="availability" aria-label="Availability results">
      <header className="availability__summary">
        <div>
          <strong>
            {formatDate(result.check_in)} → {formatDate(result.check_out)}
          </strong>
          <span>
            {plural(result.nights, 'night')} · {partyLabel(result.adults, result.children)}
          </span>
        </div>
        <button type="button" className="btn btn--link" onClick={onChangeDates}>
          Change
        </button>
      </header>

      {result.season_label && <p className="availability__season">{result.season_label} rates apply to some nights.</p>}

      {result.available ? (
        <ul className="room-list">
          {result.rooms.map((room) => (
            <li key={room.room_id} className="room-card">
              <div className="room-card__main">
                <h4>{room.name}</h4>
                <p className="room-card__meta">
                  Sleeps {room.max_occupancy} · {room.beds} · {room.size_sqm} m²
                </p>
                <div className="room-card__badges">
                  {room.breakfast_included ? (
                    <span className="badge badge--good">Breakfast included</span>
                  ) : (
                    <span className="badge">Room only</span>
                  )}
                  {room.rooms_left <= 3 && (
                    <span className="badge badge--warn">
                      Only {room.rooms_left} left
                    </span>
                  )}
                </div>
              </div>
              <div className="room-card__price">
                <span className="room-card__total">{formatMoney(room.total_price, room.currency)}</span>
                <span className="room-card__nightly">
                  {formatMoney(room.nightly_rate, room.currency)} / night avg.
                </span>
              </div>
            </li>
          ))}
        </ul>
      ) : (
        <div className="availability__empty">
          <p>{result.message}</p>
          {result.sold_out_room_names.length > 0 && (
            <p className="muted">Sold out for these dates: {result.sold_out_room_names.join(', ')}</p>
          )}
          <button type="button" className="btn btn--secondary" onClick={onChangeDates}>
            Try different dates
          </button>
        </div>
      )}

      {result.available && (
        <p className="availability__footnote">
          Prices exclude taxes and are not held until booked.
          {contactPhone && (
            <>
              {' '}
              To reserve, call <a href={`tel:${contactPhone.replace(/\s/g, '')}`}>{contactPhone}</a>.
            </>
          )}
        </p>
      )}
    </section>
  )
}
