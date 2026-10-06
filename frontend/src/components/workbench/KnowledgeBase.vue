<script setup lang="ts">
// 知识库：拖拽 / 点击选择上传（.txt/.md/.pdf）+ 文档列表（块数·字数·更新时间 / 删除）+ 空态
// 上传与删除失败用 ElMessage 提示；列表加载失败静默降级
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { extractErrorMessage } from '@/api/client'
import { useSystemStore } from '@/stores/system'

const store = useSystemStore()

const ACCEPT = '.txt,.md,.pdf'
const inputRef = ref<HTMLInputElement | null>(null)
const dragging = ref(false)
const uploading = ref(false)
const deletingName = ref<string | null>(null)

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
  try {
    const res = await store.uploadKnowledge(file)
    ElMessage.success(`已入库：${res.filename}（新增 ${res.added} 块）`)
  } catch (e) {
    ElMessage.error(extractErrorMessage(e))
  } finally {
    uploading.value = false
  }
}

async function doRemove(name: string): Promise<void> {
  if (deletingName.value) return
  deletingName.value = name
  try {
    const ok = await store.removeKnowledge(name)
    if (ok) ElMessage.success(`已删除：${name}`)
    else ElMessage.error('删除失败，请稍后重试')
  } finally {
    deletingName.value = null
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
</script>

<template>
  <section class="panel kb-zone">
    <header class="panel-head">
      <span class="panel-title serif">知识库</span>
      <span class="panel-sub">{{ store.kbFiles.length }} DOCS · 检索知识边界</span>
    </header>

    <div class="panel-body kb-body">
      <input ref="inputRef" type="file" class="kb-input" :accept="ACCEPT" @change="onPick" />

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
        <span class="kb-drop-sub">支持 <b class="accent">.txt / .md / .pdf</b> · 上传后自动分块入库，问答时扩大检索范围</span>
        <div v-if="uploading" class="kb-progress">
          <div class="loading-bar" />
          <span class="kb-progress-text">正在分块入库…</span>
        </div>
      </div>

      <div v-if="store.kbLoading && !store.kbFiles.length" class="kb-skeleton">
        <div class="loading-bar" />
        <span class="kb-skeleton-text">正在读取文档列表…</span>
      </div>

      <div v-else-if="store.kbFiles.length" class="kb-list">
        <div v-for="f in store.kbFiles" :key="f.filename" class="kb-item">
          <span class="kb-file-icon mono">{{ fileExt(f.filename) }}</span>
          <div class="kb-info">
            <span class="kb-name mono" :title="f.filename">{{ f.filename }}</span>
            <span class="kb-meta">{{ f.chunks }} 块 · {{ f.chars.toLocaleString() }} 字 · {{ fmtTime(f.updated_at) }}</span>
          </div>
          <button
            class="kb-del"
            type="button"
            :disabled="deletingName === f.filename"
            @click="doRemove(f.filename)"
          >
            {{ deletingName === f.filename ? '删除中' : '删除' }}
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
