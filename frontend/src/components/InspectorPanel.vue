<script setup lang="ts">
// 右侧可折叠 Inspector：阶段、置信度、人工复核、错误、耗时、会话元信息
import { computed } from 'vue'
import ConfidenceGauge from './ConfidenceGauge.vue'
import { useSessionStore } from '@/stores/session'

const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ (e: 'update:open', value: boolean): void }>()

const store = useSessionStore()

// 智能体英文名 → 中文标签
const AGENT_LABELS: Record<string, string> = {
  supervisor: '总控调度',
  retriever: '文献检索',
  data_analyst: '数据分析',
  experiment_designer: '实验设计',
  writing_assistant: '论文写作',
  academic_reviewer: '学术审阅',
  kg_builder: '知识图谱',
  hallucination: '幻觉防御',
}

const PHASE_LABELS: Record<string, string> = {
  literature: '文献调研',
  experiment: '实验执行',
  writing: '论文写作',
  review: '学术审阅',
}

function agentLabel(key: string): string {
  return AGENT_LABELS[key] ?? key
}

function phaseLabel(p: string): string {
  return PHASE_LABELS[p] ?? (p || '未开始')
}

const scoreEntries = computed(() =>
  Object.entries(store.confidenceScores).map(([k, v]) => ({
    key: k,
    label: agentLabel(k),
    value: Number.isFinite(Number(v)) ? Math.max(0, Math.min(1, Number(v))) : 0,
  })),
)

const overall = computed(() => {
  const vals = scoreEntries.value.map((s) => s.value)
  if (vals.length) return vals.reduce((a, b) => a + b, 0) / vals.length
  const last = [...store.messages].reverse().find((m) => m.role === 'assistant' && !m.error)
  return last?.confidence ?? 0
})

const phase = computed(() => store.phase || store.status?.current_phase || '')

