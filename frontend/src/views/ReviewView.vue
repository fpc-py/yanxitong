<script setup lang="ts">
// 学术审阅：对整篇论文逐段扫描（引用格式 / 学术表达 / 潜在学术不端），绝不改稿
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import MarkdownView from '@/components/MarkdownView.vue'
import api from '@/api/client'
import { useSessionStore } from '@/stores/session'
import type { ReviewIssue, ReviewPayload } from '@/api/types'
import {
  ISSUE_TYPE_LABELS,
  SEVERITY_LABELS,
  STATUS_LABELS,
  issueLocation,
  statusClass,
  typeClass,
} from '@/utils/review'

const store = useSessionStore()
const router = useRouter()

const styles = [
  { value: 'GBT', label: 'GB/T 7714' },
  { value: 'APA', label: 'APA' },
  { value: 'MLA', label: 'MLA' },
]

const style = ref('GBT')
const draft = ref('')
const review = ref<ReviewPayload | null>(null)
const fallbackAnswer = ref('')
const confidence = ref(0)
const elapsed = ref<number | null>(null)

const charCount = computed(() => draft.value.replace(/\s+/g, '').length)
const issues = computed<ReviewIssue[]>(() => review.value?.issues ?? [])
const stats = computed(() => review.value?.stats ?? null)
const similarity = computed(() => review.value?.similarity ?? null)
const hasSessionDraft = computed(() => !!store.status?.writing_draft)

const REC_LABELS: Record<string, string> = {
  accept: '接受',
  minor_revision: '小修',
  major_revision: '大修',
  reject: '拒稿',
}

const recommendation = computed(() => {
  const rec = review.value?.recommendation ?? ''
  return REC_LABELS[rec] ?? rec
})

const dimensionScores = computed(() => {
  const r = review.value
  if (!r) return []
  const rows: { label: string; score?: number }[] = [
    { label: '结构', score: r.structure?.score },
    { label: '逻辑', score: r.logic?.score },
    { label: '引用', score: r.citations?.score },
    { label: '数据', score: r.data_consistency?.score },
    { label: '格式', score: r.format?.score },
    { label: '语言', score: r.language?.score },
    { label: '创新', score: r.novelty?.score },
  ]
  return rows.filter((row) => typeof row.score === 'number')
})

async function run(): Promise<void> {
  if (!store.sessionId || !draft.value.trim() || store.loading) return
  const res = await store.runTask('学术审阅', () =>
    api.review(store.sessionId as string, draft.value.trim(), style.value),
  )
  if (!res) {
    if (store.error) ElMessage.error(store.error)
    return
  }
  review.value = res.review ?? null
  fallbackAnswer.value = res.review ? '' : res.answer
  confidence.value = res.confidence
  elapsed.value = store.lastLatencyMs
  await store.refreshStatus()
}

function loadDraftFromSession(): void {
  if (!hasSessionDraft.value) return
  draft.value = store.status?.writing_draft ?? ''
  ElMessage.success('已载入会话中最近一次生成的草稿')
}

async function copyReport(): Promise<void> {
  if (!review.value) return
  try {
    await navigator.clipboard.writeText(fallbackAnswer.value || buildReportText())
    ElMessage.success('审稿报告已复制到剪贴板')
  } catch {
    ElMessage.error('复制失败，请手动选择文本')
  }
}

function buildReportText(): string {
  const r = review.value
  if (!r) return ''
  const lines = [
    `## 审稿报告 (${r.style})`,
    `**总分**: ${r.overall_score}/100`,
    `**建议**: ${recommendation.value}`,
    `**摘要**: ${r.summary}`,
    `**优点**: ${(r.strengths ?? []).join('；')}`,
    `**缺点**: ${(r.weaknesses ?? []).join('；')}`,
  ]
  if (r.issues?.length) {
    lines.push('## 逐段审查')
    for (const i of r.issues) {
      lines.push(`- [${ISSUE_TYPE_LABELS[i.type]}] ${issueLocation(i)}：${i.description}${i.suggestion ? `（建议：${i.suggestion}）` : ''}`)
    }
  }
  if (r.revision_checklist?.length) {
    lines.push('## 修改清单')
    r.revision_checklist.forEach((item, idx) => lines.push(`${idx + 1}. ${item}`))
  }
  return lines.join('\n\n')
}

// ---- 打开页面/切换会话时恢复上次审阅与草稿 ----
function restoreFromStatus(): void {
  const st = store.status
  if (!st) return
  if (!review.value && st.review) {
    review.value = st.review
    fallbackAnswer.value = ''
  }
  if (!draft.value && st.writing_draft) draft.value = st.writing_draft
}

onMounted(async () => {
  await store.refreshStatus()
  restoreFromStatus()
})

watch(
  () => store.sessionId,
  async () => {
    draft.value = ''
    review.value = null
    fallbackAnswer.value = ''
    confidence.value = 0
    elapsed.value = null
    await store.refreshStatus()
    restoreFromStatus()
  },
)
</script>

