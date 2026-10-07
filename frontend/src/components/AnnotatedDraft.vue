<script setup lang="ts">
// 带审阅标注的草稿：markdown 渲染后注入问题标记（黄/红）与已核验引用标记（绿）。
// 标注仅作展示，绝不修改草稿文本；建议卡可通过 expose 的 scrollToIssue 定位到原文。
import { computed, nextTick, ref } from 'vue'
import MarkdownIt from 'markdown-it'
import DOMPurify from 'dompurify'
import type { ReviewIssue } from '@/api/types'

const props = withDefaults(
  defineProps<{
    content: string
    issues?: ReviewIssue[]
    /** 引用核验通过的标记，如 [{ marker: '[12]', title: '...' }] */
    verifiedCitations?: { marker: string; title: string }[]
    emptyText?: string
  }>(),
  { issues: () => [], verifiedCitations: () => [], emptyText: '暂无内容' },
)

const emit = defineEmits<{ (e: 'issue-click', id: string): void }>()

const rootEl = ref<HTMLElement | null>(null)

const md = new MarkdownIt({ html: false, linkify: true, breaks: true, typographer: false })

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

/** idx 位置是否已处于某个 mark 内部（避免嵌套标注） */
function insideMark(html: string, idx: number): boolean {
  const slice = html.slice(0, idx)
  const opens = (slice.match(/<mark[\s>]/g) || []).length
  const closes = (slice.match(/<\/mark>/g) || []).length
  return opens > closes
}

/** 用 <mark> 包裹 needle 的第 n 次可替换出现（跳过已标注区间） */
function wrapOccurrences(
  html: string,
  needle: string,
  openTag: string,
  maxCount: number,
): string {
  if (!needle) return html
  let out = html
  let replaced = 0
  let from = 0
  while (replaced < maxCount) {
    const idx = out.indexOf(needle, from)
    if (idx < 0) break
    if (insideMark(out, idx)) {
      from = idx + 1
      continue
    }
    out = out.slice(0, idx) + openTag + needle + '</mark>' + out.slice(idx + needle.length)
    replaced += 1
    from = idx + openTag.length + needle.length + '</mark>'.length
  }
  return out
}

const html = computed(() => {
  const src = props.content || ''
  if (!src.trim()) return ''
  let out = DOMPurify.sanitize(md.render(src), { ADD_ATTR: ['target', 'rel'] })

  // 1) 问题标注：长 quote 优先，避免短片段抢先命中
  const marks = (props.issues || [])
    .filter((i) => i.located && i.quote)
    .slice()
    .sort((a, b) => b.quote.length - a.quote.length)
  for (const issue of marks) {
    const cls = issue.severity === 'high' ? 'mark-high' : 'mark-warn'
    const title = `${issue.description}${issue.suggestion ? `｜建议：${issue.suggestion}` : ''}`
    const open = `<mark class="${cls}" data-issue-id="${escapeHtml(issue.id)}" title="${escapeHtml(title)}">`
    let next = wrapOccurrences(out, escapeHtml(issue.quote), open, 1)
    if (next === out) {
      // quote 里可能残留 markdown 标记（** * `），去掉后重试
      const stripped = escapeHtml(issue.quote.replace(/[*_`#]/g, ''))
      if (stripped.length >= 4) next = wrapOccurrences(out, stripped, open, 1)
    }
    out = next
  }

  // 2) 已核验引用：[12] → 绿标（同一标记可多次出现，逐一标注）
  for (const cite of props.verifiedCitations || []) {
    const needle = escapeHtml(cite.marker)
    const title = `引用已核验：${cite.title}`
    const open = `<mark class="mark-good" data-cite="${needle}" title="${escapeHtml(title)}">`
    out = wrapOccurrences(out, needle, open, 12)
  }

  return out
})

function onClick(e: MouseEvent): void {
  const target = (e.target as HTMLElement).closest('mark[data-issue-id]')
  if (target) emit('issue-click', target.getAttribute('data-issue-id') || '')
}

/** 将指定问题的行内标注滚动到视野中央并闪烁提示 */
async function scrollToIssue(id: string): Promise<void> {
  await nextTick()
  const el = rootEl.value?.querySelector(`mark[data-issue-id="${id}"]`)
  if (!el) return
  el.scrollIntoView({ behavior: 'smooth', block: 'center' })
  el.classList.add('is-flash')
  window.setTimeout(() => el.classList.remove('is-flash'), 1400)
}

defineExpose({ scrollToIssue })
</script>

<template>
  <div ref="rootEl" class="annotated-draft">
    <div v-if="html" class="md-body" v-html="html" @click="onClick" />
    <div v-else class="md-empty">{{ emptyText }}</div>
  </div>
</template>

<style scoped>
.md-empty {
  padding: 18px 0;
  color: var(--text-3);
  font-size: 13px;
}

.annotated-draft :deep(mark) {
  background: transparent;
  color: inherit;
  cursor: pointer;
  padding: 1px 1px 2px;
  border-radius: 3px;
  transition: background 0.15s var(--ease);
}

.annotated-draft :deep(mark.mark-warn) {
  background: var(--amber-soft);
  border-bottom: 1.5px solid var(--amber);
}

.annotated-draft :deep(mark.mark-warn:hover) {
  background: rgba(212, 162, 76, 0.26);
}

.annotated-draft :deep(mark.mark-high) {
  background: var(--danger-soft);
  border-bottom: 1.5px solid var(--danger);
}

.annotated-draft :deep(mark.mark-high:hover) {
  background: rgba(168, 68, 58, 0.18);
}

.annotated-draft :deep(mark.mark-good) {
  background: var(--green-soft);
  border-bottom: 1.5px dotted var(--green);
}

.annotated-draft :deep(mark.is-flash) {
  animation: mark-flash 1.4s var(--ease);
}

@keyframes mark-flash {
  0%,
  100% {
    box-shadow: none;
  }
  25% {
    box-shadow: 0 0 0 3px var(--accent-glow);
  }
  60% {
    box-shadow: 0 0 0 3px var(--accent-glow);
  }
}
</style>
