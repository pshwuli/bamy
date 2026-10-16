import { http } from './http'
import { P } from './paths'
import type { LoginResult, User } from './types'

export const authApi = {
  setupStatus: () => http.get<{ initialized: boolean }>(P.setupStatus, undefined, { skipAuth: true, silent: true }),
  setup: (body: { username: string; password: string }) =>
    http.post<{ user_id: number; username: string; role: string }>(P.setup, body, { skipAuth: true, silent: true }),
  login: (body: { username: string; password: string; two_factor_code?: string }) =>
    http.post<LoginResult>(P.auth.login, body, { skipAuth: true, silent: true }),
  register: (body: { username: string; password: string; email?: string; invite_code?: string }) =>
    http.post<{ user_id: number; username: string; status: string }>(P.auth.register, body, { skipAuth: true, silent: true }),
  logout: (all_devices = false) => http.post<void>(P.auth.logout, { all_devices }, { silent: true }),
  me: () => http.get<User>(P.auth.me, undefined, { silent: true }),
  changePassword: (body: { old_password: string; new_password: string }) => http.post<void>(P.auth.changePassword, body),
  forgotPassword: (email: string) => http.post<void>(P.auth.forgotPassword, { email }, { skipAuth: true, silent: true }),
  resetPassword: (body: { token: string; new_password: string }) =>
    http.post<void>(P.auth.resetPassword, body, { skipAuth: true, silent: true }),
  tfaEnable: () => http.post<{ secret?: string; qr_code?: string; otpauth_uri?: string; otpauth_url?: string }>(P.auth.tfaEnable),
  tfaVerify: (code: string) => http.post<{ recovery_codes?: string[] }>(P.auth.tfaVerify, { code }),
  tfaDisable: (code: string) => http.post<void>(P.auth.tfaDisable, { code }),
  tfaRecovery: (code: string) => http.post<{ recovery_codes: string[] }>(P.auth.tfaRecovery, { code }),
}