<template>
  <div class="page">
    <header class="page-head">
      <div>
        <span class="eyebrow page-kicker">OUTPUT / ACADEMIC REVIEWER</span>
        <h1 class="page-title">学术规范审查</h1>
        <p class="page-desc">
          对整篇论文做逐段扫描：引用格式、学术表达与潜在学术不端标记；系统绝不改稿，只输出定位到原文的建议。
        </p>
      </div>
      <div class="page-actions">
        <span v-if="similarity?.safe" class="pill-tag g sim-tag">已通过查重模拟 · {{ similarity.pct }}%</span>
        <el-button v-if="review" size="small" @click="copyReport">复制报告</el-button>
      </div>
    </header>

    <div v-if="!store.hasSession" class="empty big-empty">
      <span class="empty-icon mono">◌</span>
      <span>学术审阅需要先建立会话</span>
      <el-button type="primary" size="small" @click="router.push('/chat')">前往建立会话</el-button>
    </div>

    <template v-else>
      <section class="panel">
        <header class="panel-head">
          <span class="panel-title serif">待审草稿</span>
          <span class="panel-sub">{{ style === 'GBT' ? 'GB/T 7714' : style }}</span>
        </header>
        <div v-if="store.loading" class="loading-bar" />
        <div class="panel-body">
          <el-input
            v-model="draft"
            type="textarea"
            :rows="10"
            resize="none"
            placeholder="在此粘贴待审阅的论文草稿（摘要 / 引言 / 方法 / 结果…），或点击「载入会话草稿」使用写作页生成的草稿"
          />
          <div class="run-row">
            <div class="inline">
              <span class="hint-line">{{ charCount }} 字</span>
              <button
                v-for="s in styles"
                :key="s.value"
                class="style-chip"
                :class="{ 'is-active': style === s.value }"
                @click="style = s.value"
              >
                {{ s.label }}
              </button>
            </div>
            <div class="inline">
              <el-button v-if="hasSessionDraft" size="small" :disabled="store.loading" @click="loadDraftFromSession">
                载入会话草稿
              </el-button>
              <el-button
                type="primary"
                :loading="store.loading"
                :disabled="store.loading || !draft.trim()"
                @click="run"
              >
                {{ store.loading ? '审阅中…' : '提交审阅' }}
              </el-button>
            </div>
          </div>
        </div>
      </section>

      <template v-if="stats">
        <div class="grid grid-3">
          <div class="stat">
            <span class="stat-label">引用格式问题</span>
            <span class="stat-value text-amber">{{ stats.citation_format }}</span>
            <span class="stat-hint">
              {{ stats.citation_fixable }} 处可自动修复 · {{ stats.citation_manual }} 处需人工确认
            </span>
          </div>
          <div class="stat">
            <span class="stat-label">建议性润色</span>
            <span class="stat-value" style="color: var(--blue)">{{ stats.polish }}</span>
            <span class="stat-hint">口语化 / 表达不严谨</span>
          </div>
          <div class="stat">
            <span class="stat-label">学术不端标记</span>
            <span class="stat-value" :style="{ color: stats.misconduct ? 'var(--danger)' : 'var(--green)' }">
              {{ stats.misconduct }}
            </span>
            <span class="stat-hint">
              <template v-if="stats.similarity_pct != null">
                查重相似度 {{ stats.similarity_pct }}% ·
                {{ stats.similarity_safe ? '安全' : '超出阈值' }}（阈值 {{ stats.similarity_threshold }}%）
              </template>
              <template v-else>本地自检未运行（会话内暂无可比对文献）</template>
            </span>
          </div>
        </div>
      </template>

      <section v-if="review" class="panel">
        <header class="panel-head">
          <span class="panel-title serif">逐段审查报告</span>
          <span class="panel-sub mono">
            {{ issues.length }} 条 · 覆盖 {{ new Set(issues.map((i) => i.section)).size }} 个章节<template v-if="elapsed"> · {{ (elapsed / 1000).toFixed(1) }}s</template>
          </span>
        </header>
        <table class="grid">
          <colgroup>
            <col style="width: 18%" />
            <col style="width: 12%" />
            <col style="width: 8%" />
            <col style="width: 48%" />
            <col style="width: 14%" />
          </colgroup>
          <thead>
            <tr>
              <th>章节</th>
              <th>类型</th>
              <th>严重度</th>
              <th>说明</th>
              <th>状态</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="issue in issues" :key="issue.id">
              <td>
                <b>{{ issue.section || '全文' }}</b>
                <div class="cell-sub mono">{{ issueLocation(issue) }}</div>
              </td>
              <td><span class="pill-tag" :class="typeClass(issue.type)">{{ ISSUE_TYPE_LABELS[issue.type] }}</span></td>
              <td :class="`sev-${issue.severity}`">{{ SEVERITY_LABELS[issue.severity] }}</td>
              <td>
                {{ issue.description }}
                <div v-if="issue.suggestion" class="cell-sugg">建议：{{ issue.suggestion }}</div>
              </td>
              <td><span class="pill-tag" :class="statusClass(issue.status)">{{ STATUS_LABELS[issue.status] }}</span></td>
            </tr>
            <tr v-if="!issues.length">
              <td colspan="5" class="empty-row">未发现逐段问题，草稿引用与表达规范通过检查。</td>
            </tr>
          </tbody>
        </table>
      </section>

      <section v-if="review" class="panel">
        <header class="panel-head">
          <span class="panel-title serif">总评</span>
          <span class="panel-sub mono">{{ review.style }} · 置信度 {{ (confidence * 100).toFixed(0) }}%</span>
        </header>
        <div class="panel-body">
          <div class="summary-head">
            <div class="score-block">
              <span class="score-num">{{ review.overall_score }}</span>
              <span class="score-max">/100</span>
              <span class="pill-tag a rec-tag">{{ recommendation }}</span>
            </div>
            <div v-if="dimensionScores.length" class="dim-row">
              <span v-for="dim in dimensionScores" :key="dim.label" class="dim-chip mono">
                {{ dim.label }} {{ dim.score }}
              </span>
            </div>
          </div>
          <p class="summary-text">{{ review.summary }}</p>
          <div v-if="review.strengths?.length || review.weaknesses?.length" class="grid grid-2 sw-grid">
            <div class="sw-col">
              <div class="sw-title">优点</div>
              <ul class="sw-list">
                <li v-for="(s, idx) in review.strengths" :key="idx">{{ s }}</li>
              </ul>
            </div>
            <div class="sw-col">
              <div class="sw-title">缺点</div>
              <ul class="sw-list">
                <li v-for="(w, idx) in review.weaknesses" :key="idx">{{ w }}</li>
              </ul>
            </div>
          </div>
          <div v-if="review.revision_checklist?.length" class="checklist">
            <div class="sw-title">修改清单</div>
            <ol class="sw-list">
              <li v-for="(item, idx) in review.revision_checklist" :key="idx">{{ item }}</li>
            </ol>
          </div>
          <div v-if="fallbackAnswer" class="result-body">
            <MarkdownView :content="fallbackAnswer" />
          </div>
        </div>
      </section>

      <div v-if="!review && !store.loading" class="empty">
        <span class="empty-icon mono">◌</span>
        <span>尚未提交审阅：粘贴草稿或载入会话草稿后点击「提交审阅」。</span>
      </div>
    </template>
  </div>
