<script setup lang="ts">
// 工作台概览：会话状态、核心指标、能力清单与快捷入口
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import ConfidenceGauge from '@/components/ConfidenceGauge.vue'
import { useSessionStore } from '@/stores/session'

const store = useSessionStore()
const router = useRouter()

const PHASE_LABELS: Record<string, string> = {
  literature: '文献调研',
  experiment: '实验执行',
  writing: '论文写作',
  review: '学术审阅',
}

const phaseText = computed(() => PHASE_LABELS[store.phase] ?? store.phase ?? '未开始')

const featureList = computed(() => store.health?.features ?? [])

const overallConfidence = computed(() => {
  const vals = Object.values(store.confidenceScores).map((v) => Number(v)).filter((v) => Number.isFinite(v))
  if (vals.length) return vals.reduce((a, b) => a + b, 0) / vals.length
  return store.averageConfidence
})

const entries = [
  { path: '/chat', title: '会话问答', desc: '创建会话并连续追问，查看引用与置信度', tag: 'DIALOGUE' },
  { path: '/literature', title: '文献与引用', desc: '文献计数与「结论 → 证据」引用链', tag: 'CITATIONS' },
  { path: '/kg', title: '知识图谱', desc: '论文与结论的力导向关系图', tag: 'GRAPH' },
  { path: '/analyze', title: '数据分析', desc: '上传 CSV 并执行数据分析', tag: 'ANALYZE' },
  { path: '/design', title: '实验设计', desc: '生成假设、变量与统计方法方案', tag: 'DESIGN' },
  { path: '/write', title: '论文写作', desc: '按章节生成论文草稿', tag: 'WRITE' },
  { path: '/review', title: '学术审阅', desc: '按引用格式生成审稿报告', tag: 'REVIEW' },
  { path: '/bibliography', title: '参考文献', desc: 'GB/T 7714、APA、MLA 格式化', tag: 'BIBLIO' },
]

async function refresh(): Promise<void> {
  await Promise.all([store.refreshHealth(), store.refreshStatus(), store.loadCitationChain()])
}
</script>

<template>
  <div class="page overview">
    <header class="page-head">
      <div>
        <span class="eyebrow page-kicker">WORKBENCH / OVERVIEW</span>
        <h1 class="page-title">科研工作台</h1>
        <p class="page-desc">
          面向高校科研全生命周期的多智能体工作台：文献检索、知识图谱、数据分析、实验设计、论文写作与学术审阅在同一会话内串联。
        </p>
      </div>
      <div class="page-actions">
        <el-button size="small" :loading="store.loading" @click="refresh">刷新状态</el-button>
        <el-button v-if="!store.hasSession" type="primary" size="small" @click="router.push('/chat')">建立会话</el-button>
      </div>
    </header>

    <div class="stagger stack">
      <!-- 指标条 -->
      <div class="grid grid-3">
        <div class="stat">
          <span class="stat-label">文献数量</span>
          <span class="stat-value">{{ store.papersCount }}</span>
          <span class="stat-hint">来自会话检索结果</span>
        </div>
        <div class="stat">
          <span class="stat-label">引用链结论</span>
          <span class="stat-value">{{ store.claims.length }}</span>
          <span class="stat-hint">结论 → 证据 条目数</span>
        </div>
        <div class="stat">
          <span class="stat-label">当前阶段</span>
          <span class="stat-value" style="font-size: 20px">{{ phaseText }}</span>
          <span class="stat-hint">{{ store.hasSession ? `会话 ${store.sessionId}` : '尚未建立会话' }}</span>
        </div>
      </div>

      <div class="grid grid-main-side">
        <!-- 能力清单 -->
        <section class="panel">
          <header class="panel-head">
            <span class="panel-title serif">后端能力</span>
            <span class="panel-sub">{{ store.health?.features.length ?? 0 }} FEATURES</span>
          </header>
          <div class="panel-body">
            <div v-if="featureList.length" class="feat-grid">
              <div v-for="f in featureList" :key="f" class="feat">
                <span class="dot is-ok" />
                <span class="mono feat-name">{{ f }}</span>
              </div>
            </div>
            <div v-else class="empty">
              <span class="empty-icon mono">◌</span>
              <span>尚未获取后端能力清单，请确认后端已启动</span>
            </div>
          </div>
        </section>

        <!-- 置信度 + 会话状态 -->
        <section class="panel">
          <header class="panel-head">
            <span class="panel-title serif">会话状态</span>
          </header>
          <div class="panel-body">
            <div class="gauge-wrap">
              <ConfidenceGauge :value="overallConfidence" label="综合置信度" sublabel="SESSION CONFIDENCE" :size="168" />
            </div>
            <dl class="meta">
              <div><dt>会话 ID</dt><dd class="mono">{{ store.sessionId || '—' }}</dd></div>
              <div><dt>研究主题</dt><dd>{{ store.topic || '—' }}</dd></div>
              <div><dt>人工复核</dt><dd :class="store.humanReviewRequired ? 'text-amber' : 'text-accent'">{{ store.humanReviewRequired ? '需要' : '不需要' }}</dd></div>
              <div><dt>数据文件</dt><dd class="mono">{{ store.status?.has_data_file ? '已上传' : '未上传' }}</dd></div>
              <div><dt>论文草稿</dt><dd class="mono">{{ store.status?.has_draft ? '已生成' : '未生成' }}</dd></div>
              <div><dt>最近耗时</dt><dd class="mono">{{ store.lastLatencyMs === null ? '—' : (store.lastLatencyMs / 1000).toFixed(1) + 's' }}</dd></div>
            </dl>
          </div>
        </section>
      </div>

      <!-- 快捷入口 -->
      <div class="grid grid-2 entry-grid">
        <router-link v-for="e in entries" :key="e.path" :to="e.path" class="entry">
          <span class="entry-tag mono">{{ e.tag }}</span>
          <span class="entry-title">{{ e.title }}</span>
          <span class="entry-desc">{{ e.desc }}</span>
          <span class="entry-arrow mono">→</span>
        </router-link>
      </div>
    </div>
  </div>
