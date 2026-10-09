<script setup lang="ts">
// 知识图谱工作台（规格①③⑤⑥）：全局事实底座总览 / 实体检索 / 研究路线图 / 人工复核 / 幻觉标记
// 图谱按领域包（KnowledgeBase 分区）隔离；顶部下拉可切换过滤，缺省=全部包
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import api, { extractErrorMessage } from '@/api/client'
import type { KgGraphData, KgNode, KgOverview, KgReviewItem } from '@/api/types'
import GraphCanvas from '@/components/GraphCanvas.vue'
import AgentTrace from '@/components/AgentTrace.vue'
import RoadmapPanel from '@/components/workbench/RoadmapPanel.vue'
import { useSessionStore } from '@/stores/session'

const store = useSessionStore()
const router = useRouter()

const activeTab = ref('overview')

// ---------- 领域包过滤（空=全部包，向后兼容） ----------
const kbFilter = ref('')

watch(kbFilter, () => void reload())

// ---------- 图谱总览 ----------
const overview = ref<KgOverview | null>(null)
const subgraph = ref<KgGraphData>({ nodes: [], edges: [] })
const overviewLoading = ref(false)
const backfilling = ref(false)

const schemaEntityTypes = computed(() => overview.value?.schema.entity_types ?? [])
const schemaRelationTypes = computed(() => overview.value?.schema.relation_types ?? [])

async function loadOverview(): Promise<void> {
  overviewLoading.value = true
  try {
    const jobs: Promise<unknown>[] = [
      api.getKgOverview(kbFilter.value || undefined).then((d) => (overview.value = d)),
    ]
    if (store.sessionId) {
      jobs.push(
        api
          .getKgSessionSubgraph(store.sessionId)
          .then((d) => (subgraph.value = d))
          .catch(() => (subgraph.value = { nodes: [], edges: [] })),
      )
    } else {
      subgraph.value = { nodes: [], edges: [] }
    }
    await Promise.all(jobs)
  } catch (err) {
    ElMessage.error(extractErrorMessage(err))
  } finally {
    overviewLoading.value = false
  }
}

async function onBackfill(): Promise<void> {
  backfilling.value = true
  try {
    const res = await api.kgBackfill()
    if (res.degraded) {
      ElMessage.warning(`回填降级：${res.error || '图谱不可用'}`)
    } else {
      ElMessage.success(
        `回填完成：${res.sessions} 个会话 · 论文 ${res.papers}（去重后 ${res.unique_papers}）· 实体 ${res.entities} · 边 ${res.edges}`,
      )
    }
    await loadOverview()
  } catch (err) {
    ElMessage.error(extractErrorMessage(err))
  } finally {
    backfilling.value = false
  }
}

// ---------- 实体检索 ----------
const query = ref('')
const queryType = ref('')
const scopedToSession = ref(false)
const searching = ref(false)
const searchResults = ref<KgNode[]>([])
const searched = ref(false)

const neighborDepth = ref(1)
const neighbors = ref<KgGraphData>({ nodes: [], edges: [] })
const activeEntity = ref<KgNode | null>(null)
const neighborsLoading = ref(false)

async function onSearch(): Promise<void> {
  const q = query.value.trim()
  if (!q) {
    ElMessage.info('请输入实体名称关键词')
    return
  }
  searching.value = true
  try {
    const scope = scopedToSession.value ? (store.sessionId ?? undefined) : undefined
    const res = await api.searchKgEntities(q, 30, queryType.value || undefined, scope, kbFilter.value || undefined)
    searchResults.value = res.entities
    searched.value = true
  } catch (err) {
    ElMessage.error(extractErrorMessage(err))
  } finally {
    searching.value = false
  }
}

async function openEntity(entity: KgNode): Promise<void> {
  activeEntity.value = entity
  neighborsLoading.value = true
  try {
    neighbors.value = await api.getKgNeighbors(entity.id, neighborDepth.value)
  } catch (err) {
    ElMessage.error(extractErrorMessage(err))
    neighbors.value = { nodes: [], edges: [] }
  } finally {
    neighborsLoading.value = false
  }
}

