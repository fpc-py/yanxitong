<script setup lang="ts">
// 知识库：双区上传（课题组共享 team / 我的私有 personal）+ 解析报告 + 文档列表（归属徽章 · 块数·字数 / 删除）
// team 需登录且仅上传者可删；personal 仅本人可见。上传与删除失败用 ElMessage 提示；列表加载失败静默降级
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { extractErrorMessage } from '@/api/client'
import { getAnonId } from '@/api/identity'
import { useAuthStore } from '@/stores/auth'
import { useSessionStore } from '@/stores/session'
import { useSystemStore } from '@/stores/system'
import type { KnowledgeFileItem, KnowledgeUploadResult } from '@/api/types'

const store = useSystemStore()
const auth = useAuthStore()
const sessionStore = useSessionStore()

const ACCEPT = '.txt,.md,.pdf'
const inputRef = ref<HTMLInputElement | null>(null)
const dragging = ref(false)
const uploading = ref(false)
const deletingKey = ref<string | null>(null)
const library = ref<'team' | 'personal'>('personal')
// 领域标签：'' = 通用（所有领域包均可召回）；实包 id = 仅该包召回
const kbTag = ref('')
const lastReport = ref<KnowledgeUploadResult | null>(null)

onMounted(() => void sessionStore.loadPacks())

/** 可打标签的领域包（默认包吸收所有未打标签内容，不需要显式选择） */
const tagOptions = computed(() => sessionStore.packs.filter((p) => p.kb_id !== 'default'))

/** 当前身份标签（与后端 owner 对齐：user:<id> / anon:<anon_id>），用于判断删除权限 */
const myLabel = computed(() => (auth.isLoggedIn && auth.user ? `user:${auth.user.id}` : `anon:${getAnonId()}`))

// 登出后自动退回私有库，避免选中态停留在不可用的 team
watch(
  () => auth.isLoggedIn,
  (loggedIn) => {
    if (!loggedIn && library.value === 'team') library.value = 'personal'
  },
)

function fileKey(f: KnowledgeFileItem): string {
  return `${f.library}|${f.filename}|${f.uploader}`
}

function canRemove(f: KnowledgeFileItem): boolean {
  if (f.library === 'personal') return true
  return f.uploader === myLabel.value
}

function openPicker(): void {
  if (uploading.value) return
  inputRef.value?.click()
}

function onPick(e: Event): void {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  if (file) void doUpload(file)
  input.value = ''
}

function onDragOver(e: DragEvent): void {
  e.preventDefault()
  dragging.value = true
}

function onDragLeave(e: DragEvent): void {
  if (!(e.currentTarget as HTMLElement).contains(e.relatedTarget as Node | null)) {
    dragging.value = false
  }
}

function onDrop(e: DragEvent): void {
  e.preventDefault()
  dragging.value = false
  const file = e.dataTransfer?.files?.[0]
  if (file) void doUpload(file)
}

async function doUpload(file: File): Promise<void> {
  if (uploading.value) return
  uploading.value = true
  lastReport.value = null
  try {
    const res = await store.uploadKnowledge(file, library.value, kbTag.value)
    lastReport.value = res
    if (res.deduped) {
      ElMessage.info(`「${res.filename}」内容与库中已有文档一致，已跳过重复入库`)
    } else {
      ElMessage.success(`已入库：${res.filename}（新增 ${res.added} 块）`)
    }
  } catch (e) {
    ElMessage.error(extractErrorMessage(e))
  } finally {
    uploading.value = false
  }
}

async function doRemove(f: KnowledgeFileItem): Promise<void> {
  const key = fileKey(f)
  if (deletingKey.value) return
  deletingKey.value = key
  try {
    const ok = await store.removeKnowledge(f.filename, f.library as 'team' | 'personal')
    if (ok) ElMessage.success(`已删除：${f.filename}`)
    else ElMessage.error('删除失败，请稍后重试')
  } finally {
    deletingKey.value = null
  }
}

