import type {
  AvailabilityRequest,
  AvailabilityResult,
  ConversationCreated,
  ConversationTurnResponse,
  HotelInfo,
} from './types'

// Only the backend URL and public hotel id are configured here; provider credentials live on the server.
const API_BASE = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(/\/$/, '') ?? ''
export const HOTEL_ID = (import.meta.env.VITE_HOTEL_ID as string | undefined) ?? 'hotel-goa-001'
export const REQUEST_TIMEOUT_MS = 45_000

export type ApiErrorKind = 'network' | 'timeout' | 'validation' | 'rate_limited' | 'not_found' | 'unavailable' | 'server'

export class ApiError extends Error {
  readonly kind: ApiErrorKind
  readonly status?: number
  readonly code?: string
  readonly requestId?: string
  readonly retryAfterSeconds?: number

  constructor(
    kind: ApiErrorKind,
    message: string,
    opts: { status?: number; code?: string; requestId?: string; retryAfterSeconds?: number } = {},
  ) {
    super(message)
    this.name = 'ApiError'
    this.kind = kind
    this.status = opts.status
    this.code = opts.code
    this.requestId = opts.requestId
    this.retryAfterSeconds = opts.retryAfterSeconds
  }

  get retryable(): boolean {
    return this.kind !== 'validation' && this.kind !== 'not_found'
  }
}

interface ErrorEnvelope {
  error?: { code?: string; message?: string; request_id?: string }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS)
  let response: Response
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: { 'Content-Type': 'application/json', ...init.headers },
      signal: controller.signal,
    })
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') {
      throw new ApiError('timeout', 'timeout')
    }
    throw new ApiError('network', 'network')
  } finally {
    clearTimeout(timer)
  }

  if (response.status === 204) return undefined as T
  const body = (await response.json().catch(() => null)) as (T & ErrorEnvelope) | null
  if (response.ok && body) return body

  const code = body?.error?.code
  const opts = { status: response.status, code, requestId: body?.error?.request_id ?? response.headers.get('X-Request-ID') ?? undefined }
  // Server messages are shown only for validation errors, which are written for guests. Everything
  // else maps to a localised message in the UI, so internals never reach the page.
  if (response.status === 422) throw new ApiError('validation', body?.error?.message ?? 'validation', opts)
  if (response.status === 429) {
    throw new ApiError('rate_limited', 'rate_limited', { ...opts, retryAfterSeconds: Number(response.headers.get('Retry-After') ?? 5) })
  }
  if (response.status === 404) throw new ApiError('not_found', 'not_found', opts)
  if (response.status === 503) throw new ApiError('unavailable', 'unavailable', opts)
  throw new ApiError('server', 'server', opts)
}

const hotelPath = `/api/v1/hotels/${encodeURIComponent(HOTEL_ID)}`

export const api = {
  hotel: () => request<HotelInfo>(hotelPath),
  createConversation: (locale: string) =>
    request<ConversationCreated>(`${hotelPath}/conversations`, { method: 'POST', body: JSON.stringify({ locale }) }),
  sendMessage: (conversationId: string, message: string, locale: string) =>
    request<ConversationTurnResponse>(`${hotelPath}/conversations/${encodeURIComponent(conversationId)}/messages`, {
      method: 'POST',
      body: JSON.stringify({ message, locale }),
    }),
  checkAvailability: (conversationId: string, payload: AvailabilityRequest) =>
    request<AvailabilityResult>(`${hotelPath}/conversations/${encodeURIComponent(conversationId)}/availability`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
}
