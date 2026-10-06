// axios 实例 + 全部后端接口封装
// Base URL 走 Vite 代理（/api），不写死后端地址

import axios, { AxiosError, type AxiosInstance } from 'axios'
import type {
  BibliographyResponse,
  Citation,
  CitationChainResponse,
  CreateSessionBody,
  HealthResponse,
  KnowledgeFileItem,
  KnowledgeUploadResult,
  MetricsSummary,
  QueryResponse,
  SessionListItem,
  SessionStatus,
  SystemCapabilities,
  UploadResponse,
} from './types'

/** 后端请求普遍较慢（几十秒），超时放宽到 4 分钟 */
export const REQUEST_TIMEOUT = 240_000

const http: AxiosInstance = axios.create({
  baseURL: '/api',
  timeout: REQUEST_TIMEOUT,
  headers: { 'Content-Type': 'application/json' },
})

/** 统一错误信息提取 */
export function extractErrorMessage(err: unknown): string {
  if (axios.isAxiosError(err)) {
    const e = err as AxiosError<{ detail?: string; message?: string }>
    if (e.code === 'ECONNABORTED') {
      return `请求超时（超过 ${Math.round(REQUEST_TIMEOUT / 1000)} 秒）：后端仍在计算或未启动，请稍后重试。`
    }
    if (!e.response) {
      return '无法连接后端服务：请确认后端已在 http://localhost:8001 启动。'
    }
    const detail = e.response.data?.detail || e.response.data?.message
    if (Array.isArray(detail)) {
      return `请求参数错误（${e.response.status}）`
    }
    return `请求失败（HTTP ${e.response.status}）${detail ? '：' + detail : ''}`
  }
  return err instanceof Error ? err.message : String(err)
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
    const looksLikePaper =
      obj.title || obj.url || obj.authors || obj.year || obj.source || obj.venue || obj.journal

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
      })
    }

    const nested = obj.citations || obj.evidence || obj.sources || obj.references
    if (nested) visit(nested)
  }

  visit(raw)

  // 去重并按序号补齐
  const seen = new Set<string>()
  const dedup: Citation[] = []
  out.forEach((c, i) => {
    const key = `${c.title}|${c.url ?? ''}`
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

  /** 16. 上传知识库文档（.txt/.md/.pdf，multipart 需显式覆盖 Content-Type） */
  async uploadKnowledge(file: File): Promise<KnowledgeUploadResult> {
    const form = new FormData()
    form.append('file', file)
    const { data } = await http.post<KnowledgeUploadResult>('/knowledge/upload', form, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 60_000,
    })
    return data
  },

  /** 17. 知识库文件列表 */
  async getKnowledgeFiles(): Promise<KnowledgeFileItem[]> {
    const { data } = await http.get<KnowledgeFileItem[]>('/knowledge/files')
    return data
  },

  /** 18. 删除知识库文件 */
  async deleteKnowledgeFile(name: string): Promise<{ ok: boolean }> {
    const { data } = await http.delete<{ ok: boolean }>(`/knowledge/file/${encodeURIComponent(name)}`)
    return data
  },
}

export default api
