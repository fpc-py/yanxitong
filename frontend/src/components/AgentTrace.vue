<script setup lang="ts">
// 推理轨迹面板（规格⑥）：agent 执行 span + 幻觉标记 + 审计事件
// 数据源 /api/kg/trace/{session_id}（audit.db）
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api, { extractErrorMessage } from '@/api/client'
import type { KgTraceResponse } from '@/api/types'
import { useSessionStore } from '@/stores/session'

const store = useSessionStore()

const data = ref<KgTraceResponse | null>(null)
const loading = ref(false)
const activeTab = ref('traces')

const traces = computed(() => [...(data.value?.traces ?? [])].reverse())
const flags = computed(() => [...(data.value?.flags ?? [])].reverse())
const audit = computed(() => [...(data.value?.audit ?? [])].reverse())

const totalMs = computed(() => traces.value.reduce((sum, t) => sum + (t.duration_ms || 0), 0))

function fmtTime(ts?: number): string {
  if (!ts) return ''
  return new Date(ts * 1000).toLocaleTimeString('zh-CN', { hour12: false })
}

function fmtMs(ms?: number): string {
  if (!ms && ms !== 0) return ''
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.round(ms)}ms`
}

function riskTone(level: string): string {
  if (level === 'high' || level === 'critical') return 'is-danger'
  if (level === 'medium') return 'is-warn'
  return ''
}

async function load(): Promise<void> {
  if (!store.sessionId) return
  loading.value = true
  try {
    data.value = await api.getKgTrace(store.sessionId)
  } catch (err) {
    ElMessage.error(extractErrorMessage(err))
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  if (store.hasSession) void load()
})

defineExpose({ load })
</script>

<template>
  <div v-loading="loading" class="trace-panel">
    <div v-if="!store.hasSession" class="empty">
      <span class="empty-icon mono">◌</span>
      <span>当前没有活跃会话</span>
    </div>

    <template v-else>
      <div class="trace-stats mono">
        <span>{{ traces.length }} SPANS</span>
        <span>总耗时 {{ fmtMs(totalMs) || '0ms' }}</span>
        <span>{{ flags.length }} FLAGS</span>
        <span>{{ audit.length }} AUDIT</span>
        <el-button size="small" text class="trace-reload" @click="load">刷新</el-button>
      </div>

      <el-tabs v-model="activeTab" class="trace-tabs">
        <el-tab-pane name="traces">
          <template #label>
            执行轨迹
            <span class="tab-badge mono">{{ traces.length }}</span>
          </template>
          <div v-if="traces.length" class="span-list">
            <div v-for="(t, i) in traces" :key="t.id ?? i" class="span-item">
              <span class="span-time mono">{{ fmtTime(t.created_at) }}</span>
              <span class="span-agent">{{ t.agent }}</span>
              <span class="span-status mono" :class="{ failed: t.status !== 'success' }">{{ t.status }}</span>
              <span class="span-dur mono">{{ fmtMs(t.duration_ms) }}</span>
            </div>
          </div>
          <div v-else class="empty small">
            <span class="empty-icon mono">◌</span>
            <span>暂无执行轨迹</span>
            <span class="hint-line">每次问答的多智能体执行会自动记录 agent / 状态 / 耗时。</span>
          </div>
        </el-tab-pane>

        <el-tab-pane name="flags">
          <template #label>
            幻觉标记
            <span class="tab-badge mono">{{ flags.length }}</span>
          </template>
          <div v-if="flags.length" class="flag-list">
            <article v-for="(f, i) in flags" :key="f.id ?? i" class="flag-item" :class="riskTone(f.risk_level)">
              <div class="flag-head">
                <span class="chip" :class="{ 'is-danger': f.risk_level === 'high' || f.risk_level === 'critical' }">
                  {{ f.layer }}
                </span>
                <span class="flag-risk mono">{{ f.risk_level }}</span>
                <span class="flag-time mono">{{ fmtTime(f.created_at) }}</span>
              </div>
              <pre v-if="f.detail" class="flag-detail mono">{{ JSON.stringify(f.detail, null, 2) }}</pre>
            </article>
          </div>
          <div v-else class="empty small">
            <span class="empty-icon mono">⚖</span>
            <span>本会话暂无幻觉标记</span>
            <span class="hint-line">三元组冲突或质量门禁升级时会在此落库（audit.db）。</span>
          </div>
        </el-tab-pane>

        <el-tab-pane name="audit">
          <template #label>
            审计事件
            <span class="tab-badge mono">{{ audit.length }}</span>
          </template>
          <div v-if="audit.length" class="span-list">
            <div v-for="(a, i) in audit" :key="a.id ?? i" class="span-item">
              <span class="span-time mono">{{ fmtTime(a.created_at) }}</span>
              <span class="span-agent">{{ a.agent }}</span>
              <span class="span-action mono">{{ a.action }}</span>
            </div>
          </div>
          <div v-else class="empty small">
            <span class="empty-icon mono">◌</span>
            <span>暂无审计事件</span>
            <span class="hint-line">建图统计、引用门控、复核操作等关键事件会记入审计日志。</span>
          </div>
        </el-tab-pane>
      </el-tabs>
    </template>
  </div>
</template>

<style scoped>
.trace-stats {
  display: flex;
  align-items: center;
  gap: 16px;
  font-size: 10.5px;
  letter-spacing: 0.05em;
  color: var(--text-3);
  padding-bottom: 8px;
}

.trace-reload {
  margin-left: auto;
}

.trace-tabs :deep(.el-tabs__header) {
  margin-bottom: 14px;
}

.tab-badge {
  display: inline-block;
  margin-left: 6px;
  padding: 0 6px;
  border-radius: 8px;
  font-size: 10px;
  line-height: 16px;
  color: var(--text-2);
  background: var(--ink-750);
  border: 1px solid var(--hair-soft);
}

.span-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.span-item {
  display: grid;
  grid-template-columns: 84px 1fr 84px 64px;
  gap: 10px;
  align-items: baseline;
  padding: 8px 12px;
  border-radius: 6px;
  font-size: 12.5px;
  color: var(--text-1);
}

.span-item:nth-child(odd) {
  background: rgba(255, 255, 255, 0.75);
}

.span-time,
.span-status,
.span-dur,
.span-action {
  font-size: 10.5px;
  color: var(--text-3);
  letter-spacing: 0.03em;
}

.span-status.failed {
  color: var(--danger);
}

.span-dur {
  text-align: right;
}

.flag-list {
  display: flex;
  flex-direction: column;
  gap: 9px;
}

.flag-item {
  padding: 11px 14px;
  border: 1px solid var(--hair-soft);
  border-radius: var(--radius);
  background: rgba(255, 255, 255, 0.9);
}

.flag-item.is-danger {
  border-color: var(--danger);
}

.flag-item.is-warn {
  border-color: var(--amber, #b98a33);
}

.flag-head {
  display: flex;
  align-items: center;
  gap: 10px;
}

.flag-risk {
  font-size: 11px;
  color: var(--text-2);
}

.flag-time {
  margin-left: auto;
  font-size: 10.5px;
  color: var(--text-3);
}

.flag-detail {
  margin: 9px 0 0;
  padding: 9px 11px;
  max-height: 180px;
  overflow: auto;
  font-size: 10.5px;
  line-height: 1.55;
  color: var(--text-2);
  background: var(--ink-750);
  border-radius: 6px;
  white-space: pre-wrap;
  word-break: break-word;
}

.empty.small {
  padding: 46px 16px;
  gap: 10px;
}
</style>
