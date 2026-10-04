<script setup lang="ts">
// 数据分析：上传 CSV → 填写分析意图 → 调用后端分析 → 展示结果与历史记录
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import MarkdownView from '@/components/MarkdownView.vue'
import ResultCard from '@/components/ResultCard.vue'
import api, { extractErrorMessage } from '@/api/client'
import { useSessionStore } from '@/stores/session'
import type { FigureItem, UploadResponse } from '@/api/types'

const store = useSessionStore()
const router = useRouter()

const fileInput = ref<HTMLInputElement | null>(null)
const uploading = ref(false)
const uploadResult = ref<UploadResponse | null>(null)

const intent = ref('请对该数据集做描述性统计与相关性分析，并给出主要发现与可视化建议。')
const answer = ref('')
const figures = ref<FigureItem[]>([])
const confidence = ref(0)
const elapsed = ref<number | null>(null)

interface RecordItem {
  at: string
  query: string
  confidence: number
  elapsedMs: number
}
const records = ref<RecordItem[]>([])

const fileLabel = computed(() => {
  if (!uploadResult.value) return ''
  const kb = uploadResult.value.size_bytes / 1024
  return kb >= 1024 ? `${(kb / 1024).toFixed(2)} MB` : `${kb.toFixed(1)} KB`
})

function pickFile(): void {
  fileInput.value?.click()
}

async function onFileChange(e: Event): Promise<void> {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file || !store.sessionId) return

  uploading.value = true
  try {
    const res = await api.uploadFile(store.sessionId, file)
    uploadResult.value = res
    ElMessage.success(`已上传：${res.filename}`)
    await store.refreshStatus()
  } catch (err) {
    ElMessage.error(extractErrorMessage(err))
  } finally {
    uploading.value = false
  }
}

async function runAnalyze(): Promise<void> {
  if (!store.sessionId || !intent.value.trim() || store.loading) return
  const q = intent.value.trim()
  const res = await store.runTask('数据分析', () => api.analyze(store.sessionId as string, q))
  if (!res) {
    if (store.error) ElMessage.error(store.error)
    return
  }
  answer.value = res.answer
  figures.value = res.figures ?? []
  confidence.value = res.confidence
  elapsed.value = store.lastLatencyMs
  records.value.unshift({
    at: new Date().toLocaleTimeString('zh-CN'),
    query: q,
    confidence: res.confidence,
    elapsedMs: store.lastLatencyMs ?? 0,
  })
  await store.refreshStatus()
}
</script>

<template>
  <div class="page">
    <header class="page-head">
      <div>
        <span class="eyebrow page-kicker">DATA / ANALYZE</span>
        <h1 class="page-title">数据分析</h1>
        <p class="page-desc">
          先上传数据文件（CSV），再描述分析意图。后端在沙箱中执行计算并返回文本结论，耗时通常较长。
        </p>
      </div>
      <div class="page-actions">
        <span class="chip" :class="store.status?.has_data_file ? 'is-accent' : ''">
          数据文件 {{ store.status?.has_data_file ? '已就绪' : '未上传' }}
        </span>
      </div>
    </header>

    <div v-if="!store.hasSession" class="empty big-empty">
      <span class="empty-icon mono">◌</span>
      <span>数据分析需要先建立会话</span>
      <el-button type="primary" size="small" @click="router.push('/chat')">前往建立会话</el-button>
    </div>

    <div v-else class="grid grid-main-side stagger">
      <div class="stack">
        <!-- 上传 -->
        <section class="panel">
          <header class="panel-head">
            <span class="panel-title serif">① 上传数据</span>
            <span class="panel-sub">multipart · field=file</span>
          </header>
          <div class="panel-body">
            <input ref="fileInput" type="file" accept=".csv,.txt,.xlsx" class="hidden-input" @change="onFileChange" />
            <div class="dropzone" @click="pickFile">
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="M12 16V4M6 10l6-6 6 6M4 20h16" />
              </svg>
              <span class="dz-title">{{ uploading ? '正在上传…' : '点击选择 CSV 数据文件' }}</span>
              <span class="dz-hint">支持 .csv / .txt / .xlsx</span>
            </div>

            <div v-if="uploadResult" class="file-info">
              <div class="file-row">
                <span class="fname">{{ uploadResult.filename }}</span>
                <span class="mono fmeta">{{ fileLabel }}</span>
              </div>
              <div class="file-path mono">{{ uploadResult.file_path }}</div>
              <div class="hint-line">{{ uploadResult.message }}</div>
            </div>
          </div>
        </section>

        <!-- 分析意图 -->
        <section class="panel">
          <header class="panel-head">
            <span class="panel-title serif">② 分析意图</span>
          </header>
          <div class="panel-body">
            <el-input v-model="intent" type="textarea" :rows="4" resize="none" placeholder="描述希望进行的分析，例如：分组比较、相关性、回归、异常检测…" />
            <div class="run-row">
              <span class="hint-line">需要先完成上传，后端会读取最近一次上传的数据文件</span>
              <el-button
                type="primary"
                :loading="store.loading"
                :disabled="store.loading || !store.status?.has_data_file || !intent.trim()"
                @click="runAnalyze"
              >
                {{ store.loading ? '分析中…' : '执行数据分析' }}
              </el-button>
            </div>
          </div>
        </section>

        <ResultCard
          title="分析结果"
          eyebrow="ANALYSIS OUTPUT"
          :loading="store.loading"
          loading-text="沙箱执行中，正在等待后端返回…"
          :error="null"
          :elapsed-ms="elapsed"
        >
          <MarkdownView :content="answer" empty-text="尚未执行分析，结果将显示在这里。" />
          <!-- 沙箱生成的图表：后端以 data URL 返回，直接渲染 -->
          <div v-if="figures.length" class="figures">
            <figure v-for="fig in figures" :key="fig.name" class="figure-item">
              <img :src="fig.data_url" :alt="fig.name" />
              <figcaption class="figure-name mono">{{ fig.name }}</figcaption>
            </figure>
          </div>
        </ResultCard>
      </div>

      <!-- 历史记录 -->
      <section class="panel">
        <header class="panel-head">
          <span class="panel-title serif">分析记录</span>
          <span class="panel-sub">{{ records.length }} RUNS</span>
        </header>
        <div class="panel-body">
          <div class="stat" style="margin-bottom: 14px">
            <span class="stat-label">最近置信度</span>
            <span class="stat-value">{{ (confidence * 100).toFixed(0) }}<span class="stat-unit">%</span></span>
            <span class="stat-hint">confidence_scores.data_analyst</span>
          </div>

          <ul v-if="records.length" class="rec-list">
            <li v-for="(r, i) in records" :key="i">
              <div class="rec-head">
                <span class="mono t">{{ r.at }}</span>
                <span class="chip">{{ (r.confidence * 100).toFixed(0) }}%</span>
                <span class="mono t">{{ (r.elapsedMs / 1000).toFixed(1) }}s</span>
              </div>
              <p class="rec-query">{{ r.query }}</p>
            </li>
          </ul>
          <div v-else class="empty">
            <span class="empty-icon mono">∅</span>
            <span>暂无分析记录</span>
          </div>
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

