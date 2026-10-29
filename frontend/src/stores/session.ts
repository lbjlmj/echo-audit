/** 会话 store：登录态 + 权限矩阵（前端仅兜底，后端为准——AGENTS.md 硬约束 5）。 */
import { defineStore } from 'pinia'
import * as authApi from '@/api/auth'
import { LEVEL_ORDER } from '@/api/types'

export const useSessionStore = defineStore('session', {
  state: () => ({
    userId: '' as string,
    userName: '' as string,
    loginName: '' as string,
    globalRole: '' as string, // 合伙人/项目经理/审计员/质控专员
    token: localStorage.getItem('echo_token') ?? '' as string,
    permissions: {} as Record<string, string>,
  }),
  getters: {
    isLoggedIn: (s) => Boolean(s.token),
  },
  actions: {
    async login(loginName: string, password: string) {
      const token = await authApi.login({ login_name: loginName, password })
      this.token = token.access_token
      localStorage.setItem('echo_token', this.token)
      await this.fetchMe()
    },
    async fetchMe() {
      const payload = await authApi.fetchMe()
      const me = payload.user
      this.userId = me.id
      this.userName = me.name
      this.loginName = me.login_name
      this.globalRole = me.global_role
      this.permissions = payload.permissions ?? {}
    },
    /** 权限兜底判断：当前角色对权限码是否具备不低于 level 的等级 */
    can(perm: string, level: 'NONE' | 'VIEW' | 'OPERATE' | 'PROPOSE' | 'APPROVE') {
      const got = this.permissions[perm]
      if (!got) return false
      return LEVEL_ORDER.indexOf(got as never) >= LEVEL_ORDER.indexOf(level)
    },
    async logout() {
      try {
        await authApi.logout()
      } finally {
        this.token = ''
        this.permissions = {}
        localStorage.removeItem('echo_token')
      }
    },
  },
})
