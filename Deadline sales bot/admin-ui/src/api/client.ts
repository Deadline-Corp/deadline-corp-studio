/* Fetch-обёртка: Bearer-токен из localStorage, 401/403 → разлогин. */

const TOKEN_KEY = 'deadline_admin_token'

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}
export function setToken(t: string) {
  localStorage.setItem(TOKEN_KEY, t)
}
export function clearToken() {
  localStorage.removeItem(TOKEN_KEY)
}

export class ApiError extends Error {
  status: number
  detail: any
  constructor(status: number, detail: any) {
    super(typeof detail === 'string' ? detail : JSON.stringify(detail))
    this.status = status
    this.detail = detail
  }
}

async function request<T>(method: string, path: string, body?: any): Promise<T> {
  const token = getToken()
  const res = await fetch(`/admin/api${path}`, {
    method,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  if (res.status === 401 || res.status === 403) {
    clearToken()
    // Жёсткий редирект на логин — состояние приложения всё равно невалидно.
    if (!location.hash.includes('/login')) location.hash = '#/login'
    throw new ApiError(res.status, 'Unauthorized')
  }
  if (!res.ok) {
    let detail: any = res.statusText
    try { detail = (await res.json()).detail ?? detail } catch { /* not json */ }
    throw new ApiError(res.status, detail)
  }
  return res.json() as Promise<T>
}

export const api = {
  get: <T>(path: string) => request<T>('GET', path),
  post: <T>(path: string, body?: any) => request<T>('POST', path, body),
}
