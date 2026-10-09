<script setup lang="ts">
// 研究路线图（规格⑤）：时间线 / 技术演进链 / 矛盾发现 / 研究空白
// 数据源 /api/kg/roadmap（按领域包聚合；图不可用时后端返回 degraded 空结构）
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import type { KgRoadmapResponse } from '@/api/types'
import { extractErrorMessage } from '@/api/client'
import { useSessionStore } from '@/stores/session'

const props = defineProps<{ kbId?: string }>()

const store = useSessionStore()

const data = ref<KgRoadmapResponse | null>(null)
const loading = ref(false)
const failed = ref(false)

const degraded = computed(() => !data.value || data.value.degraded)
const timeline = computed(() => data.value?.timeline ?? [])
const evolution = computed(() => data.value?.evolution ?? [])
const contradictions = computed(() => data.value?.contradictions ?? [])
const gaps = computed(() => data.value?.gaps ?? [])

async function load(): Promise<void> {
  loading.value = true
  failed.value = false
  try {
    // scope 带上会话 id：空白优先呈现会话视图内的稀疏实体；kbId 按领域包过滤
    data.value = await api.getKgRoadmap(store.sessionId ?? undefined, props.kbId)
  } catch (err) {
    failed.value = true
    ElMessage.error(extractErrorMessage(err))
  } finally {
    loading.value = false
  }
}

watch(() => props.kbId, () => void load())
onMounted(() => void load())

defineExpose({ load })
</script>

<template>
  <div v-loading="loading" class="roadmap stack">
    <el-alert
      v-if="failed"
      type="warning"
      :closable="false"
      title="图谱服务暂不可用"
      description="未能连接 Neo4j 图谱底座，路线图与空白分析已降级显示。"
    />
    <el-alert
      v-else-if="degraded && data"
      type="info"
      :closable="false"
      title="图谱暂无数据"
      description="先进行一次问答生成论文事实图谱，或使用「从历史会话回填」重建。"
    />

    <section class="panel">
      <header class="panel-head">
        <span class="panel-title serif">技术演进时间线</span>
        <span class="panel-sub">{{ timeline.length }} YEARS</span>
      </header>
      <div class="panel-body">
        <div v-if="timeline.length" class="tl">
          <div v-for="group in timeline" :key="group.year" class="tl-year">
            <div class="tl-marker">
              <span class="tl-dot" />
              <span class="tl-year-num mono">{{ group.year }}</span>
            </div>
            <div class="tl-papers">
              <article v-for="p in group.papers" :key="p.paper_id" class="tl-paper">
                <div class="tl-paper-head">
                  <span class="tl-title">{{ p.title || p.paper_id }}</span>
                  <span v-if="p.arxiv_id" class="tl-arxiv mono">arXiv:{{ p.arxiv_id }}</span>
                </div>
                <div v-if="p.methods.length" class="tl-methods">
                  <span v-for="m in p.methods" :key="m" class="chip">{{ m }}</span>
                </div>
              </article>
            </div>
          </div>
        </div>
        <div v-else class="empty">
          <span class="empty-icon mono">◌</span>
          <span>暂无带年份的论文节点</span>
          <span class="hint-line">提问建图后，带 year 的 Paper 节点会按年份排入时间线。</span>
        </div>
      </div>
    </section>

    <section class="panel">
      <header class="panel-head">
        <span class="panel-title serif">技术演进链</span>
        <span class="panel-sub">{{ evolution.length }} CHAINS</span>
      </header>
      <div class="panel-body">
        <div v-if="evolution.length" class="evo-list">
          <article v-for="(c, i) in evolution" :key="i" class="evo-item">
            <div class="evo-head">
              <span v-if="c.year" class="evo-year mono">{{ c.year }}</span>
              <span class="evo-text">{{ c.text }}</span>
            </div>
            <ul v-if="c.evidence.length" class="evo-ev">
              <li v-for="(ev, j) in c.evidence" :key="j">“{{ ev }}”</li>
            </ul>
          </article>
        </div>
        <div v-else class="empty">
          <span class="empty-icon mono">◌</span>
          <span>暂无方法间演进关系</span>
          <span class="hint-line">EXTENDS / IMPROVES_ON / BASED_ON 关系的实体对会在此串成演进链。</span>
        </div>
      </div>
    </section>

    <section class="panel">
      <header class="panel-head">
        <span class="panel-title serif">矛盾发现</span>
        <span class="panel-sub">{{ contradictions.length }} CONTRADICTS</span>
      </header>
      <div class="panel-body">
        <div v-if="contradictions.length" class="cf-list">
          <article v-for="(c, i) in contradictions" :key="i" class="cf-item">
            <div class="cf-row">
              <span class="cf-side">{{ c.source }}</span>
              <span class="cf-vs serif">VS</span>
              <span class="cf-side">{{ c.target }}</span>
            </div>
            <p v-if="c.evidence" class="cf-ev">“{{ c.evidence }}”</p>
          </article>
        </div>
        <div v-else class="empty">
          <span class="empty-icon mono">⚖</span>
          <span>图谱中暂无 CONTRADICTS 边</span>
        </div>
      </div>
    </section>

    <section class="panel">
      <header class="panel-head">
        <span class="panel-title serif">研究空白候选</span>
        <span class="panel-sub">{{ gaps.length }} GAPS</span>
      </header>
      <div class="panel-body">
        <div v-if="gaps.length" class="gap-list">
          <article v-for="g in gaps" :key="g.entity_id" class="gap-item">
            <div class="gap-head">
              <span class="chip is-accent">{{ g.type || 'Entity' }}</span>
              <span class="gap-name">{{ g.name }}</span>
            </div>
            <span class="gap-degree mono">关联 {{ g.degree }} 条 · {{ g.entity_id }}</span>
          </article>
        </div>
        <div v-else class="empty">
          <span class="empty-icon mono">◌</span>
          <span>暂无稀疏实体</span>
          <span class="hint-line">低度数（几乎无关系）的实体是研究空白候选，图谱积累后自动浮现。</span>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.tl {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.tl-year {
  display: grid;
  grid-template-columns: 74px 1fr;
  gap: 12px;
}

.tl-marker {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  padding-top: 10px;
}

.tl-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-glow);
}

