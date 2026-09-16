// Mirrors backend/app/schemas.py

export type ReplyType = 'answer' | 'clarification' | 'fallback' | 'availability' | 'collect_booking_details'

export interface RoomOffer {
  room_id: string
  name: string
  description: string
  beds: string
  size_sqm: number
  max_occupancy: number
  breakfast_included: boolean
  rooms_left: number
  nightly_rate: number
  total_price: number
  currency: string
  features: string[]
}

export interface AvailabilityResult {
  check_in: string
  check_out: string
  nights: number
  adults: number
  children: number
  available: boolean
  rooms: RoomOffer[]
  sold_out_room_names: string[]
  message: string
  season_label: string | null
}

export interface BookingDetails {
  check_in: string | null
  check_out: string | null
  adults: number | null
  children: number | null
}

export interface Source {
  id: string
  title: string
}

export interface ChatReply {
  type: ReplyType
  text: string
  sources: Source[]
  suggestions: string[]
  availability: AvailabilityResult | null
  booking_prefill: BookingDetails | null
  form_error: string | null
}

export interface ChatResponse {
  request_id: string
  mode: 'ai' | 'offline'
  reply: ChatReply
  notice: string | null
}

export interface HistoryItem {
  role: 'user' | 'assistant'
  content: string
}

export interface ChatRequest {
  message: string
  history: HistoryItem[]
  booking_context: Partial<BookingDetails> | null
}

export interface AvailabilityRequest {
  check_in: string
  check_out: string
  adults: number
  children: number
}

export interface HotelInfo {
  hotel: {
    name: string
    tagline: string
    phone: string
    email: string
    whatsapp: string
    currency: string
    check_in_time: string
    check_out_time: string
  }
  today: string
  max_guests: number
  suggested_questions: string[]
}
