import { bootFetch } from "./boot"

const API_BASE = "/api"

// Wherever a request sends the legacy tz_offset (a single fixed offset), pair
// it with the IANA timezone name so the backend can bucket reading days
// DST-correctly per timestamp instead of applying today's offset to all of
// history (a January 03:39 CET read otherwise lands on the wrong day when
// queried in summer, breaking streaks).
function withTz(path: string): string {
  if (!path.includes("tz_offset=") || /[?&]tz=/.test(path)) return path
  try {
    const zone = Intl.DateTimeFormat().resolvedOptions().timeZone
    return zone ? `${path}&tz=${encodeURIComponent(zone)}` : path
  } catch {
    return path
  }
}

// The token stopped working. While impersonating, that is the one-hour
// impersonation token running out: go back to the admin's own sign-in
// (kept in sessionStorage by AuthContext) instead of the login page. If the
// admin's token is dead too, its own 401 lands on /login as usual.
function signedOut() {
  const adminToken = sessionStorage.getItem("tome_admin_token")
  sessionStorage.removeItem("tome_admin_token")
  sessionStorage.removeItem("tome_impersonated_username")
  if (adminToken) {
    localStorage.setItem("tome_token", adminToken)
    window.location.href = "/"
    return
  }
  localStorage.removeItem("tome_token")
  window.location.href = "/login"
}

// `noRedirect`: a 401 is the caller's answer, not an expired session (the
// login form shows "wrong password" inline instead of reloading /login).
async function request<T>(path: string, options?: RequestInit, noRedirect = false): Promise<T> {
  path = withTz(path)
  const token = localStorage.getItem("tome_token")
  const headers: HeadersInit = {
    "Content-Type": "application/json",
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...options?.headers,
  }
  const res = await bootFetch(path, options?.method, () => fetch(`${API_BASE}${path}`, { ...options, headers }))
  if (res.status === 401 && !noRedirect) {
    signedOut()
    throw new Error("Unauthorized")
  }
  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: res.statusText }))
    throw Object.assign(new Error(error.detail ?? "Request failed"), { status: res.status })
  }
  if (res.status === 204 || res.headers.get("content-length") === "0") {
    return undefined as T
  }
  return res.json()
}

async function requestWithHeaders<T>(
  path: string,
  options?: RequestInit,
): Promise<{ data: T; headers: Headers }> {
  const token = localStorage.getItem("tome_token")
  const headers: HeadersInit = {
    "Content-Type": "application/json",
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...options?.headers,
  }
  const res = await bootFetch(path, options?.method, () => fetch(`${API_BASE}${path}`, { ...options, headers }))
  if (res.status === 401) {
    signedOut()
    throw new Error("Unauthorized")
  }
  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(error.detail ?? "Request failed")
  }
  const data: T =
    res.status === 204 || res.headers.get("content-length") === "0"
      ? (undefined as T)
      : await res.json()
  return { data, headers: res.headers }
}

export const api = {
  get: <T>(path: string, signal?: AbortSignal) => request<T>(path, { signal }),
  getWithHeaders: <T>(path: string, signal?: AbortSignal) =>
    requestWithHeaders<T>(path, { signal }),
  post: <T>(path: string, body?: unknown, opts?: { noRedirect?: boolean }) =>
    request<T>(path, { method: "POST", body: JSON.stringify(body) }, opts?.noRedirect),
  // keepalive: the request survives the page being closed or navigated away.
  put: <T>(path: string, body?: unknown, opts?: { keepalive?: boolean }) =>
    request<T>(path, { method: "PUT", body: JSON.stringify(body), keepalive: opts?.keepalive }),
  patch: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "PATCH", body: JSON.stringify(body) }),
  delete: <T>(path: string) => request<T>(path, { method: "DELETE" }),
  upload: <T>(path: string, form: FormData) => {
    const token = localStorage.getItem("tome_token")
    return fetch(`${API_BASE}${path}`, {
      method: "POST",
      headers: token ? { Authorization: `Bearer ${token}` } : {},
      body: form,
    }).then(async res => {
      if (!res.ok) {
        const error = await res.json().catch(() => ({ detail: res.statusText }))
        throw new Error(error.detail ?? "Upload failed")
      }
      return res.json() as Promise<T>
    })
  },
}
