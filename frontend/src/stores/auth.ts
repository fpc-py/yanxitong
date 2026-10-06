// Pinia：登录状态（token/用户）+ 未登录免费配额 + 身份切换时的研究视图重置

import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import api, { extractErrorMessage } from '@/api/client'
import { getToken, setToken } from '@/api/identity'
import type { AuthResponse, QuotaInfo, UserInfo } from '@/api/types'

export const useAuthStore = defineStore('auth', () => {
  // ---- state ----
  const user = ref<UserInfo | null>(null)
  const token = ref<string | null>(getToken())
  /** 匿名免费配额（/auth/me 与问答响应同步；登录后为 null） */
  const quota = ref<QuotaInfo | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)
  /** 账号弹窗开关与默认页签（全局唯一入口由左下角身份卡唤起） */
  const authOpen = ref(false)
  const authMode = ref<'register' | 'login'>('register')

  // ---- getters ----
  const isLoggedIn = computed(() => !!user.value)
  const displayName = computed(() => user.value?.username ?? '未登录访客')
  const remaining = computed(() => quota.value?.remaining ?? null)
  const quotaExhausted = computed(() => !!quota.value && quota.value.remaining <= 0)

  // ---- actions ----
  function openAuth(mode: 'register' | 'login' = 'register'): void {
    authMode.value = mode
    authOpen.value = true
    error.value = null
  }

  function closeAuth(): void {
    authOpen.value = false
  }

  function applyAuth(res: AuthResponse): void {
    setToken(res.token)
    token.value = res.token
    user.value = res.user
    quota.value = null
    error.value = null
  }

  /**
   * 身份切换（注册/登录/登出）后清空研究视图：
   * 会话与文献在服务端按身份隔离，旧身份的本地历史不应继续展示。
   * 动态导入避免与 session store 形成循环依赖。
   */
  async function resetResearchView(): Promise<void> {
    const { useSessionStore } = await import('@/stores/session')
    useSessionStore().resetSession()
  }

  async function register(username: string, password: string): Promise<boolean> {
    error.value = null
    loading.value = true
    try {
      applyAuth(await api.register(username, password))
      await resetResearchView()
      return true
    } catch (e) {
      error.value = extractErrorMessage(e)
      return false
    } finally {
      loading.value = false
    }
  }

  async function login(username: string, password: string): Promise<boolean> {
    error.value = null
    loading.value = true
    try {
      applyAuth(await api.login(username, password))
      await resetResearchView()
      return true
    } catch (e) {
      error.value = extractErrorMessage(e)
      return false
    } finally {
      loading.value = false
    }
  }

  async function logout(): Promise<void> {
    loading.value = true
    try {
      await api.logout()
    } catch {
      /* token 已失效或后端离线：本地照常退出 */
    } finally {
      setToken(null)
      token.value = null
      user.value = null
      loading.value = false
    }
    await resetResearchView()
    await refreshMe()
  }

  /** 拉取当前身份：token 有效则恢复用户，否则同步匿名配额 */
  async function refreshMe(): Promise<void> {
    try {
      const me = await api.getMe()
      user.value = me.user
      quota.value = me.quota
      if (!me.user && token.value) {
        // 本地 token 已失效（过期/被吊销）：清除，回到匿名身份
        setToken(null)
        token.value = null
      }
      error.value = null
    } catch (e) {
      // 后端离线等场景：保留本地状态，不阻塞页面
      error.value = extractErrorMessage(e)
    }
  }

  /** 问答响应携带 quota_remaining 时同步本地配额显示 */
  async function syncQuota(remainingCount: number | null | undefined): Promise<void> {
    if (remainingCount === null || remainingCount === undefined) return
    if (quota.value) {
      quota.value = {
        limit: quota.value.limit,
        used: Math.max(0, quota.value.limit - remainingCount),
        remaining: remainingCount,
      }
    } else {
      await refreshMe()
    }
  }

  return {
    // state
    user,
    token,
    quota,
    loading,
    error,
    authOpen,
    authMode,
    // getters
    isLoggedIn,
    displayName,
    remaining,
    quotaExhausted,
    // actions
    openAuth,
    closeAuth,
    register,
    login,
    logout,
    refreshMe,
    syncQuota,
    resetResearchView,
  }
})
