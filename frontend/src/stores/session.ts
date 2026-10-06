// Pinia：当前会话、问答历史、文献/结论、置信度、加载与错误、耗时统计

import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import api, { REQUEST_TIMEOUT, claimConfidence, claimText, extractErrorMessage, flattenCitations, isNotFound, isQuotaExceeded } from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import type { Citation, Claim, HealthResponse, SessionListItem, SessionStatus } from '@/api/types'

/** 一条问答记录 */
export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  citations: Citation[]
  confidence: number
  phase: string
  humanReviewRequired: boolean
  elapsedMs: number
  at: string
  error?: boolean
}

/** 一次请求的耗时记录 */
export interface LatencyRecord {
  label: string
  ms: number
  at: string
}

let uid = 0
const nextId = (): string => `${Date.now().toString(36)}-${(uid++).toString(36)}`

// 会话 id 持久化到 localStorage：整页刷新后仍能找回同一次会话
// （后端 _sessions 是进程内存储，只要后端没重启会话就还在）
const STORAGE_KEY = 'yanxitong.session_id'

function readStoredSession(): string | null {
  try {
    return localStorage.getItem(STORAGE_KEY)
  } catch {
    return null
  }
}

function writeStoredSession(id: string | null): void {
  try {
    if (id) localStorage.setItem(STORAGE_KEY, id)
    else localStorage.removeItem(STORAGE_KEY)
  } catch {
    /* 无痕模式等场景下静默忽略 */
  }
}

// 对话记录一并持久化，避免刷新后聊天历史为空（只保留最近若干条）
const MESSAGES_KEY = 'yanxitong.messages'
const MAX_STORED_MESSAGES = 20

function readStoredMessages(): ChatMessage[] {
  try {
    const raw = localStorage.getItem(MESSAGES_KEY)
    const parsed = raw ? (JSON.parse(raw) as ChatMessage[]) : []
    return Array.isArray(parsed) ? parsed : []
  } catch {
    return []
  }
}

function writeStoredMessages(list: ChatMessage[]): void {
  try {
    localStorage.setItem(MESSAGES_KEY, JSON.stringify(list.slice(-MAX_STORED_MESSAGES)))
  } catch {
    /* 超出存储配额或隐私模式：静默忽略 */
  }
}

