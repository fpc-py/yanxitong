<script setup lang="ts">
// 引用 / 文献列表：论文与知识库（[KB]）两类来源；KB 条目可点击预览原文块（前后邻块）
import { ref } from 'vue'
import api, { extractErrorMessage } from '@/api/client'
import type { ChunkPreview, Citation } from '@/api/types'

const props = withDefaults(
  defineProps<{
    citations: Citation[]
    compact?: boolean
  }>(),
  { compact: false },
)

function isKb(c: Citation): boolean {
  return c.kind === 'knowledge' || c.title.startsWith('[KB]')
}

function authorsText(authors: string[] | undefined): string {
  if (!authors || authors.length === 0) return '作者未标注'
  if (authors.length <= 3) return authors.join('、')
  return `${authors.slice(0, 3).join('、')} 等`
}

function sourceText(c: Citation): string {
  const parts: string[] = []
  if (c.source) parts.push(c.source)
  if (c.year) parts.push(String(c.year))
  return parts.join(' · ') || '来源未标注'
}

// ---- KB 原文块预览 ----
const previewOpen = ref(false)
const previewLoading = ref(false)
const previewError = ref('')
const preview = ref<ChunkPreview | null>(null)
const previewTitle = ref('')

async function openPreview(c: Citation): Promise<void> {
  if (!c.filename || !c.chunk_hash) return
  previewTitle.value = c.filename
  preview.value = null
  previewError.value = ''
  previewOpen.value = true
  previewLoading.value = true
  try {
    preview.value = await api.getChunkPreview(c.filename, c.chunk_hash)
  } catch (e) {
    previewError.value = extractErrorMessage(e) || '原文块加载失败'
  } finally {
    previewLoading.value = false
  }
}
</script>

<template>
  <div v-if="props.citations.length" class="cite-list" :class="{ compact }">
    <div v-for="(c, i) in props.citations" :key="`${c.title}-${i}`" class="cite-item">
      <div class="cite-idx mono">{{ String(i + 1).padStart(2, '0') }}</div>
      <div class="cite-main">
        <span class="cite-line">
          <span v-if="isKb(c)" class="kb-badge mono">知识库</span>
          <a v-if="c.url" class="cite-title" :href="c.url" target="_blank" rel="noopener noreferrer">
            {{ c.title }}
            <svg class="ext" viewBox="0 0 24 24" aria-hidden="true">
              <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6M15 3h6v6M10 14L21 3" />
            </svg>
          </a>
          <button
            v-else-if="isKb(c) && c.chunk_hash"
            class="cite-title kb-title"
            type="button"
            @click="openPreview(c)"
          >
            {{ c.title }}
            <span class="kb-open-hint mono">查看原文</span>
          </button>
          <span v-else class="cite-title">{{ c.title }}</span>
        </span>
        <div class="cite-meta">
          <template v-if="isKb(c)">
            <span class="authors">{{ c.filename || '未命名文档' }}<template v-if="c.page"> · 第 {{ c.page }} 页</template></span>
            <template v-if="c.library">
              <span class="sep" />
              <span class="mono src">{{ c.library === 'team' ? '课题组共享' : '我的私有' }}</span>
            </template>
          </template>
          <template v-else>
            <span class="authors">{{ authorsText(c.authors) }}</span>
            <span class="sep" />
            <span class="mono src">{{ sourceText(c) }}</span>
          </template>
        </div>
      </div>
    </div>

    <el-dialog v-model="previewOpen" :title="`原文预览 · ${previewTitle}`" width="640px" append-to-body>
      <div v-if="previewLoading" class="chunk-loading">
        <div class="loading-bar" />
        <span>正在读取原文块…</span>
      </div>
      <div v-else-if="previewError" class="chunk-error">{{ previewError }}</div>
      <div v-else-if="preview" class="chunk-view">
        <div class="chunk-meta mono">
          <span v-if="preview.section_title">章节：{{ preview.section_title }}</span>
          <span v-if="preview.page"> · 第 {{ preview.page }} 页</span>
          <span v-if="preview.chunk_index !== undefined"> · 块 #{{ preview.chunk_index + 1 }}</span>
        </div>
        <p v-if="preview.before" class="chunk-neighbor">{{ preview.before }}</p>
        <p class="chunk-text">{{ preview.text }}</p>
        <p v-if="preview.after" class="chunk-neighbor">{{ preview.after }}</p>
      </div>
      <template #footer>
        <el-button size="small" @click="previewOpen = false">关闭</el-button>
      </template>
    </el-dialog>
  </div>
  <div v-else class="empty cite-empty">
    <span class="empty-icon mono">∅</span>
    <span>本次回答未返回引用文献</span>
  </div>
