/** auth 认证域 API（/auth）。 */
import { http } from './client'
import type { MeOut, TokenOut, UserOut } from './types'

export function login(body: { login_name: string; password: string }) {
  return http.post<never, TokenOut>('/auth/login', body)
}

export function fetchMe() {
  return http.get<never, MeOut>('/auth/me')
}

export function logout() {
  return http.post<never, { code: string }>('/auth/logout')
}

export function listUsers() {
  return http.get<never, UserOut[]>('/auth/users')
}
