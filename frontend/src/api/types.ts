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
