<script setup lang="ts">
// 实验设计：根据研究问题生成假设、变量、统计方法与验证方案
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import MarkdownView from '@/components/MarkdownView.vue'
import ResultCard from '@/components/ResultCard.vue'
import api from '@/api/client'
import { useSessionStore } from '@/stores/session'

const store = useSessionStore()
const router = useRouter()

const query = ref('设计一套验证「图神经网络能提升分子性质预测精度」的实验方案。')
const answer = ref('')
const confidence = ref(0)
const elapsed = ref<number | null>(null)

async function run(): Promise<void> {
  if (!store.sessionId || !query.value.trim() || store.loading) return
  const res = await store.runTask('实验设计', () => api.design(store.sessionId as string, query.value.trim()))
  if (!res) {
    if (store.error) ElMessage.error(store.error)
    return
  }
  answer.value = res.answer
  confidence.value = res.confidence
  elapsed.value = store.lastLatencyMs
  await store.refreshStatus()
}

async function copy(): Promise<void> {
  if (!answer.value) return
  try {
    await navigator.clipboard.writeText(answer.value)
    ElMessage.success('设计方案已复制到剪贴板')
  } catch {
    ElMessage.error('复制失败，请手动选择文本')
  }
}
</script>

<template>
  <div class="page">
    <header class="page-head">
      <div>
        <span class="eyebrow page-kicker">EXPERIMENT / DESIGN</span>
        <h1 class="page-title">实验设计</h1>
        <p class="page-desc">
          输入研究问题，后端实验设计智能体输出假设、变量定义、统计方法与文献冲突检测结果。
        </p>
      </div>
      <div class="page-actions">
        <span v-if="confidence" class="chip is-accent">置信度 {{ (confidence * 100).toFixed(0) }}%</span>
      </div>
    </header>

    <div v-if="!store.hasSession" class="empty big-empty">
      <span class="empty-icon mono">◌</span>
      <span>实验设计需要先建立会话</span>
      <el-button type="primary" size="small" @click="router.push('/chat')">前往建立会话</el-button>
    </div>

    <div v-else class="stack stagger">
      <section class="panel">
        <header class="panel-head">
          <span class="panel-title serif">研究问题</span>
        </header>
        <div class="panel-body">
          <el-input v-model="query" type="textarea" :rows="4" resize="none" placeholder="描述要验证的假设或研究问题" />
          <div class="run-row">
            <span class="hint-line">生成结果包含「假设 / 依据 / 变量 / 统计方法 / 推荐验证方案」</span>
            <el-button type="primary" :loading="store.loading" :disabled="store.loading || !query.trim()" @click="run">
              {{ store.loading ? '设计中…' : '生成实验设计' }}
            </el-button>
          </div>
        </div>
      </section>

      <ResultCard
        title="设计方案"
        eyebrow="DESIGN OUTPUT"
        :loading="store.loading"
        loading-text="实验设计智能体正在推理，请稍候…"
        :error="store.error"
        :elapsed-ms="elapsed"
      >
        <template #actions>
          <el-button v-if="answer" size="small" @click="copy">复制</el-button>
        </template>
        <MarkdownView :content="answer" empty-text="尚未生成设计方案。" />
      </ResultCard>
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
</style>
