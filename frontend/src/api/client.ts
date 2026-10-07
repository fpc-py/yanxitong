// axios 实例 + 全部后端接口封装
// Base URL 走 Vite 代理（/api），不写死后端地址

import axios, { AxiosError, type AxiosInstance } from 'axios'
import { getAnonId, getToken } from './identity'
import type {
  AuthResponse,
  BibliographyResponse,
  ChunkPreview,
  Citation,
  CitationChainResponse,
  CreateSessionBody,
  HealthResponse,
  KgAuditEntry,
  KgBackfillResult,
  KgEntitySearchResponse,
  KgEvidencePathResponse,
  KgGraphData,
  KgHallucinationFlag,
  KgOverview,
  KgPaperDetail,
  KgReviewQueueResponse,
  KgRoadmapResponse,
  KgTraceResponse,
  KnowledgeFileItem,
  KnowledgeUploadResult,
  LiteratureConflictsResponse,
  LiteratureMatrixResponse,
  MeResponse,
  MetricsSummary,
  QueryResponse,
  ResearchGapsResponse,
  SessionListItem,
  SessionStatus,
  SystemCapabilities,
  UploadResponse,
} from './types'

/** 后端请求普遍较慢（多智能体编排 + 结构化抽取），超时放宽到 7 分钟 */
export const REQUEST_TIMEOUT = 420_000

const http: AxiosInstance = axios.create({
  baseURL: '/api',
  timeout: REQUEST_TIMEOUT,
  headers: { 'Content-Type': 'application/json' },
})

// 每个请求都带上身份：Authorization（已登录）+ X-Anon-Id（匿名配额跟踪）
http.interceptors.request.use((config) => {
  config.headers.set('X-Anon-Id', getAnonId())
  const token = getToken()
  if (token) config.headers.set('Authorization', `Bearer ${token}`)
  return config
})

/** 统一错误信息提取 */
export function extractErrorMessage(err: unknown): string {
  if (axios.isAxiosError(err)) {
    const e = err as AxiosError<{ detail?: unknown; message?: unknown }>
    if (e.code === 'ECONNABORTED') {
      return `请求超时（超过 ${Math.round(REQUEST_TIMEOUT / 1000)} 秒）：后端仍在计算或未启动，请稍后重试。`
    }
    if (!e.response) {
      return '无法连接后端服务：请确认后端已在 http://localhost:8001 启动。'
    }
    const detail = e.response.data?.detail ?? e.response.data?.message
    if (Array.isArray(detail)) {
      return `请求参数错误（${e.response.status}）`
    }
    // 后端业务错误：detail 为 {code, message} 对象（如 QUOTA_EXCEEDED）
    if (detail && typeof detail === 'object') {
      const message = (detail as { message?: unknown }).message
      if (typeof message === 'string' && message) return message
      return `请求失败（HTTP ${e.response.status}）`
    }
    return `请求失败（HTTP ${e.response.status}）${typeof detail === 'string' && detail ? '：' + detail : ''}`
  }
  return err instanceof Error ? err.message : String(err)
}

/** 判断是否为「未登录免费次数用尽」错误（403 + detail.code=QUOTA_EXCEEDED） */
export function isQuotaExceeded(err: unknown): boolean {
  if (!axios.isAxiosError(err) || err.response?.status !== 403) return false
  const detail = (err.response.data as { detail?: unknown } | undefined)?.detail
  return !!detail && typeof detail === 'object' && (detail as { code?: string }).code === 'QUOTA_EXCEEDED'
}

/** 判断是否为「会话不存在」错误（后端重启或会话过期后回 404） */
export function isNotFound(err: unknown): boolean {
  return axios.isAxiosError(err) && err.response?.status === 404
}

// ---------- 数据归一化 ----------

