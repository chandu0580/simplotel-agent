import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import App from './App'
import type { AvailabilityResult, ChatReply, ConversationTurnResponse } from './api/types'
import { I18nProvider } from './i18n'

type Handler = (body: unknown) => Promise<Response> | Response

const json = (status: number, body: unknown, headers: Record<string, string> = {}) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json', ...headers } })

const hotelInfo = {
  hotel: {
    id: 'hotel-goa-001',
    name: 'The Palm Grove Resort',
    tagline: 'A beachside retreat',
    city: 'Candolim, Goa',
    phone: '+91 832 555 0142',
    email: 'stay@palmgroveresort.example',
    whatsapp: '+91 98220 55501',
    currency: 'INR',
    check_in_time: '14:00',
    check_out_time: '11:00',
    languages: ['en', 'hi'],
    brand: { assistant_name: 'Palm Grove Assistant', primary_color: '#0f5d4e' },
  },
  today: '2026-10-05',
  max_guests: 5,
  suggested_questions: [],
  features: { ai_assistant: true },
}

function reply(overrides: Partial<ChatReply>): ChatReply {
  return { type: 'answer', text: '', sources: [], suggestions: [], availability: null, booking_prefill: null, form_error: null, ...overrides }
}

function turn(r: Partial<ChatReply>, extra: Partial<ConversationTurnResponse> = {}): ConversationTurnResponse {
  return {
    request_id: 'req1',
    conversation_id: 'conv_1',
    mode: 'ai',
    notice: null,
    reply: reply(r),
    meta: { trace_id: 't', prompt_version: 'p', tool_schema_version: 's', knowledge_version: 'k' },
    ...extra,
  }
}

const availabilityResult: AvailabilityResult = {
  check_in: '2026-10-07',
  check_out: '2026-10-09',
  nights: 2,
  adults: 2,
  children: 0,
  available: true,
  rooms: [
    {
      room_id: 'deluxe-pool-view',
      name: 'Deluxe Pool View Room',
      description: '',
      beds: '1 king bed',
      size_sqm: 34,
      max_occupancy: 3,
      breakfast_included: true,
      rooms_left: 2,
      nightly_rate: 7800,
      total_price: 15600,
      currency: 'INR',
      features: [],
    },
  ],
  sold_out_room_names: [],
  message: '1 room type available for 2 adults, 2 nights.',
  season_label: null,
}

/** Routes fetch calls for the v1 API by path and records them. */
function mockApi(routes: { messages?: Handler[]; availability?: Handler[]; conversations?: Handler[] }) {
  const calls: { method: string; path: string; body: unknown }[] = []
  const queues = { messages: [...(routes.messages ?? [])], availability: [...(routes.availability ?? [])], conversations: [...(routes.conversations ?? [])] }
  let created = 0
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input)
    const body = init?.body ? JSON.parse(String(init.body)) : undefined
    calls.push({ method: init?.method ?? 'GET', path, body })
    if (path.endsWith('/api/v1/hotels/hotel-goa-001')) return json(200, hotelInfo)
    if (path.endsWith('/conversations')) {
      const handler = queues.conversations.shift()
      if (handler) return handler(body)
      created += 1
      return json(201, { conversation_id: `conv_${created}`, hotel_id: 'hotel-goa-001', channel: 'web', locale: 'en', expires_at: '2026-10-06T00:00:00Z' })
    }
    const key = path.endsWith('/messages') ? 'messages' : 'availability'
    const handler = queues[key].shift()
    if (!handler) throw new Error(`Unexpected call to ${path}`)
    return handler(body)
  })
  vi.stubGlobal('fetch', fetchMock)
  return calls
}

function deferred() {
  let resolve!: (r: Response) => void
  const promise = new Promise<Response>((r) => (resolve = r))
  return { promise, resolve }
}

function renderApp() {
  return render(
    <I18nProvider initialLocale="en">
      <App />
    </I18nProvider>,
  )
}

async function ask(text: string) {
  const user = userEvent.setup()
  await user.type(screen.getByLabelText('Ask a question'), text)
  await user.click(screen.getByRole('button', { name: 'Send message' }))
  return user
}

const messageCalls = <T extends { path: string }>(calls: T[]) => calls.filter((c) => c.path.endsWith('/messages'))