function fileExt(name: string): string {
  const i = name.lastIndexOf('.')
  return i >= 0 ? name.slice(i + 1).toUpperCase() : 'DOC'
}

function fmtTime(iso: string): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const p = (n: number): string => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

const PARSER_LABEL: Record<string, string> = {
  pymupdf: 'PyMuPDF',
  ocr: 'OCR 识别',
  text: '文本直读',
  dedup: '内容去重',
}

function uploaderText(uploader: string): string {
  if (uploader.startsWith('user:')) return `用户 ${uploader.slice(5)}`
  if (uploader.startsWith('anon:')) return '访客'
  return uploader || '未知'
}
</script>

<template>
  <section class="panel kb-zone">
    <header class="panel-head">
      <span class="panel-title serif">知识库</span>
      <span class="panel-sub">{{ store.kbFiles.length }} DOCS · 检索知识边界</span>
    </header>

    <div class="panel-body kb-body">
      <input ref="inputRef" type="file" class="kb-input" :accept="ACCEPT" @change="onPick" />

      <div class="kb-lib-row">
        <el-radio-group v-model="library" :disabled="uploading">
          <el-radio-button value="personal">我的私有</el-radio-button>
          <el-radio-button value="team" :disabled="!auth.isLoggedIn">课题组共享</el-radio-button>
        </el-radio-group>
        <span v-if="!auth.isLoggedIn" class="kb-lib-hint">登录后可上传到课题组共享库</span>
        <span v-else-if="library === 'team'" class="kb-lib-hint">共享库对所有成员可检索</span>
        <span v-else class="kb-lib-hint">私有库仅本人可见与检索</span>
      </div>

      <div class="kb-lib-row">
        <span class="kb-lib-hint">领域标签</span>
        <el-select v-model="kbTag" size="small" class="kb-tag-select" :disabled="uploading" placeholder="通用（所有领域包可见）">
          <el-option label="通用（所有领域包可见）" value="" />
          <el-option v-for="p in tagOptions" :key="p.kb_id" :label="`仅 ${p.name}`" :value="p.kb_id" />
        </el-select>
        <span class="kb-lib-hint">打标签后仅在对应领域包的问答中被召回</span>
      </div>

      <div
        class="kb-drop"
        :class="{ 'is-drag': dragging }"
        role="button"
        tabindex="0"
        @click="openPicker"
        @keydown.enter="openPicker"
        @dragover="onDragOver"
        @dragleave="onDragLeave"
        @drop="onDrop"
      >
        <span class="kb-drop-icon">⤵</span>
        <span class="kb-drop-title">拖拽文档到此处，或点击选择</span>
        <span class="kb-drop-sub">
          支持 <b class="accent">.txt / .md / .pdf</b> · 上传至
          <b class="accent">{{ library === 'team' ? '课题组共享库' : '我的私有库' }}</b>，问答时自动纳入检索
        </span>
        <div v-if="uploading" class="kb-progress">
          <div class="loading-bar" />
          <span class="kb-progress-text">正在解析并分块入库…</span>
        </div>
      </div>

      <div v-if="lastReport" class="kb-report">
        <div class="kb-report-head">
          <span class="kb-report-title serif">解析报告</span>
          <span class="mono kb-report-file" :title="lastReport.filename">{{ lastReport.filename }}</span>
          <span class="kb-report-close" role="button" @click="lastReport = null">✕</span>
        </div>
        <div class="kb-report-grid">
          <span class="kb-report-k">解析方式</span>
          <span class="kb-report-v">{{ PARSER_LABEL[lastReport.parser_used] || lastReport.parser_used }}</span>
          <span class="kb-report-k">入库位置</span>
          <span class="kb-report-v">{{ lastReport.library === 'team' ? '课题组共享' : '我的私有' }}</span>
          <span class="kb-report-k">页数</span>
          <span class="kb-report-v">{{ lastReport.pages || '—' }}</span>
          <span class="kb-report-k">入库块数</span>
          <span class="kb-report-v">{{ lastReport.chunks }}</span>
          <span class="kb-report-k">OCR 兜底</span>
          <span class="kb-report-v">{{ lastReport.ocr_used ? '已启用（扫描页识别）' : '未使用' }}</span>
          <span class="kb-report-k">重复入库</span>
          <span class="kb-report-v">{{ lastReport.deduped ? '是（内容去重，秒回）' : '否' }}</span>
        </div>
      </div>

      <div v-if="store.kbLoading && !store.kbFiles.length" class="kb-skeleton">
        <div class="loading-bar" />
        <span class="kb-skeleton-text">正在读取文档列表…</span>
      </div>

      <div v-else-if="store.kbFiles.length" class="kb-list">
        <div v-for="f in store.kbFiles" :key="fileKey(f)" class="kb-item">
          <span class="kb-file-icon mono">{{ fileExt(f.filename) }}</span>
          <div class="kb-info">
            <span class="kb-name-row">
              <span class="kb-name mono" :title="f.filename">{{ f.filename }}</span>
              <span class="kb-lib-badge mono" :class="f.library === 'team' ? 'is-team' : 'is-personal'">
                {{ f.library === 'team' ? '共享' : '私有' }}
              </span>
            </span>
            <span class="kb-meta">
              {{ f.chunks }} 块 · {{ f.chars.toLocaleString() }} 字 · {{ fmtTime(f.updated_at) }}
              · 上传者 {{ uploaderText(f.uploader) }}
            </span>
          </div>
          <el-tooltip
            v-if="!canRemove(f)"
            content="共享库文档仅上传者可删除"
            placement="top"
          >
            <span class="kb-del-wrap">
              <button class="kb-del" type="button" disabled>删除</button>
            </span>
          </el-tooltip>
          <button
            v-else
            class="kb-del"
            type="button"
            :disabled="deletingKey === fileKey(f)"
            @click="doRemove(f)"
          >
            {{ deletingKey === fileKey(f) ? '删除中' : '删除' }}
          </button>
        </div>
      </div>

      <div v-else class="kb-empty">
        <span class="kb-empty-icon">◫</span>
        <span class="kb-empty-text">上传文档，扩大检索知识边界</span>
        <span class="kb-empty-sub">论文、笔记、实验记录均可入库，问答时自动纳入检索范围</span>
      </div>
    </div>
  </section>
