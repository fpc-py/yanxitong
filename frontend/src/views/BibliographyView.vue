<script setup lang="ts">
// 参考文献：按样式生成格式化参考文献列表
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import ResultCard from '@/components/ResultCard.vue'
import api from '@/api/client'
import { useSessionStore } from '@/stores/session'

const store = useSessionStore()
const router = useRouter()

const styles = [
  { value: 'gbt7714', label: 'GB/T 7714', desc: '中国国家标准 · 顺序编码制' },
  { value: 'apa', label: 'APA 7th', desc: '美国心理学会 · 作者-年份制' },
  { value: 'mla', label: 'MLA 9th', desc: '现代语言协会 · 作者-页码制' },
]

const style = ref('gbt7714')
const bibliography = ref('')
const count = ref(0)
const elapsed = ref<number | null>(null)

const current = computed(() => styles.find((s) => s.value === style.value) as (typeof styles)[number])

const lineCount = computed(() => (bibliography.value ? bibliography.value.split('\n').filter((l) => l.trim()).length : 0))

async function run(): Promise<void> {
  if (!store.sessionId || store.loading) return
  const res = await store.runTask(`参考文献 · ${current.value.label}`, () =>
    api.bibliography(store.sessionId as string, style.value),
  )
  if (!res) {
    if (store.error) ElMessage.error(store.error)
    return
  }
  bibliography.value = res.bibliography
  count.value = res.count
  elapsed.value = store.lastLatencyMs
}

async function copy(): Promise<void> {
  if (!bibliography.value) return
  try {
    await navigator.clipboard.writeText(bibliography.value)
    ElMessage.success('参考文献已复制到剪贴板')
  } catch {
    ElMessage.error('复制失败，请手动选择文本')
  }
}
</script>

<template>
  <div class="page">
    <header class="page-head">
      <div>
        <span class="eyebrow page-kicker">BIBLIOGRAPHY / FORMATTING</span>
        <h1 class="page-title">参考文献</h1>
        <p class="page-desc">
          按选定样式将本会话检索到的文献格式化为标准参考文献列表，可直接复制粘贴到论文中。
        </p>
      </div>
      <div class="page-actions">
        <el-button size="small" :disabled="!store.hasSession" @click="run">生成</el-button>
      </div>
    </header>

    <div v-if="!store.hasSession" class="empty big-empty">
      <span class="empty-icon mono">◌</span>
      <span>生成参考文献需要先建立会话</span>
      <el-button type="primary" size="small" @click="router.push('/chat')">前往建立会话</el-button>
    </div>

    <div v-else class="stack stagger">
      <div class="grid grid-3">
        <button
          v-for="s in styles"
          :key="s.value"
          class="style-card"
          :class="{ 'is-active': style === s.value }"
          @click="style = s.value"
        >
          <span class="st-label mono">{{ s.value }}</span>
          <span class="st-name">{{ s.label }}</span>
          <span class="st-desc">{{ s.desc }}</span>
        </button>
      </div>

      <div class="grid grid-3">
        <div class="stat">
          <span class="stat-label">文献数量</span>
          <span class="stat-value">{{ count || store.papersCount }}</span>
          <span class="stat-hint">返回条目数</span>
        </div>
        <div class="stat">
          <span class="stat-label">输出行数</span>
          <span class="stat-value">{{ lineCount }}</span>
          <span class="stat-hint">非空行</span>
        </div>
        <div class="stat">
          <span class="stat-label">当前样式</span>
          <span class="stat-value" style="font-size: 19px">{{ current.label }}</span>
          <span class="stat-hint">{{ current.desc }}</span>
        </div>
      </div>

      <ResultCard
        title="参考文献列表"
        eyebrow="BIBLIOGRAPHY OUTPUT"
        :loading="store.loading"
        loading-text="正在格式化文献…"
        :error="store.error"
        :elapsed-ms="elapsed"
      >
        <template #actions>
          <el-button v-if="bibliography" size="small" @click="copy">复制</el-button>
        </template>
        <pre v-if="bibliography" class="plain-block">{{ bibliography }}</pre>
        <div v-else class="empty">
          <span class="empty-icon mono">∅</span>
          <span>尚未生成参考文献</span>
          <span class="hint-line">若文献数量为 0，说明当前会话尚未检索到文献，请先在「会话问答」中提问。</span>
        </div>
      </ResultCard>
    </div>
  </div>
</template>

<style scoped>
.big-empty {
  padding: 64px 20px;
  gap: 14px;
}

.style-card {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 3px;
  padding: 13px 15px;
  border: 1px solid var(--hair);
  border-radius: var(--radius);
  background: rgba(20, 27, 38, 0.5);
  cursor: pointer;
  text-align: left;
  transition: all 0.2s var(--ease);
}

.style-card:hover {
  border-color: var(--hair-strong);
  transform: translateY(-2px);
}

.style-card.is-active {
  border-color: var(--accent-line);
  background: var(--accent-soft);
  box-shadow: inset 0 0 0 1px var(--accent-glow);
}

.st-label {
  font-size: 10px;
  letter-spacing: 0.12em;
  color: var(--text-3);
  text-transform: uppercase;
}

.style-card.is-active .st-label {
  color: var(--accent);
}

.st-name {
  font-family: var(--font-display);
  font-size: 16px;
  color: var(--text-1);
}

.st-desc {
  font-size: 11.5px;
  color: var(--text-2);
}
</style>
