<script setup lang="ts">
// 论文写作：生成草稿 → 带标注草稿 + 逐段审阅建议（原文位置映射，绝不改稿）
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import MarkdownIt from 'markdown-it'
import AnnotatedDraft from '@/components/AnnotatedDraft.vue'
import api from '@/api/client'
import { useSessionStore } from '@/stores/session'
import type { ReviewIssue, ReviewPayload } from '@/api/types'
import {
  ISSUE_TYPE_LABELS,
  STATUS_LABELS,
  issueLocation,
  statusClass,
  typeClass,
} from '@/utils/review'

const store = useSessionStore()
const router = useRouter()

interface SectionOption {
  value: string
  label: string
  keyword: string
}

// keyword 用于拼接 query，确保后端 section_map 能命中
const sections: SectionOption[] = [
  { value: 'abstract', label: '摘要', keyword: '摘要' },
  { value: 'introduction', label: '引言', keyword: '引言' },
  { value: 'methods', label: '方法', keyword: '方法' },
  { value: 'results', label: '结果', keyword: '结果' },
  { value: 'discussion', label: '讨论', keyword: '讨论' },
  { value: 'full_paper', label: '全文', keyword: '全文' },
]

const section = ref('abstract')
const extra = ref('')
const draft = ref('')
const review = ref<ReviewPayload | null>(null)
const confidence = ref(0)
const elapsed = ref<number | null>(null)
const generatedAt = ref('')
const draftRef = ref<InstanceType<typeof AnnotatedDraft> | null>(null)

const current = computed(() => sections.find((s) => s.value === section.value) as SectionOption)
const wordCount = computed(() => draft.value.replace(/\s+/g, '').length)
const issues = computed<ReviewIssue[]>(() => review.value?.issues ?? [])
const similarity = computed(() => review.value?.similarity ?? null)
const stats = computed(() => review.value?.stats ?? null)

// 已核验引用（绿标）：[n] → 对应文献标题
const verifiedCitations = computed(() =>
  (review.value?.citation_trace?.resolved ?? []).map((r) => ({ marker: r.marker, title: r.title })),
)

function buildQuery(): string {
  const base = `撰写${current.value.keyword}（${current.value.value}）`
  const tail = extra.value.trim()
  return tail ? `${base}。补充要求：${tail}` : base
}

async function run(): Promise<void> {
  if (!store.sessionId || store.loading) return
  const res = await store.runTask(`论文写作 · ${current.value.label}`, () =>
    api.write(store.sessionId as string, buildQuery()),
  )
  if (!res) {
    if (store.error) ElMessage.error(store.error)
    return
  }
  draft.value = res.writing?.content ?? res.answer
  review.value = res.review ?? null
  confidence.value = res.confidence
  elapsed.value = store.lastLatencyMs
  generatedAt.value = new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
  await store.refreshStatus()
}

async function copy(): Promise<void> {
  if (!draft.value) return
  try {
    await navigator.clipboard.writeText(draft.value)
    ElMessage.success('草稿已复制到剪贴板')
  } catch {
    ElMessage.error('复制失败，请手动选择文本')
  }
}

const exportMd = new MarkdownIt({ html: false, breaks: true })