</template>

<style scoped>
.kb-lib-row {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 10px;
  flex-wrap: wrap;
}

.kb-lib-hint {
  font-size: 11px;
  color: var(--text-3);
}

.kb-tag-select {
  width: 210px;
}

.kb-report {
  margin-top: 10px;
  padding: 10px 12px;
  border: 1px solid var(--accent-line);
  border-radius: var(--radius-sm);
  background: rgba(255, 255, 255, 0.75);
}

.kb-report-head {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin-bottom: 8px;
}

.kb-report-title {
  font-size: 12.5px;
  color: var(--text-1);
}

.kb-report-file {
  font-size: 10.5px;
  color: var(--text-3);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1 1 auto;
}

.kb-report-close {
  cursor: pointer;
  font-size: 11px;
  color: var(--text-3);
}

.kb-report-close:hover {
  color: var(--text-1);
}

.kb-report-grid {
  display: grid;
  grid-template-columns: auto 1fr auto 1fr;
  gap: 4px 14px;
  font-size: 11.5px;
}

.kb-report-k {
  color: var(--text-3);
}

.kb-report-v {
  color: var(--text-1);
}

.kb-name-row {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.kb-lib-badge {
  flex: 0 0 auto;
  padding: 0 5px;
  border-radius: 3px;
  font-size: 9.5px;
  line-height: 15px;
  letter-spacing: 0.06em;
}

.kb-lib-badge.is-team {
  color: var(--accent-deep);
  background: rgba(90, 122, 90, 0.12);
  border: 1px solid var(--accent-line);
}

.kb-lib-badge.is-personal {
  color: var(--text-2);
  background: var(--ink-750);
  border: 1px solid var(--hair-soft);
}

.kb-del-wrap {
  display: inline-block;
}

.kb-del:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}
</style>
