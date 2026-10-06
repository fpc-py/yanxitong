// 后端 API 契约类型定义（严格对齐 src/api/schemas.py）

/** 健康检查响应 */
export interface HealthResponse {
  status: string
  version: string
  timestamp: string
  features: string[]
}

/** 引用/文献条目 */
export interface Citation {
  index?: number
  title: string
  url?: string
  authors: string[]
  year?: number | string
  source?: string
}

/** 引用链中的“结论—证据”条目（后端结构不固定，做宽松类型） */
export interface Claim {
  claim?: string
  text?: string
  statement?: string
  conclusion?: string
  confidence?: number
  citations?: unknown
  evidence?: unknown
  sources?: unknown
  [key: string]: unknown
}

/** 沙箱生成的图表（后端返回 data URL，前端直接作为 img src） */
export interface FigureItem {
  name: string
  data_url: string
}

/** 问答/生成类统一响应 */
export interface QueryResponse {
  session_id: string
  answer: string
  confidence: number
  citations: Citation[]
  human_review_required: boolean
  phase: string
  figures?: FigureItem[]
  /** 未登录用户的剩余免费问答次数；已登录或配额服务不可用时为 null */
  quota_remaining?: number | null
}

/** 上传文件响应 */
export interface UploadResponse {
  session_id: string
  filename: string
  size_bytes: number
  file_path: string
  message: string
}

/** 会话状态 */
export interface SessionStatus {
  session_id: string
  topic: string
  current_phase: string
  papers_count: number
  kg_entities_count: number
  confidence_scores: Record<string, number>
  human_review_required: boolean
  error: string | null
  has_data_file: boolean
  has_draft: boolean
}

/** 会话列表项（历史会话侧栏） */
export interface SessionListItem {
  session_id: string
  topic: string
  papers_count: number
}

/** 引用链响应 */
export interface CitationChainResponse {
  session_id: string
  claims: Claim[]
  average_confidence: number
}

/** 参考文献格式化响应 */
export interface BibliographyResponse {
  session_id: string
  style: string
  count: number
  bibliography: string
}

/** 创建会话请求体 */
export interface CreateSessionBody {
  query: string
  topic: string
  session_id?: string
}

/** 六道防线中的一道 */
export interface DefenseItem {
  key: string
  label: string
  active: boolean
  note: string
}

/** 系统能力：沙箱模式 / 防线状态 / 最近追踪 / 评估层数 */
export interface SystemCapabilities {
  sandbox_mode: 'docker' | 'mock'
  defenses: DefenseItem[]
  last_trace_id: string | null
  eval_layers: number
}

/** 实时指标聚合（进程内 Prometheus 计数器） */
export interface MetricsSummary {
  cost_cents: number
  total_tokens: number
  prompt_tokens: number
  completion_tokens: number
  request_count: number
  cache_hits: number
  cache_misses: number
  hallucination_flags: Record<string, number>
  guard_blocks: number
  errors: number
  avg_latency_s: number
}

/** 知识库文件条目 */
export interface KnowledgeFileItem {
  filename: string
  chunks: number
  chars: number
  updated_at: string
  preview: string
}

/** 知识库上传结果 */
export interface KnowledgeUploadResult {
  filename: string
  added: number
  total_docs: number
}

// ---- 认证（对齐 src/api/schemas.py Auth 段） ----

/** 登录用户信息 */
export interface UserInfo {
  id: number
  username: string
  created_at?: string | null
}

/** 未登录免费问答配额（仅匿名身份返回） */
export interface QuotaInfo {
  limit: number
  used: number
  remaining: number
}

/** 注册/登录响应 */
export interface AuthResponse {
  token: string
  user: UserInfo
}

/** /auth/me 响应：已登录只带 user，未登录只带 quota */
export interface MeResponse {
  user: UserInfo | null
  quota: QuotaInfo | null
}
