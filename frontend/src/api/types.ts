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
  /** paper | knowledge（上传文档块，前端显示「知识库」徽章） */
  kind?: string
  filename?: string
  page?: number | string
  chunk_hash?: string
  library?: string
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
  library: 'team' | 'personal' | string
  uploader: string
  content_hash: string
  chunks: number
  chars: number
  updated_at: string
  preview: string
}

/** 知识库上传结果（含解析报告：三级链用了哪级 / 页数 / 是否 OCR / 是否去重） */
export interface KnowledgeUploadResult {
  filename: string
  added: number
  total_docs: number
  library: string
  parser_used: string
  pages: number
  chunks: number
  ocr_used: boolean
  deduped: boolean
  uploader: string
}

/** 证据条目（字段级 span 溯源：quote 必须能在来源文本中找到） */
export interface EvidenceEntry {
  quote: string
  page?: number | string | null
  source?: string
}

/** 文献矩阵行（六字段 + 相关度 + 证据） */
export interface MatrixRow {
  index: number
  title: string
  url?: string
  year?: number | string
  sources: string[]
  relevance: { score: number; reason: string }
  research_problem: string
  methods: { name: string; baseline?: string }[]
  datasets: { name: string; sample_size?: string }[]
  results: { metric?: string; value?: string; unit?: string }[]
  limitations: string[]
  evidence: Record<string, EvidenceEntry[]>
}

export interface LiteratureMatrixResponse {
  session_id: string
  count: number
  rows: MatrixRow[]
}

/** 矛盾检测条目（claim_a vs claim_b + 双方证据与建议） */
export interface ConflictItem {
  claim_a: string
  source_a: string
  source_a_url?: string
  claim_b: string
  source_b: string
  source_b_url?: string
  suggested_resolution: string
  evidence_a?: string
  evidence_b?: string
  evidence_a_verified?: boolean
  evidence_b_verified?: boolean
}

/** 研究空白条目（KG 稀疏节点 + 局限性归纳） */
export interface GapItem {
  gap: string
  reason: string
  related_entities: string[]
}

export interface LiteratureConflictsResponse {
  session_id: string
  conflicts: ConflictItem[]
}

export interface ResearchGapsResponse {
  session_id: string
  gaps: GapItem[]
}

/** 知识库块原文预览（含前后各一块，用于引用溯源） */
export interface ChunkPreview {
  filename: string
  library?: string
  uploader?: string
  page?: number
  section_title?: string
  chunk_index?: number
  chunk_hash?: string
  text: string
  before: string
  after: string
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

// ---- 知识图谱（对齐 /api/kg/* 端点） ----

/** 图谱节点（子图 / 邻域 / 检索通用；paper_id 为 ax:/th:/url: 格式） */
export interface KgNode {
  id: string
  name: string
  type?: string
  kind?: string
  year?: number | string
  arxiv_id?: string
  degree?: number
  [key: string]: unknown
}

/** 图谱边（evidence 为写入时校验过的原文引文） */
export interface KgEdge {
  source: string
  target: string
  type: string
  evidence?: string | null
  evidence_source?: string | null
  value?: unknown
  properties?: Record<string, unknown>
}

/** 子图 / 邻域响应（degraded=true 表示图谱不可用降级） */
export interface KgGraphData {
  nodes: KgNode[]
  edges: KgEdge[]
  paths?: { edges: KgEdge[] }[]
  degraded?: boolean
  [key: string]: unknown
}

/** 图谱总览（schema 为封闭白名单，配置唯一权威） */
export interface KgOverview {
  papers: number
  sessions: number
  entities: Record<string, number>
  entities_total: number
  relations: Record<string, number>
  relations_total: number
  pending_review: number
  schema: { entity_types: string[]; relation_types: string[] }
  degraded?: boolean
}

/** 实体检索响应 */
export interface KgEntitySearchResponse {
  query: string
  count: number
  entities: KgNode[]
  degraded?: boolean
}

/** 论文节点详情（含直接相连实体） */
export interface KgPaperDetail {
  paper_id?: string
  title?: string
  year?: number | string
  authors?: string | string[]
  arxiv_id?: string
  venue?: string
  abstract?: string
  entities?: KgNode[]
  [key: string]: unknown
}

/** 证据链条目：节点 → 带原文引文的边 → 对端论文 */
export interface KgEvidenceRow {
  paper_id: string
  paper_title: string
  arxiv_id: string
  entity_id: string
  entity_name: string
  entity_type: string
  relation: string
  evidence: string
  evidence_source: string
  value: string
  sessions: string[]
}

export interface KgEvidencePathResponse {
  target: string
  count: number
  path: KgEvidenceRow[]
  degraded?: boolean
}

/** 人工复核队列条目（抽样 + 引文校验失败强制入队） */
export interface KgReviewItem {
  edge_key: string
  rel: string
  source_id: string
  source_name: string
  target_id: string
  target_name: string
  evidence: string | null
  evidence_source: string | null
  sessions: string[] | null
  source_abstract?: string
  created_at?: number
}

export interface KgReviewQueueResponse {
  count: number
  queue: KgReviewItem[]
  degraded?: boolean
}

/** 研究路线图（时间线 / 演进链 / 矛盾 / 空白） */
export interface KgTimelineItem {
  year: number
  paper_count: number
  papers: { paper_id: string; title: string; arxiv_id?: string; methods: string[] }[]
}

export interface KgEvolutionItem {
  year: number
  nodes: string[]
  relations: string[]
  text: string
  evidence: string[]
}

export interface KgContradiction {
  source: string
  target: string
  evidence?: string | null
  evidence_source?: string | null
}

export interface KgRoadmapResponse {
  timeline: KgTimelineItem[]
  evolution: KgEvolutionItem[]
  contradictions: KgContradiction[]
  gaps: { entity_id: string; name: string; type: string; degree: number }[]
  degraded?: boolean
}

/** 幻觉标记（三元组冲突 / 质量门禁升级） */
export interface KgHallucinationFlag {
  id?: number
  session_id: string
  layer: string
  risk_level: string
  detail?: Record<string, unknown> | null
  created_at?: number
}

/** 审计日志条目 */
export interface KgAuditEntry {
  id?: number
  session_id: string
  agent: string
  action: string
  detail?: Record<string, unknown> | null
  trace_id?: string
  created_at?: number
}

/** 推理轨迹（agent span + 幻觉标记 + 审计事件） */
export interface KgTraceSpan {
  id?: number
  session_id: string
  agent: string
  status: string
  duration_ms?: number
  trace_id?: string
  detail?: Record<string, unknown> | null
  created_at?: number
}

export interface KgTraceResponse {
  session_id: string
  traces: KgTraceSpan[]
  flags: KgHallucinationFlag[]
  audit: KgAuditEntry[]
}

/** 图谱回填结果 */
export interface KgBackfillResult {
  sessions: number
  papers: number
  entities: number
  edges: number
  unique_papers: number
  degraded: boolean
  error?: string
  errors?: string[]
}