</template>

<style scoped>
.gauge-wrap {
  display: flex;
  justify-content: center;
  padding: 4px 0 16px;
  border-bottom: 1px solid var(--hair-soft);
  margin-bottom: 14px;
}

.feat-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(190px, 1fr));
  gap: 9px;
}

.feat {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 11px;
  border: 1px solid var(--hair-soft);
  border-radius: var(--radius-sm);
  background: rgba(26, 35, 49, 0.3);
  transition: border-color 0.2s var(--ease), background 0.2s var(--ease);
}

.feat:hover {
  border-color: var(--accent-line);
  background: var(--accent-soft);
}

.feat-name {
  font-size: 11.5px;
  color: var(--text-2);
  letter-spacing: 0.02em;
}

.meta {
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 7px;
}

.meta > div {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
}

.meta dt {
  font-size: 12px;
  color: var(--text-3);
}

.meta dd {
  margin: 0;
  font-size: 12px;
  color: var(--text-1);
  text-align: right;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 200px;
}

.entry-grid {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.entry {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 15px 18px;
  border: 1px solid var(--hair);
  border-radius: var(--radius);
  background: linear-gradient(180deg, rgba(20, 27, 38, 0.8), rgba(14, 20, 32, 0.66));
  text-decoration: none;
  overflow: hidden;
  transition: border-color 0.22s var(--ease), transform 0.22s var(--ease), background 0.22s var(--ease);
}

.entry::before {
  content: '';
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  width: 2px;
  background: var(--accent);
  transform: scaleY(0);
  transform-origin: top;
  transition: transform 0.26s var(--ease);
}

.entry:hover {
  border-color: var(--accent-line);
  transform: translateX(2px);
  background: linear-gradient(180deg, rgba(24, 33, 46, 0.9), rgba(14, 20, 32, 0.7));
}

.entry:hover::before {
  transform: scaleY(1);
}

.entry-tag {
  font-size: 9.5px;
  letter-spacing: 0.14em;
  color: var(--text-3);
}

.entry-title {
  font-family: var(--font-display);
  font-size: 16px;
  color: var(--text-1);
}

.entry-desc {
  font-size: 12px;
  color: var(--text-2);
  line-height: 1.55;
}

.entry-arrow {
  position: absolute;
  right: 16px;
  top: 15px;
  color: var(--text-3);
  transition: color 0.2s var(--ease), transform 0.2s var(--ease);
}

.entry:hover .entry-arrow {
  color: var(--accent);
  transform: translateX(3px);
}
</style>
