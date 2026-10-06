<script setup lang="ts">
// 右侧可折叠 Inspector（对应 v3.0 原型 .insp）：
// 综合置信度 / 状态标记 / 六道幻觉防线 / 分智能体置信度 / 请求耗时·成本 / 执行轨迹 / 会话元信息
// 数据全部来自 session 与 system 两个 store，后端离线时显示占位符且不报错
import { computed, onMounted, onUnmounted } from 'vue'
import { useSessionStore } from '@/stores/session'
import { useSystemStore } from '@/stores/system'

const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ (e: 'update:open', value: boolean): void }>()

const store = useSessionStore()
const system = useSystemStore()

// 智能体英文名 → 中文标签
const AGENT_LABELS: Record<string, string> = {
  supervisor: '总控调度',
  retriever: '文献检索',
  kg_builder: '知识图谱',
  data_analyst: '数据分析',
  experiment_designer: '实验设计',
  writing_assistant: '论文写作',
  academic_reviewer: '学术审阅',
  hallucination: '幻觉防御',
}

const PHASE_LABELS: Record<string, string> = {
  literature: '文献调研',
  experiment: '实验执行',
  writing: '论文写作',
  review: '学术审阅',
}

function phaseLabel(p: string): string {
  return PHASE_LABELS[p] ?? (p || '未开始')
}

const phase = computed(() => store.phase || store.status?.current_phase || '')

// ---- 综合置信度 ----
const scoreEntries = computed(() =>
  Object.entries(store.confidenceScores).map(([k, v]) => ({
    key: k,
    label: AGENT_LABELS[k] ?? k,
    value: Number.isFinite(Number(v)) ? Math.max(0, Math.min(1, Number(v))) : 0,
  })),
)

const overall = computed(() => {
  const vals = scoreEntries.value.map((s) => s.value)
  if (vals.length) return vals.reduce((a, b) => a + b, 0) / vals.length
  const last = [...store.messages].reverse().find((m) => m.role === 'assistant' && !m.error)
  return last?.confidence ?? 0
})

const confPct = computed(() => Math.round(overall.value * 100))

// 环形进度：r=55 → 周长 ≈ 345.6
const RING_CIRC = 2 * Math.PI * 55
const ringOffset = computed(() => RING_CIRC * (1 - overall.value))

// ---- 状态标记 ----
const errors = computed(() => {
  const list: string[] = []
  if (store.status?.error) list.push(store.status.error)
  if (store.error && store.error !== store.status?.error) list.push(store.error)
  return list
})

// ---- 六道幻觉防线：状态来自后端真实能力探测，右侧数值取自当前会话 ----
const defenses = computed(() => system.capabilities?.defenses ?? [])

function defenseValue(key: string, active: boolean, note: string): string {
  switch (key) {
    case 'rag':
      return `${store.papersCount} 篇`
    case 'citation':
      return `${store.claims.length} 条`
    case 'calibration':
      return `${confPct.value}%`
    case 'human_breaker':
      return store.humanReviewRequired ? '已触发' : '未触发'
    case 'self_consistency':
      return note || '规划中'
    default:
      return active ? note || '已启用' : note || '未启用'
  }
}

