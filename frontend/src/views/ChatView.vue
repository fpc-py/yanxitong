<script setup lang="ts">
// 会话问答：创建会话 + 连续提问，渲染 Markdown 回答、引用、置信度与阶段
import { computed, nextTick, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import MarkdownView from '@/components/MarkdownView.vue'
import CitationList from '@/components/CitationList.vue'
import { useSessionStore } from '@/stores/session'

const store = useSessionStore()

const topicInput = ref('')
const queryInput = ref('')
const listEl = ref<HTMLDivElement | null>(null)

const PHASE_LABELS: Record<string, string> = {
  literature: '文献调研',
  experiment: '实验执行',
  writing: '论文写作',
  review: '学术审阅',
}

const phaseText = computed(() => PHASE_LABELS[store.messages.at(-1)?.phase ?? ''] ?? store.messages.at(-1)?.phase ?? '—')

const quickQueries = [
  '请检索该主题近五年的核心文献并给出研究现状综述',
  '该方向存在哪些主要争议与未解决的问题？',
  '总结现有方法在数据集与评价指标上的差异',
]

const elapsedSeconds = computed(() => (store.elapsedMs / 1000).toFixed(1))

function scrollToBottom(): void {
  void nextTick(() => {
    if (listEl.value) listEl.value.scrollTop = listEl.value.scrollHeight
  })
}

watch(() => store.messages.length, scrollToBottom)

function useQuick(q: string): void {
  queryInput.value = q
}

async function submit(): Promise<void> {
  const q = queryInput.value.trim()
  if (!q || store.loading) return
  queryInput.value = ''
  await store.sendQuery(q, topicInput.value.trim())
  if (store.error) ElMessage.error(store.error)
  scrollToBottom()
}

function reset(): void {
  store.resetSession()
  ElMessage.success('已重置本地会话')
}
</script>

<template>
  <div class="page chat-page">
    <header class="page-head">
      <div>
        <span class="eyebrow page-kicker">DIALOGUE / MULTI-AGENT</span>
        <h1 class="page-title">会话问答</h1>
        <p class="page-desc">
          首次提问自动创建会话，此后复用同一会话持续追问。后端为多智能体编排，单次响应通常需要数十秒，请耐心等待。
        </p>
      </div>
      <div class="page-actions">
        <span v-if="store.hasSession" class="chip is-accent">{{ phaseText }}</span>
        <el-button v-if="store.hasSession" size="small" @click="reset">重置会话</el-button>
      </div>
    </header>

    <div class="stagger stack">
      <!-- 未建立会话：创建表单 -->
      <section v-if="!store.hasSession" class="panel composer-hollow">
        <div class="panel-body create-body">
          <div class="create-copy">
            <div class="rail">
              <h2 class="create-title">建立研究会话</h2>
              <p class="hint-line">
                填写研究主题与首个问题。系统将依次执行「文献检索 → 知识图谱构建 → 结论生成」，
                并在右侧 Inspector 中记录阶段、置信度与耗时。
              </p>
            </div>
          </div>

          <label class="field-label">研究主题（topic）</label>
          <el-input v-model="topicInput" placeholder="例如：图神经网络在药物分子性质预测中的应用" size="large" />

          <label class="field-label" style="margin-top: 14px">首个问题</label>
          <el-input
            v-model="queryInput"
            type="textarea"
            :rows="4"
            resize="none"
            placeholder="例如：请梳理该主题近五年的研究进展与主要方法对比"
          />

          <div class="quick-row">
            <span class="eyebrow">快捷提问</span>
            <button v-for="q in quickQueries" :key="q" class="quick" @click="useQuick(q)">{{ q }}</button>
          </div>

          <div class="create-foot">
            <span class="hint-line">超时上限 {{ store.timeoutSeconds }} 秒 · 请求期间按钮将禁用</span>
            <el-button type="primary" :loading="store.loading" :disabled="store.loading || !queryInput.trim()" @click="submit">
              {{ store.loading ? `正在生成 ${elapsedSeconds}s` : '创建会话并提问' }}
            </el-button>
          </div>
        </div>
        <div v-if="store.loading" class="loading-bar" />
      </section>

      <!-- 已有会话：对话流 -->
      <template v-else>
        <section class="panel conversation">
          <header class="panel-head">
            <span class="panel-title serif">对话记录</span>
            <span class="panel-sub">{{ store.messages.length }} 条消息</span>
          </header>
          <div ref="listEl" class="msg-list">
            <div v-for="m in store.messages" :key="m.id" class="msg" :class="m.role">
              <div class="msg-gutter">
                <span class="role-tag mono">{{ m.role === 'user' ? 'REQ' : 'RES' }}</span>
              </div>
              <div class="msg-body">
                <div class="msg-head">
                  <span class="mono t">{{ m.at }}</span>
                  <template v-if="m.role === 'assistant'">
                    <span class="chip">置信度 {{ (m.confidence * 100).toFixed(0) }}%</span>
                    <span class="chip">{{ phaseText && m.phase ? (PHASE_LABELS[m.phase] ?? m.phase) : m.phase }}</span>
                    <span class="chip">耗时 {{ (m.elapsedMs / 1000).toFixed(1) }}s</span>
                  </template>
                </div>

                <div v-if="m.role === 'user'" class="user-text">{{ m.content }}</div>
                <MarkdownView v-else :content="m.content" empty-text="（空响应）" />

                <div v-if="m.humanReviewRequired" class="review-flag">
                  <span class="dot is-warn" />
                  <span>该回答标记为「需人工复核」(human_review_required)，请核实关键结论与引用。</span>
                </div>

                <div v-if="m.role === 'assistant' && m.citations.length" class="msg-cites">
                  <div class="eyebrow cites-label">引用文献 {{ m.citations.length }}</div>
                  <CitationList :citations="m.citations" compact />
                </div>
              </div>
            </div>
          </div>
          <div v-if="store.loading" class="loading-bar" />
        </section>

        <section class="panel composer">
          <div class="panel-body">
            <el-input
              v-model="queryInput"
              type="textarea"
              :rows="3"
              resize="none"
              placeholder="继续追问，例如：上述方法的实验设置与基线对比如何？  （Enter 发送 / Shift+Enter 换行）"
              @keydown.enter.exact.prevent="submit"
            />
            <div class="composer-foot">
              <span v-if="store.loading" class="waiting">
                <span class="dot is-ok" />
                <span class="mono">{{ store.activeLabel }} · 已等待 {{ elapsedSeconds }}s</span>
              </span>
              <span v-else class="hint-line">会话 {{ store.sessionId }}</span>
              <el-button
                type="primary"
                :loading="store.loading"
                :disabled="store.loading || !queryInput.trim()"
                @click="submit"
              >
                {{ store.loading ? '生成中…' : '发送' }}
              </el-button>
            </div>
          </div>
        </section>
      </template>
    </div>
  </div>
</template>

<style scoped>
.create-body {
  display: flex;
  flex-direction: column;
  padding: 22px 24px 20px;
}

.create-title {
  font-size: 18px;
  margin: 0 0 6px;
}

.quick-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 14px;
}

