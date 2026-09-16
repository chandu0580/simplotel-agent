import { useCallback, useRef, useState } from 'react'
import { api, ApiError } from '../api/client'
import type { AvailabilityRequest, AvailabilityResult, BookingDetails, ChatReply, HistoryItem } from '../api/types'
import { formatShortDate, partyLabel } from '../format'

export type UIMessage =
  | { id: string; kind: 'user'; text: string }
  | {
      id: string
      kind: 'assistant'
      reply: ChatReply
      mode?: 'ai' | 'offline'
      notice?: string | null
      /** For booking forms: set once the guest has searched, so the form collapses. */
      formSummary?: string
    }
  | { id: string; kind: 'error'; text: string; retryable: boolean; retryText?: string }

const HISTORY_LIMIT = 12
let counter = 0
const nextId = () => `m${++counter}`

function emptyReply(overrides: Partial<ChatReply>): ChatReply {
  return {
    type: 'clarification',
    text: '',
    sources: [],
    suggestions: [],
    availability: null,
    booking_prefill: null,
    form_error: null,
    ...overrides,
  }
}

export function welcomeMessage(hotelName: string, suggestions: string[]): UIMessage {
  return {
    id: 'welcome',
    kind: 'assistant',
    reply: emptyReply({
      text: `Hi! I'm the virtual assistant for ${hotelName}. Ask me about rooms, amenities and policies, or check room availability for your dates.`,
      suggestions,
    }),
  }
}

function toHistory(messages: UIMessage[]): HistoryItem[] {
  const items: HistoryItem[] = []
  for (const m of messages) {
    if (m.kind === 'user') items.push({ role: 'user', content: m.text })
    else if (m.kind === 'assistant' && m.reply.text) items.push({ role: 'assistant', content: m.reply.text })
  }
  return items.slice(-HISTORY_LIMIT)
}

function bookingFromResult(result: AvailabilityResult): Partial<BookingDetails> {
  return { check_in: result.check_in, check_out: result.check_out, adults: result.adults, children: result.children }
}

function mergeBooking(current: Partial<BookingDetails> | null, update: BookingDetails): Partial<BookingDetails> {
  const merged: Partial<BookingDetails> = { ...current }
  for (const key of ['check_in', 'check_out', 'adults', 'children'] as const) {
    if (update[key] !== null) (merged as Record<string, unknown>)[key] = update[key]
  }
  return merged
}

export function useChat(initialMessages: UIMessage[] = []) {
  const [messages, setMessagesState] = useState<UIMessage[]>(initialMessages)
  const [pending, setPending] = useState(false)
  const [mode, setMode] = useState<'ai' | 'offline' | null>(null)
  const messagesRef = useRef(messages)
  const bookingRef = useRef<Partial<BookingDetails> | null>(null)
  const pendingRef = useRef(false)
  const modeRef = useRef<'ai' | 'offline' | null>(null)

  const setMessages = useCallback((update: (prev: UIMessage[]) => UIMessage[]) => {
    messagesRef.current = update(messagesRef.current)
    setMessagesState(messagesRef.current)
  }, [])

  const ask = useCallback(
    async (text: string) => {
      pendingRef.current = true
      setPending(true)
      // History is everything before the latest user message, which is sent separately.
      const prior = messagesRef.current.filter((m) => m.kind !== 'error')
      const lastUserIndex = prior.map((m) => m.kind).lastIndexOf('user')
      try {
        const response = await api.chat({
          message: text,
          history: toHistory(prior.slice(0, lastUserIndex)),
          booking_context: bookingRef.current,
        })
        const { reply } = response
        if (reply.availability) bookingRef.current = bookingFromResult(reply.availability)
        else if (reply.booking_prefill) bookingRef.current = mergeBooking(bookingRef.current, reply.booking_prefill)
        // Show the degraded-mode notice once when the mode changes, not on every reply.
        const notice = response.mode !== modeRef.current ? response.notice : null
        modeRef.current = response.mode
        setMode(response.mode)
        setMessages((prev) => [...prev, { id: nextId(), kind: 'assistant', reply, mode: response.mode, notice }])
      } catch (err) {
        const apiError = err instanceof ApiError ? err : new ApiError('server', 'Something went wrong. Please try again.')
        setMessages((prev) => [
          ...prev,
          { id: nextId(), kind: 'error', text: apiError.message, retryable: apiError.retryable, retryText: text },
        ])
      } finally {
        pendingRef.current = false
        setPending(false)
      }
    },
    [setMessages],
  )

  const send = useCallback(
    async (raw: string) => {
      const text = raw.trim()
      if (!text || pendingRef.current) return
      setMessages((prev) => [...prev.filter((m) => m.kind !== 'error'), { id: nextId(), kind: 'user', text }])
      await ask(text)
    },
    [ask, setMessages],
  )

  const retry = useCallback(
    async (errorId: string) => {
      const error = messagesRef.current.find((m) => m.id === errorId)
      if (!error || error.kind !== 'error' || !error.retryText || pendingRef.current) return
      setMessages((prev) => prev.filter((m) => m.id !== errorId))
      await ask(error.retryText)
    },
    [ask, setMessages],
  )

  const openBookingForm = useCallback(() => {
    if (pendingRef.current) return
    const b = bookingRef.current
    setMessages((prev) => [
      ...prev.filter((m) => m.kind !== 'error'),
      {
        id: nextId(),
        kind: 'assistant',
        reply: emptyReply({
          type: 'collect_booking_details',
          text: 'Choose your dates and number of guests, and I will check what is available.',
          booking_prefill: {
            check_in: b?.check_in ?? null,
            check_out: b?.check_out ?? null,
            adults: b?.adults ?? null,
            children: b?.children ?? null,
          },
        }),
      },
    ])
  }, [setMessages])

  /** Submits a booking form directly to the deterministic availability API (no LLM round-trip). Throws ApiError. */
  const checkAvailability = useCallback(
    async (formMessageId: string, details: AvailabilityRequest) => {
      const result = await api.availability(details)
      bookingRef.current = bookingFromResult(result)
      const summary = `${formatShortDate(details.check_in)} – ${formatShortDate(details.check_out)} · ${partyLabel(details.adults, details.children)}`
      setMessages((prev) => [
        ...prev
          .filter((m) => m.kind !== 'error')
          .map((m) => (m.id === formMessageId && m.kind === 'assistant' ? { ...m, formSummary: summary } : m)),
        { id: nextId(), kind: 'user', text: `Check availability: ${summary}` },
        {
          id: nextId(),
          kind: 'assistant',
          reply: emptyReply({
            type: 'availability',
            text: result.message,
            availability: result,
            suggestions: result.available
              ? ['What is the cancellation policy?', 'Is breakfast included?']
              : ['Try different dates', 'How can I contact the hotel?'],
          }),
        },
      ])
    },
    [setMessages],
  )

  return { messages, pending, mode, send, retry, openBookingForm, checkAvailability }
}