.tl-year-num {
  font-size: 11.5px;
  color: var(--text-2);
}

.tl-papers {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 8px 0 14px 12px;
  border-left: 1px solid var(--hair-soft);
}

.tl-paper {
  padding: 10px 12px;
  border: 1px solid var(--hair-soft);
  border-radius: var(--radius);
  background: rgba(255, 255, 255, 0.9);
}

.tl-paper-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px;
}

.tl-title {
  font-size: 13px;
  color: var(--text-1);
  line-height: 1.5;
}

.tl-arxiv {
  font-size: 10px;
  color: var(--text-3);
  flex: 0 0 auto;
}

.tl-methods {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
  margin-top: 7px;
}

.evo-list {
  display: flex;
  flex-direction: column;
  gap: 9px;
}

.evo-item {
  padding: 12px 14px;
  border: 1px solid var(--hair-soft);
  border-radius: var(--radius);
  background: rgba(255, 255, 255, 0.9);
}

.evo-head {
  display: flex;
  align-items: baseline;
  gap: 10px;
}

.evo-year {
  font-size: 10.5px;
  color: var(--accent);
  flex: 0 0 auto;
}

.evo-text {
  font-size: 13px;
  line-height: 1.6;
  color: var(--text-1);
  word-break: break-word;
}

.evo-ev {
  margin: 8px 0 0;
  padding-left: 16px;
  font-size: 11.5px;
  color: var(--text-2);
  line-height: 1.6;
}

.evo-ev li::marker {
  color: var(--accent);
}

.cf-list,
.gap-list {
  display: flex;
  flex-direction: column;
  gap: 9px;
}

.cf-item {
  padding: 12px 14px;
  border: 1px solid var(--hair-soft);
  border-radius: var(--radius);
  background: rgba(255, 255, 255, 0.9);
}

.cf-row {
  display: grid;
  grid-template-columns: 1fr auto 1fr;
  gap: 12px;
  align-items: start;
}

.cf-side {
  font-size: 12.5px;
  color: var(--text-1);
  line-height: 1.55;
}

.cf-vs {
  color: var(--danger);
  font-size: 12px;
  align-self: center;
}

.cf-ev {
  margin: 9px 0 0;
  font-size: 11.5px;
  color: var(--text-2);
  line-height: 1.6;
}

.gap-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 11px 14px;
  border: 1px solid var(--hair-soft);
  border-radius: var(--radius);
  background: rgba(255, 255, 255, 0.9);
}

.gap-head {
  display: flex;
  align-items: center;
  gap: 9px;
  min-width: 0;
}

.gap-name {
  font-size: 13px;
  color: var(--text-1);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.gap-degree {
  font-size: 10px;
  color: var(--text-3);
  flex: 0 0 auto;
}
</style>