// ---- 请求耗时 / 成本 ----
const latencyText = computed(() => {
  const ms = store.lastLatencyMs
  if (ms === null) return '—'
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`
})
const elapsedText = computed(() => `${(store.elapsedMs / 1000).toFixed(1)}s`)

const tokensText = computed(() => {
  const t = system.metrics?.total_tokens
  return t === undefined || t === null ? '—' : t.toLocaleString('zh-CN')
})

const cacheHitText = computed(() => {
  const hits = system.metrics?.cache_hits ?? 0
  const misses = system.metrics?.cache_misses ?? 0
  const total = hits + misses
  if (!total) return '—'
  return `${Math.round((hits / total) * 100)}%`
})

const costText = computed(() => {
  const c = system.metrics?.cost_cents
  if (c === undefined || c === null) return '—'
  return `¥${(Number.isFinite(c) ? c / 100 : 0).toFixed(2)}`
})

const requestCount = computed(() => system.metrics?.request_count ?? 0)

// ---- 执行轨迹 ----
const trail = computed(() => store.latencyLog)

function fmtMs(ms: number): string {
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`
}

function toggle(): void {
  emit('update:open', !props.open)
}

// ---- 环境数据：概览页已轮询时不重复请求 ----
let ambientTimer: number | null = null

onMounted(() => {
  void system.loadCapabilities()
  void system.loadMetrics()
  ambientTimer = window.setInterval(() => {
    if (system.pollingOn) return
    void system.loadCapabilities()
    void system.loadMetrics()
  }, 30_000)
})

onUnmounted(() => {
  if (ambientTimer !== null) window.clearInterval(ambientTimer)
})
</script>

<template>
  <aside class="inspector" :class="{ 'is-closed': !props.open }">
    <button class="insp-toggle" :title="props.open ? '收起检查面板' : '展开检查面板'" @click="toggle">
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path :d="props.open ? 'M9 6l6 6-6 6' : 'M15 6l-6 6 6 6'" />
      </svg>
    </button>

    <div v-show="props.open" class="insp-pad">
      <div class="insp-head">
        <span class="insp-title mono">INSPECTOR</span>
      </div>

      <!-- 综合置信度 -->
      <section class="insp-sec">
        <div class="insp-sec-t mono">综合置信度</div>
        <div class="conf-wrap">
          <div class="conf-ring">
            <svg viewBox="0 0 120 120">
              <circle class="ring-bg" cx="60" cy="60" r="55" />
              <circle
                class="ring-fg"
                cx="60"
                cy="60"
                r="55"
                :stroke-dasharray="RING_CIRC"
                :stroke-dashoffset="ringOffset"
              />
            </svg>
            <div class="conf-num">
              <div class="v serif">{{ confPct }}<span>%</span></div>
              <div class="l mono">CONFIDENCE</div>
            </div>
          </div>
        </div>
      </section>

      <!-- 状态标记 -->
      <section class="insp-sec">
        <div class="insp-sec-t mono">状态标记</div>
        <div class="status-card" :class="{ 'is-warn': store.humanReviewRequired }">
          <span class="d" />
          人工复核：{{ store.humanReviewRequired ? '需要' : '不需要' }}
        </div>
        <div v-for="(msg, i) in errors" :key="i" class="status-card is-err">
          <span class="d" />
          <span class="err-text">{{ msg }}</span>
        </div>
      </section>

      <!-- 六道幻觉防线 -->
      <section class="insp-sec">
        <div class="insp-sec-t mono">六道幻觉防线</div>
        <template v-if="defenses.length">
          <div
            v-for="d in defenses"
            :key="d.key"
            class="fence"
            :class="d.active ? 'is-ok' : 'is-warn'"
          >
            <svg class="fi" viewBox="0 0 24 24" aria-hidden="true">
              <path
                :d="
                  d.active
                    ? 'M5 13l4 4L19 7'
                    : 'M12 9v4M12 17h.01M10.3 3.9L1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z'
                "
              />
            </svg>
            <span class="fence-name">{{ d.label }}</span>
            <span class="n mono">{{ defenseValue(d.key, d.active, d.note) }}</span>
          </div>
        </template>
        <div v-else class="sec-empty">后端离线，防线状态暂不可用</div>
      </section>

      <!-- 分智能体置信度 -->
      <section class="insp-sec">
        <div class="insp-sec-t mono">分智能体置信度</div>
        <template v-if="scoreEntries.length">
          <div v-for="s in scoreEntries" :key="s.key" class="conf-row">
            <div class="lab">
              <span>{{ s.label }}</span>
              <b class="mono">{{ (s.value * 100).toFixed(0) }}%</b>
            </div>
            <div class="conf-bar">
              <i :style="{ width: `${s.value * 100}%` }" />
            </div>
          </div>
        </template>
        <div v-else class="sec-empty">暂无置信度数据</div>
      </section>

      <!-- 请求耗时 / 成本 -->
      <section class="insp-sec">
        <div class="insp-sec-t mono">请求耗时 / 成本</div>
        <div class="cost-big serif">{{ latencyText }}</div>
        <div class="cost-sub">
          {{ tokensText }} tokens · 缓存命中 {{ cacheHitText }}
        </div>
        <div v-if="store.loading" class="cost-live">
          <span class="d" />
          <span class="mono">{{ store.activeLabel }} · 已等待 {{ elapsedText }}</span>
        </div>
        <div class="cost-tags">
          <span class="tag mono">累计 {{ costText }}</span>
          <span class="tag mono">请求 {{ requestCount }} 次</span>
        </div>
      </section>

      <!-- 执行轨迹 -->
      <section v-if="trail.length" class="insp-sec">
        <div class="insp-sec-t mono">执行轨迹</div>
        <div class="tl">
          <div v-for="(l, i) in trail" :key="i" class="tl-item done">
            <div class="tl-row">
              <span class="t">{{ l.label }}</span>
              <span class="d mono">{{ fmtMs(l.ms) }}</span>
            </div>
          </div>
        </div>
      </section>

      <!-- 会话元信息 -->
      <section class="insp-sec">
        <div class="insp-sec-t mono">会话元信息</div>
        <div class="kv"><span class="k">Session</span><span class="v mono">{{ store.sessionId || '—' }}</span></div>
        <div class="kv"><span class="k">Topic</span><span class="v">{{ store.topic || '—' }}</span></div>
        <div class="kv"><span class="k">阶段</span><span class="v">{{ phaseLabel(phase) }}</span></div>
        <div class="kv"><span class="k">文献 / 结论</span><span class="v mono">{{ store.papersCount }} / {{ store.claims.length }}</span></div>
        <div class="kv"><span class="k">数据文件</span><span class="v mono">{{ store.status?.has_data_file ? '已上传' : '未上传' }}</span></div>
        <div class="kv"><span class="k">草稿</span><span class="v mono">{{ store.status?.has_draft ? '已生成' : '未生成' }}</span></div>
      </section>
    </div>
  </aside>
</template>

<style scoped>
.inspector {
  position: relative;
  flex: 0 0 var(--inspector-w);
  width: var(--inspector-w);
  border-left: 1px solid var(--line);
  background: var(--bg);
  transition: flex-basis 0.28s var(--ease), width 0.28s var(--ease);
  overflow: hidden;
}

.inspector.is-closed {
  flex-basis: 34px;
  width: 34px;
}

/* 展开时按钮在标题行右侧；收起时居中于 34px 竖条内，保证始终可点 */
.insp-toggle {
  position: absolute;
  top: 17px;
  right: 14px;
  z-index: 3;
  width: 26px;
  height: 26px;
  display: grid;
  place-items: center;
  border: 1px solid var(--line);
  border-radius: 7px;
  background: var(--raise);
  color: var(--ink-2);
  cursor: pointer;
  transition: all 0.18s var(--ease);
}

.insp-toggle:hover {
  color: var(--ink);
  border-color: var(--ink-3);
}

.insp-toggle svg {
  width: 13px;
  height: 13px;
  fill: none;
  stroke: currentColor;
  stroke-width: 2;
  stroke-linecap: round;
  stroke-linejoin: round;
}

.inspector.is-closed .insp-toggle {
  right: auto;
  left: 5px;
  top: 12px;
}

.insp-pad {
  height: 100%;
  overflow-y: auto;
  padding: 20px 18px 30px;
  animation: fadeIn 0.3s var(--ease) both;
}

.insp-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 18px;
  padding-right: 34px;
  min-height: 26px;
}