</template>

<style scoped>
.cite-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.cite-item {
  display: flex;
  gap: 11px;
  padding: 10px 10px 10px 6px;
  border-radius: var(--radius-sm);
  border-left: 2px solid transparent;
  transition: background 0.18s var(--ease), border-color 0.18s var(--ease);
}

.cite-item:hover {
  background: rgba(232, 228, 217, 0.6);
  border-left-color: var(--accent);
}

.cite-idx {
  flex: 0 0 auto;
  font-size: 10.5px;
  color: var(--text-3);
  padding-top: 2px;
  letter-spacing: 0.06em;
}

.cite-main {
  min-width: 0;
  flex: 1 1 auto;
}

.cite-line {
  display: inline-flex;
  align-items: baseline;
  gap: 6px;
  flex-wrap: wrap;
}

.kb-badge {
  display: inline-block;
  padding: 0 5px;
  border-radius: 3px;
  font-size: 9.5px;
  line-height: 15px;
  letter-spacing: 0.06em;
  color: var(--accent-deep);
  background: var(--accent-ghost, rgba(90, 122, 90, 0.12));
  border: 1px solid var(--accent-line);
}

.cite-title {
  display: inline-flex;
  align-items: baseline;
  gap: 5px;
  font-size: 13.5px;
  line-height: 1.5;
  color: var(--text-1);
  text-decoration: none;
}

a.cite-title:hover {
  color: var(--accent);
}

button.kb-title {
  border: none;
  background: none;
  padding: 0;
  font-family: inherit;
  cursor: pointer;
  text-align: left;
}

button.kb-title:hover {
  color: var(--accent);
}

.kb-open-hint {
  font-size: 10px;
  color: var(--text-3);
  border-bottom: 1px dashed var(--hair-strong);
}

.ext {
  width: 11px;
  height: 11px;
  fill: none;
  stroke: currentColor;
  stroke-width: 1.7;
  stroke-linecap: round;
  stroke-linejoin: round;
  opacity: 0.7;
  align-self: center;
}

.cite-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 3px;
}

.authors {
  font-size: 11.5px;
  color: var(--text-2);
}

.sep {
  width: 3px;
  height: 3px;
  border-radius: 50%;
  background: var(--hair-strong);
}

.src {
  font-size: 10.5px;
  letter-spacing: 0.05em;
  color: var(--text-3);
}

.compact .cite-item {
  padding: 7px 8px 7px 6px;
}

.cite-empty {
  padding: 22px 16px;
  font-size: 12px;
}

/* ---- 原文块预览 ---- */
.chunk-loading {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 18px 4px;
  font-size: 12px;
  color: var(--text-2);
}

.chunk-error {
  padding: 18px 4px;
  font-size: 12.5px;
  color: var(--danger);
}

.chunk-view {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.chunk-meta {
  font-size: 10.5px;
  color: var(--text-3);
  letter-spacing: 0.04em;
}

.chunk-text {
  margin: 0;
  padding: 12px 14px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--accent-line);
  background: rgba(255, 255, 255, 0.85);
  font-size: 13px;
  line-height: 1.85;
  color: var(--text-1);
  white-space: pre-wrap;
  word-break: break-word;
}

.chunk-neighbor {
  margin: 0;
  padding: 8px 12px;
  border-radius: var(--radius-sm);
  background: var(--ink-750);
  font-size: 12px;
  line-height: 1.75;
  color: var(--text-3);
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
