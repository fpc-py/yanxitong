<script setup lang="ts">
// 系统工程三栏：可观测性 / 成本看板 / 评估中心
// 数据源：system store（capabilities / metrics）+ session store（置信度 / 人工复核 / status）
// 后端离线（capabilities / metrics 为 null）时所有数值显示「—」，不报错
import { computed } from 'vue'
import { useSessionStore } from '@/stores/session'
import { useSystemStore } from '@/stores/system'

const system = useSystemStore()
const session = useSessionStore()

const metrics = computed(() => system.metrics)
const caps = computed(() => system.capabilities)

// ---- 栏一：可观测性 ----
const avgLatency = computed(() => {
  const v = metrics.value?.avg_latency_s
  // 0 表示尚无请求样本（后端无样本时返回 0.0），此时如实显示「—」而非 0.00s
  if (!v) return '—'
  return `${Number(v).toFixed(2)}s`
})

const errorsText = computed(() => {
  const v = metrics.value?.errors
  return v === undefined || v === null ? '—' : v.toLocaleString('zh-CN')
})

const cacheRate = computed(() => {
  if (!metrics.value) return '—'
  const hits = metrics.value.cache_hits ?? 0
  const misses = metrics.value.cache_misses ?? 0
  const total = hits + misses
  if (!total) return '—'
  return `${Math.round((hits / total) * 100)}%`
})

const lastTrace = computed(() => {
  if (!caps.value) return '—'
  const id = caps.value.last_trace_id
  return id ? id.slice(0, 8) : '未产生'
})

const sandboxText = computed(() => {
  const mode = caps.value?.sandbox_mode
  if (mode === 'docker') return 'Docker 隔离'
  if (mode === 'mock') return 'Mock 降级'
  return '—'
})

const defenses = computed(() => caps.value?.defenses ?? [])

// ---- 栏二：成本看板 ----
const costText = computed(() => {
  const c = metrics.value?.cost_cents
  if (c === undefined || c === null) return '—'
  return `¥${(Number.isFinite(c) ? c / 100 : 0).toFixed(2)}`
})

function metricNum(v: number | null | undefined): string {
  if (v === null || v === undefined) return '—'
  const n = Number(v)
  return Number.isFinite(n) ? n.toLocaleString('zh-CN') : '—'
}

const COST_TIERS = [
  { tag: 'L1', text: '智能模型路由：按任务复杂度路由 turbo / plus / coder / max。' },
  { tag: 'L2', text: '语义缓存：相似度 >0.95 命中缓存，重复提问近乎零成本。' },
  { tag: 'L3', text: 'Prompt 压缩：长上下文压缩至约 40%，保留实体 / 数字 / 引用。' },
  { tag: 'L4', text: '批处理：文献解析与 KG 导入攒批，摊薄调用次数。' },
  { tag: 'L5', text: 'Token 预算管控：每会话配额，超限自动降级并提示。' },
]

// ---- 栏三：评估中心 ----
const confidenceMean = computed(() => {
  const vals = Object.values(session.confidenceScores)
    .map((v) => Number(v))
    .filter((v) => Number.isFinite(v))
  if (vals.length) return vals.reduce((a, b) => a + b, 0) / vals.length
  return session.averageConfidence
})

interface EvalRow {
  tag: string
  name: string
  value: string
  status: string
  tone: 'ok' | 'wait' | 'warn'
}

const evalRows = computed<EvalRow[]>(() => {
  // L1 基础能力：幻觉触发 + 熔断次数
  let l1: EvalRow
  if (!metrics.value) {
    l1 = { tag: 'L1', name: '基础能力', value: '—', status: '待收集', tone: 'wait' }
  } else {
    const flags = Object.values(metrics.value.hallucination_flags ?? {}).reduce((s, n) => s + Number(n), 0)
    const blocked = metrics.value.guard_blocks ?? 0
    const bad = flags + blocked
    l1 = {
      tag: 'L1',
      name: '基础能力',
      value: `幻觉触发 ${flags} 次 · 熔断 ${blocked} 次`,
      status: bad === 0 ? '通过' : '待提升',
      tone: bad === 0 ? 'ok' : 'warn',
    }
  }

  // L2 组件性能：平均置信度
  const c = confidenceMean.value
  const l2: EvalRow =
    c >= 0.8
      ? { tag: 'L2', name: '组件性能', value: `平均置信度 ${c.toFixed(2)}`, status: '通过', tone: 'ok' }
      : c > 0
        ? { tag: 'L2', name: '组件性能', value: `平均置信度 ${c.toFixed(2)}`, status: '待提升', tone: 'warn' }
        : { tag: 'L2', name: '组件性能', value: '—', status: '待收集', tone: 'wait' }

  // L3 任务质量：沙箱执行
  const mode = caps.value?.sandbox_mode
  const l3: EvalRow =
    mode === 'docker'
      ? { tag: 'L3', name: '任务质量', value: '沙箱执行 · Docker 就绪', status: '通过', tone: 'ok' }
      : mode === 'mock'
        ? { tag: 'L3', name: '任务质量', value: '沙箱执行 · Mock 降级', status: '待提升', tone: 'warn' }
        : { tag: 'L3', name: '任务质量', value: '—', status: '待收集', tone: 'wait' }

  // L4 业务效果：人工复核
  let l4: EvalRow
  if (!session.status) {
    l4 = { tag: 'L4', name: '业务效果', value: '—', status: '待收集', tone: 'wait' }
  } else if (session.humanReviewRequired) {
    l4 = { tag: 'L4', name: '业务效果', value: '人工复核 · 需要', status: '待提升', tone: 'warn' }
  } else {
    l4 = { tag: 'L4', name: '业务效果', value: '人工复核 · 不需要', status: '通过', tone: 'ok' }
  }

  return [l1, l2, l3, l4]
})
</script>

