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
  /** ① 数据画像（/analyze 返回） */
  profile?: DataProfile | null
  /** ② 任务规划 */
  task_plan?: TaskPlan | null
  /** ③ 知识召回 */
  knowledge_recall?: KnowledgeRecall | null
  /** ⑦ 结果验证报告 */
  validation?: ValidationReport | null
  /** ⑨ 本次运行的打包信息 */
  analysis_run?: AnalysisRun | null
  /** 实验设计引擎（/design 返回，规格①-⑫） */
  experiment_config?: DesignConfig | null
  design_diagnosis?: DesignDiagnosis | null
  design_evidence?: DesignEvidence | null
  design_candidates?: {
    candidates: DesignCandidate[]
    pruned_candidates?: { id: string; title?: string; reason: string }[]
    pruned?: { prior: number; resource: number }
  } | null
  design_optimization?: DesignOptimization | null
  design_validation?: DesignValidation | null
  design_run?: DesignRun | null
  /** 论文写作产出（/write 返回） */
  writing?: WritingPayload | null
  /** 学术审阅产出（/write、/review 返回） */
  review?: ReviewPayload | null
}

/** 论文写作产出 */
export interface WritingPayload {
  section: string
  content: string
}

/** 审阅问题类型（与后端 checks.py 的规范 key 对应） */
export type ReviewIssueType =
  | 'citation_format'
  | 'expression'
  | 'data_integrity'
  | 'logic'
  | 'ai_disclosure'
  | 'similarity'
  | 'other'

export type ReviewSeverity = 'high' | 'medium' | 'low'

/** fixable=可自动修复 / open=待修 / suggest=建议 */
export type ReviewIssueStatus = 'fixable' | 'open' | 'suggest'

/** 逐段审阅问题（带原文定位） */
export interface ReviewIssue {
  id: string
  type: ReviewIssueType
  severity: ReviewSeverity
  status: ReviewIssueStatus
  quote: string
  description: string
  suggestion: string
  /** 所属章节（草稿标题） */
  section: string
  /** 章节内段号（1 基），未定位时为 null */
  para: number | null
  fixable: boolean
  located: boolean
  source: 'rule' | 'reviewer'
}

/** 审阅统计 */
export interface ReviewStats {
  total: number
  citation_format: number
  citation_fixable: number
  citation_manual: number
  polish: number
  misconduct: number
  high: number
  similarity_pct: number | null
  similarity_safe: boolean | null
  similarity_threshold: number
}

/** 本地查重自检结果 */
export interface ReviewSimilarity {
  pct: number | null
  safe: boolean | null
  threshold: number
  matches: { quote: string; ratio: number; title: string; located: boolean }[]
  papers_compared: number
  method: string
}

/** 审阅维度评分块 */
export interface ReviewDimension {
  score?: number
  issues?: string[]
  suggestions?: string[]
}

/** 学术审阅产出 */
export interface ReviewPayload {
  overall_score: number
  recommendation: string
  summary: string
  strengths: string[]
  weaknesses: string[]
  revision_checklist: string[]
  detailed_comments?: string
  issues: ReviewIssue[]
  stats: ReviewStats
  similarity: ReviewSimilarity
  style: string
  structure?: ReviewDimension
  logic?: ReviewDimension
  citations?: ReviewDimension & { missing_citations?: string[] }
  data_consistency?: ReviewDimension
  format?: ReviewDimension
  language?: ReviewDimension
  novelty?: { score?: number; assessment?: string }
  /** 引用核验结果（绿标数据源） */
  citation_trace?: {
    resolved: { marker: string; paper_id: string; title: string; arxiv_id: string }[]
    unresolved: number[]
    markers: number
  }
}

/** 上传文件响应 */
export interface UploadResponse {
  session_id: string
  filename: string
  size_bytes: number
  file_path: string
  message: string
}

/** 数据画像列信息（规格①：Schema 推断 + 质量报告） */
export interface DataProfileColumn {
  name: string
  dtype: string
  missing: number
  missing_pct: number
  unique: number
  sample?: string
  min?: number
  max?: number
  mean?: number
  outliers_iqr?: number
}

