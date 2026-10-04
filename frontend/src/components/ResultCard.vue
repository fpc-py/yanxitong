<script setup lang="ts">
// 通用结果卡片：标题 + 操作区 + 加载/错误/耗时状态
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{
    title: string
    eyebrow?: string
    loading?: boolean
    loadingText?: string
    error?: string | null
    elapsedMs?: number | null
  }>(),
  { eyebrow: '', loading: false, loadingText: '', error: null, elapsedMs: null },
)

const elapsedText = computed(() => {
  if (props.elapsedMs === null || props.elapsedMs === undefined) return ''
  return props.elapsedMs >= 1000 ? `${(props.elapsedMs / 1000).toFixed(1)} s` : `${props.elapsedMs} ms`
})
</script>

<template>
  <section class="panel result-card">
    <header class="panel-head">
      <div class="head-left">
        <span class="panel-title serif">{{ title }}</span>
        <span v-if="eyebrow" class="panel-sub">{{ eyebrow }}</span>
      </div>
      <div class="head-right">
        <span v-if="elapsedText && !loading" class="chip">耗时 {{ elapsedText }}</span>
        <slot name="actions" />
      </div>
    </header>

    <div v-if="loading" class="loading-bar" />

    <div class="panel-body">
      <el-alert
        v-if="error"
        type="error"
        :closable="false"
        show-icon
        :title="error"
        class="card-alert"
      />
      <div v-if="loading" class="busy">
        <span class="spinner" />
        <span class="busy-text">{{ loadingText || '正在等待后端计算…' }}</span>
      </div>
      <slot />
    </div>

    <footer v-if="$slots.footer" class="card-foot">
      <slot name="footer" />
    </footer>
  </section>
</template>

<style scoped>
.result-card {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.panel-head {
  justify-content: space-between;
  align-items: center;
}

.head-left {
  display: flex;
  align-items: baseline;
  gap: 9px;
  min-width: 0;
}

.head-right {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 0 0 auto;
}

.card-alert {
  margin-bottom: 12px;
}

.busy {
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 8px 0 12px;
  color: var(--text-2);
  font-size: 12.5px;
}

.spinner {
  width: 13px;
  height: 13px;
  border-radius: 50%;
  border: 1.6px solid var(--hair-strong);
  border-top-color: var(--accent);
  animation: spin 0.85s linear infinite;
  flex: 0 0 auto;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

.card-foot {
  border-top: 1px solid var(--hair-soft);
  padding: 10px 16px;
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
</style>
