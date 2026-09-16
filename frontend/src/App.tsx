import { useEffect, useState } from 'react'
import { api } from './api/client'
import type { HotelInfo } from './api/types'
import { Composer } from './components/Composer'
import { MessageList } from './components/MessageList'
import { toISODate } from './format'
import { useChat, welcomeMessage } from './hooks/useChat'

const DEFAULT_HOTEL_NAME = 'The Palm Grove Resort'
const DEFAULT_SUGGESTIONS = ['What time is check-in?', 'Is breakfast included?', 'Check room availability']

export default function App() {
  const [hotel, setHotel] = useState<HotelInfo | null>(null)
  const chat = useChat([welcomeMessage(DEFAULT_HOTEL_NAME, DEFAULT_SUGGESTIONS)])

  useEffect(() => {
    // The chat still works if this fails; the header just uses defaults.
    api.hotel().then(setHotel).catch(() => undefined)
  }, [])

  const hotelName = hotel?.hotel.name ?? DEFAULT_HOTEL_NAME
  const today = hotel?.today ?? toISODate(new Date())

  return (
    <div className="app">
      <main className="chat" aria-label={`${hotelName} guest assistant`}>
        <header className="chat__header">
          <div className="chat__avatar" aria-hidden="true">
            🌴
          </div>
          <div className="chat__title">
            <h1>{hotelName}</h1>
            <p>{hotel?.hotel.tagline ?? 'Guest assistant'}</p>
          </div>
          {chat.mode && (
            <span className={`status status--${chat.mode}`} title={chat.mode === 'ai' ? 'AI assistant online' : 'Answering from hotel FAQ'}>
              {chat.mode === 'ai' ? 'AI assistant' : 'FAQ mode'}
            </span>
          )}
        </header>

        <MessageList
          messages={chat.messages}
          pending={chat.pending}
          hotel={hotel}
          today={today}
          onSuggestion={chat.send}
          onRetry={chat.retry}
          onOpenBookingForm={chat.openBookingForm}
          onCheckAvailability={chat.checkAvailability}
        />

        <Composer pending={chat.pending} onSend={chat.send} onOpenBookingForm={chat.openBookingForm} />
        <p className="disclaimer">
          AI answers are based on hotel information and may occasionally be incomplete. Please confirm important details with the front desk.
        </p>
      </main>
    </div>
  )
}
