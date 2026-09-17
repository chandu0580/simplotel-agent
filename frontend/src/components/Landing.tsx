import type { ComponentType, SVGProps } from 'react'
import type { HotelInfo } from '../api/types'
import { monogram } from '../format'
import { useI18n } from '../i18n/context'
import type { MessageKey } from '../i18n/messages'
import { IconBed, IconBreakfast, IconCalendar, IconPolicy, IconPool } from './icons'

/**
 * First impression before the guest asks anything: what this assistant is for, and the quickest ways in.
 * The composer stays visible underneath, so typing a question immediately is always possible.
 */
interface Props {
  hotel: HotelInfo | null
  onAsk: (text: string) => void
  onOpenBookingForm: () => void
}

interface TopicAction {
  id: string
  Icon: ComponentType<SVGProps<SVGSVGElement>>
  labelKey: MessageKey
  /** The question sent on the guest's behalf. */
  messageKey: MessageKey
}

const TOPIC_ACTIONS: TopicAction[] = [
  { id: 'rooms', Icon: IconBed, labelKey: 'action.rooms', messageKey: 'action.rooms.message' },
  { id: 'breakfast', Icon: IconBreakfast, labelKey: 'action.breakfast', messageKey: 'action.breakfast.message' },
  { id: 'amenities', Icon: IconPool, labelKey: 'action.amenities', messageKey: 'action.amenities.message' },
  { id: 'policies', Icon: IconPolicy, labelKey: 'action.policies', messageKey: 'action.policies.message' },
]

const EXAMPLES: MessageKey[] = ['example.checkIn', 'example.breakfast', 'example.roomForThree', 'example.weekend']

export function Landing({ hotel, onAsk, onOpenBookingForm }: Props) {
  const { t } = useI18n()

  return (
    <section className="landing" aria-labelledby="landing-title">
      <div className="landing__inner">
        <span className="landing__emblem" aria-hidden="true">
          {monogram(hotel?.hotel.name ?? t('header.fallbackTagline'))}
        </span>
        <p className="landing__eyebrow">{hotel?.hotel.name ?? t('header.fallbackTagline')}</p>
        <h2 id="landing-title" className="landing__title">
          {t('landing.title')}
        </h2>
        <p className="landing__subtitle">{t('landing.subtitle')}</p>

        {/* The search shortcut leads on its own line; the topic actions are alternatives to typing. */}
        <div className="landing__actions" role="group" aria-label={t('landing.actionsLabel')}>
          <button type="button" className="quick-action quick-action--primary" onClick={onOpenBookingForm}>
            <span className="quick-action__icon">
              <IconCalendar />
            </span>
            {t('action.availability')}
          </button>
          <div className="landing__topics">
            {TOPIC_ACTIONS.map(({ id, Icon, labelKey, messageKey }) => (
              <button key={id} type="button" className="quick-action" onClick={() => onAsk(t(messageKey))}>
                <span className="quick-action__icon">
                  <Icon />
                </span>
                {t(labelKey)}
              </button>
            ))}
          </div>
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
      </div>
    </section>
  )
}