.quick {
  border: 1px solid var(--hair);
  background: rgba(184, 84, 58, 0.05);
  color: var(--text-2);
  font-family: var(--font-body);
  font-size: 12px;
  padding: 5px 11px;
  border-radius: 999px;
  cursor: pointer;
  transition: all 0.18s var(--ease);
}

.quick:hover {
  border-color: var(--accent-line);
  color: var(--accent);
  background: var(--accent-soft);
}

.create-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  margin-top: 18px;
  padding-top: 14px;
  border-top: 1px solid var(--hair-soft);
}

.msg-list {
  padding: 8px 16px 16px;
  max-height: 56vh;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
}

.msg {
  display: flex;
  gap: 12px;
  padding: 16px 0;
  border-bottom: 1px dashed var(--hair-soft);
  animation: riseIn 0.4s var(--ease) both;
}

.msg:last-child {
  border-bottom: 0;
}

.msg-gutter {
  flex: 0 0 42px;
  padding-top: 2px;
}

.role-tag {
  font-size: 9.5px;
  letter-spacing: 0.14em;
  color: var(--text-3);
  border: 1px solid var(--hair);
  border-radius: var(--radius-xs);
  padding: 2px 5px;
}

.msg.assistant .role-tag {
  color: var(--accent);
  border-color: var(--accent-line);
  background: var(--accent-soft);
}

.msg-body {
  flex: 1 1 auto;
  min-width: 0;
}

.msg-head {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 9px;
}

.msg-head .t {
  font-size: 10px;
  letter-spacing: 0.08em;
  color: var(--text-3);
}

.user-text {
  font-size: 13.5px;
  color: var(--text-1);
  line-height: 1.7;
  padding: 10px 13px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--hair-soft);
  border-left: 2px solid var(--accent-line);
  background: rgba(184, 84, 58, 0.05);
  white-space: pre-wrap;
}

.review-flag {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  margin-top: 12px;
  padding: 9px 12px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--amber-line);
  background: var(--amber-soft);
  color: #7a5a18;
  font-size: 12.5px;
  line-height: 1.55;
}

.msg-cites {
  margin-top: 14px;
  padding-top: 10px;
  border-top: 1px solid var(--hair-soft);
}

.cites-label {
  margin-bottom: 4px;
}

.composer-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 12px;
}

.waiting {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--accent);
}
</style>
