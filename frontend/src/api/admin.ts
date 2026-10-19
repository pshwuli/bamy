import { http } from './http'
import { P } from './paths'
import type { Account, AuditLog, Id, Instance, LogEntry, Page, Query, Role, User } from './types'

export interface Overview {
  total_users: number
  total_accounts: number
  total_instances: number
  running_instances: number
  paper_instances: number
  live_instances: number
  today_orders: number
  today_volume: number
  active_alerts: number
  active_users_top?: { user_id: number; username: string; score?: number; orders?: number }[]
  system?: { cpu_percent?: number; memory_percent?: number; db_size_mb?: number; db_status?: string }
  plugin_alerts?: { plugin_id: string; kind: string; message: string; at?: string }[]
}

export interface HealthInfo {
  status: string
  version: string
  uptime: number
  checks: Record<string, string>
}

export interface AdminUser extends User {
  account_count?: number
  instance_count?: number
  binding_count?: number
  last_login_at?: string | null
}

export interface ReconcileDiff {
  id: number
  account_id: number
  account_name?: string
  type: string
  expected: unknown
  actual: unknown
  resolved: boolean
  resolved_at?: string | null
  created_at: string
  detail?: unknown
}

export interface CircuitBreaker {
  id: number
  level: 'account' | 'user' | 'global'
  target_id: number | null
  target_name?: string | null
  reason: string
  active: boolean
  status?: string
  triggered_at?: string
  created_at?: string
  recovered_at?: string | null
  recover_reason?: string | null
}

export interface ConfigItem {
  key: string
  value: unknown
  description?: string
  group?: string
  type?: string
  editable?: boolean
  hot_reload?: boolean
  default?: unknown
}

export interface Invite {
  id: number
  code: string
  max_uses: number
  used_count?: number
  role?: Role
  enabled?: boolean
  status?: string
  expires_at?: string | null
  created_at?: string
}

export interface InviteUsage {
  user_id: number
  username: string
  used_at: string
}

export interface Backup {
  id: number | string
  filename?: string
  size_bytes?: number
  size?: number
  status?: string
  created_at: string
  created_by?: string
}

export const adminApi = {
  overview: () => http.get<Overview>(P.admin.overview),
  health: () => http.get<HealthInfo>(P.health, undefined, { silent: true, skipAuth: true }),
  users: (q: Query) => http.get<Page<AdminUser>>(P.admin.users, q),
  user: (id: Id) => http.get<AdminUser>(P.admin.user(id)),
  disableUser: (id: Id) => http.post<void>(P.admin.userDisable(id)),
  enableUser: (id: Id) => http.post<void>(P.admin.userEnable(id)),
  resetPassword: (id: Id) => http.post<{ temp_password: string }>(P.admin.userResetPassword(id)),
  setRole: (id: Id, role: Role) => http.patch<void>(P.super.userRole(id), { role }),
  deleteUser: (id: Id, username: string) => http.delete<void>(P.super.user(id), { data: { confirm: username } }),
  accounts: (q: Query) => http.get<Page<Account>>(P.admin.accounts, q),
  disableAccount: (id: Id) => http.post<void>(P.admin.accountDisable(id)),
  enableAccount: (id: Id) => http.post<void>(P.admin.accountEnable(id)),
  instances: (q: Query) => http.get<Page<Instance>>(P.admin.instances, q),
  forceStop: (id: Id) => http.post<void>(P.admin.forceStop(id)),
  reconcileTrigger: (body: { account_id?: number | null; type: string }) => http.post<unknown>(P.admin.reconcileTrigger, body),
  reconcileDiffs: (q: Query) => http.get<Page<ReconcileDiff>>(P.admin.reconcileDiffs, q),
  reconcileDiff: (id: Id) => http.get<ReconcileDiff>(P.admin.reconcileDiff(id)),
  reconcileResolve: (id: Id, note?: string) => http.post<void>(P.admin.reconcileResolve(id), { note }),
  reconcileAutoFix: (id: Id) => http.post<ReconcileDiff>(P.admin.reconcileAutoFix(id)),
  circuitBreakers: (q: Query) => http.get<Page<CircuitBreaker>>(P.admin.circuitBreakers, q),
  circuitBreaker: (id: Id) => http.get<CircuitBreaker>(P.admin.circuitBreaker(id)),
  triggerBreaker: (body: { level: string; target_id: number | null; reason: string }) =>
    http.post<CircuitBreaker>(P.admin.circuitBreakers, body),
  recoverBreaker: (id: Id, reason: string) => http.post<void>(P.admin.circuitRecover(id), { reason }),
  systemLogs: (q: Query) => http.get<Page<LogEntry>>(P.admin.systemLogs, q),
  auditLogs: (q: Query) => http.get<Page<AuditLog>>(P.admin.auditLogs, q),
  auditLog: (id: Id) => http.get<AuditLog>(P.admin.auditLog(id)),
  configs: () => http.get<{ items: ConfigItem[] } | ConfigItem[] | Record<string, unknown>>(P.admin.configs),
  updateConfigs: (configs: Record<string, unknown>) => http.patch<void>(P.super.configs, { configs }),
  invites: (q: Query) => http.get<Page<Invite>>(P.admin.invites, q),
  createInvite: (body: { max_uses: number; expires_in_days: number; role: Role }) => http.post<Invite>(P.admin.invites, body),
  disableInvite: (id: Id) => http.post<void>(P.admin.inviteDisable(id)),
  enableInvite: (id: Id) => http.post<void>(P.admin.inviteEnable(id)),
  inviteUsages: (id: Id) => http.get<Page<InviteUsage> | InviteUsage[]>(P.admin.inviteUsages(id)),
  backups: (q: Query) => http.get<Page<Backup> | Backup[]>(P.super.backups, q),
  createBackup: () => http.post<Backup>(P.super.backups),
  downloadBackup: (b: Backup) => http.download(P.super.backupDownload(b.id), undefined, b.filename || `bamy-backup-${b.id}`),
  restoreBackup: (id: Id) => http.post<void>(P.super.backupRestore(id), { confirm: 'RESTORE' }),
}