const latencyText = computed(() => {
  const ms = store.lastLatencyMs
  if (ms === null) return '—'
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`
})

const elapsedText = computed(() => `${(store.elapsedMs / 1000).toFixed(1)}s`)

function toggle(): void {
  emit('update:open', !props.open)
}
</script>

<template>
  <div class="inspector" :class="{ 'is-closed': !props.open }">
    <button class="toggle" :title="props.open ? '收起检查面板' : '展开检查面板'" @click="toggle">
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path :d="props.open ? 'M15 6l-6 6 6 6' : 'M9 6l6 6-6 6'" />
      </svg>
    </button>

    <div v-show="props.open" class="insp-body">
      <div class="insp-head">
        <span class="eyebrow">INSPECTOR</span>
        <span class="chip" :class="store.phase === 'literature' ? '' : 'is-accent'">{{ phaseLabel(phase) }}</span>
      </div>

      <!-- 置信度仪表 -->
      <section class="block">
        <div class="block-label eyebrow">综合置信度</div>
        <ConfidenceGauge :value="overall" label="模型自评" sublabel="OVERALL CONFIDENCE" :size="176" />
      </section>

      <!-- 人工复核 / 错误 -->
      <section class="block">
        <div class="block-label eyebrow">状态标记</div>
        <div class="flags">
          <div class="flag" :class="store.humanReviewRequired ? 'is-amber' : 'is-ok'">
            <span class="dot" :class="store.humanReviewRequired ? 'is-warn' : 'is-ok'" />
            <span>人工复核{{ store.humanReviewRequired ? '：需要' : '：不需要' }}</span>
          </div>
          <div v-if="store.status?.error" class="flag is-danger">
            <span class="dot is-err" />
            <span class="flag-text">{{ store.status.error }}</span>
          </div>
          <div v-if="store.error" class="flag is-danger">
            <span class="dot is-err" />
            <span class="flag-text">{{ store.error }}</span>
          </div>
        </div>
      </section>

      <!-- 分智能体置信度 -->
      <section class="block">
        <div class="block-label eyebrow">分智能体置信度</div>
        <div v-if="scoreEntries.length" class="scores">
          <div v-for="s in scoreEntries" :key="s.key" class="score-row">
            <span class="score-label">{{ s.label }}</span>
            <div class="bar">
              <div class="bar-fill" :style="{ width: `${s.value * 100}%` }" />
            </div>
            <span class="score-val mono">{{ (s.value * 100).toFixed(0) }}%</span>
          </div>
        </div>
        <div v-else class="block-empty">暂无置信度数据</div>
      </section>

      <!-- 请求耗时 -->
      <section class="block">
        <div class="block-label eyebrow">请求耗时</div>
        <div class="latency-top">
          <span class="latency-big mono">{{ latencyText }}</span>
          <span class="latency-note">最近一次请求</span>
        </div>
        <div v-if="store.loading" class="latency-live">
          <span class="dot is-ok" />
          <span class="mono">{{ store.activeLabel }} · 已等待 {{ elapsedText }}</span>
        </div>
        <ul v-if="store.latencyLog.length" class="latency-list">
          <li v-for="(l, i) in store.latencyLog" :key="i">
            <span class="mono t">{{ l.at }}</span>
            <span class="l-name">{{ l.label }}</span>
            <span class="mono t">{{ l.ms >= 1000 ? (l.ms / 1000).toFixed(1) + 's' : l.ms + 'ms' }}</span>
          </li>
        </ul>
      </section>

      <!-- 会话元信息 -->
      <section class="block">
        <div class="block-label eyebrow">会话元信息</div>
        <dl class="meta">
          <div><dt>会话 ID</dt><dd class="mono">{{ store.sessionId || '—' }}</dd></div>
          <div><dt>研究主题</dt><dd>{{ store.topic || '—' }}</dd></div>
          <div><dt>文献数量</dt><dd class="mono">{{ store.papersCount }}</dd></div>
          <div><dt>引用链结论</dt><dd class="mono">{{ store.claims.length }}</dd></div>
          <div><dt>平均置信度</dt><dd class="mono">{{ (store.averageConfidence * 100).toFixed(0) }}%</dd></div>
          <div><dt>数据文件</dt><dd class="mono">{{ store.status?.has_data_file ? '已上传' : '未上传' }}</dd></div>
          <div><dt>草稿</dt><dd class="mono">{{ store.status?.has_draft ? '已生成' : '未生成' }}</dd></div>
        </dl>
      </section>

      <section class="block cost">
        <div class="block-label eyebrow">运行成本</div>
        <p class="cost-note">未接入 —— 后端暂未暴露 token / 费用字段，此处不展示推算数据。</p>
      </section>
    </div>
  </div>
</template>

<style scoped>
.inspector {
  position: relative;
  flex: 0 0 var(--inspector-w);
  width: var(--inspector-w);
  border-left: 1px solid var(--hair);
  background: linear-gradient(180deg, rgba(14, 20, 32, 0.82), rgba(11, 15, 20, 0.6));
  transition: flex-basis 0.28s var(--ease), width 0.28s var(--ease);
  overflow: hidden;
  display: flex;
}

.inspector.is-closed {
  flex-basis: 34px;
  width: 34px;
}

.toggle {
  position: absolute;
  left: 4px;
  top: 12px;
  z-index: 3;
  width: 24px;
  height: 24px;
  display: grid;
  place-items: center;
  border: 1px solid var(--hair);
  border-radius: var(--radius-xs);
  background: rgba(11, 15, 20, 0.9);
  color: var(--text-2);
  cursor: pointer;
  transition: all 0.2s var(--ease);
}

.toggle:hover {
  color: var(--accent);
  border-color: var(--accent-line);
  background: var(--accent-soft);
}

.toggle svg {
  width: 14px;
  height: 14px;
  fill: none;
  stroke: currentColor;
  stroke-width: 1.8;
  stroke-linecap: round;
  stroke-linejoin: round;
}

.insp-body {
  flex: 1 1 auto;
  min-width: 0;
  overflow-y: auto;
  padding: 16px 16px 30px 38px;
  display: flex;
  flex-direction: column;
  gap: 18px;
  animation: fadeIn 0.3s var(--ease) both;
}

.insp-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding-bottom: 10px;
  border-bottom: 1px solid var(--hair-soft);
}

.block {
  display: flex;
  flex-direction: column;
  gap: 9px;
}

.block-label {
  padding-bottom: 2px;
}

.flags {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.flag {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 7px 10px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--hair);
  background: rgba(26, 35, 49, 0.45);
  font-size: 12px;
  color: var(--text-2);
}

.flag.is-ok {
  border-color: rgba(61, 214, 196, 0.22);
}

.flag.is-amber {
  border-color: var(--amber-line);
  background: var(--amber-soft);
  color: #f0d49a;
}

.flag.is-danger {
  border-color: var(--danger-line);
  background: var(--danger-soft);
  color: #f0b3ad;
}

.flag-text {
  word-break: break-word;
  line-height: 1.5;
}

.scores {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.score-row {
  display: grid;
  grid-template-columns: 74px 1fr 38px;
  align-items: center;
  gap: 8px;
}

.score-label {
  font-size: 11.5px;
  color: var(--text-2);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.bar {
  height: 5px;
  border-radius: 3px;
  background: var(--ink-750);
  overflow: hidden;
}

.bar-fill {
  height: 100%;
  border-radius: 3px;
  background: linear-gradient(90deg, var(--accent-deep), var(--accent));
  transition: width 0.6s var(--ease);
  box-shadow: 0 0 8px var(--accent-glow);
}

.score-val {
  font-size: 10.5px;
  color: var(--text-2);
  text-align: right;
}

.block-empty {
  font-size: 12px;
  color: var(--text-3);
  padding: 6px 0;
}

.latency-top {
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.latency-big {
  font-size: 22px;
  color: var(--text-1);
  letter-spacing: -0.02em;
}

.latency-note {
  font-size: 11px;
  color: var(--text-3);
}

.latency-live {
  display: flex;
  align-items: center;
  gap: 7px;
  font-size: 11.5px;
  color: var(--accent);
}

.latency-list {
  list-style: none;
  margin: 4px 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 3px;
  max-height: 148px;
  overflow-y: auto;
}

.latency-list li {
  display: grid;
  grid-template-columns: 58px 1fr auto;
  gap: 8px;
  align-items: center;
  font-size: 11px;
  color: var(--text-2);
  padding: 3px 0;
  border-bottom: 1px dashed var(--hair-soft);
}

.latency-list li .t {
  color: var(--text-3);
  font-size: 10px;
}

.l-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.meta {
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.meta > div {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px;
}

.meta dt {
  font-size: 11.5px;
  color: var(--text-3);
  flex: 0 0 auto;
}

.meta dd {
  margin: 0;
  font-size: 11.5px;
  color: var(--text-1);
  text-align: right;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 172px;
}

.cost-note {
  margin: 0;
  font-size: 11.5px;
  line-height: 1.6;
  color: var(--text-3);
  padding: 8px 10px;
  border: 1px dashed var(--hair);
  border-radius: var(--radius-sm);
}
</style>
