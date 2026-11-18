// Talks to the FastAPI backend.
//
// The access token lives only in this module's memory (not localStorage), so a
// malicious script can't read it out of storage. The refresh token is an
// httpOnly cookie that JavaScript can't see at all; `credentials: 'include'`
// makes the browser send it. On page load and whenever the access token
// expires, we call /auth/refresh to get a fresh one.

export const API_URL = (import.meta.env.VITE_API_URL ?? 'http://localhost:8000').replace(/\/$/, '')
export const googleLoginUrl = `${API_URL}/auth/google/login`

export type User = {
  id: number
  email: string
  full_name: string | null
  avatar_url: string | null
  email_verified: boolean
  has_password: boolean
  google_linked: boolean
  created_at: string
}

export type AuthResponse = {
  access_token: string
  token_type: string
  expires_in: number
  user: User
}

export class ApiError extends Error {
  readonly status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

let accessToken: string | null = null
let refreshInFlight: Promise<AuthResponse | null> | null = null

async function toApiError(res: Response): Promise<ApiError> {
  let message = `Request failed (${res.status})`
  try {
    const body = await res.json()
    if (typeof body.detail === 'string') {
      message = body.detail
    } else if (Array.isArray(body.detail)) {
      // FastAPI validation errors: [{ loc: [..., "password"], msg: "..." }]
      message = body.detail
        .map((d: { loc?: unknown[]; msg?: string }) => `${String(d.loc?.at(-1) ?? 'field')}: ${d.msg}`)
        .join('\n')
    }
  } catch {
    // not JSON; keep the generic message
  }
  return new ApiError(res.status, message)
}

async function send(path: string, init: RequestInit, token: string | null): Promise<Response> {
  const headers = new Headers(init.headers)
  if (init.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json')
  if (token) headers.set('Authorization', `Bearer ${token}`)
  try {
    return await fetch(`${API_URL}${path}`, { ...init, headers, credentials: 'include' })
  } catch {
    throw new ApiError(0, `Can't reach the server at ${API_URL}. Is the backend running?`)
  }
}

async function request<T>(path: string, init: RequestInit = {}, { auth = false } = {}): Promise<T> {
  let res = await send(path, init, auth ? accessToken : null)

  // Access token expired: refresh once and retry.
  if (res.status === 401 && auth) {
    const session = await refreshSession()
    if (session) res = await send(path, init, session.access_token)
  }

  if (!res.ok) throw await toApiError(res)
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

/** Returns the restored session, or null if not signed in. Concurrent calls share one request,
 *  which matters because refresh tokens are single-use (React StrictMode mounts twice in dev). */
export function refreshSession(): Promise<AuthResponse | null> {
  refreshInFlight ??= (async () => {
    try {
      const res = await send('/auth/refresh', { method: 'POST' }, null)
      if (!res.ok) {
        accessToken = null
        return null
      }
      const session = (await res.json()) as AuthResponse
      accessToken = session.access_token
      return session
    } finally {
      refreshInFlight = null
    }
  })()
  return refreshInFlight
}

function keep(session: AuthResponse): AuthResponse {
  accessToken = session.access_token
  return session
}

const post = (body?: unknown): RequestInit => ({
  method: 'POST',
  body: body === undefined ? undefined : JSON.stringify(body),
})

export const api = {
  providers: () => request<{ password: boolean; google: boolean }>('/auth/providers'),

  register: (body: { email: string; password: string; full_name?: string }) =>
    request<AuthResponse>('/auth/register', post(body)).then(keep),

  login: (email: string, password: string) =>
    request<AuthResponse>('/auth/login', post({ email, password })).then(keep),

  logout: async () => {
    try {
      await request<void>('/auth/logout', post())
    } finally {
      accessToken = null
    }
  },

  me: () => request<User>('/users/me', {}, { auth: true }),

  changePassword: (body: { current_password?: string; new_password: string }) =>
    request<void>('/auth/password', post(body), { auth: true }),

  /** Exposed for the dashboard's "inspect token" panel. Never persist it. */
  currentAccessToken: () => accessToken,
}
