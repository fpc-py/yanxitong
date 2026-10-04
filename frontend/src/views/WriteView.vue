<script setup lang="ts">
// 论文写作：选择章节生成草稿
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import MarkdownView from '@/components/MarkdownView.vue'
import ResultCard from '@/components/ResultCard.vue'
import api from '@/api/client'
import { useSessionStore } from '@/stores/session'

const store = useSessionStore()
const router = useRouter()

interface SectionOption {
  value: string
  label: string
  keyword: string
  hint: string
}

// keyword 用于拼接 query，确保后端 section_map 能命中
const sections: SectionOption[] = [
  { value: 'abstract', label: '摘要', keyword: '摘要', hint: 'Abstract' },
  { value: 'introduction', label: '引言', keyword: '引言', hint: 'Introduction' },
  { value: 'methods', label: '方法', keyword: '方法', hint: 'Methods' },
  { value: 'results', label: '结果', keyword: '结果', hint: 'Results' },
  { value: 'discussion', label: '讨论', keyword: '讨论', hint: 'Discussion' },
  { value: 'full_paper', label: '全文', keyword: '全文', hint: 'Full Paper' },
]

const section = ref('abstract')
const extra = ref('')
const draft = ref('')
const confidence = ref(0)
const elapsed = ref<number | null>(null)

const current = computed(() => sections.find((s) => s.value === section.value) as SectionOption)

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
  draft.value = res.answer
  confidence.value = res.confidence
  elapsed.value = store.lastLatencyMs
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

const wordCount = computed(() => draft.value.replace(/\s+/g, '').length)
</script>

<template>
  <div class="page">
    <header class="page-head">
      <div>
        <span class="eyebrow page-kicker">WRITING / SECTION DRAFT</span>
        <h1 class="page-title">论文写作</h1>
        <p class="page-desc">
          选择章节后，后端写作智能体会结合本会话已积累的文献与结论生成草稿，可直接复制到文档继续编辑。
        </p>
      </div>
      <div class="page-actions">
        <span class="chip">{{ current.hint }}</span>
      </div>
    </header>

    <div v-if="!store.hasSession" class="empty big-empty">
      <span class="empty-icon mono">◌</span>
      <span>论文写作需要先建立会话</span>
      <el-button type="primary" size="small" @click="router.push('/chat')">前往建立会话</el-button>
    </div>

    <div v-else class="stack stagger">
      <section class="panel">
        <header class="panel-head">
          <span class="panel-title serif">章节选择</span>
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
              <span class="sc-hint mono">{{ s.value }}</span>
            </button>
          </div>

          <label class="field-label" style="margin-top: 16px">补充要求（可选）</label>
          <el-input v-model="extra" type="textarea" :rows="2" resize="none" placeholder="例如：控制在 300 字以内，突出与已有工作的差异" />

          <div class="run-row">
            <span class="hint-line">请求 query 会自动携带章节关键词，后端据此判定 section</span>
            <el-button type="primary" :loading="store.loading" :disabled="store.loading" @click="run">
              {{ store.loading ? '撰写中…' : `生成${current.label}` }}
            </el-button>
          </div>
        </div>
      </section>

      <ResultCard
        title="草稿"
        eyebrow="DRAFT OUTPUT"
        :loading="store.loading"
        loading-text="写作智能体正在生成草稿，请稍候…"
        :error="store.error"
        :elapsed-ms="elapsed"
      >
        <template #actions>
          <span v-if="draft" class="chip">{{ wordCount }} 字</span>
          <span v-if="confidence" class="chip is-accent">置信度 {{ (confidence * 100).toFixed(0) }}%</span>
          <el-button v-if="draft" size="small" @click="copy">复制</el-button>
        </template>
        <div class="result-body">
          <MarkdownView :content="draft" empty-text="尚未生成草稿，请选择章节后点击生成。" />
        </div>
      </ResultCard>
    </div>
  </div>
</template>

<style scoped>
.big-empty {
  padding: 64px 20px;
  gap: 14px;
}

.section-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(118px, 1fr));
  gap: 9px;
}

.section-card {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 3px;
  padding: 12px 13px;
  border: 1px solid var(--hair);
  border-radius: var(--radius-sm);
  background: rgba(20, 27, 38, 0.5);
  cursor: pointer;
  text-align: left;
  transition: all 0.2s var(--ease);
}

.section-card:hover {
  border-color: var(--hair-strong);
  background: rgba(26, 35, 49, 0.7);
}

.section-card.is-active {
  border-color: var(--accent-line);
  background: var(--accent-soft);
  box-shadow: inset 0 0 0 1px var(--accent-glow);
}

.sc-label {
  font-family: var(--font-display);
  font-size: 15px;
  color: var(--text-1);
}

.section-card.is-active .sc-label {
  color: var(--accent);
}

.sc-hint {
  font-size: 9.5px;
  letter-spacing: 0.1em;
  color: var(--text-3);
}

.run-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  margin-top: 16px;
  padding-top: 13px;
  border-top: 1px solid var(--hair-soft);
}
</style>
