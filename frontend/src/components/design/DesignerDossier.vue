<script setup lang="ts">
// 实验设计 Dossier：配置&瓶颈① / 证据链② / 候选④ / Pareto&Top-K⑤⑥ / 可行性⑦ /
// 验证计划⑧ / 推荐配置&代码⑨ / 产出包⑫ / 闭环反馈⑩ / 运行记录
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import api, { extractErrorMessage } from '@/api/client'
import type {
  DesignCandidate,
  DesignConfig,
  DesignDiagnosis,
  DesignEvidence,
  DesignEvidenceAnchor,
  DesignFeedbackResult,
  DesignOptimization,
  DesignPriorsResponse,
  DesignRun,
  DesignRunManifest,
  DesignValidation,
} from '@/api/types'

const props = defineProps<{
  sessionId: string
  config?: DesignConfig | null
  diagnosis?: DesignDiagnosis | null
  evidence?: DesignEvidence | null
  candidates?: {
    candidates: DesignCandidate[]
    pruned_candidates?: { id: string; title?: string; reason: string }[]
    pruned?: { prior: number; resource: number }
  } | null
  optimization?: DesignOptimization | null
  validation?: DesignValidation | null
  run?: DesignRun | null
}>()

const hasAnything = computed(
  () =>
    !!(props.config || props.diagnosis || props.evidence || props.candidates ||
      props.optimization || props.validation || props.run),
)

const BOTTLENECK_LABELS: Record<string, string> = {
  model_outdated: '模型落后',
  hyperparam_drift: '超参漂移',
  data_insufficient: '数据不足',
  strategy_missing: '策略缺失',
  resource_mismatch: '资源不匹配',
}

const ROUTE_LABELS: Record<string, string> = {
  structure: '结构',
  hyperparam: '超参',
  data: '数据',
  strategy: '策略',
}

const KIND_LABELS: Record<string, string> = {
  'kg-entity': '图谱实体',
  'kg-chain': '图谱链',
  'kg-path': '图谱路径',
  kb: '知识库',
  paper: '文献',
}

const warnings = computed(() => {
  const out: string[] = []
  const ev = props.evidence
  if (ev?.degraded) {
    out.push(`证据检索降级${ev.kg?.error ? `：${ev.kg.error}` : '：KG 或知识库来源不可用，仅用剩余来源'}`)
  }
  if (props.optimization?.fallback) out.push('Optuna 不可用：已回退内置随机采样（method=builtin）')
  if (props.optimization && !props.optimization.ok) {
    out.push(`多目标优化未产出可行解：${props.optimization.error || '未知原因'}`)
  }
  const v = props.validation
  if (v?.llm?.degraded) out.push('LLM 复核降级：解析失败，仅确定性检查生效')
  if (v?.overall === 'fail') out.push('分层校验不通过：已记录幻觉标记（design_validation）并压低置信度')
  if (props.run?.degraded) out.push('运行降级：部分阶段或产物缺失（详见阶段轨迹）')
  return out
})

const bottlenecks = computed(() => props.diagnosis?.bottlenecks ?? [])
const configHyper = computed(() => Object.entries(props.config?.hyperparams ?? {}))
const configChips = computed(() => {
  const c = props.config
  if (!c) return [] as string[]
  const chips: string[] = []
  if (c.task) chips.push(`任务：${c.task}`)
  if (c.model?.name) chips.push(`模型：${c.model.name}${c.model.family ? `（${c.model.family}）` : ''}`)
  if (c.data?.name) chips.push(`数据：${c.data.name}${c.data.n_samples ? ` · ${c.data.n_samples} 样本` : ''}`)
  if (c.resources?.gpu_hours) chips.push(`预算：${c.resources.gpu_hours} GPU·h${c.resources.memory_gb ? ` / ${c.resources.memory_gb} GB` : ''}`)
  if (c.metrics?.length) chips.push(`指标：${c.metrics.join('/')}`)
  return chips
})

const anchors = computed<DesignEvidenceAnchor[]>(() => props.evidence?.anchors ?? [])
const anchorGroups = computed(() => {
  const groups = new Map<string, DesignEvidenceAnchor[]>()
  for (const a of anchors.value) {
    const key = a.kind || 'other'
    if (!groups.has(key)) groups.set(key, [])
    groups.get(key)!.push(a)
  }
  return [...groups.entries()].map(([kind, items]) => ({
    kind,
    label: KIND_LABELS[kind] || kind,
    items: items.slice(0, 6),
    total: items.length,
  }))
})

const candidateList = computed(() => props.candidates?.candidates ?? [])
const prunedCandidates = computed(() => props.candidates?.pruned_candidates ?? [])