/** 前端导出 Word（.doc，Word/WPS 可直接打开；样式保持简洁以便继续编辑） */
function exportWord(): void {
  if (!draft.value) return
  const body = exportMd.render(draft.value)
  const html = `<!DOCTYPE html><html><head><meta charset="utf-8"><title>${current.value.label}</title></head><body>${body}</body></html>`
  const blob = new Blob(['\ufeff', html], { type: 'application/msword' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `草稿-${current.value.label}-${new Date().toISOString().slice(0, 10)}.doc`
  a.click()
  URL.revokeObjectURL(url)
  ElMessage.success('已导出 Word 文档（.doc）')
}

// ---- 建议卡 ↔ 原文 双向定位 ----
function focusIssue(id: string): void {
  void draftRef.value?.scrollToIssue(id)
}

function focusCard(id: string): void {
  const el = document.querySelector<HTMLElement>(`[data-card-id="${id}"]`)
  if (!el) return
  el.scrollIntoView({ behavior: 'smooth', block: 'center' })
  el.classList.add('is-flash')
  window.setTimeout(() => el.classList.remove('is-flash'), 1400)
}

// ---- 打开页面/切换会话时从会话状态恢复上次草稿与审阅 ----
function restoreFromStatus(): void {
  const st = store.status
  if (!st?.writing_draft || draft.value) return
  draft.value = st.writing_draft
  review.value = st.review ?? null
  if (st.writing_section && sections.some((s) => s.value === st.writing_section)) {
    section.value = st.writing_section
  }
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
    generatedAt.value = ''
    elapsed.value = null
    confidence.value = 0
    await store.refreshStatus()
    restoreFromStatus()
  },
)
</script>

<template>
  <div class="page">
    <header class="page-head">
      <div>
        <span class="eyebrow page-kicker">OUTPUT / PAPER WRITING</span>
        <h1 class="page-title">论文写作助手</h1>
        <p class="page-desc">
          生成草稿后自动逐段审阅：右栏给出修改建议与原文位置映射，系统绝不直接修改你的论文，AI 参与部分已自动标注。
        </p>
      </div>
      <div class="page-actions">
        <el-button size="small" :disabled="!draft" @click="copy">复制</el-button>
        <el-button size="small" type="primary" :disabled="!draft" @click="exportWord">导出 Word</el-button>
      </div>
    </header>

    <div v-if="!store.hasSession" class="empty big-empty">
      <span class="empty-icon mono">◌</span>
      <span>论文写作需要先建立会话</span>
      <el-button type="primary" size="small" @click="router.push('/chat')">前往建立会话</el-button>
    </div>

    <template v-else>
      <section class="panel">
        <header class="panel-head">
          <span class="panel-title serif">生成草稿</span>
          <span class="panel-sub">SECTION</span>
        </header>
        <div class="panel-body">
          <div class="section-grid">
            <button
              v-for="s in sections"
              :key="s.value"
              class="section-card"
              :class="{ 'is-active': section === s.value }"
              @click="section = s.value"
            >
              <span class="sc-label">{{ s.label }}</span>
            </button>
          </div>
          <div class="run-row">
            <el-input
              v-model="extra"
              class="extra-input"
              placeholder="补充要求（可选）：例如 控制在 300 字以内，突出与已有工作的差异"
            />
            <el-button
              type="primary"
              :loading="store.loading"
              :disabled="store.loading"
              @click="run"
            >
              {{ store.loading ? '撰写并审阅中…' : `生成${current.label}` }}
            </el-button>
          </div>
          <p class="hint-line">
            生成后自动串接审阅智能体：草稿与逐段建议一并返回（两次模型调用，请稍候）。
          </p>
        </div>
      </section>

      <div class="grid grid-main-side">
        <section class="panel">
          <header class="panel-head">
            <span class="panel-title serif">{{ current.label }} · 草稿</span>
            <span class="panel-sub">
              <template v-if="draft">
                <span class="chip">{{ wordCount }} 字</span>
                <span v-if="generatedAt" class="chip">生成于 {{ generatedAt }}</span>
                <span v-if="confidence" class="chip is-accent">
                  置信度 {{ (confidence * 100).toFixed(0) }}%
                </span>
                <span v-if="elapsed" class="chip">{{ (elapsed / 1000).toFixed(1) }}s</span>
              </template>
            </span>
          </header>
          <div v-if="store.loading" class="loading-bar" />
          <div class="panel-body">
            <AnnotatedDraft
              ref="draftRef"
              :content="draft"
              :issues="issues"
              :verified-citations="verifiedCitations"
              empty-text="尚未生成草稿：选择章节后点击「生成」。"
              @issue-click="focusCard"
            />
            <p v-if="draft && review" class="ai-note">
              [AI 协助标注] 本节草稿由写作智能体生成，右栏审阅建议由 Academic Reviewer
              提供；系统不修改原文，采纳前请人工复核。
            </p>
          </div>
        </section>

        <section class="panel">
          <header class="panel-head">
            <span class="panel-title serif">审阅建议</span>
            <span class="panel-sub mono">{{ issues.length ? `${issues.length} 条` : 'Academic Reviewer' }}</span>
          </header>
          <div class="panel-body issue-list">
            <template v-if="issues.length">
              <article
                v-for="issue in issues"
                :key="issue.id"
                class="issue-card"
                :class="`sev-${issue.severity}`"
                :data-card-id="issue.id"
                @click="focusIssue(issue.id)"
              >
                <div class="ic-head">
                  <span class="pill-tag" :class="typeClass(issue.type)">
                    {{ ISSUE_TYPE_LABELS[issue.type] }}
                  </span>
                  <span class="pill-tag" :class="statusClass(issue.status)">
                    {{ STATUS_LABELS[issue.status] }}
                  </span>
                </div>
                <p class="ic-desc">{{ issue.description }}</p>
                <p v-if="issue.suggestion" class="ic-sugg">建议：{{ issue.suggestion }}</p>
                <div class="ic-foot">
                  <span class="mono">{{ issueLocation(issue) }}</span>
                  <span v-if="issue.located" class="ic-link">点击定位原文</span>
                </div>
              </article>
            </template>
            <p v-else-if="store.loading" class="hint-line">审阅智能体正在逐段检查草稿…</p>
            <p v-else-if="!review" class="hint-line">生成草稿后自动审阅，逐段建议会出现在这里。</p>
            <p v-else class="hint-line">本轮审阅未发现需要修改的问题。</p>

            <article v-if="review && stats" class="issue-card is-pass">
              <div class="ic-head">
                <span class="pass-title">已通过</span>
              </div>
              <p class="ic-desc">
                <template v-if="verifiedCitations.length">
                  引用核验 {{ verifiedCitations.length }} 处通过
                </template>
                <template v-if="similarity?.pct != null">
                  <template v-if="verifiedCitations.length"> · </template>
                  查重相似度 {{ similarity.pct }}% · 安全阈值 {{ similarity.threshold }}%
                </template>
                <template v-if="verifiedCitations.length === 0 && similarity?.pct == null">
                  IMRaD 结构与格式检查已完成，详见「学术审阅」页
                </template>
              </p>
            </article>
          </div>
        </section>
      </div>
    </template>
  </div>
</template>

<style scoped>
.big-empty {
  padding: 64px 20px;
  gap: 14px;
}

.section-grid {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 8px;
}

@media (max-width: 900px) {
  .section-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}

.section-card {
  padding: 9px 10px;
  border: 1px solid var(--hair);
  border-radius: var(--radius-sm);
  background: rgba(255, 255, 255, 0.9);
  cursor: pointer;
  text-align: center;
  transition: all 0.2s var(--ease);
}

.section-card:hover {
  border-color: var(--hair-strong);
  background: rgba(184, 84, 58, 0.05);
}

.section-card.is-active {
  border-color: var(--accent-line);
  background: var(--accent-soft);
  box-shadow: inset 0 0 0 1px var(--accent-glow);
}

.sc-label {
  font-family: var(--font-display);
  font-size: 13.5px;
  color: var(--text-1);
}

.section-card.is-active .sc-label {
  color: var(--accent);
}

.run-row {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 12px;
}

.extra-input {
  flex: 1 1 auto;
}

.hint-line {
  margin: 10px 0 0;
}

.ai-note {
  margin: 22px 0 0;
  padding-top: 10px;
  border-top: 1px dashed var(--line);
  color: var(--text-3);
  font-size: 11.5px;
  line-height: 1.7;
}

.issue-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.issue-card {
  border: 1px solid var(--hair);
  border-left: 3px solid var(--amber);
  border-radius: var(--radius-sm);
  background: rgba(255, 255, 255, 0.92);
  padding: 11px 13px;
  cursor: pointer;
  transition: border-color 0.15s var(--ease), box-shadow 0.15s var(--ease);
}

.issue-card:hover {
  border-color: var(--hair-strong);
  border-left-color: var(--amber);
  box-shadow: var(--inner-glow);
}

.issue-card.sev-high {
  border-left-color: var(--danger);
}

.issue-card.sev-high:hover {
  border-left-color: var(--danger);
}

.issue-card.is-pass {
  border-left-color: var(--green);
  background: var(--green-soft);
  cursor: default;
}

.issue-card.is-flash {
  animation: card-flash 1.4s var(--ease);
}

@keyframes card-flash {
  0%,
  100% {
    box-shadow: none;
  }
  30% {
    box-shadow: 0 0 0 3px var(--accent-glow);
  }
  65% {
    box-shadow: 0 0 0 3px var(--accent-glow);
  }
}

.ic-head {
  display: flex;
  align-items: center;
  gap: 6px;
}

.pass-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--green);
}

.ic-desc {
  margin: 8px 0 0;
  font-size: 12.5px;
  color: var(--text-1);
  line-height: 1.65;
}

.ic-sugg {
  margin: 5px 0 0;
  font-size: 12px;
  color: var(--accent-deep);
  line-height: 1.65;
}

.ic-foot {
  margin-top: 8px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  font-size: 10.5px;
  color: var(--text-3);
}

.ic-link {
  color: var(--accent);
}
</style>