/** 把 citation_chain / claims 里嵌套的文献结构拍平成 Citation[] */
export function flattenCitations(raw: unknown): Citation[] {
  const out: Citation[] = []

  const visit = (node: unknown): void => {
    if (!node) return
    if (Array.isArray(node)) {
      node.forEach(visit)
      return
    }
    if (typeof node === 'string') {
      if (node.trim()) {
        out.push({ title: node.trim(), authors: [] })
      }
      return
    }
    if (typeof node !== 'object') return
    const obj = node as Record<string, unknown>
    // 注意：source 只有在是字符串时才代表「来源」字段；claim 节点里 source 是
    // 嵌套的论文/知识库对象，不能据此把 claim 本身当成一篇文献。
    const looksLikePaper =
      obj.title || obj.url || obj.authors || obj.year || typeof obj.source === 'string' || obj.venue || obj.journal

    if (looksLikePaper) {
      const authorsRaw = obj.authors
      out.push({
        index: typeof obj.index === 'number' ? obj.index : undefined,
        title: String(obj.title || obj.name || obj.claim || '未命名文献'),
        url: typeof obj.url === 'string' ? obj.url : typeof obj.link === 'string' ? obj.link : undefined,
        authors: Array.isArray(authorsRaw)
          ? authorsRaw.map((a) => String(a))
          : authorsRaw
            ? [String(authorsRaw)]
            : [],
        year: obj.year as number | string | undefined,
        source: (obj.source || obj.venue || obj.journal) as string | undefined,
        // 知识库来源字段（citation-chain source 段携带；无需预览时静默忽略）
        kind: typeof obj.kind === 'string' ? obj.kind : undefined,
        filename: typeof obj.filename === 'string' ? obj.filename : undefined,
        chunk_hash: typeof obj.chunk_hash === 'string' ? obj.chunk_hash : undefined,
        page: (obj.page as number | string | undefined) ?? undefined,
      })
    }

    const nested = obj.citations || obj.evidence || obj.sources || obj.references
    if (nested) visit(nested)
    // claim 节点（citation-chain）把真正的来源挂在 source 对象上，拍平它
    if (obj.source && typeof obj.source === 'object') visit(obj.source)
  }

  visit(raw)

  // 去重并按序号补齐（知识库条目按块哈希区分：同一文件的多个块不应互相吞并）
  const seen = new Set<string>()
  const dedup: Citation[] = []
  out.forEach((c, i) => {
    const key = `${c.title}|${c.url ?? ''}|${c.chunk_hash ?? ''}`
    if (seen.has(key)) return
    seen.add(key)
    dedup.push({ ...c, index: c.index ?? i + 1 })
  })
  return dedup
}

/** 提取 claim 的展示文本 */
export function claimText(c: Record<string, unknown>): string {
  const v = c.claim ?? c.text ?? c.statement ?? c.conclusion ?? c.name
  if (typeof v === 'string' && v.trim()) return v
  return '未命名结论'
}

/** 提取 claim 的置信度 */
export function claimConfidence(c: Record<string, unknown>): number {
  const v = c.confidence ?? c.score
  return typeof v === 'number' && Number.isFinite(v) ? v : 0
}

// ---------- 接口封装 ----------