.insp-title {
  font-size: 10px;
  letter-spacing: 0.2em;
  color: var(--ink-3);
  font-weight: 500;
}

.insp-sec {
  margin-bottom: 22px;
}

.insp-sec-t {
  font-size: 9.5px;
  letter-spacing: 0.18em;
  color: var(--ink-3);
  text-transform: uppercase;
  margin-bottom: 10px;
  font-weight: 500;
}

.sec-empty {
  font-size: 11.5px;
  color: var(--ink-3);
  padding: 4px 0;
}

/* ---- 综合置信度环 ---- */
.conf-wrap {
  display: flex;
  justify-content: center;
  padding: 6px 0 4px;
}

.conf-ring {
  position: relative;
  width: 120px;
  height: 120px;
}

.conf-ring svg {
  width: 100%;
  height: 100%;
  transform: rotate(-90deg);
}

.ring-bg {
  fill: none;
  stroke: var(--line);
  stroke-width: 5;
}

.ring-fg {
  fill: none;
  stroke: var(--accent);
  stroke-width: 5;
  stroke-linecap: round;
  transition: stroke-dashoffset 0.7s var(--ease);
}

.conf-num {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
}

.conf-num .v {
  font-size: 30px;
  font-weight: 500;
  line-height: 1;
}

.conf-num .v span {
  font-size: 14px;
  color: var(--ink-2);
}

.conf-num .l {
  font-size: 9px;
  color: var(--ink-3);
  letter-spacing: 0.15em;
  margin-top: 3px;
}

