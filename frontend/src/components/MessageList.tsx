import { useEffect, useRef, useState } from 'react'
import type { ApiErrorKind } from '../api/client'
import type { AvailabilityRequest, HotelInfo } from '../api/types'
import type { UIMessage } from '../hooks/useChat'
import { useI18n } from '../i18n/context'
import type { MessageKey } from '../i18n/messages'
import { AvailabilityForm } from './AvailabilityForm'
import { AvailabilityResults } from './AvailabilityResults'

const OPEN_FORM_SUGGESTIONS = new Set(['check room availability', 'try different dates'])
const SLOW_RESPONSE_MS = 8000
const ERROR_KEYS: Record<ApiErrorKind, MessageKey> = {
  network: 'error.network',
  timeout: 'error.timeout',
  rate_limited: 'error.rateLimited',
  busy: 'error.busy',
  too_large: 'error.tooLarge',
  validation: 'error.server',
  not_found: 'error.server',
  unavailable: 'error.unavailable',
  unexpected: 'error.server',
  server: 'error.server',
}

interface Props {
  messages: UIMessage[]
  pending: boolean
  hotel: HotelInfo | null
  today: string
  welcomeText: string
  welcomeSuggestions: string[]
  onSuggestion: (text: string) => void
  onRetry: (errorId: string) => void
  onOpenBookingForm: () => void
  onCheckAvailability: (formMessageId: string, details: AvailabilityRequest) => Promise<void>
}

export function MessageList(props: Props) {
  const { messages, pending, hotel, today, welcomeText, welcomeSuggestions, onSuggestion, onRetry, onOpenBookingForm, onCheckAvailability } = props
  const { t } = useI18n()
  const endRef = useRef<HTMLDivElement>(null)
  const lastAssistantId = [...messages].reverse().find((m) => m.kind === 'assistant')?.id
  const lastMessageId = messages.at(-1)?.id

  useEffect(() => {
    endRef.current?.scrollIntoView?.({ behavior: 'smooth', block: 'end' })
  }, [messages.length, pending])

  function handleSuggestion(text: string) {
    if (OPEN_FORM_SUGGESTIONS.has(text.toLowerCase())) onOpenBookingForm()
    else onSuggestion(text)
  }

  const suggestionChips = (suggestions: string[]) => (
    <div className="suggestions" aria-label={t('suggestions.label')}>
      {suggestions.map((s) => (
        <button key={s} type="button" className="chip" onClick={() => handleSuggestion(s)}>
          {s}
        </button>
      ))}
    </div>
  )

  return (
    <div className="messages" role="log" aria-live="polite" aria-busy={pending} aria-label={t('conversation.label')} tabIndex={0}>
      <div className="bubble-row">
        <div className="bubble bubble--assistant">
          <p className="bubble__text">{welcomeText}</p>
        </div>
        {messages.length === 0 && !pending && welcomeSuggestions.length > 0 && suggestionChips(welcomeSuggestions)}
      </div>

      {messages.map((message) => {
        if (message.kind === 'user') {
          return (
            <div key={message.id} className="bubble-row bubble-row--user">
              <div className="bubble bubble--user">{message.text}</div>
            </div>
          )
        }

        if (message.kind === 'error') {
          return (
            <div key={message.id} className="bubble-row">
              <div className="bubble bubble--error" role="alert">
                <p>{t(ERROR_KEYS[message.errorKind])}</p>
                {message.retryable && message.retryText && (
                  <button type="button" className="btn btn--secondary" onClick={() => onRetry(message.id)} disabled={pending}>
                    {t('error.retry')}
                  </button>
                )}
              </div>
            </div>
          )
        }

        const { reply } = message
        const isLatest = message.id === lastAssistantId && message.id === lastMessageId
        return (
          <div key={message.id} className="bubble-row">
            <div className={`bubble bubble--assistant ${reply.type === 'fallback' ? 'bubble--fallback' : ''}`}>
              {message.notice && (
                <p className="notice" role="status">
                  {message.notice}
                </p>
              )}
              {reply.text && reply.type !== 'availability' && <p className="bubble__text">{reply.text}</p>}

              {reply.type === 'fallback' && hotel && (
                <p className="contact-links">
                  <a href={`tel:${hotel.hotel.phone.replace(/\s/g, '')}`}>{t('contact.call')}</a>
                  <a href={`https://wa.me/${hotel.hotel.whatsapp.replace(/\D/g, '')}`} target="_blank" rel="noreferrer">
                    {t('contact.whatsapp')}
                  </a>
                  <a href={`mailto:${hotel.hotel.email}`}>{t('contact.email')}</a>
                </p>
              )}

              {reply.type === 'collect_booking_details' &&
                (message.formSummary ? (
                  <p className="form-summary">{t('form.searched', { summary: message.formSummary })}</p>
                ) : (
                  <AvailabilityForm
                    today={today}
                    prefill={reply.booking_prefill}
                    serverError={reply.form_error}
                    disabled={pending}
                    autoFocus={isLatest}
                    onSubmit={(details) => onCheckAvailability(message.id, details)}
                  />
                ))}

              {reply.availability && (
                <AvailabilityResults result={reply.availability} contactPhone={hotel?.hotel.phone} onChangeDates={onOpenBookingForm} />
              )}

              {reply.sources.length > 0 && (
                <p className="sources">
                  <span>{t('sources.basedOn')}</span> {reply.sources.map((s) => s.title).join(' · ')}
                </p>
              )}
            </div>

            {isLatest && !pending && reply.suggestions.length > 0 && suggestionChips(reply.suggestions)}
          </div>
        )
      })}

      {pending && <TypingIndicator />}
      <div ref={endRef} />
    </div>
  )
}

function TypingIndicator() {
  const { t } = useI18n()
  const [slow, setSlow] = useState(false)
  useEffect(() => {
    const timer = setTimeout(() => setSlow(true), SLOW_RESPONSE_MS)
    return () => clearTimeout(timer)
  }, [])
  return (
    <div className="bubble-row" role="status" aria-label={t('typing.label')}>
      <div className="bubble bubble--assistant bubble--typing">
        <span className="dots" aria-hidden="true">
          <span />
          <span />
          <span />
        </span>
        <span className="typing-label">{slow ? t('typing.slow') : t('typing.checking')}</span>
      </div>
    </div>
  )
}