async function reloadNeighbors(): Promise<void> {
  if (activeEntity.value) await openEntity(activeEntity.value)
}

// ---------- 人工复核 ----------
const reviewQueue = ref<KgReviewItem[]>([])
const reviewLoading = ref(false)
const decidingKey = ref('')

async function loadReviewQueue(): Promise<void> {
  reviewLoading.value = true
  try {
    const res = await api.getKgReviewQueue(100, kbFilter.value || undefined)
    reviewQueue.value = res.queue
  } catch (err) {
    ElMessage.error(extractErrorMessage(err))
  } finally {
    reviewLoading.value = false
  }
}

async function decide(item: KgReviewItem, decision: 'approved' | 'rejected'): Promise<void> {
  let note = ''
  try {
    const { value } = await ElMessageBox.prompt(
      decision === 'rejected'
        ? '驳回将删除该边，请填写依据（可选）'
        : '通过后保留该边，请填写备注（可选）',
      decision === 'rejected' ? '驳回边' : '通过边',
      { confirmButtonText: '确认', cancelButtonText: '取消', inputPlaceholder: '复核备注（可选）' },
    )
    note = value || ''
  } catch {
    return // 取消
  }
  decidingKey.value = item.edge_key
  try {
    await api.markKgReviewed(item.edge_key, decision, note)
    ElMessage.success(decision === 'rejected' ? '已驳回并删除该边' : '已通过')
    reviewQueue.value = reviewQueue.value.filter((r) => r.edge_key !== item.edge_key)
  } catch (err) {
    ElMessage.error(extractErrorMessage(err))
  } finally {
    decidingKey.value = ''
  }
}

// ---------- 公共 ----------
async function reload(): Promise<void> {
  if (activeTab.value === 'search') {
    if (searched.value) await onSearch()
    return
  }
  if (activeTab.value === 'review') return loadReviewQueue()
  return loadOverview()
}

onMounted(() => {
  void store.loadPacks()
  void loadOverview()
})
</script>