export const useSessionStore = defineStore('session', () => {
  // ---- 会话标识 ----
  const sessionId = ref<string | null>(readStoredSession())
  const topic = ref<string>('')

  /** 写入会话 id 并同步持久化 */
  function setSessionId(id: string | null): void {
    sessionId.value = id
    writeStoredSession(id)
  }

  // ---- 后端健康 ----
  const health = ref<HealthResponse | null>(null)
  const healthError = ref<string | null>(null)

  // ---- 会话状态 / 文献 / 结论 ----
  const status = ref<SessionStatus | null>(null)
  const claims = ref<Claim[]>([])
  const averageConfidence = ref<number>(0)

  // ---- 问答 ----
  const messages = ref<ChatMessage[]>(readStoredMessages())

  // ---- 历史会话列表 ----
  const sessionList = ref<SessionListItem[]>([])
  /** 是否已完成至少一次拉取（用于侧栏“暂无历史会话”空态，加载中不显示） */
  const sessionLoaded = ref(false)

  // ---- 加载 / 错误 / 计时 ----
  const loading = ref(false)
  const activeLabel = ref<string>('')
  const error = ref<string | null>(null)
  /** 最近一次失败的原始错误对象（用于识别配额用尽等业务错误码） */
  const lastError = ref<unknown>(null)
  const elapsedMs = ref(0)
  const lastLatencyMs = ref<number | null>(null)
  const latencyLog = ref<LatencyRecord[]>([])

  let tickTimer: number | null = null
  let startedAt = 0

  function startTimer(label: string): void {
    stopTimer()
    activeLabel.value = label
    startedAt = Date.now()
    elapsedMs.value = 0
    tickTimer = window.setInterval(() => {
      elapsedMs.value = Date.now() - startedAt
    }, 200)
  }

  function stopTimer(): void {
    if (tickTimer !== null) {
      window.clearInterval(tickTimer)
      tickTimer = null
    }
  }

  function recordLatency(label: string, ms: number): void {
    lastLatencyMs.value = ms
    latencyLog.value = [{ label, ms, at: new Date().toLocaleTimeString('zh-CN') }, ...latencyLog.value].slice(0, 20)
  }

  /**
   * 统一执行一个慢请求：负责加载态、计时、错误捕获。
   * 失败返回 null，成功返回结果。
   */
  async function runTask<T>(label: string, fn: () => Promise<T>): Promise<T | null> {
    error.value = null
    lastError.value = null
    loading.value = true
    startTimer(label)
    const t0 = Date.now()
    try {
      const result = await fn()
      recordLatency(label, Date.now() - t0)
      return result
    } catch (e) {
      const ms = Date.now() - t0
      recordLatency(label, ms)
      lastError.value = e
      error.value = extractErrorMessage(e)
      return null
    } finally {
      stopTimer()
      loading.value = false
      activeLabel.value = ''
    }
  }

  // ---- 健康检查 ----
  async function refreshHealth(): Promise<void> {
    try {
      health.value = await api.getHealth()
      healthError.value = null
    } catch (e) {
      healthError.value = extractErrorMessage(e)
    }
  }

  // ---- 会话状态 ----
  async function refreshStatus(): Promise<void> {
    if (!sessionId.value) return
    try {
      status.value = await api.getSessionStatus(sessionId.value)
      if (status.value.topic) topic.value = status.value.topic
    } catch (e) {
      if (isNotFound(e)) {
        // 会话在后端已失效（后端重启或过期）→ 清空本地会话与历史，避免各页面反复报错
        resetSession()
        return
      }
      error.value = extractErrorMessage(e)
    }
  }

  // ---- 引用链 ----
  async function loadCitationChain(): Promise<void> {
    if (!sessionId.value) return
    try {
      const res = await api.getCitationChain(sessionId.value)
      claims.value = res.claims ?? []
      averageConfidence.value = res.average_confidence ?? 0
    } catch (e) {
      if (isNotFound(e)) {
        resetSession()
        return
      }
      error.value = extractErrorMessage(e)
    }
  }

  // ---- 问答 ----
  function pushMessage(msg: Omit<ChatMessage, 'id' | 'at'>): ChatMessage {
    const full: ChatMessage = { ...msg, id: nextId(), at: new Date().toLocaleTimeString('zh-CN') }
    messages.value.push(full)
    writeStoredMessages(messages.value)
    return full
  }

  /** 首次提问：创建会话（接口 2）；已有会话：继续提问（接口 3） */
  async function sendQuery(query: string, topicInput: string): Promise<void> {
    pushMessage({ role: 'user', content: query, citations: [], confidence: 0, phase: '', humanReviewRequired: false, elapsedMs: 0 })

    const label = sessionId.value ? '继续提问' : '创建会话并提问'
    const t0 = Date.now()
    const res = await runTask(label, async () => {
      if (sessionId.value) {
        try {
          return await api.query(sessionId.value, query)
        } catch (e) {
          if (!isNotFound(e)) throw e
          // 本地记录的会话在后端已不存在：自动降级为新建会话，用户无需手动重置。
          // 这里刻意不清空 sessionId，否则视图会瞬间切回「建立会话」表单。
        }
      }
      return api.createSession({ query, topic: topicInput || topic.value || 'general' })
    })
    const ms = Date.now() - t0

    if (!res) {
      const quotaHit = isQuotaExceeded(lastError.value)
      if (quotaHit) void useAuthStore().refreshMe()
      pushMessage({
        role: 'assistant',
        content: quotaHit
          ? `⚠️ ${error.value ?? '未登录免费体验次数已用完'}\n\n注册 / 登录后即可继续提问，研究数据也将永久保存到你的账号。`
          : `⚠️ 本次请求未能完成。\n\n${error.value ?? '未知错误'}\n\n提示：后端计算耗时较长（常见几十秒），也可能是后端未启动（http://localhost:8001）。`,
        citations: [],
        confidence: 0,
        phase: '',
        humanReviewRequired: false,
        elapsedMs: ms,
        error: true,
      })
      return
    }

    setSessionId(res.session_id)
    void useAuthStore().syncQuota(res.quota_remaining) // 未登录时后端返回剩余次数
    void loadSessions() // 新建会话后刷新历史列表，保持最新
    if (topicInput) topic.value = topicInput
    pushMessage({
      role: 'assistant',
      content: res.answer,
      citations: flattenCitations(res.citations),
      confidence: res.confidence,
      phase: res.phase,
      humanReviewRequired: res.human_review_required,
      elapsedMs: ms,
    })

    await Promise.all([refreshStatus(), loadCitationChain()])
  }

  /** 切换/恢复会话：重置本地历史 */
  function resetSession(): void {
    const oldId = sessionId.value
    if (oldId) {
      // 把当前会话消息存入专属 key，避免“新研究任务”后历史丢失
      try {
        localStorage.setItem(`${MESSAGES_KEY}.${oldId}`, JSON.stringify(messages.value.slice(-MAX_STORED_MESSAGES)))
      } catch {
        /* 静默 */
      }
    }
    setSessionId(null)
    topic.value = ''
    status.value = null
    claims.value = []
    averageConfidence.value = 0
    messages.value = []
    writeStoredMessages([])
    error.value = null
    lastLatencyMs.value = null
    latencyLog.value = []
    void loadSessions()
  }

  /** 拉取后端全部会话摘要；失败静默（清空列表即可，不弹错） */
  async function loadSessions(): Promise<void> {
    try {
      sessionList.value = await api.getSessions()
    } catch {
      sessionList.value = []
    } finally {
      sessionLoaded.value = true
    }
  }

  /** 在历史会话之间切换：按会话保存/恢复本地消息，再拉取新会话状态 */
  async function switchSession(id: string): Promise<void> {
    // 1. 先把当前会话消息存入专属 key
    const currentId = sessionId.value
    if (currentId) {
      try {
        localStorage.setItem(`${MESSAGES_KEY}.${currentId}`, JSON.stringify(messages.value.slice(-MAX_STORED_MESSAGES)))
      } catch {
        /* 静默 */
      }
    }
    // 2. 读回目标会话消息（没有则清空）
    let restored: ChatMessage[] = []
    try {
      const raw = localStorage.getItem(`${MESSAGES_KEY}.${id}`)
      const parsed = raw ? (JSON.parse(raw) as ChatMessage[]) : []
      restored = Array.isArray(parsed) ? parsed : []
    } catch {
      restored = []
    }
    messages.value = restored
    writeStoredMessages(messages.value) // MESSAGES_KEY 与当前会话保持同步，刷新后不串台
    // 3. 切换会话标识并清空会话级状态
    setSessionId(id)
    topic.value = ''
    status.value = null
    claims.value = []
    averageConfidence.value = 0
    error.value = null
    lastLatencyMs.value = null
    latencyLog.value = []
    // 4. 拉取目标会话状态（404 时已有逻辑会自动 reset）
    await refreshStatus()
    await loadCitationChain()
    await loadSessions()
  }

  /** 删除历史会话；删除的正是当前会话时重置本地状态 */
  async function removeSession(id: string): Promise<void> {
    const wasCurrent = sessionId.value === id
    await api.deleteSession(id)
    if (wasCurrent) resetSession()
    await loadSessions()
  }

  const hasSession = computed(() => !!sessionId.value)
  const confidenceScores = computed<Record<string, number>>(() => status.value?.confidence_scores ?? {})
  const phase = computed(() => status.value?.current_phase ?? '')
  const humanReviewRequired = computed(() => status.value?.human_review_required ?? false)
  const papersCount = computed(() => status.value?.papers_count ?? 0)

  /**
   * 从一条 claim 中提取可解析的引用来源。
   * 后端 citation-chain 把来源放在 `source` 字段（{title,url,authors,year}），
   * 早期前端只读 citations/evidence/sources，导致图谱没有连线、文献页显示“无证据”。
   */
  function claimCitations(c: Claim): Citation[] {
    return flattenCitations([
      c.source ?? null,
      c.citations ?? null,
      c.evidence ?? null,
      c.sources ?? null,
    ])
  }

  /** 全量引用（来自问答历史 + 引用链），用于知识图谱 */
  const allCitations = computed<Citation[]>(() => {
    const pool: unknown[] = []
    messages.value.forEach((m) => pool.push(...m.citations))
    claims.value.forEach((c) => pool.push(...claimCitations(c)))
    return flattenCitations(pool)
  })

  /** 归一化后的结论列表（用于图谱与文献面板） */
  const normalizedClaims = computed(() =>
    claims.value.map((c) => ({
      text: claimText(c),
      confidence: claimConfidence(c),
      citations: claimCitations(c),
    })),
  )

  return {
    // state
    sessionId,
    topic,
    health,
    healthError,
    status,
    claims,
    averageConfidence,
    messages,
    sessionList,
    sessionLoaded,
    loading,
    activeLabel,
    error,
    elapsedMs,
    lastLatencyMs,
    latencyLog,
    timeoutSeconds: REQUEST_TIMEOUT / 1000,
    // getters
    hasSession,
    confidenceScores,
    phase,
    humanReviewRequired,
    papersCount,
    allCitations,
    normalizedClaims,
    // actions
    refreshHealth,
    refreshStatus,
    loadCitationChain,
    sendQuery,
    resetSession,
    loadSessions,
    switchSession,
    removeSession,
    runTask,
    pushMessage,
  }
})
