<script setup lang="ts">
// 学术审阅：粘贴草稿 + 选择引用格式 → 生成审稿报告
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import MarkdownView from '@/components/MarkdownView.vue'
import ResultCard from '@/components/ResultCard.vue'
import api from '@/api/client'
import { useSessionStore } from '@/stores/session'

const store = useSessionStore()
const router = useRouter()

const styles = [
  { value: 'APA', label: 'APA' },
  { value: 'MLA', label: 'MLA' },
  { value: 'GBT', label: 'GB/T 7714' },
]

const style = ref('GBT')
const draft = ref('')
const report = ref('')
const confidence = ref(0)
const elapsed = ref<number | null>(null)

const charCount = computed(() => draft.value.replace(/\s+/g, '').length)

async function run(): Promise<void> {
  if (!store.sessionId || !draft.value.trim() || store.loading) return
  // 后端按 [APA]/[MLA]/[GBT] 前缀识别引用格式
  const payload = `[${style.value}]${draft.value.trim()}`
  const res = await store.runTask('学术审阅', () => api.review(store.sessionId as string, payload))
  if (!res) {
    if (store.error) ElMessage.error(store.error)
    return
  }
  report.value = res.answer
  confidence.value = res.confidence
  elapsed.value = store.lastLatencyMs
  await store.refreshStatus()
}

async function copy(): Promise<void> {
  if (!report.value) return
  try {
    await navigator.clipboard.writeText(report.value)
    ElMessage.success('审稿报告已复制到剪贴板')
  } catch {
    ElMessage.error('复制失败，请手动选择文本')
  }
}
</script>

<template>
  <div class="page">
    <header class="page-head">
      <div>
        <span class="eyebrow page-kicker">REVIEW / PEER ASSESSMENT</span>
        <h1 class="page-title">学术审阅</h1>
        <p class="page-desc">
          粘贴论文草稿并指定引用格式，后端审阅智能体将输出总分、建议、优缺点与修改清单。
        </p>
      </div>
      <div class="page-actions">
        <span v-if="confidence" class="chip is-accent">置信度 {{ (confidence * 100).toFixed(0) }}%</span>
      </div>
    </header>

    <div v-if="!store.hasSession" class="empty big-empty">
      <span class="empty-icon mono">◌</span>
      <span>学术审阅需要先建立会话</span>
      <el-button type="primary" size="small" @click="router.push('/chat')">前往建立会话</el-button>
    </div>

    <div v-else class="grid grid-main-side stagger">
      <div class="stack">
        <section class="panel">
          <header class="panel-head">
            <span class="panel-title serif">待审草稿</span>
            <span class="panel-sub">以 [{{ style }}] 前缀提交</span>
          </header>
          <div class="panel-body">
            <el-input
              v-model="draft"
              type="textarea"
              :rows="14"
              resize="none"
              placeholder="在此粘贴待审阅的论文草稿（摘要 / 引言 / 方法 / 结果…）"
            />
            <div class="run-row">
              <span class="hint-line">当前 {{ charCount }} 字 · 后端将按选定格式检查引用规范性</span>
              <el-button type="primary" :loading="store.loading" :disabled="store.loading || !draft.trim()" @click="run">
                {{ store.loading ? '审阅中…' : '提交审阅' }}
              </el-button>
            </div>
          </div>
        </section>

        <ResultCard
          title="审稿报告"
          eyebrow="REVIEW OUTPUT"
          :loading="store.loading"
          loading-text="审阅智能体正在评估草稿，请稍候…"
          :error="store.error"
          :elapsed-ms="elapsed"
        >
          <template #actions>
            <el-button v-if="report" size="small" @click="copy">复制</el-button>
          </template>
          <div class="result-body">
            <MarkdownView :content="report" empty-text="尚未提交审阅。" />
          </div>
        </ResultCard>
      </div>

      <section class="panel">
        <header class="panel-head">
          <span class="panel-title serif">引用格式</span>
        </header>
        <div class="panel-body">
          <div class="style-list">
            <button
              v-for="s in styles"
              :key="s.value"
              class="style-card"
              :class="{ 'is-active': style === s.value }"
              @click="style = s.value"
            >
              <span class="st-label mono">{{ s.value }}</span>
              <span class="st-name">{{ s.label }}</span>
            </button>
          </div>
          <p class="hint-line" style="margin-top: 14px">
            格式仅用于审阅时的引用规范检查；如需生成完整参考文献列表，请前往「参考文献」页面。
          </p>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.big-empty {
  padding: 64px 20px;
  gap: 14px;
}

.run-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  margin-top: 14px;
  padding-top: 13px;
  border-top: 1px solid var(--hair-soft);
}

.style-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.style-card {
  display: flex;
  align-items: center;
  gap: 11px;
  padding: 11px 13px;
  border: 1px solid var(--hair);
  border-radius: var(--radius-sm);
  background: rgba(20, 27, 38, 0.5);
  cursor: pointer;
  text-align: left;
  transition: all 0.2s var(--ease);
}

.style-card:hover {
  border-color: var(--hair-strong);
}

.style-card.is-active {
  border-color: var(--accent-line);
  background: var(--accent-soft);
}

.st-label {
  font-size: 12px;
  color: var(--text-2);
  min-width: 42px;
}

.style-card.is-active .st-label {
  color: var(--accent);
}

.st-name {
  font-size: 12.5px;
  color: var(--text-1);
}
</style>