/** 数据画像（degraded=true 表示沙箱不可用，仅提示不阻塞） */
export interface DataProfile {
  ok: boolean
  degraded?: boolean
  error?: string
  filename?: string
  format?: string
  rows?: number
  cols?: number
  columns?: DataProfileColumn[]
  numeric_columns?: string[]
  categorical_columns?: string[]
  datetime_columns?: string[]
  quality?: {
    duplicates: number
    duplicate_pct: number
    issue_count: number
    issues: { kind: string; column?: string; detail: string }[]
  }
  sandbox?: string
}

/** 任务规划步骤（规格②） */
export interface TaskPlanStep {
  id?: number
  type?: string
  description?: string
  depends_on?: number[]
  output?: string
}

export interface TaskPlanMethod {
  name: string
  why?: string
  assumptions?: string[]
  source?: string
}

export interface TaskPlanTemplate {
  name: string
  when?: string
  params?: string
  source?: string
}

/** 任务规划（fallback=true 表示 LLM 规划失败、走启发式单步计划） */
export interface TaskPlan {
  intent_summary?: string
  steps?: TaskPlanStep[]
  methods?: TaskPlanMethod[]
  templates?: TaskPlanTemplate[]
  journal_rules?: string[]
  fallback?: boolean
}

/** 知识召回块（规格③） */
export interface KnowledgeRecallChunk {
  section: string
  text: string
  source: string
  similarity: number
}

export interface KnowledgeRecall {
  query: string
  libraries: Record<string, { label: string; chunks: KnowledgeRecallChunk[] }>
  degraded: boolean
  sources: number
}

/** 校验条目（规格⑦：确定性 + LLM 两层） */
export interface ValidationCheck {
  check: string
  status: 'ok' | 'warn' | 'fail' | string
  detail?: string
  suggestion?: string
}

export interface ValidationReport {
  deterministic: { checks: ValidationCheck[]; status: string; issues: number }
  llm: {
    assumptions?: { test?: string; assumption?: string; status?: string; note?: string }[]
    narrative?: { claim?: string; status?: string; note?: string }[]
    overall?: string
    comments?: string[]
  } | null
  overall: 'pass' | 'warn' | 'fail' | string
  revision_suggestions: string[]
}

export interface AnalysisArtifact {
  name: string
  size: number
}

/** 流水线阶段（含耗时与是否降级） */
export interface AnalysisStage {
  stage: string
  ok: boolean
  ms?: number
  [key: string]: unknown
}

/** 本次运行打包信息（规格⑨） */
export interface AnalysisRun {
  run_id: string
  degraded: boolean
  degrade_reason?: string
  attempts: number
  stages: AnalysisStage[]
  artifacts: AnalysisArtifact[]
  files?: AnalysisArtifact[]
  validation_overall?: string
}

