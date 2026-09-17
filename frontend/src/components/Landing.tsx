import type { HotelInfo } from '../api/types'
import { useI18n } from '../i18n/context'
import type { MessageKey } from '../i18n/messages'

/**
 * First impression before the guest asks anything: what this assistant is for, and the quickest ways in.
 * The composer stays visible underneath, so typing a question immediately is always possible.
 */
interface Props {
  hotel: HotelInfo | null
  onAsk: (text: string) => void
  onOpenBookingForm: () => void
}

interface QuickAction {
  id: string
  icon: string
  labelKey: MessageKey
  /** The question sent on the guest's behalf; availability opens the form instead. */
  messageKey?: MessageKey
}

const QUICK_ACTIONS: QuickAction[] = [
  { id: 'rooms', icon: '🛏', labelKey: 'action.rooms', messageKey: 'action.rooms.message' },
  { id: 'breakfast', icon: '🍳', labelKey: 'action.breakfast', messageKey: 'action.breakfast.message' },
  { id: 'amenities', icon: '🏊', labelKey: 'action.amenities', messageKey: 'action.amenities.message' },
  { id: 'availability', icon: '📅', labelKey: 'action.availability' },
  { id: 'policies', icon: '📋', labelKey: 'action.policies', messageKey: 'action.policies.message' },
]

const EXAMPLES: MessageKey[] = ['example.checkIn', 'example.breakfast', 'example.roomForThree', 'example.weekend']

export function Landing({ hotel, onAsk, onOpenBookingForm }: Props) {
  const { t } = useI18n()

  return (
    <section className="landing" aria-labelledby="landing-title">
      <p className="landing__eyebrow">{hotel?.hotel.name ?? t('header.fallbackTagline')}</p>
      <h2 id="landing-title" className="landing__title">
        {t('landing.title')}
      </h2>
      <p className="landing__subtitle">{t('landing.subtitle')}</p>

      <div className="landing__actions" role="group" aria-label={t('landing.actionsLabel')}>
        {QUICK_ACTIONS.map((action) => (
          <button
            key={action.id}
            type="button"
            className="quick-action"
            onClick={() => (action.messageKey ? onAsk(t(action.messageKey)) : onOpenBookingForm())}
          >
            <span aria-hidden="true">{action.icon}</span>
            {t(action.labelKey)}
          </button>
        ))}
      </div>

      <div className="landing__examples">
        <p className="landing__examples-label" id="landing-examples">
          {t('landing.examplesLabel')}
        </p>
        <ul aria-labelledby="landing-examples">
          {EXAMPLES.map((key) => (
            <li key={key}>
              <button type="button" className="chip" onClick={() => onAsk(t(key))}>
                {t(key)}
              </button>
            </li>
          ))}
        </ul>
      </div>
    </section>
  )
}