.hidden-input {
  display: none;
}

.figures {
  display: grid;
  gap: 12px;
  margin-top: 14px;
}

.figure-item {
  margin: 0;
  padding: 8px;
  border: 1px solid var(--hair);
  border-radius: var(--radius);
  background: rgba(11, 15, 20, 0.5);
}

.figure-item img {
  display: block;
  width: 100%;
  height: auto;
  border-radius: 6px;
  background: #fff;
}

.figure-name {
  display: block;
  margin-top: 6px;
  font-size: 10.5px;
  color: var(--text-3);
  letter-spacing: 0.04em;
}

.dropzone {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 7px;
  padding: 30px 20px;
  border: 1px dashed var(--hair-strong);
  border-radius: var(--radius);
  background: repeating-linear-gradient(
    45deg,
    rgba(36, 48, 67, 0.12) 0,
    rgba(36, 48, 67, 0.12) 8px,
    transparent 8px,
    transparent 16px
  );
  cursor: pointer;
  transition: all 0.2s var(--ease);
}

.dropzone:hover {
  border-color: var(--accent-line);
  background-color: var(--accent-soft);
}

.dropzone svg {
  width: 22px;
  height: 22px;
  fill: none;
  stroke: var(--accent);
  stroke-width: 1.5;
  stroke-linecap: round;
  stroke-linejoin: round;
}

.dz-title {
  font-size: 13px;
  color: var(--text-1);
}

.dz-hint {
  font-size: 11px;
  color: var(--text-3);
}

.file-info {
  margin-top: 14px;
  padding: 12px 14px;
  border: 1px solid var(--accent-line);
  border-radius: var(--radius-sm);
  background: var(--accent-soft);
}

.file-row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
}

.fname {
  font-size: 13px;
  color: var(--text-1);
}

.fmeta {
  font-size: 11px;
  color: var(--text-2);
}

.file-path {
  margin: 5px 0 4px;
  font-size: 10.5px;
  color: var(--text-3);
  word-break: break-all;
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

.rec-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 9px;
}

.rec-list li {
  padding: 10px 12px;
  border: 1px solid var(--hair-soft);
  border-radius: var(--radius-sm);
  background: rgba(20, 27, 38, 0.5);
}

.rec-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 5px;
}

.rec-head .t {
  font-size: 10px;
  color: var(--text-3);
  letter-spacing: 0.06em;
}

.rec-query {
  margin: 0;
  font-size: 12px;
  color: var(--text-2);
  line-height: 1.6;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
</style>