<template>
  <div class="page kg-page">
    <header class="page-head">
      <div>
        <span class="eyebrow page-kicker">KNOWLEDGE GRAPH / SHARED FACT BASE</span>
        <h1 class="page-title">知识图谱</h1>
        <p class="page-desc">
          论文事实底座由全组会话持续累积（schema 封闭约束 + 证据锚定）；会话视图展示本人会话检索到的论文与实体。
        </p>
      </div>
      <div class="page-actions">
        <el-select v-model="kbFilter" placeholder="全部领域包" clearable class="kb-filter" size="small">
          <el-option label="全部领域包" value="" />
          <el-option v-for="p in store.packs" :key="p.kb_id" :label="p.name" :value="p.kb_id" />
        </el-select>
        <el-button size="small" :loading="backfilling" @click="onBackfill">从历史会话回填</el-button>
        <el-button size="small" @click="reload">重新加载</el-button>
      </div>
    </header>

    <el-tabs v-model="activeTab" class="kg-tabs">
      <!-- 图谱总览 -->
      <el-tab-pane label="图谱总览" name="overview">
        <div v-loading="overviewLoading" class="stack">
          <el-alert
            v-if="overview?.degraded"
            type="warning"
            :closable="false"
            title="图谱服务暂不可用"
            description="未连接 Neo4j，计数与子图为空。可先「从历史会话回填」或稍后重试。"
          />

          <div class="grid grid-5">
            <div class="stat">
              <span class="stat-label">论文节点</span>
              <span class="stat-value">{{ overview?.papers ?? 0 }}</span>
              <span class="stat-hint mono">ax: / th: / url:</span>
            </div>
            <div class="stat">
              <span class="stat-label">会话</span>
              <span class="stat-value">{{ overview?.sessions ?? 0 }}</span>
              <span class="stat-hint">RETRIEVED 链</span>
            </div>
            <div class="stat">
              <span class="stat-label">实体</span>
              <span class="stat-value">{{ overview?.entities_total ?? 0 }}</span>
              <span class="stat-hint mono">{{ Object.keys(overview?.entities ?? {}).length }} 类</span>
            </div>
            <div class="stat">
              <span class="stat-label">关系边</span>
              <span class="stat-value">{{ overview?.relations_total ?? 0 }}</span>
              <span class="stat-hint mono">{{ Object.keys(overview?.relations ?? {}).length }} 种</span>
            </div>
            <div class="stat">
              <span class="stat-label">待复核</span>
              <span class="stat-value" :class="{ 'is-warn': (overview?.pending_review ?? 0) > 0 }">
                {{ overview?.pending_review ?? 0 }}
              </span>
              <span class="stat-hint">抽样 + 强制复核</span>
            </div>
          </div>

          <section class="panel">
            <header class="panel-head">
              <span class="panel-title serif">封闭 Schema（配置唯一权威）</span>
              <span class="panel-sub">写入前代码级 gate 丢弃白名单外类型</span>
            </header>
            <div class="panel-body schema-body">
              <div class="schema-group">
                <span class="eyebrow">实体类型</span>
                <div class="chips">
                  <span v-for="t in schemaEntityTypes" :key="t" class="chip">{{ t }}</span>
                  <span v-if="!schemaEntityTypes.length" class="hint-line">未获取到 schema</span>
                </div>
              </div>
              <div class="schema-group">
                <span class="eyebrow">关系类型</span>
                <div class="chips">
                  <span v-for="t in schemaRelationTypes" :key="t" class="chip is-amber">{{ t }}</span>
                  <span v-if="!schemaRelationTypes.length" class="hint-line">未获取到 schema</span>
                </div>
              </div>
            </div>
          </section>

          <section class="panel">
            <header class="panel-head">
              <span class="panel-title serif">会话视图子图</span>
              <div class="legend inline">
                <span class="chip is-accent">论文</span>
                <span class="chip">实体</span>
                <span class="panel-sub mono">
                  {{ subgraph.nodes.length }} 节点 · {{ subgraph.edges.length }} 边
                </span>
              </div>
            </header>
            <div class="panel-body">
              <div v-if="!store.hasSession" class="empty graph-empty">
                <span class="empty-icon mono">◌</span>
                <span>当前没有活跃会话</span>
                <el-button type="primary" size="small" @click="router.push('/chat')">前往建立会话</el-button>
              </div>
              <GraphCanvas v-else :nodes="subgraph.nodes" :edges="subgraph.edges" height="520px">
                <template #empty>
                  <span>本会话暂无图谱节点</span>
                  <span class="hint-line">先提一个问题触发检索建图，或点击右上角「从历史会话回填」。</span>
                </template>
              </GraphCanvas>
            </div>
          </section>
        </div>
      </el-tab-pane>

      <!-- 实体检索 -->
      <el-tab-pane label="实体检索" name="search">
        <div class="stack">
          <section class="panel">
            <header class="panel-head">
              <span class="panel-title serif">检索实体</span>
              <span class="panel-sub">全图 = 课题组共享事实底座</span>
            </header>
            <div class="panel-body search-body">
              <div class="search-row">
                <el-input
                  v-model="query"
                  placeholder="实体名称关键词，如 FedProx / FEMNIST / 精度"
                  class="search-input"
                  clearable
                  @keydown.enter="onSearch"
                />
                <el-select v-model="queryType" placeholder="全部类型" clearable class="search-type">
                  <el-option v-for="t in schemaEntityTypes" :key="t" :label="t" :value="t" />
                </el-select>
                <el-select v-model="neighborDepth" class="search-depth">
                  <el-option :value="1" label="邻域 1 跳" />
                  <el-option :value="2" label="邻域 2 跳" />
                </el-select>
                <el-button type="primary" :loading="searching" @click="onSearch">检索</el-button>
              </div>
              <el-checkbox v-model="scopedToSession" :disabled="!store.hasSession">
                仅本会话视图（不勾选则跨会话全图检索）
              </el-checkbox>

              <div v-if="searched" class="results">
                <div v-if="searchResults.length" class="result-list">
                  <button
                    v-for="e in searchResults"
                    :key="e.id"
                    class="result-item"
                    :class="{ active: activeEntity?.id === e.id }"
                    @click="openEntity(e)"
                  >
                    <span class="chip">{{ e.type || 'Entity' }}</span>
                    <span class="result-name">{{ e.name }}</span>
                    <span class="result-id mono">{{ e.id }}</span>
                  </button>
                </div>
                <div v-else class="empty small">
                  <span class="empty-icon mono">∅</span>
                  <span>没有匹配的实体</span>
                  <span class="hint-line">换个关键词；未勾选「仅本会话」时可检索全组累积的实体。</span>
                </div>
              </div>
            </div>
          </section>

          <section v-if="activeEntity" class="panel">
            <header class="panel-head">
              <span class="panel-title serif">{{ activeEntity.name }} · 邻域</span>
              <div class="legend inline">
                <span class="panel-sub mono">
                  {{ neighbors.nodes.length }} 节点 · {{ neighbors.edges.length }} 边 · {{ neighborDepth }} 跳
                </span>
                <el-button size="small" :loading="neighborsLoading" @click="reloadNeighbors">重新加载</el-button>
              </div>
            </header>
            <div class="panel-body">
              <GraphCanvas :nodes="neighbors.nodes" :edges="neighbors.edges" height="480px">
                <template #empty>
                  <span>该实体暂无邻接关系</span>
                </template>
              </GraphCanvas>
            </div>
          </section>
        </div>
      </el-tab-pane>

      <!-- 研究路线图 -->
      <el-tab-pane label="研究路线图" name="roadmap">
        <RoadmapPanel v-if="activeTab === 'roadmap'" :kb-id="kbFilter || undefined" />
      </el-tab-pane>

      <!-- 人工复核 -->
      <el-tab-pane name="review">
        <template #label>
          人工复核
          <span v-if="reviewQueue.length" class="tab-badge mono">{{ reviewQueue.length }}</span>
        </template>
        <div v-loading="reviewLoading" class="stack">
          <section class="panel">
            <header class="panel-head">
              <span class="panel-title serif">待复核边队列</span>
              <div class="legend inline">
                <span class="panel-sub">确定性抽样 10% + 引文校验失败强制入队</span>
                <el-button size="small" @click="loadReviewQueue">刷新队列</el-button>
              </div>
            </header>
            <div class="panel-body">
              <div v-if="reviewQueue.length" class="review-list">
                <article v-for="item in reviewQueue" :key="item.edge_key" class="review-item">
                  <div class="review-row">
                    <span class="review-side">{{ item.source_name || item.source_id }}</span>
                    <span class="review-rel mono">--{{ item.rel }}--&gt;</span>
                    <span class="review-side">{{ item.target_name || item.target_id }}</span>
                  </div>
                  <p v-if="item.evidence" class="review-ev">“{{ item.evidence }}”</p>
                  <p v-else class="review-ev is-missing">该边没有通过引文校验的原文证据</p>
                  <div class="review-foot">
                    <span class="review-meta mono">
                      {{ item.edge_key }}<template v-if="item.evidence_source"> · 来源 {{ item.evidence_source }}</template>
                      <template v-if="item.sessions?.length"> · {{ item.sessions.length }} 个会话贡献</template>
                    </span>
                    <div class="review-actions">
                      <el-button
                        size="small"
                        type="success"
                        plain
                        :loading="decidingKey === item.edge_key"
                        @click="decide(item, 'approved')"
                      >
                        通过
                      </el-button>
                      <el-button
                        size="small"
                        type="danger"
                        plain
                        :loading="decidingKey === item.edge_key"
                        @click="decide(item, 'rejected')"
                      >
                        驳回
                      </el-button>
                    </div>
                  </div>
                </article>
              </div>
              <div v-else class="empty">
                <span class="empty-icon mono">✓</span>
                <span>复核队列为空</span>
                <span class="hint-line">入队规则：图谱随机抽样 10% 的边 + LLM 边引文校验失败强制入队。</span>
              </div>
            </div>
          </section>
        </div>
      </el-tab-pane>

      <!-- 幻觉标记 -->
      <el-tab-pane name="flags">
        <template #label>幻觉标记 / 推理轨迹</template>
        <section class="panel">
          <div class="panel-body">
            <AgentTrace v-if="activeTab === 'flags' && store.hasSession" />
            <div v-else-if="!store.hasSession" class="empty">
              <span class="empty-icon mono">◌</span>
              <span>当前没有活跃会话</span>
              <el-button type="primary" size="small" @click="router.push('/chat')">前往建立会话</el-button>
            </div>
          </div>
        </section>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<style scoped>
