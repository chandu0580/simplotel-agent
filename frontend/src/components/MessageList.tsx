import { useEffect, useRef, useState } from 'react'
import type { AvailabilityRequest, HotelInfo } from '../api/types'
import type { UIMessage } from '../hooks/useChat'
import { AvailabilityForm } from './AvailabilityForm'
import { AvailabilityResults } from './AvailabilityResults'

const OPEN_FORM_SUGGESTIONS = new Set(['check room availability', 'try different dates'])
const SLOW_RESPONSE_MS = 8000

interface Props {
  messages: UIMessage[]
  pending: boolean
  hotel: HotelInfo | null
  today: string
  onSuggestion: (text: string) => void
  onRetry: (errorId: string) => void
  onOpenBookingForm: () => void
  onCheckAvailability: (formMessageId: string, details: AvailabilityRequest) => Promise<void>
}

export function MessageList({ messages, pending, hotel, today, onSuggestion, onRetry, onOpenBookingForm, onCheckAvailability }: Props) {
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

  return (
    <div className="messages" role="log" aria-live="polite" aria-label="Conversation">
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
                <p>{message.text}</p>
                {message.retryable && message.retryText && (
                  <button type="button" className="btn btn--secondary" onClick={() => onRetry(message.id)} disabled={pending}>
                    Try again
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
                  <a href={`tel:${hotel.hotel.phone.replace(/\s/g, '')}`}>Call</a>
                  <a href={`https://wa.me/${hotel.hotel.whatsapp.replace(/\D/g, '')}`} target="_blank" rel="noreferrer">
                    WhatsApp
                  </a>
                  <a href={`mailto:${hotel.hotel.email}`}>Email</a>
                </p>
              )}

              {reply.type === 'collect_booking_details' &&
                (message.formSummary ? (
                  <p className="form-summary">Searched: {message.formSummary}</p>
                ) : (
                  <AvailabilityForm
                    today={today}
                    prefill={reply.booking_prefill}
                    serverError={reply.form_error}
                    disabled={pending}
                    onSubmit={(details) => onCheckAvailability(message.id, details)}
                  />
                ))}

              {reply.availability && (
                <AvailabilityResults result={reply.availability} contactPhone={hotel?.hotel.phone} onChangeDates={onOpenBookingForm} />
              )}

              {reply.sources.length > 0 && (
                <p className="sources">
                  <span>Based on:</span> {reply.sources.map((s) => s.title).join(' · ')}
                </p>
              )}
            </div>

            {isLatest && !pending && reply.suggestions.length > 0 && (
              <div className="suggestions" aria-label="Suggested questions">
                {reply.suggestions.map((s) => (
                  <button key={s} type="button" className="chip" onClick={() => handleSuggestion(s)}>
                    {s}
                  </button>
                ))}
              </div>
            )}
          </div>
        )
      })}

      {pending && <TypingIndicator />}
      <div ref={endRef} />
    </div>
  )
}

function TypingIndicator() {
  const [slow, setSlow] = useState(false)
  useEffect(() => {
    const timer = setTimeout(() => setSlow(true), SLOW_RESPONSE_MS)
    return () => clearTimeout(timer)
  }, [])
  return (
    <div className="bubble-row" role="status" aria-label="Assistant is typing">
      <div className="bubble bubble--assistant bubble--typing">
        <span className="dots" aria-hidden="true">
          <span />
          <span />
          <span />
        </span>
        <span className="typing-label">{slow ? 'Still working on it…' : 'Checking hotel information…'}</span>
      </div>
    </div>
  )
}
