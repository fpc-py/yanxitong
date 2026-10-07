<script setup lang="ts">
// 文献与引用：四页签 —— 引用链（结论 → 证据）/ 文献矩阵（结构化抽取）/ 矛盾与空白 / 证据链（图谱追溯）
// 统计卡与页签共同消费 session store（会话状态、citation-chain、matrix/conflicts/gaps 端点）
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useSessionStore } from '@/stores/session'
import LiteratureMatrix from '@/components/workbench/LiteratureMatrix.vue'
import LiteratureInsights from '@/components/workbench/LiteratureInsights.vue'
import EvidenceChain from '@/components/workbench/EvidenceChain.vue'

const store = useSessionStore()
const router = useRouter()

const activeTab = ref('chain')

const claims = computed(() => store.normalizedClaims)

const totalEvidence = computed(() => claims.value.reduce((sum, c) => sum + c.citations.length, 0))

const kbDocCount = computed(() => {
  const names = new Set<string>()
  for (const c of claims.value) {
    for (const cit of c.citations) {
      if (cit.kind === 'knowledge' && cit.filename) names.add(cit.filename)
    }
  }
  return names.size
})

function confidenceTone(v: number): string {
  if (v >= 0.7) return 'ok'
  if (v >= 0.45) return 'warn'
  return 'low'
}

async function reload(): Promise<void> {
  await Promise.all([
    store.refreshStatus(),
    store.loadCitationChain(),
    store.loadLiteratureInsights(),
  ])
}

onMounted(() => {
  if (store.hasSession) void store.loadLiteratureInsights()
})
</script>

<template>
  <div class="page">
    <header class="page-head">
      <div>
        <span class="eyebrow page-kicker">LITERATURE / CITATION CHAIN</span>
        <h1 class="page-title">文献与引用</h1>
        <p class="page-desc">
          文献数量取自会话状态，引用链来自后端 citation-chain 接口；文献矩阵与矛盾/空白来自结构化抽取分析结果。
        </p>
      </div>
      <div class="page-actions">
        <el-button size="small" :disabled="!store.hasSession" @click="reload">重新加载</el-button>
      </div>
    </header>

    <div v-if="!store.hasSession" class="empty big-empty">
      <span class="empty-icon mono">◌</span>
      <span>当前没有活跃会话</span>
      <el-button type="primary" size="small" @click="router.push('/chat')">前往建立会话</el-button>
    </div>

    <div v-else class="stagger stack">
      <div class="grid grid-3">
        <div class="stat">
          <span class="stat-label">文献数量</span>
          <span class="stat-value">{{ store.papersCount }}<span class="stat-unit">篇</span></span>
          <span class="stat-hint">literature_results 长度</span>
        </div>
        <div class="stat">
          <span class="stat-label">引用链结论</span>
          <span class="stat-value">{{ claims.length }}<span class="stat-unit">条</span></span>
          <span class="stat-hint">共 {{ totalEvidence }} 条证据<template v-if="kbDocCount"> · 含知识库 {{ kbDocCount }} 篇</template></span>
        </div>
        <div class="stat">
          <span class="stat-label">平均置信度</span>
          <span class="stat-value">{{ (store.averageConfidence * 100).toFixed(1) }}<span class="stat-unit">%</span></span>
          <span class="stat-hint">citation-chain average_confidence</span>
        </div>
      </div>

      <el-tabs v-model="activeTab" class="lit-tabs">
        <el-tab-pane label="引用链" name="chain">
          <section class="panel">
            <header class="panel-head">
              <span class="panel-title serif">结论 → 证据</span>
              <span class="panel-sub">{{ claims.length }} CLAIMS</span>
            </header>
            <div class="panel-body">
              <div v-if="claims.length" class="claim-list">
                <article v-for="(c, i) in claims" :key="i" class="claim">
                  <div class="claim-head">
                    <span class="claim-idx mono">{{ String(i + 1).padStart(2, '0') }}</span>
                    <p class="claim-text">{{ c.text }}</p>
                  </div>
                  <div class="claim-conf">
                    <div class="bar">
                      <div
                        class="bar-fill"
                        :class="confidenceTone(c.confidence)"
                        :style="{ width: `${Math.min(100, c.confidence * 100)}%` }"
                      />
                    </div>
                    <span class="mono conf-val">{{ (c.confidence * 100).toFixed(0) }}%</span>
                  </div>

                  <div v-if="c.citations.length" class="evidence">
                    <div class="eyebrow ev-label">支撑证据 {{ c.citations.length }}</div>
                    <ul class="ev-list">
                      <li v-for="(cit, j) in c.citations" :key="j">
                        <a v-if="cit.url" :href="cit.url" target="_blank" rel="noopener noreferrer">{{ cit.title }}</a>
                        <span v-else>{{ cit.title }}</span>
                        <span class="ev-meta mono">
                          {{ (cit.authors || []).slice(0, 2).join('、') || '作者未标注' }}
                          <template v-if="cit.year"> · {{ cit.year }}</template>
                          <template v-if="cit.source"> · {{ cit.source }}</template>
                        </span>
                      </li>
                    </ul>
                  </div>
                  <div v-else class="no-evidence">该结论未附带可解析的证据条目</div>
                </article>
              </div>
              <div v-else class="empty">
                <span class="empty-icon mono">∅</span>
                <span>引用链为空</span>
                <span class="hint-line">后端会随问答逐步累积 citation_chain，先进行一次提问后再查看。</span>
              </div>
            </div>
          </section>
        </el-tab-pane>

        <el-tab-pane name="matrix">
          <template #label>
            文献矩阵
            <span class="tab-badge mono">{{ store.matrixRows.length }}</span>
          </template>
          <LiteratureMatrix />
        </el-tab-pane>

        <el-tab-pane name="insights">
          <template #label>
            矛盾与空白
            <span class="tab-badge mono">{{ store.conflicts.length + store.researchGaps.length }}</span>
          </template>
          <LiteratureInsights />
        </el-tab-pane>

        <el-tab-pane label="证据链" name="evidence">
          <EvidenceChain v-if="activeTab === 'evidence'" />
        </el-tab-pane>
      </el-tabs>
    </div>
  </div>