.kg-tabs :deep(.el-tabs__header) {
  margin-bottom: 18px;
}

.kb-filter {
  width: 170px;
}

.kg-tabs :deep(.el-tabs__item) {
  font-size: 13px;
  letter-spacing: 0.04em;
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

.grid-5 {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 10px;
}

.stat-value.is-warn {
  color: var(--amber, #b98a33);
}

.schema-body {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.schema-group .eyebrow {
  display: block;
  margin-bottom: 6px;
}

.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
}

.graph-empty {
  min-height: 320px;
  gap: 14px;
}

.search-body {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.search-row {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.search-input {
  flex: 1 1 260px;
}

.search-type {
  width: 140px;
}

.search-depth {
  width: 120px;
}

.results {
  margin-top: 4px;
}

.result-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-height: 300px;
  overflow-y: auto;
}

.result-item {
  display: grid;
  grid-template-columns: 96px 1fr auto;
  gap: 10px;
  align-items: center;
  text-align: left;
  padding: 9px 12px;
  border: 1px solid var(--hair-soft);
  border-radius: var(--radius);
  background: rgba(255, 255, 255, 0.9);
  cursor: pointer;
  font: inherit;
  color: inherit;
  transition: border-color 0.2s var(--ease);
}

.result-item:hover,
.result-item.active {
  border-color: var(--accent);
}

.result-name {
  font-size: 13px;
  color: var(--text-1);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.result-id {
  font-size: 10px;
  color: var(--text-3);
}

.review-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.review-item {
  padding: 13px 15px;
  border: 1px solid var(--hair-soft);
  border-radius: var(--radius);
  background: rgba(255, 255, 255, 0.9);
  transition: border-color 0.2s var(--ease);
}

.review-item:hover {
  border-color: var(--accent-line);
}

.review-row {
  display: grid;
  grid-template-columns: 1fr auto 1fr;
  gap: 12px;
  align-items: baseline;
}

.review-side {
  font-size: 13px;
  color: var(--text-1);
  line-height: 1.5;
}

.review-rel {
  font-size: 10.5px;
  color: var(--accent);
  letter-spacing: 0.04em;
}

.review-ev {
  margin: 9px 0 0;
  font-size: 12px;
  line-height: 1.6;
  color: var(--text-2);
}

.review-ev.is-missing {
  color: var(--danger);
}

.review-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 10px;
  padding-top: 9px;
  border-top: 1px dashed var(--hair-soft);
}

.review-meta {
  font-size: 10px;
  color: var(--text-3);
  letter-spacing: 0.03em;
}

.review-actions {
  display: flex;
  gap: 6px;
  flex: 0 0 auto;
}

.empty.small {
  padding: 42px 16px;
  gap: 10px;
}
</style>
