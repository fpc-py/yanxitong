<script setup lang="ts">
// 全局底部 Composer（对应 v3.0 原型）：
// 除研究对话页（自带输入区）外全站可见；Enter 发送，Shift+Enter 换行，兼容中文输入法组词
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useSessionStore } from '@/stores/session'

const store = useSessionStore()
const router = useRouter()
const text = ref('')
const composing = ref(false)
const ta = ref<HTMLTextAreaElement | null>(null)

function autoResize(): void {
  const el = ta.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = `${Math.min(el.scrollHeight, 160)}px`
}

function onCompositionStart(): void {
  composing.value = true
}

function onCompositionEnd(): void {
  composing.value = false
}

function onKeydown(e: KeyboardEvent): void {
  if (e.key === 'Enter' && !e.shiftKey && !composing.value) {
    e.preventDefault()
    void send()
  }
}

async function send(): Promise<void> {
  const q = text.value.trim()
  if (!q || store.loading) return
  text.value = ''
  if (ta.value) ta.value.style.height = 'auto'
  // 先跳转再研究：让用户立刻看到「研究对话」页的加载态，研究过程在后台执行
  if (router.currentRoute.value.path !== '/chat') await router.push('/chat')
  await store.sendQuery(q, store.topic || '')
}
</script>

<template>
  <div class="composer">
    <div class="composer-inner">
      <textarea
        ref="ta"
        v-model="text"
        rows="1"
        placeholder="描述你的研究任务，例如：钙钛矿太阳能电池的稳定性研究进展"
        @input="autoResize"
        @compositionstart="onCompositionStart"
        @compositionend="onCompositionEnd"
        @keydown="onKeydown"
      />
      <div class="composer-foot">
        <span class="composer-hint">
          <template v-if="store.loading">
            {{ store.activeLabel || '研究中…' }}（{{ (store.elapsedMs / 1000).toFixed(1) }}s）
          </template>
          <template v-else>Enter 发送 · Shift+Enter 换行</template>
        </span>
        <button class="send-btn" :disabled="store.loading || !text.trim()" aria-label="发送" @click="send">
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z" />
          </svg>
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.send-btn:disabled {
  background: var(--line);
  color: var(--ink-3);
  cursor: not-allowed;
}
</style>