/** 历史运行 manifest（GET /analysis/runs） */
export interface AnalysisRunManifest {
  run_id: string
  session_id: string
  created_at: string
  intent: string
  degraded?: boolean
  attempts?: number
  files: AnalysisArtifact[]
  [key: string]: unknown
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
  /** 最近一次写作的章节 key（用于写作页恢复） */
  writing_section: string
  /** 会话中最近一次生成的草稿全文（用于页面恢复） */
  writing_draft: string
  /** 会话中最近一次的审阅结果（用于页面恢复） */
  review: ReviewPayload | null
  /** 会话绑定的领域包 id / 显示名 */
  kb_id?: string
  kb_name?: string
  /** 显式声明的跨域参考包 id 列表 */
  cross_kb_ids?: string[]
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
  /** 领域包 id（缺省=未分类默认包）；换领域=新建会话 */
  kb_id?: string
  /** 显式声明的跨域参考包 id 列表（未声明=零跨包召回） */
  cross_kb_ids?: string[]
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

// ---- 实验设计引擎（对齐 /api/session/{sid}/design* 端点，规格①-⑫） ----

/** 规范配置（① 解析产物：请求体 experiment_config 优先，query 由 LLM 补齐） */
export interface DesignConfig {
  task?: string
  model?: { name?: string; family?: string; params_m?: number }
  hyperparams?: Record<string, number | string>
  data?: { name?: string; n_samples?: number; n_classes?: number; augmentation?: string[] }
  resources?: { gpu?: string; gpu_hours?: number; memory_gb?: number }
  metrics?: string[]
  missing?: string[]
}

/** 瓶颈条目（① 五类：model_outdated/hyperparam_drift/data_insufficient/strategy_missing/resource_mismatch） */
export interface DesignBottleneck {
  type: string
  severity: string
  finding: string
  recommendation?: string
  evidence_refs?: string[]
}

export interface DesignDiagnosis {
  bottlenecks: DesignBottleneck[]
  summary?: string
  /** 无证据锚点支撑被剔除的瓶颈数（规格⑪） */
  dropped?: number
}

/** 证据锚点（② id 前缀：kg:ent:/kg:chain:/kg:path:/kb:/paper:） */
export interface DesignEvidenceAnchor {
  id: string
  kind: string
  ref?: string
  text?: string
  library?: string
  label?: string
  section?: string
  similarity?: number
  title?: string
  year?: number | string
}

/** 证据集（② KG 路径 + 知识库 + 文献，任一来源失败只降级） */
export interface DesignEvidence {
  keywords?: string[]
  kg?: {
    entities: Record<string, unknown>[]
    chains: Record<string, unknown>[]
    paths: Record<string, unknown>[]
    degraded: boolean
    error?: string
  }
  kb?: KnowledgeRecall | null
  literature?: DesignEvidenceAnchor[]
  anchors?: DesignEvidenceAnchor[]
  degraded?: boolean
  sources?: number
}

/** 候选方案（④ 多路由 + 证据门控；source=llm|heuristic） */
export interface DesignCandidate {
  id: string
  route: string
  title: string
  description?: string
  config_patch?: Record<string, unknown>
  hyperparams?: Record<string, number | string>
  evidence_refs?: string[]
  expected?: string
  risk?: string
  complexity?: string
  interpretability?: string
  source?: string
  severity?: string
}

/** 代理模型三目标估计（性能↑/成本↓/时间↓，带不确定度） */
export interface DesignObjectives {
  performance: number
  cost: number
  time: number
}

/** 排序条目（⑥ 六维加权 Top-K） */
export interface DesignRankEntry {
  rank: number
  candidate_id: string
  title: string
  route: string
  severity?: string
  source?: string
  evidence_refs?: string[]
  config_patch?: Record<string, unknown>
  params: Record<string, number | string>
  objectives: DesignObjectives
  uncertainty: DesignObjectives
  score: number
  risk?: string
  complexity?: string
  interpretability?: string
  expected?: string
}

/** Pareto 前沿点（⑤） */
export interface DesignParetoPoint {
  trial: number
  candidate_id: string
  title?: string
  route?: string
  params: Record<string, number | string>
  objectives: DesignObjectives
  uncertainty: DesignObjectives
}

/** 搜索空间维度（source=prior 来自课题组先验 / kb 来自经验带） */
export interface DesignSpaceDim {
  low: number
  high: number
  kind: string
  log: boolean
  source: string
}

/** 多目标优化结果（⑤⑥；method=optuna|builtin，ok=false 时看 error） */
export interface DesignOptimization {
  method: string
  fallback: boolean
  objectives: string[]
  n_trials: number
  n_evaluated: number
  pruned: { prior: number; resource: number }
  pruned_candidates: { id: string; title?: string; reason: string }[]
  space: Record<string, DesignSpaceDim>
  pareto: DesignParetoPoint[]
  ranking: DesignRankEntry[]
  top_k: DesignRankEntry[]
  note?: string
  ok: boolean
  error?: string
}

/** 确定性检查（⑪第一层：引用覆盖率/数值域/不确定度标注/资源自洽/多样性） */
export interface DesignCheck {
  id: string
  ok: boolean
  severity: string
  finding: string
}

/** 可行性条目（⑦ 五类：resource/dependency/data/ethics/reproducibility） */
export interface DesignFeasibilityItem {
  dimension: string
  ok: boolean
  severity: string
  finding: string
  alternative?: string
}

/** 验证计划（⑧ 消融/对照/显著性/功效/回滚/可复现） */
export interface DesignValidationPlan {
  ablation: { component: string; remove_to_test: string; expect: string }[]
  controls: string[]
  significance: { primary_test?: string; correction?: string; alpha?: number; report?: string }
  power: {
    min_meaningful_difference_sigma?: number
    required_n_per_group?: number
    planned_seeds?: number
    adequate?: boolean
    note?: string
  }
  rollback: { criterion: string; action: string }[]
  reproducibility: string[]
  evidence_refs?: string[]
}

/** 校验报告（⑦⑪：可行性 + 确定性 + LLM 复核 + 验证计划） */
export interface DesignValidation {
  overall: string
  deterministic: { checks: DesignCheck[]; overall: string }
  llm: {
    consistency?: string
    issues?: { claim?: string; problem?: string; severity?: string; evidence_ref?: string }[]
    narrative?: string
    degraded?: boolean
  } | null
  feasibility: { items: DesignFeasibilityItem[]; overall: string; dimensions: string[] }
  plan?: DesignValidationPlan | null
}

/** 静态校验（⑨ 语法 + 依赖对照沙箱镜像清单；不实际执行训练） */
export interface DesignStaticCheck {
  ok: boolean
  mode: string
  syntax_ok: boolean
  syntax_error?: string
  imports?: string[]
  missing?: string[]
  env_packages?: number
  note?: string
}

/** 推荐配置（含 _meta 估计声明） */
export interface DesignRecommendedConfig extends DesignConfig {
  _meta?: {
    candidate_id?: string
    title?: string
    route?: string
    evidence_refs?: string[]
    score?: number
    objectives?: DesignObjectives
    uncertainty?: DesignObjectives
    estimates_note?: string
    execution_note?: string
  }
}

/** 设计运行打包信息（⑫） */
export interface DesignRun {
  run_id: string
  degraded: boolean
  stages: AnalysisStage[]
  files: AnalysisArtifact[]
  optimization_method?: string
  validation_overall?: string
  recommended?: DesignRecommendedConfig | null
  static_check?: DesignStaticCheck | null
  report?: string
}

/** 历次设计运行 manifest（GET /design/runs） */
export interface DesignRunManifest {
  run_id: string
  session_id: string
  created_at: string
  intent: string
  candidates?: number
  stages?: { stage?: string; ok?: boolean; ms?: number; degraded?: boolean }[]
  recommended?: { candidate_id?: string; title?: string; route?: string }
  files?: AnalysisArtifact[]
  optimization?: { method?: string; n_trials?: number; n_evaluated?: number; pruned?: Record<string, number> }
  validation_overall?: string
  [key: string]: unknown
}

/** 反馈回写结果（⑩ 闭环：先验存储必写 + KG 尽力） */
export interface DesignFeedbackResult {
  ok: boolean
  record_id?: number | null
  candidate_id?: string
  task?: string
  method?: string
  dataset?: string
  metric?: string
  value?: number | null
  error?: string
  kg: { ok: boolean; degraded: boolean; written: number; error?: string }
}

/** 先验统计（⑩ 闭环读侧：方法-数据集-指标矩阵 + 超参区间） */
export interface DesignPriorsResponse {
  session_id: string
  /** 先验所属领域包（按会话包过滤后的读侧回显） */
  kb_id?: string
  matrix: {
    method?: string
    dataset?: string
    metric?: string
    n: number
    mean?: number
    min?: number
    max?: number
    std?: number | null
  }[]
  priors: {
    rows: number
    metrics?: DesignPriorsResponse['matrix']
    hyperparams: Record<string, { n: number; min: number; max: number; mean: number; p10?: number; p90?: number }>
    cost?: { n: number; min: number; max: number; mean: number } | null
    duration?: { n: number; min: number; max: number; mean: number } | null
  }
  recent: {
    run_id?: string
    method?: string
    dataset?: string
    metric?: string
    value?: number | null
    cost?: number | null
    duration_hours?: number | null
    created_at?: string
    [key: string]: unknown
  }[]
  total: number
}

// ---- 领域包（KnowledgeBase 分区：本体共享 · 实例隔离） ----

/** 领域包（知识分区）：图谱/文献库/先验/向量索引按 kb_id 隔离 */
export interface DomainPack {
  kb_id: string
  name: string
  description: string
  owner: string
  created_at: string
}

/** 包统计（KgView / 包管理展示） */
export interface PackStats {
  kb_id: string
  papers: number
  entities: number
  relations: number
  sessions: number
  kb_chunks: number
  degraded?: boolean
}