</template>

<style scoped>
.big-empty {
  padding: 64px 20px;
  gap: 14px;
}

.sim-tag {
  padding: 5px 11px;
  font-size: 11px;
}

.run-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  margin-top: 14px;
  padding-top: 13px;
  border-top: 1px solid var(--hair-soft);
  flex-wrap: wrap;
}

.style-chip {
  padding: 5px 12px;
  border-radius: 999px;
  border: 1px solid var(--hair);
  background: rgba(255, 255, 255, 0.9);
  font-size: 11.5px;
  color: var(--ink-2);
  cursor: pointer;
  transition: all 0.15s var(--ease);
}

.style-chip:hover {
  border-color: var(--hair-strong);
}

.style-chip.is-active {
  border-color: var(--accent-line);
  background: var(--accent-soft);
  color: var(--accent);
}

.cell-sub {
  margin-top: 3px;
  font-size: 10px;
  color: var(--ink-3);
}

.cell-sugg {
  margin-top: 3px;
  font-size: 12px;
  color: var(--accent-deep);
}

.sev-high {
  color: var(--danger);
  font-weight: 600;
}

.sev-medium {
  color: var(--amber);
}

.sev-low {
  color: var(--ink-2);
}

.empty-row {
  text-align: center;
  color: var(--ink-3);
  padding: 22px 0;
}

.summary-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}

.score-block {
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.score-num {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
  font-size: 34px;
  line-height: 1;
  color: var(--accent);
  letter-spacing: -0.03em;
}

.score-max {
  font-size: 13px;
  color: var(--ink-3);
}

.rec-tag {
  margin-left: 6px;
}

.dim-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.dim-chip {
  padding: 3px 9px;
  border-radius: 5px;
  background: var(--soft);
  border: 1px solid var(--line);
  font-size: 10.5px;
  color: var(--ink-2);
}

.summary-text {
  margin: 14px 0 0;
  font-size: 13px;
  line-height: 1.75;
  color: var(--text-1);
}

.sw-grid {
  margin-top: 14px;
}

.sw-col {
  border: 1px solid var(--hair);
  border-radius: var(--radius-sm);
  padding: 11px 13px;
  background: rgba(255, 255, 255, 0.85);
}

.sw-title {
  font-size: 11px;
  letter-spacing: 0.08em;
  color: var(--ink-3);
  margin-bottom: 6px;
}

.sw-list {
  margin: 0;
  padding-left: 16px;
  font-size: 12.5px;
  line-height: 1.7;
  color: var(--text-1);
}

.checklist {
  margin-top: 14px;
  border-top: 1px dashed var(--line);
  padding-top: 12px;
}

.result-body {
  margin-top: 14px;
  border-top: 1px dashed var(--line);
  padding-top: 12px;
}
</style>