<template>
  <div class="sys-grid">
    <!-- 栏一：可观测性 -->
    <article class="sys-panel">
      <header class="sys-head">
        <span class="sys-title serif">可观测性</span>
        <span class="sys-cap mono">OBSERVABILITY</span>
      </header>
      <div class="sys-body">
        <div class="sys-kv">
          <div><span>平均耗时</span><b class="mono">{{ avgLatency }}</b></div>
          <div><span>错误数</span><b class="mono">{{ errorsText }}</b></div>
          <div><span>缓存命中率</span><b class="mono">{{ cacheRate }}</b></div>
          <div><span>最近 Trace</span><b class="mono">{{ lastTrace }}</b></div>
          <div><span>沙箱模式</span><b>{{ sandboxText }}</b></div>
        </div>

        <div class="sys-sub mono">六道防线</div>
        <div v-if="defenses.length" class="sys-fence">
          <div v-for="d in defenses" :key="d.key" class="fence" :class="d.active ? 'is-ok' : 'is-warn'">
            <span class="fence-mark" :class="d.active ? 'is-ok' : 'is-warn'">{{ d.active ? '✓' : '◌' }}</span>
            <span class="fence-name">{{ d.label }}</span>
            <span class="fence-state mono">{{ d.note || (d.active ? '已启用' : '未启用') }}</span>
          </div>
        </div>
        <div v-else class="sys-empty">—</div>
      </div>
    </article>

    <!-- 栏二：成本看板 -->
    <article class="sys-panel">
      <header class="sys-head">
        <span class="sys-title serif">成本看板</span>
        <span class="sys-cap mono">COST</span>
      </header>
      <div class="sys-body">
        <div class="sys-cost">
          <span class="sys-cost-big serif">{{ costText }}</span>
          <span class="sys-cost-note">累计 API 支出</span>
        </div>

        <div class="sys-kv">
          <div><span>Token 总量</span><b class="mono">{{ metricNum(metrics?.total_tokens) }}</b></div>
          <div><span>提示 Token</span><b class="mono">{{ metricNum(metrics?.prompt_tokens) }}</b></div>
          <div><span>补全 Token</span><b class="mono">{{ metricNum(metrics?.completion_tokens) }}</b></div>
          <div><span>请求数</span><b class="mono">{{ metricNum(metrics?.request_count) }}</b></div>
        </div>

        <div class="sys-sub mono">五层成本策略</div>
        <div class="sys-tier-list">
          <div v-for="t in COST_TIERS" :key="t.tag" class="sys-tier">
            <span class="sys-tier-tag mono">{{ t.tag }}</span>
            <span class="sys-tier-text">{{ t.text }}</span>
          </div>
        </div>
      </div>
    </article>

    <!-- 栏三：评估中心 -->
    <article class="sys-panel">
      <header class="sys-head">
        <span class="sys-title serif">评估中心</span>
        <span class="sys-cap mono">EVALUATION</span>
      </header>
      <div class="sys-body">
        <div class="sys-eval">
          <div v-for="r in evalRows" :key="r.tag" class="sys-eval-row">
            <span class="sys-eval-tag mono">{{ r.tag }}</span>
            <span class="sys-eval-main">
              <span class="sys-eval-name">{{ r.name }}</span>
              <span class="sys-eval-val mono">{{ r.value }}</span>
            </span>
            <span class="sys-pill" :class="r.tone">{{ r.status }}</span>
          </div>
        </div>
      </div>
    </article>
  </div>
</template>
