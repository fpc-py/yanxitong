<script setup lang="ts">
// 工作台概览：Hero + 指标四卡 + 智能体编排 + 进行中任务 + 知识库 + 系统工程（可观测/成本/评估）
// 数据全部取自 session / system 两个 store，后端离线时显示占位符且不报错
import { computed, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import KnowledgeBase from '@/components/workbench/KnowledgeBase.vue'
import MetricsPanel from '@/components/workbench/MetricsPanel.vue'
import SystemPanels from '@/components/workbench/SystemPanels.vue'
import { useSessionStore } from '@/stores/session'
import { useSystemStore } from '@/stores/system'

const session = useSessionStore()
const system = useSystemStore()
const router = useRouter()

onMounted(() => {
  system.startPolling() // metrics 15s / capabilities 60s
  void system.loadKnowledgeFiles()
})
onUnmounted(() => system.stopPolling())

// ---- Hero ----
const greeting = computed(() => {
  const h = new Date().getHours()
  if (h < 12) return '早上好'
  if (h < 18) return '下午好'
  return '晚上好'
})

// ---- 指标四卡 ----
const overallConfidence = computed(() => {
  const vals = Object.values(session.confidenceScores)
    .map((v) => Number(v))
    .filter((v) => Number.isFinite(v))
  if (vals.length) return vals.reduce((a, b) => a + b, 0) / vals.length
  return session.averageConfidence
})

const papersText = computed(() => (session.hasSession ? session.papersCount.toLocaleString('zh-CN') : '—'))
const kgText = computed(() => {
  const v = session.status?.kg_entities_count
  return v === undefined || v === null ? '—' : v.toLocaleString('zh-CN')
})
const confText = computed(() => (overallConfidence.value > 0 ? overallConfidence.value.toFixed(2) : '—'))
const costText = computed(() => {
  const c = system.metrics?.cost_cents
  if (c === undefined || c === null) return '—'
  return `¥${(Number.isFinite(c) ? c / 100 : 0).toFixed(2)}`
})

// ---- 智能体编排 · 7 个执行体 ----
interface AgentItem {
  name: string
  sub: string
  path: string
  letter: string
  tone: 'ink' | 'blue' | 'purple' | 'accent' | 'green' | 'amber' | 'red'
  key: string
}

const agents: AgentItem[] = [
  { name: 'Supervisor 总控', sub: '意图路由', path: '/chat', letter: 'S', tone: 'ink', key: 'supervisor' },
  { name: 'Retriever 文献侦察', sub: '文献检索', path: '/literature', letter: 'R', tone: 'blue', key: 'retriever' },
  { name: 'KG Builder 图谱构建', sub: '实体关系抽取', path: '/kg', letter: 'K', tone: 'purple', key: 'kg_builder' },
  { name: 'Data Analyst 数据挖掘', sub: 'Docker 沙箱', path: '/analyze', letter: 'D', tone: 'accent', key: 'data_analyst' },
  { name: 'Experiment Designer 实验设计', sub: '方案优化', path: '/design', letter: 'E', tone: 'green', key: 'experiment_designer' },
  { name: 'Writing Assistant 论文写作', sub: '章节生成', path: '/write', letter: 'W', tone: 'amber', key: 'writing_assistant' },
  { name: 'Academic Reviewer 学术审阅', sub: '规范审查', path: '/review', letter: 'V', tone: 'red', key: 'academic_reviewer' },
]

// 状态徽标：运行中 > 有置信度评分 > 就绪（诚实，不编造）
function agentStatus(key: string): { text: string; cls: 'idle' | 'busy' | 'ready' } {
  if (session.loading) return { text: '运行中', cls: 'busy' }
  const v = Number(session.confidenceScores[key])
  if (Number.isFinite(v) && v > 0) return { text: `${Math.round(v * 100)}%`, cls: 'ready' }
  return { text: '就绪', cls: 'idle' }
}

const agentCards = computed(() => agents.map((a) => ({ ...a, status: agentStatus(a.key) })))

async function openAgent(path: string): Promise<void> {
  await router.push(path)
}

// ---- 进行中任务（真实后端会话列表） ----
async function openSession(id: string): Promise<void> {
  await session.switchSession(id)
  await router.push('/chat')
}

function refresh(): void {
  void session.refreshHealth()
  void session.refreshStatus()
  void session.loadCitationChain()
  void system.loadCapabilities()
  void system.loadMetrics()
  void system.loadKnowledgeFiles()
}
</script>

<template>
  <div class="page overview">
    <div class="stagger stack">
      <div class="v-label">工作台 · Overview</div>

      <!-- Hero -->
      <header class="wb-hero">
        <div class="wb-hero-text">
          <h1 class="wb-title serif">{{ greeting }}，<em>研究员</em></h1>
          <p class="wb-sub">以文献库与实验记录为知识底座，覆盖「找→读→算→写→审」全链路。</p>
        </div>
        <div class="wb-hero-actions">
          <el-button size="small" :loading="session.loading" @click="refresh">刷新状态</el-button>
        </div>
      </header>

      <!-- 指标四卡 -->
      <div class="ov-metrics">
        <article class="ov-metric">
          <span class="ov-metric-label mono">文献已索引</span>
          <span class="ov-metric-value serif">{{ papersText }}</span>
          <span class="ov-metric-sub">当前会话</span>
        </article>
        <article class="ov-metric">
          <span class="ov-metric-label mono">KG 实体数</span>
          <span class="ov-metric-value serif">{{ kgText }}</span>
          <span class="ov-metric-sub">知识图谱实体</span>
        </article>
        <article class="ov-metric">
          <span class="ov-metric-label mono">平均置信度</span>
          <span class="ov-metric-value serif">{{ confText }}</span>
          <span class="ov-metric-sub">引用链 {{ session.claims.length }} 条</span>
        </article>
        <article class="ov-metric">
          <span class="ov-metric-label mono">累计 Token 成本</span>
          <span class="ov-metric-value serif">{{ costText }}</span>
          <span class="ov-metric-sub">实时聚合</span>
        </article>
      </div>

      <!-- 智能体编排 -->
      <section class="wb-section">
        <div class="wb-section-head">
          <span class="eyebrow wb-section-kicker">AGENT ORCHESTRATION</span>
          <h2 class="wb-section-title serif">智能体编排 · 7 个执行体</h2>
          <span class="wb-section-hint">点击卡片进入对应工作流</span>
        </div>

        <div class="ov-agents">
          <button v-for="a in agentCards" :key="a.key" class="ov-agent" type="button" @click="openAgent(a.path)">
            <span class="ov-av" :class="`is-${a.tone}`">{{ a.letter }}</span>
            <span class="ov-agent-text">
              <span class="ov-agent-name">{{ a.name }}</span>
              <span class="ov-agent-sub mono">{{ a.sub }}</span>
            </span>
            <span class="ov-st" :class="a.status.cls">{{ a.status.text }}</span>
          </button>
        </div>
      </section>

      <!-- 进行中任务 -->
      <section class="wb-section">
        <div class="wb-section-head">
          <span class="eyebrow wb-section-kicker">ACTIVE SESSIONS</span>
          <h2 class="wb-section-title serif">进行中任务</h2>
          <span class="wb-section-hint">共 {{ session.sessionList.length }} 条 · 来自后端会话列表</span>
        </div>

        <div v-if="session.sessionList.length" class="ov-tasks">
          <button
            v-for="t in session.sessionList"
            :key="t.session_id"
            class="ov-task"
            type="button"
            @click="openSession(t.session_id)"
          >
            <span class="ov-task-body">
              <span class="ov-task-name">{{ t.topic || '未命名会话' }}</span>
              <span class="ov-task-sub mono">{{ t.papers_count }} 篇文献</span>
            </span>
            <span class="ov-st" :class="t.session_id === session.sessionId ? 'busy' : 'ready'">
              {{ t.session_id === session.sessionId ? '当前' : '进行中' }}
            </span>
          </button>
        </div>
        <div v-else class="ov-empty">暂无研究任务 · 点击左侧「新研究任务」开始</div>
      </section>

      <!-- 知识库 -->
      <KnowledgeBase />

      <!-- 系统工程 · 可观测（整合可观测性 / 成本看板 / 评估中心） -->
      <section class="wb-section">
        <div class="wb-section-head">
          <span class="eyebrow wb-section-kicker">SYSTEM ENGINEERING</span>
          <h2 class="wb-section-title serif">系统工程 · 可观测</h2>
          <span class="wb-section-hint">防线 · 成本 · 评估的真实运行状态</span>
        </div>

        <MetricsPanel />
        <SystemPanels />
      </section>
    </div>
  </div>
</template>
