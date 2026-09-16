import type { AvailabilityRequest, AvailabilityResult, ChatRequest, ChatResponse, HotelInfo } from './types'

// Only the backend URL is configured here; the LLM key lives on the server.
const API_BASE = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(/\/$/, '') ?? ''
export const REQUEST_TIMEOUT_MS = 45_000

export type ApiErrorKind = 'network' | 'timeout' | 'validation' | 'server'

export class ApiError extends Error {
  readonly kind: ApiErrorKind
  readonly status?: number
  readonly code?: string
  readonly requestId?: string

  constructor(kind: ApiErrorKind, message: string, opts: { status?: number; code?: string; requestId?: string } = {}) {
    super(message)
    this.name = 'ApiError'
    this.kind = kind
    this.status = opts.status
    this.code = opts.code
    this.requestId = opts.requestId
  }

  get retryable(): boolean {
    return this.kind !== 'validation'
  }
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
      throw new ApiError('timeout', 'The assistant is taking too long to respond. Please try again.')
    }
    throw new ApiError('network', "We couldn't reach the hotel assistant. Check your connection and try again.")
  } finally {
    clearTimeout(timer)
  }

  const body = await response.json().catch(() => null)
  if (response.ok && body) return body as T

  const requestId = body?.request_id ?? response.headers.get('X-Request-ID') ?? undefined
  if (response.status === 422) {
    throw new ApiError('validation', body?.error?.message ?? 'Some details are invalid.', {
      status: 422,
      code: body?.error?.code,
      requestId,
    })
  }
  throw new ApiError('server', 'Something went wrong on our side. Please try again in a moment.', {
    status: response.status,
    code: body?.error?.code,
    requestId,
  })
}

export const api = {
  hotel: () => request<HotelInfo>('/api/hotel'),
  chat: (payload: ChatRequest) => request<ChatResponse>('/api/chat', { method: 'POST', body: JSON.stringify(payload) }),
  availability: (payload: AvailabilityRequest) =>
    request<AvailabilityResult>('/api/availability', { method: 'POST', body: JSON.stringify(payload) }),
}