const opt = computed(() => props.optimization ?? null)
const ranking = computed(() => props.optimization?.ranking ?? [])
const pareto = computed(() => (props.optimization?.pareto ?? []).slice(0, 6))
const spaceDims = computed(() =>
  Object.entries(props.optimization?.space ?? {}).map(([dim, spec]) => ({ dim, ...spec })),
)

const validation = computed(() => props.validation ?? null)
const plan = computed(() => props.validation?.plan ?? null)



const run = computed(() => props.run ?? null)
const files = computed(() => props.run?.files ?? [])
const recommended = computed(() => props.run?.recommended ?? null)
const staticCheck = computed(() => props.run?.static_check ?? null)

// ---- 闭环反馈⑩ --------------------------------------------------------------

const feedback = reactive({
  metric: '',
  value: null as number | null,
  cost: null as number | null,
  duration: null as number | null,
  notes: '',
})
const sending = ref(false)
const lastFeedback = ref<DesignFeedbackResult | null>(null)
const priors = ref<DesignPriorsResponse | null>(null)
const runs = ref<DesignRunManifest[]>([])
const expandedRun = ref('')

const defaultMetric = computed(
  () => recommended.value?.metrics?.[0] || props.config?.metrics?.[0] || 'accuracy',
)

async function loadPriors(): Promise<void> {
  try {
    priors.value = await api.getDesignPriors(props.sessionId)
  } catch {
    /* 先验读侧失败不阻塞页面 */
  }
}

async function loadRuns(): Promise<void> {
  try {
    runs.value = await api.getDesignRuns(props.sessionId)
  } catch {
    /* 无运行或会话失效时保持空列表 */
  }
}

onMounted(() => {
  if (!props.sessionId) return
  void loadRuns()
  void loadPriors()
})

watch(
  () => props.run?.run_id,
  (runId, prev) => {
    if (runId && runId !== prev) void loadRuns()
  },
)

async function submitFeedback(): Promise<void> {
  if (!props.run?.run_id || sending.value) return
  const metric = (feedback.metric || defaultMetric.value).trim()
  if (feedback.value == null || !metric) {
    ElMessage.warning('请填写实测指标数值')
    return
  }
  sending.value = true
  try {
    const res = await api.submitDesignFeedback(props.sessionId, {
      run_id: props.run.run_id,
      metrics: { [metric]: Number(feedback.value) },
      cost: feedback.cost,
      duration_hours: feedback.duration,
      notes: feedback.notes,
    })
    if (!res.ok) {
      ElMessage.error(res.error || '反馈回写失败')
      return
    }
    lastFeedback.value = res
    const kgText = res.kg?.ok
      ? `，图谱写入 ${res.kg.written} 项`
      : `，图谱降级（${res.kg?.error || 'Neo4j 不可用'}）`
    ElMessage.success(`已回写先验存储（记录 #${res.record_id}）${kgText}`)
    feedback.value = null
    feedback.cost = null
    feedback.duration = null
    feedback.notes = ''
    await Promise.all([loadPriors(), loadRuns()])
  } catch (err) {
    ElMessage.error(extractErrorMessage(err))
  } finally {
    sending.value = false
  }
}

// ---- 工具 ------------------------------------------------------------------

function statusIcon(status?: string): string {
  if (status === 'ok' || status === 'pass' || status === 'consistent') return '✓'
  if (status === 'warn') return '⚠'
  if (status === 'fail' || status === 'violated' || status === 'inconsistent') return '✗'
  return '·'
}

function fmt(value: unknown): string {
  if (value == null) return '—'
  if (typeof value !== 'number') return String(value)
  if (Number.isInteger(value)) return String(value)
  return String(Number(value.toFixed(4)))
}

