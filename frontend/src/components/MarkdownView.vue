<script setup lang="ts">
// Markdown 渲染（markdown-it + DOMPurify 消毒，防 XSS）
import { computed } from 'vue'
import MarkdownIt from 'markdown-it'
import DOMPurify from 'dompurify'

const props = withDefaults(
  defineProps<{
    content: string
    emptyText?: string
  }>(),
  { emptyText: '暂无内容' },
)

const md = new MarkdownIt({
  html: false,
  linkify: true,
  breaks: true,
  typographer: false,
})

const html = computed(() => {
  const src = props.content || ''
  if (!src.trim()) return ''
  return DOMPurify.sanitize(md.render(src), {
    ADD_ATTR: ['target', 'rel'],
  })
})
</script>

<template>
  <div v-if="html" class="md-body" v-html="html" />
  <div v-else class="md-empty">{{ emptyText }}</div>
</template>

<style scoped>
.md-empty {
  padding: 18px 0;
  color: var(--text-3);
  font-size: 13px;
}
</style>