const api = {
  /** 1. 健康检查 */
  async getHealth(): Promise<HealthResponse> {
    const { data } = await http.get<HealthResponse>('/health')
    return data
  },

  /** 2. 创建会话并完成首次提问（首次必须走这个接口） */
  async createSession(body: CreateSessionBody): Promise<QueryResponse> {
    const { data } = await http.post<QueryResponse>('/session', body)
    return data
  },

  /** 3. 继续提问 */
  async query(sessionId: string, query: string): Promise<QueryResponse> {
    const { data } = await http.post<QueryResponse>(`/session/${sessionId}/query`, { query })
    return data
  },

  /** 4. 上传数据文件（CSV 等） */
  async uploadFile(sessionId: string, file: File): Promise<UploadResponse> {
    const form = new FormData()
    form.append('file', file)
    const { data } = await http.post<UploadResponse>(`/session/${sessionId}/upload`, form, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 60_000,
    })
    return data
  },

  /** 5. 数据分析 */
  async analyze(sessionId: string, query: string): Promise<QueryResponse> {
    const { data } = await http.post<QueryResponse>(`/session/${sessionId}/analyze`, {
      session_id: sessionId,
      query,
    })
    return data
  },

  /** 6. 实验设计 */
  async design(sessionId: string, query: string): Promise<QueryResponse> {
    const { data } = await http.post<QueryResponse>(`/session/${sessionId}/design`, { query })
    return data
  },

  /** 7. 论文写作（query 中需带 section 关键词） */
  async write(sessionId: string, query: string): Promise<QueryResponse> {
    const { data } = await http.post<QueryResponse>(`/session/${sessionId}/write`, { query })
    return data
  },

  /** 8. 学术审阅（draft 以 [APA]/[MLA]/[GBT] 前缀指定引用格式） */
  async review(sessionId: string, draft: string): Promise<QueryResponse> {
    const { data } = await http.post<QueryResponse>(`/session/${sessionId}/review`, {
      session_id: sessionId,
      draft,
    })
    return data
  },

  /** 9. 生成参考文献（style: gbt7714/apa/mla） */
  async bibliography(sessionId: string, style: string): Promise<BibliographyResponse> {
    const { data } = await http.post<BibliographyResponse>(
      `/session/${sessionId}/bibliography`,
      null,
      { params: { style } },
    )
    return data
  },

  /** 10. 会话状态 */
  async getSessionStatus(sessionId: string): Promise<SessionStatus> {
    const { data } = await http.get<SessionStatus>(`/session/${sessionId}`)
    return data
  },

  /** 11. 引用链（结论 → 证据） */
  async getCitationChain(sessionId: string): Promise<CitationChainResponse> {
    const { data } = await http.get<CitationChainResponse>(`/session/${sessionId}/citation-chain`)
    return data
  },

  /** 11a. 文献矩阵（六字段对比表） */
  async getLiteratureMatrix(sessionId: string): Promise<LiteratureMatrixResponse> {
    const { data } = await http.get<LiteratureMatrixResponse>(`/session/${sessionId}/literature/matrix`)
    return data
  },

  /** 11b. 导出文献矩阵 CSV（后端已带 UTF-8 BOM） */
  async exportMatrixCsv(sessionId: string): Promise<Blob> {
    const { data } = await http.get<Blob>(`/session/${sessionId}/literature/matrix`, {
      params: { format: 'csv' },
      responseType: 'blob',
      timeout: 60_000,
    })
    return data
  },

  /** 11c. 矛盾检测结果 */
  async getConflicts(sessionId: string): Promise<LiteratureConflictsResponse> {
    const { data } = await http.get<LiteratureConflictsResponse>(`/session/${sessionId}/literature/conflicts`)
    return data
  },

  /** 11d. 研究空白（KG 稀疏节点 + 局限性归纳） */
  async getResearchGaps(sessionId: string): Promise<ResearchGapsResponse> {
    const { data } = await http.get<ResearchGapsResponse>(`/session/${sessionId}/literature/research-gaps`)
    return data
  },

  /** 12. 会话列表（历史会话侧栏） */
  async getSessions(): Promise<SessionListItem[]> {
    const { data } = await http.get<SessionListItem[]>('/sessions')
    return data
  },

  /** 13. 删除会话 */
  async deleteSession(sessionId: string): Promise<void> {
    await http.delete(`/session/${sessionId}`)
  },

  /** 14. 系统能力（沙箱模式 / 六道防线 / 最近 trace） */
  async getCapabilities(): Promise<SystemCapabilities> {
    const { data } = await http.get<SystemCapabilities>('/system/capabilities')
    return data
  },

  /** 15. 实时指标聚合（进程内 Prometheus 计数器） */
  async getMetricsSummary(): Promise<MetricsSummary> {
    const { data } = await http.get<MetricsSummary>('/metrics/summary')
    return data
  },

  /** 16. 上传知识库文档（.txt/.md/.pdf；library=team 需登录） */
  async uploadKnowledge(file: File, library: 'team' | 'personal' = 'personal'): Promise<KnowledgeUploadResult> {
    const form = new FormData()
    form.append('file', file)
    form.append('library', library)
    const { data } = await http.post<KnowledgeUploadResult>('/knowledge/upload', form, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 300_000,
    })
    return data
  },

  /** 17. 知识库文件列表（team 共享 + 本人 private 合并） */
  async getKnowledgeFiles(): Promise<KnowledgeFileItem[]> {
    const { data } = await http.get<KnowledgeFileItem[]>('/knowledge/files')
    return data
  },

  /** 17a. 知识块原文预览（前后各一块，引用溯源） */
  async getChunkPreview(filename: string, chunkHash: string): Promise<ChunkPreview> {
    const { data } = await http.get<ChunkPreview>('/knowledge/chunk', {
      params: { filename, chunk_hash: chunkHash },
    })
    return data
  },

  /** 18. 删除知识库文件（team 仅上传者可删） */
  async deleteKnowledgeFile(name: string, library: 'team' | 'personal' = 'personal'): Promise<{ ok: boolean }> {
    const { data } = await http.delete<{ ok: boolean }>(`/knowledge/file/${encodeURIComponent(name)}`, {
      params: { library },
    })
    return data
  },

  /** 19. 注册（成功后自动登录，返回 token） */
  async register(username: string, password: string): Promise<AuthResponse> {
    const { data } = await http.post<AuthResponse>('/auth/register', { username, password })
    return data
  },

  /** 20. 登录 */
  async login(username: string, password: string): Promise<AuthResponse> {
    const { data } = await http.post<AuthResponse>('/auth/login', { username, password })
    return data
  },

  /** 21. 登出（吊销当前 token） */
  async logout(): Promise<void> {
    await http.post('/auth/logout')
  },

  /** 22. 当前身份：已登录返回 user，未登录返回匿名配额 */
  async getMe(): Promise<MeResponse> {
    const { data } = await http.get<MeResponse>('/auth/me')
    return data
  },

  // ---------- 知识图谱（/api/kg/*，全部端点在图不可用时返回 degraded 降级结构） ----------

  /** 23. 图谱总览：计数 + 封闭 schema（实体/关系白名单） */
  async getKgOverview(): Promise<KgOverview> {
    const { data } = await http.get<KgOverview>('/kg/overview')
    return data
  },

  /** 24. 实体检索（scope=会话 id 时限定会话视图，缺省全图共享复用） */
  async searchKgEntities(q: string, limit = 20, type?: string, scope?: string): Promise<KgEntitySearchResponse> {
    const { data } = await http.get<KgEntitySearchResponse>('/kg/entities/search', {
      params: { q, limit, type: type || undefined, scope: scope || undefined },
    })
    return data
  },

  /** 25. 实体邻域（1-3 跳子图） */
  async getKgNeighbors(entityId: string, depth = 1): Promise<KgGraphData> {
    const { data } = await http.get<KgGraphData>(`/kg/entity/${encodeURIComponent(entityId)}/neighbors`, {
      params: { depth },
    })
    return data
  },

  /** 26. 论文节点详情（paper_id 形如 ax:/th:/url:） */
  async getKgPaper(paperId: string): Promise<KgPaperDetail> {
    const { data } = await http.get<KgPaperDetail>(`/kg/papers/${encodeURIComponent(paperId)}`)
    return data
  },

  /** 27. 会话视图子图（(:Session)-[:RETRIEVED]->(:Paper) + 直接相连实体） */
  async getKgSessionSubgraph(sessionId: string, limit = 300): Promise<KgGraphData> {
    const { data } = await http.get<KgGraphData>(`/kg/session/${sessionId}/subgraph`, { params: { limit } })
    return data
  },

  /** 28. 可解释证据链：节点 → 带原文引文的边 → 对端论文 */
  async getKgEvidencePath(target: string, limit = 30): Promise<KgEvidencePathResponse> {
    const { data } = await http.get<KgEvidencePathResponse>('/kg/evidence-path', {
      params: { target, limit },
    })
    return data
  },

  /** 29. 人工复核队列 */
  async getKgReviewQueue(limit = 50): Promise<KgReviewQueueResponse> {
    const { data } = await http.get<KgReviewQueueResponse>('/kg/review-queue', { params: { limit } })
    return data
  },

  /** 30. 复核判定：approved 保留边 / rejected 删边 */
  async markKgReviewed(edgeKey: string, decision: 'approved' | 'rejected', note = ''): Promise<{ ok: boolean }> {
    const { data } = await http.post<{ ok: boolean }>(`/kg/review/${encodeURIComponent(edgeKey)}`, {
      decision,
      note,
    })
    return data
  },

  /** 31. 研究路线图（时间线 / 演进链 / 矛盾 / 空白） */
  async getKgRoadmap(sessionId?: string): Promise<KgRoadmapResponse> {
    const { data } = await http.get<KgRoadmapResponse>('/kg/roadmap', {
      params: { session_id: sessionId || undefined },
    })
    return data
  },

  /** 32. 研究空白候选（低度数实体） */
  async getKgGaps(scope?: string, limit = 20): Promise<{ count: number; gaps: { entity_id: string; name: string; type: string; degree: number }[] }> {
    const { data } = await http.get('/kg/gaps', { params: { scope: scope || undefined, limit } })
    return data
  },

  /** 33. 幻觉标记审计（三元组冲突 + 质量门禁升级） */
  async getKgHallucinationFlags(sessionId = '', limit = 100): Promise<{ flags: KgHallucinationFlag[] }> {
    const { data } = await http.get<{ flags: KgHallucinationFlag[] }>('/kg/hallucination-flags', {
      params: { session_id: sessionId, limit },
    })
    return data
  },

  /** 34. 审计日志（建图统计、复核操作等关键事件） */
  async getKgAudit(sessionId = '', limit = 200): Promise<{ entries: KgAuditEntry[] }> {
    const { data } = await http.get<{ entries: KgAuditEntry[] }>('/kg/audit', {
      params: { session_id: sessionId, limit },
    })
    return data
  },

  /** 35. 推理轨迹面板（agent span + 幻觉标记 + 审计） */
  async getKgTrace(sessionId: string, limit = 500): Promise<KgTraceResponse> {
    const { data } = await http.get<KgTraceResponse>(`/kg/trace/${sessionId}`, { params: { limit } })
    return data
  },

  /** 36. 从历史会话重建图谱（确定性通道，幂等） */
  async kgBackfill(): Promise<KgBackfillResult> {
    const { data } = await http.post<KgBackfillResult>('/kg/backfill')
    return data
  },
}

export default api
