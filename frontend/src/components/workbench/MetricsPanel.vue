<script setup lang="ts">
// 实时指标面板：总成本 / Token 用量 / 请求数 / 幻觉触发按层 / 缓存命中率 / 错误数 / 平均耗时 / 防线拦截
// 数据由 system store 每 15s 轮询，后端离线时展示占位符且不报错
import { computed } from 'vue'
import { useSystemStore } from '@/stores/system'

const store = useSystemStore()

const costYuan = computed(() => {
  const c = store.metrics?.cost_cents ?? 0
  return (Number.isFinite(c) ? c : 0) / 100
})

const cacheRate = computed<number | null>(() => {
  const hits = store.metrics?.cache_hits ?? 0
  const misses = store.metrics?.cache_misses ?? 0
  const total = hits + misses
  return total ? (hits / total) * 100 : null
})

const flags = computed(() => Object.entries(store.metrics?.hallucination_flags ?? {}))
const flagTotal = computed(() => flags.value.reduce((sum, [, n]) => sum + Number(n), 0))

const totalTokens = computed(() => store.metrics?.total_tokens ?? 0)
const promptTokens = computed(() => store.metrics?.prompt_tokens ?? 0)
const completionTokens = computed(() => store.metrics?.completion_tokens ?? 0)
const promptPct = computed(() => {
  const t = totalTokens.value
  return t ? ((promptTokens.value / t) * 100).toFixed(1) : '0'
})
const completionPct = computed(() => {
  const t = totalTokens.value
  return t ? ((completionTokens.value / t) * 100).toFixed(1) : '0'
})

const latencyText = computed(() => {
  const v = store.metrics?.avg_latency_s
  // 0 表示尚无请求样本，如实显示「—」而非 0.00s
  if (!v) return '—'
  return `${Number(v).toFixed(2)}s`
})

const errorCount = computed(() => store.metrics?.errors ?? 0)

/** 数值占位：undefined/null 显示 —，数字走千分位 */
function num(v: number | null | undefined, digits = 0): string {
  if (v === null || v === undefined) return '—'
  const n = Number(v)
  return Number.isFinite(n) ? n.toLocaleString('zh-CN', { maximumFractionDigits: digits }) : '—'
}
</script>

<template>
  <section class="panel metric-zone">
    <header class="panel-head">
      <span class="panel-title serif">实时指标</span>
      <span class="panel-sub metric-refresh" :class="{ 'is-live': store.pollingOn }">
        <span class="dot" :class="{ 'is-ok': store.metricsLoading }" />
        每 15s 自动刷新
      </span>
    </header>
    <div class="panel-body metric-body">
      <div v-if="store.metricsLoading" class="loading-bar metric-loading" />

      <div class="metric-grid">
        <div class="metric-tile is-cost">
          <span class="mt-label">总成本</span>
          <span class="mt-value">¥{{ costYuan.toFixed(2) }}</span>
          <span class="mt-sub">累计 API 支出</span>
        </div>
        <div class="metric-tile">
          <span class="mt-label">请求数</span>
          <span class="mt-value">{{ num(store.metrics?.request_count) }}</span>
          <span class="mt-sub">累计会话请求</span>
        </div>
        <div class="metric-tile">
          <span class="mt-label">缓存命中率</span>
          <span class="mt-value">{{ cacheRate === null ? '—' : num(cacheRate, 1) }}<small v-if="cacheRate !== null">%</small></span>
          <span class="mt-sub">命中 {{ num(store.metrics?.cache_hits) }} · 未中 {{ num(store.metrics?.cache_misses) }}</span>
        </div>
        <div class="metric-tile">
          <span class="mt-label">错误数</span>
          <span class="mt-value" :class="{ 'is-danger': errorCount > 0 }">{{ num(errorCount) }}</span>
          <span class="mt-sub">累计失败请求</span>
        </div>
        <div class="metric-tile">
          <span class="mt-label">平均耗时</span>
          <span class="mt-value">{{ latencyText }}</span>
          <span class="mt-sub">单次请求延迟</span>
        </div>

        <div class="metric-tile is-wide">
          <span class="mt-label">Token 用量</span>
          <span class="mt-value">{{ num(totalTokens) }}<small> tok</small></span>
          <div class="tok-bar">
            <i class="tok-prompt" :style="{ width: promptPct + '%' }" />
            <i class="tok-completion" :style="{ width: completionPct + '%' }" />
          </div>
          <div class="tok-legend">
            <span class="tok-legend-item"><i class="sw prompt" />提示 {{ num(promptTokens) }}</span>
            <span class="tok-legend-item"><i class="sw completion" />补全 {{ num(completionTokens) }}</span>
          </div>
        </div>

        <div class="metric-tile is-wide">
          <span class="mt-label">幻觉触发</span>
          <span class="mt-value">{{ num(flagTotal) }}<small> 次</small></span>
          <div v-if="flags.length" class="flag-chips">
            <span v-for="[layer, count] in flags" :key="layer" class="flag-chip mono">{{ layer }} × {{ count }}</span>
          </div>
          <div v-else class="flag-empty">按评估层累计 · 当前无触发</div>
        </div>

        <div class="metric-tile">
          <span class="mt-label">防线拦截</span>
          <span class="mt-value">{{ num(store.metrics?.guard_blocks) }}</span>
          <span class="mt-sub">幻觉防线熔断次数</span>
        </div>
      </div>
    </div>
  </section>
</template>
