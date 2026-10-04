<script setup lang="ts">
// 知识图谱：把结论与引用文献渲染为力导向「论文—结论」关系图
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import KgGraph from '@/components/KgGraph.vue'
import { useSessionStore } from '@/stores/session'

const store = useSessionStore()
const router = useRouter()

const claims = computed(() => store.normalizedClaims)
const citations = computed(() => store.allCitations)

const paperCount = computed(() => citations.value.length)
const linkCount = computed(() => claims.value.reduce((sum, c) => sum + c.citations.length, 0))

const dataSourceHint = computed(() => {
  if (linkCount.value > 0) return '关系来自会话引用链'
  if (paperCount.value > 0) return '仅有文献节点，尚无可关联的结论'
  return '暂无可用数据'
})

async function reload(): Promise<void> {
  await Promise.all([store.refreshStatus(), store.loadCitationChain()])
}
</script>

<template>
  <div class="page kg-page">
    <header class="page-head">
      <div>
        <span class="eyebrow page-kicker">KNOWLEDGE GRAPH / FORCE LAYOUT</span>
        <h1 class="page-title">知识图谱</h1>
        <p class="page-desc">
          以结论为青色节点、引用文献为琥珀节点构建力导向图。支持缩放拖拽、节点点击高亮邻接关系与悬浮详情。
        </p>
      </div>
      <div class="page-actions">
        <el-button size="small" :disabled="!store.hasSession" @click="reload">重新加载</el-button>
      </div>
    </header>

    <div class="grid grid-3">
      <div class="stat">
        <span class="stat-label">结论节点</span>
        <span class="stat-value">{{ claims.length }}</span>
        <span class="stat-hint">claim</span>
      </div>
      <div class="stat">
        <span class="stat-label">文献节点</span>
        <span class="stat-value">{{ paperCount }}</span>
        <span class="stat-hint">paper</span>
      </div>
      <div class="stat">
        <span class="stat-label">引用边</span>
        <span class="stat-value">{{ linkCount }}</span>
        <span class="stat-hint">{{ dataSourceHint }}</span>
      </div>
    </div>

    <section class="panel graph-panel">
      <header class="panel-head">
        <span class="panel-title serif">关系视图</span>
        <div class="legend inline">
          <span class="chip is-accent">结论</span>
          <span class="chip is-amber">论文</span>
          <span class="panel-sub">滚轮缩放 · 拖拽平移 · 点击节点高亮</span>
        </div>
      </header>
      <div class="graph-body">
        <div v-if="!store.hasSession" class="empty graph-empty">
          <span class="empty-icon mono">◌</span>
          <span>当前没有活跃会话</span>
          <el-button type="primary" size="small" @click="router.push('/chat')">前往建立会话</el-button>
        </div>
        <KgGraph v-else :claims="claims" :citations="citations" />
      </div>
    </section>
  </div>
</template>

<style scoped>
.graph-panel {
  display: flex;
  flex-direction: column;
}

.legend {
  margin-left: auto;
  gap: 6px;
}

.graph-body {
  height: 560px;
  padding: 8px 6px 6px;
  position: relative;
}

.graph-empty {
  height: 100%;
  gap: 14px;
}
</style>
