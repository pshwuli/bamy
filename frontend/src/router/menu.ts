import type { Component } from 'vue'
import {
  Odometer, Wallet, Cpu, List, DataLine, Collection, Goods, Connection, Document, Setting,
  Monitor, User, CreditCard, Operation, Box, Finished, Warning, Notebook, Tickets, Key, Tools, FolderOpened, Refresh,
} from '@element-plus/icons-vue'
import type { Role } from '@/api/types'

export interface MenuItem {
  path: string
  label: string
  icon: Component
}

export interface MenuGroup {
  label: string
  role: Role
  items: MenuItem[]
}

export const MENU: MenuGroup[] = [
  {
    label: 'nav.groupTrade',
    role: 'user',
    items: [
      { path: '/dashboard', label: 'nav.dashboard', icon: Odometer },
      { path: '/accounts', label: 'nav.accounts', icon: Wallet },
      { path: '/instances', label: 'nav.instances', icon: Cpu },
      { path: '/orders', label: 'nav.orders', icon: List },
      { path: '/paper', label: 'nav.paper', icon: DataLine },
      { path: '/risk-rules', label: 'nav.riskRules', icon: Warning },
    ],
  },
  {
    label: 'nav.groupExt',
    role: 'user',
    items: [
      { path: '/strategies', label: 'nav.strategies', icon: Collection },
      { path: '/plugins', label: 'nav.plugins', icon: Goods },
      { path: '/my-plugins', label: 'nav.myPlugins', icon: Connection },
    ],
  },
  {
    label: 'nav.groupOther',
    role: 'user',
    items: [
      { path: '/audit', label: 'nav.audit', icon: Document },
      { path: '/settings', label: 'nav.settings', icon: Setting },
    ],
  },
  {
    label: 'nav.groupAdmin',
    role: 'admin',
    items: [
      { path: '/admin/dashboard', label: 'nav.adminDashboard', icon: Monitor },
      { path: '/admin/users', label: 'nav.adminUsers', icon: User },
      { path: '/admin/accounts', label: 'nav.adminAccounts', icon: CreditCard },
      { path: '/admin/instances', label: 'nav.adminInstances', icon: Operation },
      { path: '/admin/strategies', label: 'nav.adminStrategies', icon: Box },
      { path: '/admin/plugins', label: 'nav.adminPlugins', icon: Goods },
      { path: '/admin/reconcile', label: 'nav.adminReconcile', icon: Finished },
      { path: '/admin/circuit-breakers', label: 'nav.adminBreakers', icon: Warning },
      { path: '/admin/logs', label: 'nav.adminLogs', icon: Notebook },
      { path: '/admin/audit', label: 'nav.adminAudit', icon: Tickets },
      { path: '/admin/invites', label: 'nav.adminInvites', icon: Key },
    ],
  },
  {
    label: 'nav.groupSuper',
    role: 'super_admin',
    items: [
      { path: '/super/configs', label: 'nav.superConfigs', icon: Tools },
      { path: '/super/backup', label: 'nav.superBackup', icon: FolderOpened },
      { path: '/super/update', label: 'nav.superUpdate', icon: Refresh },
    ],
  },
]