describe('Guest assistant chat', () => {
  it('shows a loading state, then the answer with its sources', async () => {
    const pending = deferred()
    mockApi({ messages: [() => pending.promise] })
    renderApp()

    await ask('What time is check-in?')

    expect(screen.getByText('What time is check-in?')).toBeInTheDocument()
    expect(screen.getByRole('status', { name: 'Assistant is typing' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Send message' })).toBeDisabled()
    expect(screen.getByRole('log', { name: 'Conversation' })).toHaveAttribute('aria-busy', 'true')

    pending.resolve(
      json(200, turn({ text: 'Check-in is from 2:00 PM.', sources: [{ id: 'timings.check_in_out', title: 'Check-in and check-out times' }], suggestions: ['Can I check in early?'] })),
    )

    expect(await screen.findByText('Check-in is from 2:00 PM.')).toBeInTheDocument()
    expect(screen.queryByRole('status', { name: 'Assistant is typing' })).not.toBeInTheDocument()
    expect(screen.getByRole('log', { name: 'Conversation' })).toHaveAttribute('aria-busy', 'false')
    expect(screen.getByText(/Check-in and check-out times/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Can I check in early?' })).toBeInTheDocument()
    expect(screen.getByText('AI assistant')).toBeInTheDocument()
  })

  it('shows a retryable error when the backend is unreachable, and retries without duplicating the question', async () => {
    const calls = mockApi({
      messages: [() => Promise.reject(new TypeError('Failed to fetch')), () => json(200, turn({ text: 'Yes, we have an outdoor pool.' }))],
    })
    renderApp()

    const user = await ask('Do you have a pool?')

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent("couldn't reach the hotel assistant")
    await user.click(within(alert).getByRole('button', { name: 'Try again' }))

    expect(await screen.findByText('Yes, we have an outdoor pool.')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.getAllByText('Do you have a pool?')).toHaveLength(1)
    expect(messageCalls(calls)).toHaveLength(2)
  })

  it('shows a friendly message for server errors without leaking details', async () => {
    mockApi({ messages: [() => json(500, { error: { code: 'INTERNAL_ERROR', message: 'Traceback...', request_id: 'r' } })] })
    renderApp()

    await ask('hello')

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Something went wrong on our side')
    expect(alert).not.toHaveTextContent('Traceback')
  })

  it('explains rate limiting and lets the guest retry', async () => {
    mockApi({ messages: [() => json(429, { error: { code: 'RATE_LIMITED', message: 'Too many', request_id: 'r' } }, { 'Retry-After': '7' })] })
    renderApp()

    await ask('hello')

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent("You're sending messages quickly")
    expect(within(alert).getByRole('button', { name: 'Try again' })).toBeInTheDocument()
  })

  it('shows the FAQ-mode notice when the AI is degraded', async () => {
    mockApi({
      messages: [() => json(200, turn({ text: 'Breakfast is served 7–10:30 AM.' }, { mode: 'offline', notice: 'Our AI assistant is temporarily unavailable.' }))],
    })
    renderApp()

    await ask('Is breakfast included?')

    expect(await screen.findByText('Our AI assistant is temporarily unavailable.')).toBeInTheDocument()
    expect(screen.getByText('FAQ mode')).toBeInTheDocument()
  })

  it('keeps one server-side conversation and sends only the new message', async () => {
    const calls = mockApi({
      messages: [() => json(200, turn({ text: 'The Deluxe Pool View Room sleeps three.' })), () => json(200, turn({ text: 'Yes, breakfast is included in that room.' }))],
    })
    renderApp()

    await ask('Which room is suitable for three guests?')
    await screen.findByText('The Deluxe Pool View Room sleeps three.')
    await ask('Does it include breakfast?')
    await screen.findByText('Yes, breakfast is included in that room.')

    expect(calls.filter((c) => c.path.endsWith('/conversations'))).toHaveLength(1)
    const [first, second] = messageCalls(calls)
    expect(first.path).toBe(second.path)
    expect(second.path).toContain('/api/v1/hotels/hotel-goa-001/conversations/conv_1/messages')
    expect(second.body).toEqual({ message: 'Does it include breakfast?', locale: 'en' })
  })

  it('recreates an expired conversation transparently', async () => {
    const calls = mockApi({
      messages: [
        () => json(404, { error: { code: 'CONVERSATION_NOT_FOUND', message: 'expired', request_id: 'r' } }),
        () => json(200, turn({ text: 'Check-out is by 11:00 AM.' })),
      ],
    })
    renderApp()

    await ask('What time is check-out?')

    expect(await screen.findByText('Check-out is by 11:00 AM.')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(calls.filter((c) => c.path.endsWith('/conversations'))).toHaveLength(2)
    expect(messageCalls(calls)[1].path).toContain('/conversations/conv_2/messages')
  })
})

describe('Availability flow', () => {
  it('collects details in a form, calls the availability API and renders room cards', async () => {
    const calls = mockApi({
      messages: [
        () =>
          json(
            200,
            turn({ type: 'collect_booking_details', text: 'Which dates would you like?', booking_prefill: { check_in: null, check_out: null, adults: 2, children: null } }),
          ),
      ],
      availability: [() => json(200, availabilityResult)],
    })
    renderApp()
    const user = await ask('Do you have rooms available?')

    const form = await screen.findByRole('form', { name: 'Check availability' })
    await waitFor(() => expect(within(form).getByLabelText('Check-in')).toHaveFocus())
    const submit = within(form).getByRole('button', { name: /check/i })
    expect(submit).toBeDisabled()

    fireEvent.change(within(form).getByLabelText('Check-in'), { target: { value: '2026-10-07' } })
    fireEvent.change(within(form).getByLabelText('Check-out'), { target: { value: '2026-10-09' } })
    expect(within(form).getByRole('button', { name: 'Check 2 nights' })).toBeEnabled()
    await user.click(within(form).getByRole('button', { name: 'Check 2 nights' }))

    expect(await screen.findByText('Deluxe Pool View Room')).toBeInTheDocument()
    expect(screen.getByText('₹15,600')).toBeInTheDocument()
    expect(screen.getByText('Breakfast included')).toBeInTheDocument()
    expect(screen.getByText('Only 2 left')).toBeInTheDocument()
    expect(screen.queryByRole('form', { name: 'Check availability' })).not.toBeInTheDocument()
    const availabilityCall = calls.find((c) => c.path.endsWith('/availability'))
    expect(availabilityCall?.path).toContain('/conversations/conv_1/availability')
    expect(availabilityCall?.body).toEqual({ check_in: '2026-10-07', check_out: '2026-10-09', adults: 2, children: 0 })
  })

  it('validates dates before submitting and shows backend validation errors in the form', async () => {
    mockApi({
      availability: [() => json(422, { error: { code: 'INVALID_BOOKING_DETAILS', message: 'Bookings open 12 months in advance.', request_id: 'r' } })],
    })
    renderApp()
    const user = userEvent.setup()

    // Two entry points now carry this label: the landing quick action and the composer button.
    await user.click(within(screen.getByRole('group', { name: 'Quick actions' })).getByRole('button', { name: /Check availability/ }))
    const form = await screen.findByRole('form', { name: 'Check availability' })
    fireEvent.change(within(form).getByLabelText('Check-in'), { target: { value: '2026-10-09' } })
    fireEvent.change(within(form).getByLabelText('Check-out'), { target: { value: '2026-10-08' } })

    expect(within(form).getByRole('alert')).toHaveTextContent('Check-out must be after check-in.')
    expect(within(form).getByRole('button', { name: 'Check availability' })).toBeDisabled()

    fireEvent.change(within(form).getByLabelText('Check-out'), { target: { value: '2026-10-10' } })
    await user.click(within(form).getByRole('button', { name: 'Check 1 night' }))

    await waitFor(() => expect(within(form).getByRole('alert')).toHaveTextContent('Bookings open 12 months in advance.'))
    expect(within(form).getByLabelText('Check-in')).toHaveValue('2026-10-09')
  })

  it('shows a sold-out state with a way to try other dates', async () => {
    mockApi({
      messages: [
        () =>
          json(
            200,
            turn({
              type: 'availability',
              text: 'Sorry, all suitable rooms are sold out.',
              availability: { ...availabilityResult, available: false, rooms: [], sold_out_room_names: ['Family Suite'], message: 'Sorry, all suitable rooms are sold out.' },
            }),
          ),
      ],
    })
    renderApp()
    const user = await ask('Family suite this Saturday for 5?')

    expect(await screen.findByText('Sorry, all suitable rooms are sold out.')).toBeInTheDocument()
    expect(screen.getByText(/Sold out for these dates: Family Suite/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Try different dates' }))
    expect(await screen.findByRole('form', { name: 'Check availability' })).toBeInTheDocument()
  })
})

describe('Localisation, branding and connectivity', () => {
  it('switches the interface to Hindi and sends the locale with messages', async () => {
    const calls = mockApi({ messages: [() => json(200, turn({ text: 'चेक-इन दोपहर 2 बजे से है।' }))] })
    renderApp()
    const user = userEvent.setup()

    await user.selectOptions(await screen.findByLabelText('Language'), 'hi')

    expect(document.documentElement.lang).toBe('hi')
    expect(screen.getByRole('button', { name: 'संदेश भेजें' })).toBeInTheDocument()
    await user.type(screen.getByLabelText('प्रश्न पूछें'), 'चेक-इन कब है?')
    await user.click(screen.getByRole('button', { name: 'संदेश भेजें' }))

    expect(await screen.findByText('चेक-इन दोपहर 2 बजे से है।')).toBeInTheDocument()
    expect(messageCalls(calls)[0].body).toEqual({ message: 'चेक-इन कब है?', locale: 'hi' })
  })

  it('shows hotel branding from the API', async () => {
    mockApi({})
    renderApp()
    expect(await screen.findByRole('heading', { name: 'The Palm Grove Resort' })).toBeInTheDocument()
    expect(screen.getByText('Guest Assistant · Rooms, amenities & availability')).toBeInTheDocument()
    // The landing repeats the property name above the value proposition.
    expect(await screen.findByText('Your stay, made easier')).toBeInTheDocument()
  })

  it('announces when the guest goes offline', async () => {
    mockApi({})
    renderApp()
    act(() => {
      window.dispatchEvent(new Event('offline'))
    })
    expect(await screen.findByText(/You're offline/)).toBeInTheDocument()
    act(() => {
      window.dispatchEvent(new Event('online'))
    })
    await waitFor(() => expect(screen.queryByText(/You're offline/)).not.toBeInTheDocument())
  })
})

describe('Hardening against API failures and unexpected responses', () => {
  it('retries once, automatically, when the conversation is busy', async () => {
    const calls = mockApi({
      messages: [
        () => json(409, { error: { code: 'CONVERSATION_BUSY', message: 'busy', request_id: 'r' } }, { 'Retry-After': '0' }),
        () => json(200, turn({ text: 'Check-in is from 2 PM.' })),
      ],
    })
    renderApp()

    await ask('Check-in time?')

    expect(await screen.findByText('Check-in is from 2 PM.')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(messageCalls(calls)).toHaveLength(2)
  })

  it('explains a temporary outage (503) and offers a retry', async () => {
    mockApi({ messages: [() => json(503, { error: { code: 'STATE_UNAVAILABLE', message: 'internal detail', request_id: 'r' } }, { 'Retry-After': '5' })] })
    renderApp()

    await ask('hello')

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('temporarily unavailable')
    expect(alert).not.toHaveTextContent('internal detail')
    expect(within(alert).getByRole('button', { name: 'Try again' })).toBeInTheDocument()
  })

  it('survives a 200 response with an unexpected body (e.g. a proxy page)', async () => {
    mockApi({
      messages: [
        () => new Response('<html>Gateway</html>', { status: 200, headers: { 'Content-Type': 'text/html' } }),
        () => json(200, { unexpected: true }),
      ],
    })
    renderApp()

    const user = await ask('hello')
    expect(await screen.findByRole('alert')).toHaveTextContent('Something went wrong on our side')
    await user.click(within(screen.getByRole('alert')).getByRole('button', { name: 'Try again' }))
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Something went wrong on our side'))
  })

  it('shows a timeout message when the request is aborted', async () => {
    mockApi({ messages: [() => Promise.reject(new DOMException('aborted', 'AbortError'))] })
    renderApp()

    await ask('hello')

    expect(await screen.findByRole('alert')).toHaveTextContent('taking too long')
  })

  it('does not offer a pointless retry for a message the server rejects as too large', async () => {
    mockApi({ messages: [() => json(413, { error: { code: 'PAYLOAD_TOO_LARGE', message: 'too big', request_id: 'r' } })] })
    renderApp()

    await ask('hello')

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('too long')
    expect(within(alert).queryByRole('button', { name: 'Try again' })).not.toBeInTheDocument()
  })

  it('caps input length and renders very long replies and degradation metadata without breaking', async () => {
    const longText = 'Breakfast details. '.repeat(150)
    mockApi({
      messages: [
        () => json(200, turn({ text: longText }, { mode: 'offline', meta: { trace_id: 't', prompt_version: null, tool_schema_version: null, knowledge_version: 'k', degradation: { code: 'LLM_TIMEOUT', message: 'slow model' } } })),
      ],
    })
    renderApp()

    const input = screen.getByLabelText('Ask a question') as HTMLTextAreaElement
    expect(input.maxLength).toBe(1000)
    await ask('Breakfast?')

    expect(await screen.findByText((content) => content.startsWith('Breakfast details.'))).toBeInTheDocument()
  })
})

describe('Landing experience and conversational routing', () => {
  it('opens on a welcome screen with quick actions, examples and a usable input', async () => {
    mockApi({})
    renderApp()

    expect(await screen.findByText('Your stay, made easier')).toBeInTheDocument()
    const actions = screen.getByRole('group', { name: 'Quick actions' })
    expect(within(actions).getAllByRole('button').map((b) => b.textContent)).toEqual([
      '🛏Rooms',
      '🍳Breakfast',
      '🏊Amenities',
      '📅Check availability',
      '📋Policies',
    ])
    expect(screen.getByRole('button', { name: 'What time is check-in?' })).toBeInTheDocument()
    expect(screen.getByLabelText('Ask a question')).toBeEnabled() // the guest can type straight away
    expect(screen.queryByRole('form', { name: 'Check availability' })).not.toBeInTheDocument()
  })

  it('sends the matching question when a quick action is used and then shows the conversation', async () => {
    const calls = mockApi({ messages: [() => json(200, turn({ text: 'Breakfast is included in the Deluxe rate.' }))] })
    renderApp()
    const user = userEvent.setup()

    await user.click(await screen.findByRole('button', { name: /Breakfast/ }))

    expect(await screen.findByText('Breakfast is included in the Deluxe rate.')).toBeInTheDocument()
    expect(messageCalls(calls)[0].body).toMatchObject({ message: 'Is breakfast included?' })
    expect(screen.queryByText('Your stay, made easier')).not.toBeInTheDocument() // landing gives way to the conversation
    expect(screen.getByText('Is breakfast included?')).toBeInTheDocument() // the guest's question is shown
  })

  it('opens the availability form only from the explicit quick action', async () => {
    mockApi({})
    renderApp()
    const user = userEvent.setup()

    await user.click(within(screen.getByRole('group', { name: 'Quick actions' })).getByRole('button', { name: /Check availability/ }))

    expect(await screen.findByRole('form', { name: 'Check availability' })).toBeInTheDocument()
  })

  it('never shows the availability form for greetings or capability questions', async () => {
    const calls = mockApi({
      messages: [
        () => json(200, turn({ type: 'clarification', text: 'Hello! Welcome to The Palm Grove Resort. 👋' })),
        () => json(200, turn({ type: 'clarification', text: 'I can help with rooms, amenities, breakfast, policies and availability.' })),
      ],
    })
    renderApp()

    const user = await ask('hi')
    expect(await screen.findByText(/Welcome to The Palm Grove Resort/)).toBeInTheDocument()
    await user.type(screen.getByLabelText('Ask a question'), 'how can you help me?')
    await user.click(screen.getByRole('button', { name: 'Send message' }))

    expect(await screen.findByText(/I can help with rooms/)).toBeInTheDocument()
    expect(screen.queryByRole('form', { name: 'Check availability' })).not.toBeInTheDocument()
    expect(messageCalls(calls)).toHaveLength(2) // both went to the backend as ordinary turns
  })

  it('keeps context across a conversational turn and a follow-up', async () => {
    const calls = mockApi({
      messages: [
        () => json(200, turn({ text: 'The Deluxe Pool View Room sleeps 3 guests.' })),
        () => json(200, turn({ type: 'clarification', text: "You're welcome! Let me know if there's anything else." })),
        () => json(200, turn({ text: 'Yes, breakfast is included for the Deluxe Pool View Room.' })),
      ],
    })
    renderApp()

    const user = await ask('Which room is suitable for 3 guests?')
    await screen.findByText('The Deluxe Pool View Room sleeps 3 guests.')
    await user.type(screen.getByLabelText('Ask a question'), 'thanks')
    await user.click(screen.getByRole('button', { name: 'Send message' }))
    await screen.findByText(/You're welcome/)
    await user.type(screen.getByLabelText('Ask a question'), 'does it include breakfast?')
    await user.click(screen.getByRole('button', { name: 'Send message' }))

    expect(await screen.findByText(/breakfast is included for the Deluxe Pool View Room/)).toBeInTheDocument()
    const sent = messageCalls(calls).map((c) => (c.body as { message: string }).message)
    expect(sent).toEqual(['Which room is suitable for 3 guests?', 'thanks', 'does it include breakfast?'])
    // One server-side conversation throughout: the client never restarts it after small talk.
    expect(new Set(calls.filter((c) => c.path.endsWith('/conversations')).map((c) => c.path)).size).toBe(1)
  })
})