/* ---- 状态标记 ---- */
.status-card {
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 9px 12px;
  background: var(--green-soft);
  border: 1px solid rgba(92, 122, 106, 0.25);
  border-radius: 8px;
  font-size: 12px;
  color: var(--green);
  font-weight: 500;
}

.status-card + .status-card {
  margin-top: 6px;
}

.status-card .d {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--green);
  box-shadow: 0 0 6px var(--green);
  flex: 0 0 auto;
}

.status-card.is-warn {
  background: var(--amber-soft);
  border-color: var(--amber-line);
  color: #8a6a20;
}

.status-card.is-warn .d {
  background: var(--amber);
  box-shadow: 0 0 6px var(--amber);
}

.status-card.is-err {
  background: var(--danger-soft);
  border-color: var(--danger-line);
  color: var(--danger);
  font-weight: 400;
}

.status-card.is-err .d {
  background: var(--danger);
  box-shadow: none;
}

.err-text {
  line-height: 1.5;
  word-break: break-word;
}

/* ---- 六道防线 ---- */
.fence {
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 6px 0;
  font-size: 12px;
  color: var(--ink-2);
  border-bottom: 1px dashed var(--line-soft);
}

.fence:last-child {
  border-bottom: none;
}

.fence.is-ok {
  color: var(--ink-1);
}

.fence .fi {
  width: 14px;
  height: 14px;
  flex: 0 0 auto;
  fill: none;
  stroke: currentColor;
  stroke-width: 2.4;
  stroke-linecap: round;
  stroke-linejoin: round;
}

.fence.is-ok .fi {
  color: var(--green);
}

.fence.is-warn .fi {
  color: var(--amber);
}

.fence-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.fence .n {
  margin-left: auto;
  font-size: 10px;
  color: var(--ink-3);
  flex: 0 0 auto;
}

/* ---- 分智能体置信度 ---- */
.conf-row {
  margin-bottom: 9px;
}

.conf-row:last-child {
  margin-bottom: 0;
}

.conf-row .lab {
  display: flex;
  justify-content: space-between;
  font-size: 11.5px;
  color: var(--ink-2);
  margin-bottom: 4px;
}

.conf-row .lab b {
  color: var(--ink-1);
  font-weight: 500;
  font-size: 10.5px;
}

.conf-bar {
  height: 3px;
  background: var(--soft);
  border-radius: 2px;
  overflow: hidden;
}

.conf-bar i {
  display: block;
  height: 100%;
  background: var(--accent);
  border-radius: 2px;
  transition: width 0.6s var(--ease);
}

/* ---- 请求耗时 / 成本 ---- */
.cost-big {
  font-size: 28px;
  font-weight: 500;
  line-height: 1;
}

.cost-sub {
  font-size: 11.5px;
  color: var(--ink-2);
  margin-top: 4px;
}

.cost-live {
  display: flex;
  align-items: center;
  gap: 7px;
  margin-top: 6px;
  font-size: 11.5px;
  color: var(--accent);
}

.cost-live .d {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--accent);
}

.cost-tags {
  display: flex;
  gap: 5px;
  flex-wrap: wrap;
  margin-top: 10px;
}

.tag {
  padding: 2px 8px;
  border-radius: 5px;
  font-size: 10px;
  font-weight: 500;
  background: var(--accent-soft);
  color: var(--accent);
}

.tag + .tag {
  background: var(--soft);
  color: var(--ink-2);
}

/* ---- 执行轨迹 ---- */
.tl {
  border-left: 1px solid var(--line);
  margin-left: 6px;
  padding-left: 14px;
}

.tl-item {
  position: relative;
  padding: 4px 0 10px;
}

.tl-item:last-child {
  padding-bottom: 0;
}

.tl-item::before {
  content: '';
  position: absolute;
  left: -17.5px;
  top: 8px;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--soft);
  border: 1.5px solid var(--line);
}

.tl-item.done::before {
  background: var(--green);
  border-color: var(--green);
}

.tl-row {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  font-size: 11.5px;
}

.tl-row .t {
  color: var(--ink-1);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tl-row .d {
  font-size: 10px;
  color: var(--ink-3);
  flex: 0 0 auto;
}

/* ---- 会话元信息 ---- */
.kv {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  font-size: 11.5px;
  padding: 6px 0;
  border-bottom: 1px dashed var(--line-soft);
}

.kv:last-child {
  border-bottom: none;
}

.kv .k {
  color: var(--ink-2);
  flex: 0 0 auto;
}

.kv .v {
  color: var(--ink);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 168px;
}
</style>