</template>

<style scoped>
.big-empty {
  padding: 64px 20px;
  gap: 14px;
}

.lit-tabs :deep(.el-tabs__header) {
  margin-bottom: 18px;
}

.lit-tabs :deep(.el-tabs__item) {
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

.claim-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.claim {
  padding: 15px 16px;
  border: 1px solid var(--hair-soft);
  border-radius: var(--radius);
  background: rgba(255, 255, 255, 0.9);
  transition: border-color 0.2s var(--ease);
}

.claim:hover {
  border-color: var(--accent-line);
}

.claim-head {
  display: flex;
  gap: 11px;
  align-items: baseline;
}

.claim-idx {
  font-size: 10.5px;
  color: var(--text-3);
  flex: 0 0 auto;
  letter-spacing: 0.06em;
}

.claim-text {
  margin: 0;
  font-size: 13.5px;
  line-height: 1.7;
  color: var(--text-1);
}

.claim-conf {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 11px 0 0 24px;
}

.bar {
  flex: 1 1 auto;
  max-width: 260px;
  height: 4px;
  border-radius: 3px;
  background: var(--ink-750);
  overflow: hidden;
}

.bar-fill {
  height: 100%;
  border-radius: 3px;
  transition: width 0.6s var(--ease);
}

.bar-fill.ok {
  background: linear-gradient(90deg, var(--accent-deep), var(--accent));
  box-shadow: 0 0 8px var(--accent-glow);
}

.bar-fill.warn {
  background: linear-gradient(90deg, #b98a33, var(--amber));
}

.bar-fill.low {
  background: linear-gradient(90deg, #a3544d, var(--danger));
}

.conf-val {
  font-size: 11px;
  color: var(--text-2);
}

.evidence {
  margin: 13px 0 0 24px;
  padding-top: 11px;
  border-top: 1px dashed var(--hair-soft);
}

.ev-label {
  margin-bottom: 6px;
}

.ev-list {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 7px;
}

.ev-list li {
  display: flex;
  flex-direction: column;
  gap: 1px;
  padding-left: 12px;
  position: relative;
  font-size: 12.5px;
}

.ev-list li::before {
  content: '';
  position: absolute;
  left: 0;
  top: 8px;
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: var(--accent);
  opacity: 0.7;
}

.ev-list a {
  color: var(--text-1);
  text-decoration: none;
  border-bottom: 1px solid transparent;
}

.ev-list a:hover {
  color: var(--accent);
  border-bottom-color: var(--accent-line);
}

.ev-meta {
  font-size: 10.5px;
  color: var(--text-3);
  letter-spacing: 0.02em;
}

.no-evidence {
  margin: 12px 0 0 24px;
  font-size: 11.5px;
  color: var(--text-3);
}
</style>
