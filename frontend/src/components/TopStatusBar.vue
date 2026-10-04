<script setup lang="ts">
// 顶部细状态条：后端在线状态、版本、特性标签，15 秒轮询刷新
import { computed } from 'vue'
import { useSessionStore } from '@/stores/session'

const store = useSessionStore()

const online = computed(() => !!store.health && !store.healthError)

const versionText = computed(() => (store.health ? `v${store.health.version}` : '—'))

const checkedAt = computed(() => {
  const ts = store.health?.timestamp
  if (!ts) return '尚未连通'
  const d = new Date(ts)
  if (Number.isNaN(d.getTime())) return ts
  return d.toLocaleTimeString('zh-CN')
})

// 特性标签最多展示 7 个，其余折叠计数
const features = computed(() => store.health?.features ?? [])
const visibleFeatures = computed(() => features.value.slice(0, 7))
const restCount = computed(() => Math.max(0, features.value.length - visibleFeatures.value.length))

const stateText = computed(() => {
  if (store.healthError) return '后端离线'
  if (!store.health) return '检测中'
  return '后端在线'
})
</script>

<template>
  <header class="topbar">
    <div class="brand">
      <svg class="brand-mark" viewBox="0 0 32 32" aria-hidden="true">
        <rect x="1.5" y="1.5" width="29" height="29" rx="7" fill="none" stroke="var(--accent-line)" />
        <path d="M16 7v18M7 16h18" stroke="var(--accent)" stroke-width="1.6" stroke-linecap="round" />
        <circle cx="16" cy="16" r="4.4" fill="none" stroke="var(--accent)" stroke-width="1.2" opacity="0.7" />
      </svg>
      <div class="brand-text">
        <span class="brand-name">研析通</span>
        <span class="brand-sub mono">RESEARCH&nbsp;WORKBENCH · v3.0</span>
      </div>
    </div>

    <div class="status-inline">
      <span class="dot" :class="store.healthError ? 'is-err' : online ? 'is-ok' : 'is-warn'" />
      <span class="status-text">{{ stateText }}</span>
      <span class="sep" />
      <span class="mono meta">{{ versionText }}</span>
      <span class="sep" />
      <span class="mono meta">FEATURES {{ features.length }}</span>
      <span class="sep" />
      <span class="mono meta">SYNC {{ checkedAt }}</span>
    </div>

    <div class="features">
      <span v-for="f in visibleFeatures" :key="f" class="chip">{{ f }}</span>
      <span v-if="restCount > 0" class="chip is-accent">+{{ restCount }}</span>
      <span v-if="store.sessionId" class="chip is-accent session-chip" :title="store.sessionId">
        SESSION {{ store.sessionId }}
      </span>
      <span v-else class="chip is-amber">未建立会话</span>
    </div>
  </header>
</template>

<style scoped>
.topbar {
  flex: 0 0 auto;
  height: var(--topbar-h);
  display: flex;
  align-items: center;
  gap: 18px;
  padding: 0 18px;
  border-bottom: 1px solid var(--hair);
  background: linear-gradient(180deg, rgba(11, 15, 20, 0.94), rgba(11, 15, 20, 0.78));
  backdrop-filter: blur(10px);
  position: relative;
  z-index: 20;
}

.topbar::after {
  content: '';
  position: absolute;
  left: 0;
  right: 0;
  bottom: -1px;
  height: 1px;
  background: linear-gradient(90deg, transparent, var(--accent-line) 22%, transparent 62%);
  opacity: 0.7;
}

.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  flex: 0 0 auto;
}

.brand-mark {
  width: 26px;
  height: 26px;
}

.brand-text {
  display: flex;
  flex-direction: column;
  line-height: 1.15;
}

.brand-name {
  font-family: var(--font-display);
  font-size: 15px;
  font-weight: 600;
  letter-spacing: 0.02em;
}

.brand-sub {
  font-size: 9px;
  letter-spacing: 0.14em;
  color: var(--text-3);
}

.status-inline {
  display: flex;
  align-items: center;
  gap: 9px;
  flex: 0 0 auto;
}

.status-text {
  font-size: 12.5px;
  color: var(--text-2);
}

.sep {
  width: 1px;
  height: 12px;
  background: var(--hair);
}

.meta {
  font-size: 10.5px;
  letter-spacing: 0.08em;
  color: var(--text-3);
}

.features {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 6px;
  overflow: hidden;
  mask-image: linear-gradient(90deg, transparent, #000 16px);
}

.session-chip {
  letter-spacing: 0.06em;
  max-width: 190px;
  overflow: hidden;
  text-overflow: ellipsis;
  display: inline-block;
  line-height: 20px;
}
</style>
