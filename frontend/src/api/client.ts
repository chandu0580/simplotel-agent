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

export type ApiErrorKind =
  | 'network'
  | 'timeout'
  | 'validation'
  | 'rate_limited'
  | 'busy'
  | 'too_large'
  | 'not_found'
  | 'unavailable'
  | 'unexpected'
  | 'server'

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
    return this.kind !== 'validation' && this.kind !== 'not_found' && this.kind !== 'too_large'
  }
}

interface ErrorEnvelope {
  error?: { code?: string; message?: string; request_id?: string }
}

/** Rejects bodies that don't have the shape the UI renders (proxy error pages, version skew). */
export type Validator<T> = (body: unknown) => body is T

const isObject = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null

export const isTurnResponse: Validator<ConversationTurnResponse> = (b): b is ConversationTurnResponse =>
  isObject(b) &&
  typeof b.conversation_id === 'string' &&
  (b.mode === 'ai' || b.mode === 'offline') &&
  isObject(b.reply) &&
  typeof b.reply.type === 'string' &&
  typeof b.reply.text === 'string' &&
  Array.isArray(b.reply.sources) &&
  Array.isArray(b.reply.suggestions)

const isConversationCreated: Validator<ConversationCreated> = (b): b is ConversationCreated =>
  isObject(b) && typeof b.conversation_id === 'string'
const isHotelInfo: Validator<HotelInfo> = (b): b is HotelInfo => isObject(b) && isObject(b.hotel) && typeof b.hotel.name === 'string'
const isAvailability: Validator<AvailabilityResult> = (b): b is AvailabilityResult =>
  isObject(b) && typeof b.available === 'boolean' && Array.isArray(b.rooms) && typeof b.message === 'string'

async function request<T>(path: string, init: RequestInit = {}, validate?: Validator<T>): Promise<T> {
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
  if (response.ok) {
    if (body && (!validate || validate(body))) return body
    throw new ApiError('unexpected', 'unexpected', { status: response.status })
  }

  const code = body?.error?.code
  const opts = { status: response.status, code, requestId: body?.error?.request_id ?? response.headers.get('X-Request-ID') ?? undefined }
  // Server messages are shown only for validation errors, which are written for guests. Everything
  // else maps to a localised message in the UI, so internals never reach the page.
  if (response.status === 422) throw new ApiError('validation', body?.error?.message ?? 'validation', opts)
  if (response.status === 429) {
    throw new ApiError('rate_limited', 'rate_limited', { ...opts, retryAfterSeconds: Number(response.headers.get('Retry-After') ?? 5) })
  }
  if (response.status === 404) throw new ApiError('not_found', 'not_found', opts)
  if (response.status === 409) {
    throw new ApiError('busy', 'busy', { ...opts, retryAfterSeconds: Number(response.headers.get('Retry-After') ?? 2) })
  }
  if (response.status === 413) throw new ApiError('too_large', 'too_large', opts)
  if (response.status === 503) throw new ApiError('unavailable', 'unavailable', opts)
  throw new ApiError('server', 'server', opts)
}

const hotelPath = `/api/v1/hotels/${encodeURIComponent(HOTEL_ID)}`

export const api = {
  hotel: () => request<HotelInfo>(hotelPath, {}, isHotelInfo),
  createConversation: (locale: string) =>
    request<ConversationCreated>(`${hotelPath}/conversations`, { method: 'POST', body: JSON.stringify({ locale }) }, isConversationCreated),
  sendMessage: (conversationId: string, message: string, locale: string) =>
    request<ConversationTurnResponse>(`${hotelPath}/conversations/${encodeURIComponent(conversationId)}/messages`, {
      method: 'POST',
      body: JSON.stringify({ message, locale }),
    }, isTurnResponse),
  checkAvailability: (conversationId: string, payload: AvailabilityRequest) =>
    request<AvailabilityResult>(`${hotelPath}/conversations/${encodeURIComponent(conversationId)}/availability`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }, isAvailability),
}
