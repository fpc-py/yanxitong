<script setup lang="ts">
// 引用 / 文献列表
import type { Citation } from '@/api/types'

const props = withDefaults(
  defineProps<{
    citations: Citation[]
    compact?: boolean
  }>(),
  { compact: false },
)

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
</script>

<template>
  <div v-if="props.citations.length" class="cite-list" :class="{ compact }">
    <div v-for="(c, i) in props.citations" :key="`${c.title}-${i}`" class="cite-item">
      <div class="cite-idx mono">{{ String(i + 1).padStart(2, '0') }}</div>
      <div class="cite-main">
        <a v-if="c.url" class="cite-title" :href="c.url" target="_blank" rel="noopener noreferrer">
          {{ c.title }}
          <svg class="ext" viewBox="0 0 24 24" aria-hidden="true">
            <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6M15 3h6v6M10 14L21 3" />
          </svg>
        </a>
        <span v-else class="cite-title">{{ c.title }}</span>
        <div class="cite-meta">
          <span class="authors">{{ authorsText(c.authors) }}</span>
          <span class="sep" />
          <span class="mono src">{{ sourceText(c) }}</span>
        </div>
      </div>
    </div>
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
  background: rgba(36, 48, 67, 0.28);
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
</style>
