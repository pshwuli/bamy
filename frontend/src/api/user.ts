import { http } from './http'
import { P } from './paths'
import type { AuditLog, Id, Page, Query, User } from './types'

export interface ApiToken {
  id: number
  name: string
  scope: string
  token?: string
  token_prefix?: string
  expires_at?: string | null
  last_used_at?: string | null
  created_at?: string
}

export interface SessionInfo {
  id: number | string
  ip?: string
  user_agent?: string
  device?: string
  current?: boolean
  created_at?: string
  last_active_at?: string
}

export interface NotificationSettings {
  email_enabled: boolean
  webhook_url: string | null
  event_types: string[]
}

export interface Preferences {
  theme: 'light' | 'dark' | 'auto'
  language: 'zh-CN' | 'en-US'
  time_format: '24h' | '12h'
  timezone: string
  page_size: number
}

export const userApi = {
  profile: () => http.get<User>(P.user.profile),
  updateProfile: (body: { nickname?: string; email?: string }) => http.patch<User>(P.user.profile, body),
  auditLogs: (q: Query) => http.get<Page<AuditLog>>(P.user.auditLogs, q),
  apiTokens: () => http.get<Page<ApiToken> | ApiToken[]>(P.user.apiTokens),
  createApiToken: (body: { name: string; scope: string; expires_at?: string | null }) =>
    http.post<ApiToken>(P.user.apiTokens, body),
  revokeApiToken: (id: Id) => http.delete<void>(P.user.apiToken(id)),
  sessions: () => http.get<Page<SessionInfo> | SessionInfo[]>(P.user.sessions),
  revokeSession: (id: Id) => http.delete<void>(P.user.session(id)),
  notificationSettings: () => http.get<NotificationSettings>(P.user.notificationSettings, undefined, { silent: true }),
  updateNotificationSettings: (body: Partial<NotificationSettings>) =>
    http.patch<NotificationSettings>(P.user.notificationSettings, body),
  preferences: () => http.get<Preferences>(P.user.preferences, undefined, { silent: true }),
  updatePreferences: (body: Partial<Preferences>) => http.patch<Preferences>(P.user.preferences, body, { silent: true }),
}
