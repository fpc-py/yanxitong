<script setup lang="ts">
// 主区顶栏（对应 v3.0 原型）：左侧面包屑 + 右侧状态胶囊；15 秒轮询刷新
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { useSessionStore } from '@/stores/session'

const store = useSessionStore()
const route = useRoute()

const VIEW_NAMES: Record<string, string> = {
  overview: '概览',
  chat: '研究对话',
  literature: '文献库',
  kg: '知识图谱',
  analyze: '数据分析',
  design: '实验设计',
  write: '论文写作',
  review: '学术审阅',
  bibliography: '参考文献',
}

const crumb = computed(() => {
  const name = VIEW_NAMES[route.path.replace(/^\//, '')] ?? '工作台'
  return name
})

const online = computed(() => !!store.health && !store.healthError)
const versionText = computed(() => (store.health ? `v${store.health.version}` : '—'))

const stateText = computed(() => {
  if (store.healthError) return '后端离线'
  if (!store.health) return '连接中'
  return '后端在线'
})

const confidencePct = computed(() => Math.round(store.averageConfidence * 100))
</script>

<template>
  <header class="topbar">
    <div class="crumb">
      研析通 · Research Copilot <span class="sep-c">/</span> <b>{{ crumb }}</b>
    </div>

    <div class="top-actions">
      <span class="pill">
        <span class="d" :class="store.healthError ? 'is-err' : online ? '' : 'is-warn'" />
        {{ stateText }} · {{ versionText }}
      </span>
      <span v-if="store.averageConfidence > 0" class="pill accent">置信度 {{ confidencePct }}%</span>
      <span v-if="store.sessionId" class="pill mono session-pill" :title="store.sessionId">
        SESSION {{ store.sessionId }}
      </span>
    </div>
  </header>
</template>

<style scoped>
.topbar {
  position: sticky;
  top: 0;
  z-index: 10;
  background: rgba(250, 249, 245, 0.88);
  backdrop-filter: blur(10px);
  border-bottom: 1px solid var(--line-soft);
  padding: 13px 32px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.crumb {
  font-size: 12.5px;
  color: var(--ink-2);
}

.crumb b {
  color: var(--ink);
  font-weight: 500;
}

.sep-c {
  color: var(--ink-3);
  margin: 0 4px;
}

.top-actions {
  display: flex;
  gap: 8px;
  align-items: center;
}

.pill .d.is-err {
  background: var(--danger);
}

.pill .d.is-warn {
  background: var(--amber);
}

.session-pill {
  letter-spacing: 0.04em;
  max-width: 180px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
