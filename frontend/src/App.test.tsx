import { render, screen, waitFor, within, fireEvent } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import App from './App'
import type { AvailabilityResult, ChatReply, ChatResponse } from './api/types'

type Handler = (body: unknown) => Promise<Response> | Response

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

const hotelInfo = {
  hotel: {
    name: 'The Palm Grove Resort',
    tagline: 'A beachside retreat',
    phone: '+91 832 555 0142',
    email: 'stay@palmgroveresort.example',
    whatsapp: '+91 98220 55501',
    currency: 'INR',
    check_in_time: '14:00',
    check_out_time: '11:00',
  },
  today: '2026-10-05',
  max_guests: 5,
  suggested_questions: [],
}

function reply(overrides: Partial<ChatReply>): ChatReply {
  return {
    type: 'answer',
    text: '',
    sources: [],
    suggestions: [],
    availability: null,
    booking_prefill: null,
    form_error: null,
    ...overrides,
  }
}

function chatResponse(r: Partial<ChatReply>, extra: Partial<ChatResponse> = {}): ChatResponse {
  return { request_id: 'req1', mode: 'ai', notice: null, reply: reply(r), ...extra }
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

/** Routes fetch calls by path and records request bodies. */
function mockApi(routes: { chat?: Handler[]; availability?: Handler[] }) {
  const calls: { path: string; body: unknown }[] = []
  const queues = { chat: [...(routes.chat ?? [])], availability: [...(routes.availability ?? [])] }
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input)
    const body = init?.body ? JSON.parse(String(init.body)) : undefined
    calls.push({ path, body })
    if (path.endsWith('/api/hotel')) return json(200, hotelInfo)
    const key = path.endsWith('/api/chat') ? 'chat' : 'availability'
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

async function ask(text: string) {
  const user = userEvent.setup()
  await user.type(screen.getByLabelText('Ask a question'), text)
  await user.click(screen.getByRole('button', { name: 'Send message' }))
  return user
}

describe('Guest assistant chat', () => {
  it('shows a loading state, then the answer with its sources', async () => {
    const pending = deferred()
    mockApi({ chat: [() => pending.promise] })
    render(<App />)

    await ask('What time is check-in?')

    expect(screen.getByText('What time is check-in?')).toBeInTheDocument()
    expect(screen.getByRole('status', { name: 'Assistant is typing' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Send message' })).toBeDisabled()

    pending.resolve(
      json(
        200,
        chatResponse({
          text: 'Check-in is from 2:00 PM.',
          sources: [{ id: 'timings.check_in_out', title: 'Check-in and check-out times' }],
          suggestions: ['Can I check in early?'],
        }),
      ),
    )

    expect(await screen.findByText('Check-in is from 2:00 PM.')).toBeInTheDocument()
    expect(screen.queryByRole('status', { name: 'Assistant is typing' })).not.toBeInTheDocument()
    expect(screen.getByText(/Check-in and check-out times/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Can I check in early?' })).toBeInTheDocument()
    expect(screen.getByText('AI assistant')).toBeInTheDocument()
  })

  it('shows a retryable error when the backend is unreachable, and retries without duplicating the question', async () => {
    const calls = mockApi({
      chat: [
        () => Promise.reject(new TypeError('Failed to fetch')),
        () => json(200, chatResponse({ text: 'Yes, we have an outdoor pool.' })),
      ],
    })
    render(<App />)

    const user = await ask('Do you have a pool?')

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent("couldn't reach the hotel assistant")
    await user.click(within(alert).getByRole('button', { name: 'Try again' }))

    expect(await screen.findByText('Yes, we have an outdoor pool.')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.getAllByText('Do you have a pool?')).toHaveLength(1)
    expect(calls.filter((c) => c.path.endsWith('/api/chat'))).toHaveLength(2)
  })

  it('shows a friendly message for server errors without leaking details', async () => {
    mockApi({ chat: [() => json(500, { request_id: 'r', error: { code: 'internal_error', message: 'Traceback...' } })] })
    render(<App />)

    await ask('hello')

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Something went wrong on our side')
    expect(alert).not.toHaveTextContent('Traceback')
  })

  it('shows the FAQ-mode notice when the AI is degraded', async () => {
    mockApi({
      chat: [
        () =>
          json(
            200,
            chatResponse(
              { text: 'Breakfast is served 7–10:30 AM.' },
              { mode: 'offline', notice: 'Our AI assistant is temporarily unavailable.' },
            ),
          ),
      ],
    })
    render(<App />)

    await ask('Is breakfast included?')

    expect(await screen.findByText('Our AI assistant is temporarily unavailable.')).toBeInTheDocument()
    expect(screen.getByText('FAQ mode')).toBeInTheDocument()
  })

  it('sends conversation history and booking context on follow-up questions', async () => {
    const calls = mockApi({
      chat: [
        () =>
          json(
            200,
            chatResponse({ type: 'availability', text: availabilityResult.message, availability: availabilityResult }),
          ),
        () => json(200, chatResponse({ text: 'Yes, breakfast is included in that room.' })),
      ],
    })
    render(<App />)

    await ask('Rooms for 2 adults 7 to 9 Oct?')
    await screen.findByText('Deluxe Pool View Room')
    await ask('Does it include breakfast?')
    await screen.findByText('Yes, breakfast is included in that room.')

    const followUp = calls.filter((c) => c.path.endsWith('/api/chat'))[1].body as {
      message: string
      history: { role: string; content: string }[]
      booking_context: Record<string, unknown>
    }
    expect(followUp.message).toBe('Does it include breakfast?')
    expect(followUp.history.slice(-2)).toEqual([
      { role: 'user', content: 'Rooms for 2 adults 7 to 9 Oct?' },
      { role: 'assistant', content: availabilityResult.message },
    ])
    expect(followUp.booking_context).toEqual({ check_in: '2026-10-07', check_out: '2026-10-09', adults: 2, children: 0 })
  })
})

describe('Booking context follow-ups', () => {
  it('remembers dates from a details request so "3 adults." can complete the search', async () => {
    const calls = mockApi({
      chat: [
        () =>
          json(
            200,
            chatResponse({
              type: 'collect_booking_details',
              text: 'How many guests?',
              booking_prefill: { check_in: '2026-10-10', check_out: '2026-10-12', adults: null, children: null },
            }),
          ),
        () => json(200, chatResponse({ type: 'availability', text: availabilityResult.message, availability: availabilityResult })),
      ],
    })
    render(<App />)

    await ask('Do you have rooms for October 10 to October 12?')
    await screen.findByText('How many guests?')
    await ask('3 adults.')
    await screen.findByText('Deluxe Pool View Room')

    const followUp = calls.filter((c) => c.path.endsWith('/api/chat'))[1].body as { booking_context: Record<string, unknown> }
    expect(followUp.booking_context).toEqual({ check_in: '2026-10-10', check_out: '2026-10-12' })
  })
})

describe('Availability flow', () => {
  it('collects details in a form, calls the availability API and renders room cards', async () => {
    const calls = mockApi({
      chat: [
        () =>
          json(
            200,
            chatResponse({
              type: 'collect_booking_details',
              text: 'Which dates would you like?',
              booking_prefill: { check_in: null, check_out: null, adults: 2, children: null },
            }),
          ),
      ],
      availability: [() => json(200, availabilityResult)],
    })
    render(<App />)
    const user = await ask('Do you have rooms available?')

    const form = await screen.findByRole('form', { name: 'Check availability' })
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
    expect(calls.find((c) => c.path.endsWith('/api/availability'))?.body).toEqual({
      check_in: '2026-10-07',
      check_out: '2026-10-09',
      adults: 2,
      children: 0,
    })
  })

  it('validates dates before submitting and shows backend validation errors in the form', async () => {
    mockApi({
      availability: [
        () => json(422, { request_id: 'r', error: { code: 'invalid_booking_details', message: 'Bookings open 12 months in advance.' } }),
      ],
    })
    render(<App />)
    const user = userEvent.setup()

    await user.click(screen.getByRole('button', { name: /Check availability/ }))
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
      chat: [
        () =>
          json(
            200,
            chatResponse({
              type: 'availability',
              text: 'Sorry, all suitable rooms are sold out.',
              availability: {
                ...availabilityResult,
                available: false,
                rooms: [],
                sold_out_room_names: ['Family Suite'],
                message: 'Sorry, all suitable rooms are sold out.',
              },
            }),
          ),
      ],
    })
    render(<App />)
    const user = await ask('Family suite this Saturday for 5?')

    expect(await screen.findByText('Sorry, all suitable rooms are sold out.')).toBeInTheDocument()
    expect(screen.getByText(/Sold out for these dates: Family Suite/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Try different dates' }))
    expect(await screen.findByRole('form', { name: 'Check availability' })).toBeInTheDocument()
  })
})
