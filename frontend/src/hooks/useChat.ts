import { useCallback, useRef, useState } from 'react'
import { api, ApiError, type ApiErrorKind } from '../api/client'
import type { AvailabilityRequest, AvailabilityResult, BookingDetails, ChatReply, ConversationTurnResponse } from '../api/types'

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
  | { id: string; kind: 'error'; errorKind: ApiErrorKind; retryable: boolean; retryText?: string }

let counter = 0
const nextId = () => `m${++counter}`

export function emptyReply(overrides: Partial<ChatReply>): ChatReply {
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

function bookingFromResult(result: AvailabilityResult): Partial<BookingDetails> {
  return { check_in: result.check_in, check_out: result.check_out, adults: result.adults, children: result.children }
}

function toApiError(err: unknown): ApiError {
  return err instanceof ApiError ? err : new ApiError('server', 'server')
}

/**
 * Conversation state lives on the server (history, booking context). The client keeps the
 * conversation id and what it renders. An expired conversation is recreated once, transparently.
 */
export function useChat(locale: string) {
  const [messages, setMessagesState] = useState<UIMessage[]>([])
  const [pending, setPending] = useState(false)
  const [mode, setMode] = useState<'ai' | 'offline' | null>(null)
  const messagesRef = useRef(messages)
  const conversationRef = useRef<string | null>(null)
  // Last known booking details, used only to pre-fill forms the guest opens themselves.
  const bookingRef = useRef<Partial<BookingDetails> | null>(null)
  const pendingRef = useRef(false)
  const modeRef = useRef<'ai' | 'offline' | null>(null)

  const setMessages = useCallback((update: (prev: UIMessage[]) => UIMessage[]) => {
    messagesRef.current = update(messagesRef.current)
    setMessagesState(messagesRef.current)
  }, [])

  const withConversation = useCallback(
    async <T,>(call: (conversationId: string) => Promise<T>): Promise<T> => {
      if (!conversationRef.current) conversationRef.current = (await api.createConversation(locale)).conversation_id
      try {
        return await call(conversationRef.current)
      } catch (err) {
        if (err instanceof ApiError && err.code === 'CONVERSATION_NOT_FOUND') {
          conversationRef.current = (await api.createConversation(locale)).conversation_id
          return call(conversationRef.current)
        }
        throw err
      }
    },
    [locale],
  )

  const ask = useCallback(
    async (text: string) => {
      pendingRef.current = true
      setPending(true)
      try {
        const sendOnce = () => withConversation((id) => api.sendMessage(id, text, locale))
        let response: ConversationTurnResponse
        try {
          response = await sendOnce()
        } catch (err) {
          // The previous message (another tab, a double submit) is still being answered: wait as the server asks, capped, and retry once.
          if (!(err instanceof ApiError && err.kind === 'busy')) throw err
          await new Promise((resolve) => setTimeout(resolve, Math.min(err.retryAfterSeconds ?? 2, 3) * 1000))
          response = await sendOnce()
        }
        const { reply } = response
        if (reply.availability) bookingRef.current = bookingFromResult(reply.availability)
        else if (reply.booking_prefill) {
          const known = Object.fromEntries(Object.entries(reply.booking_prefill).filter(([, v]) => v !== null))
          bookingRef.current = { ...bookingRef.current, ...known }
        }
        // Show the degraded-mode notice once when the mode changes, not on every reply.
        const notice = response.mode !== modeRef.current ? response.notice : null
        modeRef.current = response.mode
        setMode(response.mode)
        setMessages((prev) => [...prev, { id: nextId(), kind: 'assistant', reply, mode: response.mode, notice }])
      } catch (err) {
        const apiError = toApiError(err)
        setMessages((prev) => [
          ...prev,
          { id: nextId(), kind: 'error', errorKind: apiError.kind, retryable: apiError.retryable, retryText: text },
        ])
      } finally {
        pendingRef.current = false
        setPending(false)
      }
    },
    [locale, setMessages, withConversation],
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

  const openBookingForm = useCallback(
    (promptText: string) => {
      if (pendingRef.current) return
      const b = bookingRef.current
      setMessages((prev) => [
        ...prev.filter((m) => m.kind !== 'error'),
        {
          id: nextId(),
          kind: 'assistant',
          reply: emptyReply({
            type: 'collect_booking_details',
            text: promptText,
            booking_prefill: {
              check_in: b?.check_in ?? null,
              check_out: b?.check_out ?? null,
              adults: b?.adults ?? null,
              children: b?.children ?? null,
            },
          }),
        },
      ])
    },
    [setMessages],
  )

  /** Booking form → deterministic availability API (no LLM), recorded in the server conversation. Throws ApiError. */
  const checkAvailability = useCallback(
    async (formMessageId: string, details: AvailabilityRequest, summary: string, userText: string) => {
      const result = await withConversation((id) => api.checkAvailability(id, details))
      bookingRef.current = bookingFromResult(result)
      setMessages((prev) => [
        ...prev
          .filter((m) => m.kind !== 'error')
          .map((m) => (m.id === formMessageId && m.kind === 'assistant' ? { ...m, formSummary: summary } : m)),
        { id: nextId(), kind: 'user', text: userText },
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
    [setMessages, withConversation],
  )

  return { messages, pending, mode, send, retry, openBookingForm, checkAvailability }
}
