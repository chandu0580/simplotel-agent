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

export interface ResponseMeta {
  trace_id: string
  prompt_version: string | null
  tool_schema_version: string | null
  knowledge_version: string | null
}

export interface ConversationTurnResponse {
  request_id: string
  conversation_id: string
  mode: 'ai' | 'offline'
  reply: ChatReply
  notice: string | null
  meta: ResponseMeta
}

export interface ConversationCreated {
  conversation_id: string
  hotel_id: string
  channel: string
  locale: string | null
  expires_at: string
}

export interface AvailabilityRequest {
  check_in: string
  check_out: string
  adults: number
  children: number
}

export interface HotelInfo {
  hotel: {
    id: string
    name: string
    tagline: string
    city: string
    phone: string
    email: string
    whatsapp: string
    currency: string
    check_in_time: string
    check_out_time: string
    languages: string[]
    brand: { assistant_name: string; primary_color: string }
  }
  today: string
  max_guests: number
  suggested_questions: string[]
  features: { ai_assistant: boolean }
}