function formatBytes(size: number): string {
  if (size >= 1024 * 1024) return `${(size / 1024 / 1024).toFixed(2)} MB`
  if (size >= 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${size} B`
}

function fileUrl(name: string, runId?: string): string {
  return api.designFileUrl(props.sessionId, runId || props.run?.run_id || '', name)
}

async function downloadPackage(runId?: string): Promise<void> {
  const id = runId || props.run?.run_id
  if (!id) return
  try {
    const blob = await api.downloadDesignPackage(props.sessionId, id)
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `design_${id}.zip`
    a.click()
    URL.revokeObjectURL(url)
  } catch {
    ElMessage.error('产出包下载失败，请稍后重试')
  }
}

function patchEntries(patch?: Record<string, unknown>): { key: string; value: string }[] {
  return Object.entries(patch ?? {}).map(([key, value]) => ({
    key,
    value: typeof value === 'object' ? JSON.stringify(value) : String(value),
  }))
}

function severityClass(severity?: string): string {
  if (severity === 'high') return 'chip sev-high'
  if (severity === 'low') return 'chip sev-low'
  return 'chip sev-medium'
}
</script>

<template>
  <section v-if="hasAnything" class="panel dossier">
    <header class="panel-head">
      <span class="panel-title serif">设计档案 Dossier</span>
      <span class="panel-sub">CONFIG · EVIDENCE · CANDIDATES · PARETO · VERIFY · PACKAGE · LOOP</span>
    </header>
    <div class="panel-body stack-inner">
      <!-- 降级警示 -->
      <div v-if="warnings.length" class="warn-box">
        <span class="warn-title">⚠ 降级提示</span>
        <ul>
          <li v-for="(w, i) in warnings" :key="i">{{ w }}</li>
        </ul>
      </div>

      <!-- ① 配置 & 瓶颈 -->
      <div v-if="config || diagnosis" class="zone">
        <div class="zone-head">
          <span class="zone-title">① 配置与瓶颈诊断</span>
          <span v-for="chip in configChips" :key="chip" class="chip mono">{{ chip }}</span>
        </div>
        <table v-if="configHyper.length" class="mini-table">
          <thead>
            <tr><th>超参</th><th>当前值</th></tr>
          </thead>
          <tbody>
            <tr v-for="[key, value] in configHyper" :key="key">
              <td class="mono">{{ key }}</td>
              <td class="mono">{{ fmt(value) }}</td>
            </tr>
          </tbody>
        </table>
        <ul v-if="config?.missing?.length" class="issue-list">
          <li v-for="(m, i) in config.missing" :key="i">
            <span class="chip tiny warn-chip">缺失</span>
            <span class="issue-text">{{ m }}</span>
          </li>
        </ul>
        <p v-if="diagnosis?.summary" class="plan-summary">{{ diagnosis.summary }}</p>
        <ul v-if="bottlenecks.length" class="bottleneck-list">
          <li v-for="(b, i) in bottlenecks" :key="i">
            <span :class="severityClass(b.severity)">{{ BOTTLENECK_LABELS[b.type] || b.type }}</span>
            <span class="check-detail">{{ b.finding }}</span>
            <span v-if="b.recommendation" class="check-suggest">建议：{{ b.recommendation }}</span>
            <span v-for="ref in b.evidence_refs ?? []" :key="ref" class="ref-chip mono">{{ ref }}</span>
          </li>
        </ul>
        <p v-else-if="diagnosis" class="hint-line">未发现瓶颈（或全部瓶颈因无证据支撑被剔除）</p>
        <p v-if="diagnosis?.dropped" class="hint-line">已剔除 {{ diagnosis.dropped }} 个无证据锚点支撑的瓶颈（规格⑪）</p>
      </div>

      <!-- ② 证据链 -->
      <div v-if="evidence" class="zone">
        <div class="zone-head">
          <span class="zone-title">② 证据链</span>
          <span class="chip mono">{{ evidence.sources ?? anchors.length }} 条来源</span>
          <span v-if="evidence.kg?.degraded" class="chip warn-chip">KG 降级</span>
          <span v-for="kw in (evidence.keywords ?? []).slice(0, 6)" :key="kw" class="chip tiny mono">{{ kw }}</span>
        </div>
        <div v-for="group in anchorGroups" :key="group.kind" class="sub-zone">
          <span class="sub-title">{{ group.label }}（{{ group.total }}）</span>
          <div v-for="a in group.items" :key="a.id" class="chunk-row">
            <div class="chunk-head">
              <span class="chunk-section mono">{{ a.id }}</span>
              <span v-if="a.label || a.library" class="chunk-source">{{ a.label || a.library }}</span>
              <span v-if="a.section" class="chunk-source">{{ a.section }}</span>
              <span v-if="a.similarity != null" class="chip tiny mono">{{ Number(a.similarity).toFixed(2) }}</span>
            </div>
            <p class="chunk-text">{{ a.text || a.title || a.ref || '' }}</p>
          </div>
        </div>
        <p v-if="!anchors.length" class="hint-line">未检索到证据锚点（无文献/图谱/知识库来源）</p>
      </div>

      <!-- ④ 候选方案 -->
      <div v-if="candidates" class="zone">
        <div class="zone-head">
          <span class="zone-title">④ 候选方案</span>
          <span class="chip mono">{{ candidateList.length }} 个候选</span>
          <span v-if="candidates.pruned?.prior" class="chip warn-chip">先验剪枝 {{ candidates.pruned.prior }}</span>
        </div>
        <div v-for="c in candidateList" :key="c.id" class="chunk-row">
          <div class="chunk-head">
            <span class="chip tiny mono">{{ c.id }}</span>
            <span class="chip tiny">{{ ROUTE_LABELS[c.route] || c.route }}</span>
            <span class="chunk-section">{{ c.title }}</span>
            <span v-if="c.source === 'heuristic'" class="chip tiny warn-chip">启发式</span>
          </div>
          <p class="method-why">{{ c.description }}</p>
          <p v-if="c.expected" class="method-assume">预期：{{ c.expected }}</p>
          <div class="patch-row">
            <span v-for="p in patchEntries(c.config_patch)" :key="p.key" class="chip tiny mono">
              {{ p.key }} = {{ p.value }}
            </span>
            <span v-if="c.risk" class="chip tiny">风险 {{ c.risk }}</span>
            <span v-if="c.complexity" class="chip tiny">复杂度 {{ c.complexity }}</span>
            <span v-for="ref in c.evidence_refs ?? []" :key="ref" class="ref-chip mono">{{ ref }}</span>
          </div>
        </div>
        <ul v-if="prunedCandidates.length" class="rule-list">
          <li v-for="pc in prunedCandidates" :key="pc.id">
            <span class="chip tiny warn-chip">剪枝</span>
            <span class="mono">{{ pc.id }}</span> {{ pc.reason }}
          </li>
        </ul>
      </div>

      <!-- ⑤⑥ 多目标优化 -->
      <div v-if="opt" class="zone">
        <div class="zone-head">
          <span class="zone-title">⑤⑥ 多目标优化与排序</span>
          <span class="chip mono">{{ opt.method === 'optuna' ? 'Optuna NSGA-II' : '内置采样' }}</span>
          <span class="chip mono">评估 {{ opt.n_evaluated }}/{{ opt.n_trials }} 点</span>
          <span class="chip mono">资源剪枝 {{ opt.pruned?.resource ?? 0 }}</span>
          <span v-if="opt.fallback" class="chip warn-chip">回退</span>
        </div>
        <p v-if="opt.error" class="hint-line">{{ opt.error }}</p>
        <table v-if="spaceDims.length" class="mini-table">
          <thead>
            <tr><th>维度</th><th>区间</th><th>类型</th><th>来源</th></tr>
          </thead>
          <tbody>
            <tr v-for="dim in spaceDims" :key="dim.dim">
              <td class="mono">{{ dim.dim }}</td>
              <td class="mono">[{{ fmt(dim.low) }}, {{ fmt(dim.high) }}]</td>
              <td>{{ dim.kind }}{{ dim.log ? ' · log' : '' }}</td>
              <td>{{ dim.source === 'prior' ? '课题组先验' : '经验带' }}</td>
            </tr>
          </tbody>
        </table>
        <template v-if="ranking.length">
          <span class="sub-title">Top-K 排序（六维加权：性能/成本/时间/可解释性/风险/复杂度）</span>
          <table class="mini-table">
            <thead>
              <tr>
                <th>#</th><th>候选</th><th>分数</th>
                <th>性能（±）</th><th>成本 GPU·h（±）</th><th>时长 h（±）</th><th>关键参数</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="r in ranking" :key="r.candidate_id">
                <td class="mono">{{ r.rank }}</td>
                <td>
                  <span class="mono">{{ r.candidate_id }}</span> {{ r.title }}
                  <span class="chip tiny">{{ ROUTE_LABELS[r.route] || r.route }}</span>
                </td>
                <td class="mono">{{ (r.score * 100).toFixed(1) }}</td>
                <td class="mono">{{ fmt(r.objectives.performance) }} ± {{ fmt(r.uncertainty?.performance) }}</td>
                <td class="mono">{{ fmt(r.objectives.cost) }} ± {{ fmt(r.uncertainty?.cost) }}</td>
                <td class="mono">{{ fmt(r.objectives.time) }} ± {{ fmt(r.uncertainty?.time) }}</td>
                <td class="cell-sample mono">
                  {{ Object.entries(r.params ?? {}).slice(0, 3).map(([k, v]) => `${k}=${fmt(v)}`).join(' ') }}
                </td>
              </tr>
            </tbody>
          </table>
          <p class="hint-line">{{ opt.note }}</p>
        </template>
        <template v-if="pareto.length">
          <span class="sub-title">Pareto 前沿（{{ opt.pareto.length }} 点，展示前 {{ pareto.length }}）</span>
          <ul class="rule-list">
            <li v-for="p in pareto" :key="`${p.trial}-${p.candidate_id}`">
              <span class="mono">t{{ p.trial }}</span>
              <span class="mono">{{ p.candidate_id }}</span>
              <span>性能 {{ fmt(p.objectives.performance) }} ｜ 成本 {{ fmt(p.objectives.cost) }} ｜ 时长 {{ fmt(p.objectives.time) }}</span>
            </li>
          </ul>
        </template>
      </div>

      <!-- ⑦ 可行性 -->
      <div v-if="validation" class="zone">
        <div class="zone-head">
          <span class="zone-title">⑦ 可行性检查（五类）</span>
          <span class="chip" :class="validation.feasibility?.overall === 'pass' ? '' : 'warn-chip'">
            {{ validation.feasibility?.overall === 'pass' ? '通过' : '注意' }}
          </span>
        </div>
        <ul class="check-list">
          <li v-for="(item, i) in validation.feasibility?.items ?? []" :key="i" :class="item.ok ? 'st-ok' : 'st-warn'">
            <span class="check-icon">{{ item.ok ? '✓' : '⚠' }}</span>
            <span class="check-name">{{ item.dimension }}</span>
            <span class="check-detail">{{ item.finding }}</span>
            <span v-if="item.alternative" class="check-suggest">替代：{{ item.alternative }}</span>
          </li>
        </ul>
      </div>

      <!-- ⑧ 验证计划 -->
      <div v-if="plan" class="zone">
        <div class="zone-head">
          <span class="zone-title">⑧ 验证计划</span>
          <span v-if="plan.power?.adequate" class="chip">功效充足</span>
          <span v-else class="chip warn-chip">功效不足</span>
          <span class="chip mono">计划 {{ plan.power?.planned_seeds }} 种子 / 需 {{ plan.power?.required_n_per_group }}</span>
        </div>
        <div v-if="plan.ablation?.length" class="sub-zone">
          <span class="sub-title">消融实验</span>
          <ul class="rule-list">
            <li v-for="(a, i) in plan.ablation" :key="i">
              <span class="mono">{{ a.component }}</span>
              <span>移除：{{ a.remove_to_test }}</span>
              <span class="check-suggest">预期：{{ a.expect }}</span>
            </li>
          </ul>
        </div>
        <div v-if="plan.controls?.length" class="sub-zone">
          <span class="sub-title">对照设置</span>
          <ul class="rule-list">
            <li v-for="(c, i) in plan.controls" :key="i">{{ c }}</li>
          </ul>
        </div>
        <div v-if="plan.significance" class="sub-zone">
          <span class="sub-title">显著性</span>
          <p class="method-why">
            {{ plan.significance.primary_test }}；{{ plan.significance.correction }}（α={{ plan.significance.alpha }}）
          </p>
          <p class="method-assume">{{ plan.significance.report }}</p>
        </div>
        <div v-if="plan.rollback?.length" class="sub-zone">
          <span class="sub-title">回滚判据</span>
          <ul class="rule-list">
            <li v-for="(r, i) in plan.rollback" :key="i">
              <span class="check-detail">{{ r.criterion }}</span>
              <span class="check-suggest">→ {{ r.action }}</span>
            </li>
          </ul>
        </div>
        <div v-if="plan.reproducibility?.length" class="sub-zone">
          <span class="sub-title">可复现清单</span>
          <ul class="rule-list">
            <li v-for="(r, i) in plan.reproducibility" :key="i">{{ r }}</li>
          </ul>
        </div>
        <p class="hint-line">{{ plan.power?.note }}</p>
      </div>

      <!-- ⑦⑪ 分层校验 -->
      <div v-if="validation" class="zone">
        <div class="zone-head">
          <span class="zone-title">⑪ 分层幻觉校验</span>
          <span class="chip" :class="validation.overall === 'pass' ? '' : validation.overall === 'fail' ? 'warn-chip' : 'chip'">
            {{ validation.overall === 'pass' ? '通过' : validation.overall === 'fail' ? '不通过（置信度封顶 0.4）' : '注意' }}
          </span>
        </div>
        <ul class="check-list">
          <li v-for="(c, i) in validation.deterministic?.checks ?? []" :key="i" :class="`st-${c.ok ? 'ok' : c.severity === 'fail' ? 'fail' : 'warn'}`">
            <span class="check-icon">{{ statusIcon(c.ok ? 'ok' : c.severity === 'fail' ? 'fail' : 'warn') }}</span>
            <span class="check-name">{{ c.id }}</span>
            <span class="check-detail">{{ c.finding }}</span>
          </li>
        </ul>
        <div v-if="validation.llm" class="sub-zone">
          <span class="sub-title">LLM 复核（主张-证据一致性{{ validation.llm.degraded ? '：降级' : '' }}）</span>
          <ul class="check-list">
            <li v-for="(issue, i) in validation.llm.issues ?? []" :key="i" :class="`st-${issue.severity === 'high' ? 'fail' : 'warn'}`">
              <span class="check-icon">{{ issue.severity === 'high' ? '✗' : '⚠' }}</span>
              <span class="check-name">{{ issue.claim }}</span>
              <span class="check-detail">{{ issue.problem }}</span>
              <span v-if="issue.evidence_ref" class="ref-chip mono">{{ issue.evidence_ref }}</span>
            </li>
          </ul>
          <p v-if="validation.llm.narrative" class="method-why">{{ validation.llm.narrative }}</p>
        </div>
      </div>

      <!-- ⑨ 推荐配置 & 代码 -->
      <div v-if="recommended || run" class="zone">
        <div class="zone-head">
          <span class="zone-title">⑨ 推荐配置与训练脚本</span>
          <span v-if="recommended?._meta?.candidate_id" class="chip mono">
            来自 {{ recommended._meta.candidate_id }} · 分数 {{ ((recommended._meta.score ?? 0) * 100).toFixed(1) }}
          </span>
          <a v-if="files.some((f) => f.name === 'code/train.py')" :href="fileUrl('code/train.py')" target="_blank" rel="noopener" class="chip link-chip">
            下载 train.py
          </a>
        </div>
        <table v-if="Object.keys(recommended?.hyperparams ?? {}).length" class="mini-table">
          <thead>
            <tr><th>超参</th><th>推荐值</th></tr>
          </thead>
          <tbody>
            <tr v-for="[key, value] in Object.entries(recommended?.hyperparams ?? {})" :key="key">
              <td class="mono">{{ key }}</td>
              <td class="mono">{{ fmt(value) }}</td>
            </tr>
          </tbody>
        </table>
        <div v-if="staticCheck" class="sub-zone">
          <span class="sub-title">静态校验（沙箱仅做 AST + 依赖对照，不实际训练）</span>
          <ul class="check-list">
            <li :class="staticCheck.syntax_ok ? 'st-ok' : 'st-fail'">
              <span class="check-icon">{{ staticCheck.syntax_ok ? '✓' : '✗' }}</span>
              <span class="check-name">语法解析</span>
              <span class="check-detail">{{ staticCheck.syntax_ok ? 'py_compile 通过' : staticCheck.syntax_error }}</span>
            </li>
            <li :class="staticCheck.missing?.length ? 'st-warn' : 'st-ok'">
              <span class="check-icon">{{ staticCheck.missing?.length ? '⚠' : '✓' }}</span>
              <span class="check-name">依赖对照</span>
              <span class="check-detail">
                镜像清单 {{ staticCheck.env_packages }} 个包；
                {{ staticCheck.missing?.length ? `缺失：${staticCheck.missing.join(', ')}（目标训练环境需具备）` : '依赖齐备' }}
              </span>
            </li>
          </ul>
        </div>
        <p v-if="recommended?._meta?.estimates_note" class="hint-line">{{ recommended._meta.estimates_note }}</p>
      </div>

      <!-- ⑫ 产出包 -->
      <div v-if="run" class="zone">
        <div class="zone-head">
          <span class="zone-title">⑫ 产出包</span>
          <span class="chip mono">RUN {{ run.run_id }}</span>
          <span class="chip mono">{{ run.optimization_method }}</span>
          <el-button size="small" @click="downloadPackage()">下载产出包 zip</el-button>
        </div>
        <div class="stage-row">
          <span v-for="s in run.stages" :key="s.stage" class="chip tiny" :class="s.ok ? '' : 'warn-chip'">
            {{ s.stage }}<span v-if="s.ms != null" class="mono"> {{ Math.round(Number(s.ms)) }}ms</span>
          </span>
        </div>
        <ul v-if="files.length" class="file-list">
          <li v-for="f in files" :key="f.name">
            <a :href="fileUrl(f.name)" target="_blank" rel="noopener" class="mono">{{ f.name }}</a>
            <span class="mono file-size">{{ formatBytes(f.size) }}</span>
          </li>
        </ul>
      </div>

      <!-- ⑩ 闭环反馈 -->
      <div class="zone">
        <div class="zone-head">
          <span class="zone-title">⑩ 闭环反馈（实测结果回流）</span>
          <span v-if="lastFeedback" class="chip" :class="lastFeedback.kg?.ok ? '' : 'warn-chip'">
            已回写 · 记录 #{{ lastFeedback.record_id }}
            {{ lastFeedback.kg?.ok ? `· 图谱 +${lastFeedback.kg.written}` : '· 图谱降级' }}
          </span>
        </div>
        <p v-if="!run" class="hint-line">需先完成一次设计运行才能回写反馈</p>
        <template v-else>
          <div class="feedback-grid">
            <el-input v-model="feedback.metric" size="small" :placeholder="`指标名（默认 ${defaultMetric}）`" />
            <el-input-number v-model="feedback.value" size="small" :controls="false" placeholder="实测值" class="num-input" />
            <el-input-number v-model="feedback.cost" size="small" :controls="false" placeholder="成本" class="num-input" />
            <el-input-number v-model="feedback.duration" size="small" :controls="false" placeholder="时长 h" class="num-input" />
            <el-input v-model="feedback.notes" size="small" placeholder="备注（配置偏差说明等）" />
            <el-button size="small" type="primary" :loading="sending" @click="submitFeedback">回写</el-button>
          </div>
          <p class="hint-line">回写后写入先验存储（必写）与知识图谱 Experiment 节点（Neo4j 不可用时降级）；后续设计的搜索空间将引用先验区间。</p>
        </template>

        <div v-if="priors && priors.total > 0" class="sub-zone">
          <div class="zone-head">
            <span class="sub-title">先验库（共 {{ priors.total }} 条实验记录）</span>
            <el-button size="small" text @click="loadPriors">刷新</el-button>
          </div>
          <table v-if="priors.matrix?.length" class="mini-table">
            <thead>
              <tr><th>方法</th><th>数据集</th><th>指标</th><th>n</th><th>均值</th><th>范围</th></tr>
            </thead>
            <tbody>
              <tr v-for="(row, i) in priors.matrix.slice(0, 8)" :key="i">
                <td class="mono">{{ row.method || '—' }}</td>
                <td class="mono">{{ row.dataset || '—' }}</td>
                <td class="mono">{{ row.metric || '—' }}</td>
                <td class="mono">{{ row.n }}</td>
                <td class="mono">{{ fmt(row.mean) }}</td>
                <td class="mono">[{{ fmt(row.min) }}, {{ fmt(row.max) }}]</td>
              </tr>
            </tbody>
          </table>
          <table v-if="Object.keys(priors.priors?.hyperparams ?? {}).length" class="mini-table">
            <thead>
              <tr><th>超参先验</th><th>n</th><th>区间</th><th>均值</th></tr>
            </thead>
            <tbody>
              <tr v-for="[key, stat] in Object.entries(priors.priors.hyperparams)" :key="key">
                <td class="mono">{{ key }}</td>
                <td class="mono">{{ stat.n }}</td>
                <td class="mono">[{{ fmt(stat.min) }}, {{ fmt(stat.max) }}]</td>
                <td class="mono">{{ fmt(stat.mean) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- 运行记录 -->
      <div class="zone">
        <div class="zone-head">
          <span class="zone-title">运行记录</span>
          <span class="chip mono">{{ runs.length }} 次</span>
          <el-button size="small" text @click="loadRuns">刷新</el-button>
        </div>
        <p v-if="!runs.length" class="hint-line">本会话暂无历史设计运行（每会话保留最近若干次）</p>
        <ul v-else class="run-list">
          <li v-for="m in runs" :key="m.run_id">
            <div class="run-head">
              <span class="mono">{{ m.run_id }}</span>
              <span class="chip tiny" :class="m.validation_overall === 'pass' ? '' : 'warn-chip'">
                {{ m.validation_overall || '—' }}
              </span>
              <span v-if="m.candidates != null" class="chip tiny mono">{{ m.candidates }} 候选</span>
              <span v-if="m.optimization?.method" class="chip tiny mono">{{ m.optimization.method }}</span>
              <span v-if="m.recommended?.title" class="run-rec">{{ m.recommended.title }}</span>
              <span class="run-actions">
                <el-button size="small" text @click="expandedRun = expandedRun === m.run_id ? '' : m.run_id">
                  {{ expandedRun === m.run_id ? '收起' : '文件' }}
                </el-button>
                <el-button size="small" text @click="downloadPackage(m.run_id)">zip</el-button>
              </span>
            </div>
            <div v-if="expandedRun === m.run_id" class="run-files">
              <a
                v-for="f in m.files ?? []"
                :key="f.name"
                :href="fileUrl(f.name, m.run_id)"
                target="_blank"
                rel="noopener"
                class="mono run-file"
              >{{ f.name }}</a>
            </div>
          </li>
        </ul>
      </div>
    </div>
  </section>
</template>

<style scoped>
.stack-inner {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.warn-box {
  padding: 10px 12px;
  border: 1px solid rgba(201, 151, 60, 0.45);
  border-radius: var(--radius-sm);
  background: rgba(240, 228, 66, 0.14);
}

.warn-title {
  font-size: 12px;
  color: var(--text-1);
}

.warn-box ul {
  margin: 6px 0 0;
  padding-left: 16px;
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.warn-box li {
  font-size: 11.5px;
  color: var(--text-2);
  line-height: 1.6;
}

.zone {
  padding-top: 12px;
  border-top: 1px solid var(--hair-soft);
}

.zone:first-of-type {
  border-top: none;
  padding-top: 0;
}

.zone-head {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 9px;
}

.zone-title {
  font-size: 13px;
  color: var(--text-1);
}

.warn-chip {
  border-color: rgba(201, 151, 60, 0.5);
  color: #8a6a1f;
}

.link-chip {
  text-decoration: none;
  color: var(--accent);
  border-color: var(--accent-soft, rgba(90, 120, 200, 0.4));
}

.sev-high {
  border-color: rgba(176, 72, 63, 0.5);
  color: #b0483f;
}

.sev-medium {
  border-color: rgba(181, 138, 44, 0.5);
  color: #8a6a1f;
}

.sev-low {
  border-color: rgba(62, 125, 79, 0.45);
  color: #3e7d4f;
}

.mini-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 11px;
  margin-bottom: 8px;
}

.mini-table th,
.mini-table td {
  padding: 5px 8px;
  border-bottom: 1px solid var(--hair-soft);
  text-align: left;
  color: var(--text-2);
  vertical-align: top;
}

.mini-table th {
  color: var(--text-3);
  font-weight: 500;
  letter-spacing: 0.04em;
}

.cell-sample {
  max-width: 260px;
  word-break: break-all;
}

.issue-list,
.rule-list,
.check-list,
.file-list,
.bottleneck-list,
.run-list {
  list-style: none;
  margin: 8px 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.issue-list li,
.rule-list li,
.bottleneck-list li {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 7px;
  font-size: 11.5px;
  color: var(--text-2);
  line-height: 1.6;
}

.issue-text {
  color: var(--text-3);
}

.plan-summary {
  margin: 0 0 8px;
  font-size: 12px;
  color: var(--text-2);
}

.sub-zone {
  margin-top: 10px;
}

.sub-title {
  display: block;
  margin-bottom: 5px;
  font-size: 11px;
  color: var(--text-3);
  letter-spacing: 0.05em;
}

.chunk-row {
  padding: 7px 9px;
  margin-bottom: 6px;
  border: 1px solid var(--hair-soft);
  border-radius: var(--radius-sm);
  background: rgba(255, 255, 255, 0.85);
}

.chunk-head {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 7px;
}

.chunk-section {
  font-size: 12px;
  color: var(--text-1);
}

.chunk-source {
  font-size: 10px;
  color: var(--text-3);
}

.chunk-text {
  margin: 4px 0 0;
  font-size: 11px;
  color: var(--text-2);
  line-height: 1.65;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.patch-row {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
  margin-top: 5px;
}

.ref-chip {
  font-size: 10px;
  color: var(--text-3);
  border: 1px dashed var(--hair-soft);
  border-radius: 8px;
  padding: 1px 6px;
}

.method-why {
  margin: 4px 0 0;
  font-size: 11.5px;
  color: var(--text-2);
  line-height: 1.6;
}

.method-assume {
  margin: 2px 0 0;
  font-size: 11px;
  color: var(--text-3);
}

.check-list li {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 6px;
  font-size: 11.5px;
  color: var(--text-2);
  line-height: 1.6;
}

.check-icon {
  width: 14px;
  text-align: center;
}

.st-ok .check-icon {
  color: #3e7d4f;
}

.st-warn .check-icon {
  color: #b58a2c;
}

.st-fail .check-icon {
  color: #b0483f;
}

.check-name {
  color: var(--text-1);
}

.check-detail {
  color: var(--text-2);
}

.check-suggest {
  color: var(--text-3);
}

.stage-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 8px;
}

.file-list li {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px;
  font-size: 11px;
}

.file-list a {
  color: var(--accent);
  text-decoration: none;
  word-break: break-all;
}

.file-list a:hover {
  text-decoration: underline;
}

.file-size {
  color: var(--text-3);
  flex-shrink: 0;
}

.feedback-grid {
  display: grid;
  grid-template-columns: 1.4fr 1fr 1fr 1fr auto;
  gap: 8px;
  align-items: center;
}

.feedback-grid :deep(.el-input),
.feedback-grid :deep(.el-input-number) {
  width: 100%;
}

.feedback-grid .num-input {
  min-width: 72px;
}

.run-list li {
  padding: 6px 8px;
  border: 1px solid var(--hair-soft);
  border-radius: var(--radius-sm);
}

.run-head {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 7px;
  font-size: 11px;
  color: var(--text-2);
}

.run-rec {
  color: var(--text-3);
  font-size: 11px;
}

.run-actions {
  margin-left: auto;
  display: flex;
  gap: 2px;
}

.run-files {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 6px;
  padding-top: 5px;
  border-top: 1px dashed var(--hair-soft);
}

.run-file {
  font-size: 10.5px;
  color: var(--accent);
  text-decoration: none;
}

.run-file:hover {
  text-decoration: underline;
}
</style>
