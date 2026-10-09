<script setup lang="ts">
// 主区顶栏（对应 v3.0 原型）：左侧面包屑 + 右侧状态胶囊；15 秒轮询刷新
// 领域包 chip：显示当前会话绑定的包；点击弹层声明跨域参考包（读侧过滤、立即生效）
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { useRoute } from 'vue-router'
import { useSessionStore } from '@/stores/session'

const store = useSessionStore()
const route = useRoute()

onMounted(() => {
  if (store.sessionId && !store.packs.length) void store.loadPacks()
})

const VIEW_NAMES: Record<string, string> = {
  overview: '概览',
  chat: '研究对话',
  literature: '文献库',
  kg: '知识图谱',
  analyze: '数据分析',
  design: '实验设计',
  write: '论文写作',
  review: '学术审阅',
  bibliography: '参考文献',
}

const crumb = computed(() => {
  const name = VIEW_NAMES[route.path.replace(/^\//, '')] ?? '工作台'
  return name
})

const online = computed(() => !!store.health && !store.healthError)
const versionText = computed(() => (store.health ? `v${store.health.version}` : '—'))

const stateText = computed(() => {
  if (store.healthError) return '后端离线'
  if (!store.health) return '连接中'
  return '后端在线'
})

const confidencePct = computed(() => Math.round(store.averageConfidence * 100))

// ---- 领域包 popover：跨域声明（仅可勾选本身份可见的其他包） ----
const crossDraft = ref<string[]>([])
const savingCross = ref(false)

watch(
  () => store.crossKbIds,
  (ids) => {
    crossDraft.value = [...ids]
  },
  { immediate: true },
)

const crossOptions = computed(() => store.packs.filter((p) => p.kb_id !== store.activeKbId))

function toggleCross(kbId: string): void {
  crossDraft.value = crossDraft.value.includes(kbId)
    ? crossDraft.value.filter((x) => x !== kbId)
    : [...crossDraft.value, kbId]
}

async function applyCross(): Promise<void> {
  savingCross.value = true
  try {
    const ok = await store.setCrossKb(crossDraft.value)
    if (ok) {
      ElMessage.success(
        crossDraft.value.length ? `已声明 ${crossDraft.value.length} 个跨域参考包` : '已撤销跨域声明',
      )
    } else {
      ElMessage.error(store.error || '更新失败')
    }
  } finally {
    savingCross.value = false
  }
}
</script>

<template>
  <header class="topbar">
    <div class="crumb">
      研析通 · Research Copilot <span class="sep-c">/</span> <b>{{ crumb }}</b>
    </div>

    <div class="top-actions">
      <span class="pill">
        <span class="d" :class="store.healthError ? 'is-err' : online ? '' : 'is-warn'" />
        {{ stateText }} · {{ versionText }}
      </span>
      <span v-if="store.averageConfidence > 0" class="pill accent">置信度 {{ confidencePct }}%</span>
      <el-popover v-if="store.sessionId" placement="bottom-end" :width="330" trigger="click">
        <template #reference>
          <span class="pill mono kb-pill" title="领域包：图谱 / 文献库 / 先验按包隔离；跨域需显式声明">
            KG {{ store.activeKbName }}
            <template v-if="store.crossKbIds.length">· 跨域 {{ store.crossKbIds.length }}</template>
          </span>
        </template>
        <div class="kb-pop">
          <div class="kb-pop-head">
            <span class="eyebrow">当前领域包</span>
            <b>{{ store.activeKbName }}</b>
            <span class="mono kb-pop-id">{{ store.activeKbId }}</span>
          </div>
          <p class="kb-pop-hint">
            文献 / 图谱 / 先验按包隔离，同包会话跨会话沉淀。勾选其他包作为
            <b>跨域参考</b>：命中带「跨域」标记并降权排序；不声明则完全不跨包。
          </p>
          <div class="kb-pop-list">
            <el-checkbox
              v-for="p in crossOptions"
              :key="p.kb_id"
              :model-value="crossDraft.includes(p.kb_id)"
              @update:model-value="toggleCross(p.kb_id)"
            >
              {{ p.name }}
            </el-checkbox>
            <span v-if="!crossOptions.length" class="kb-pop-empty">暂无其他领域包（可在会话页新建）</span>
          </div>
          <div class="kb-pop-foot">
            <el-button size="small" type="primary" :loading="savingCross" @click="applyCross">
              应用声明
            </el-button>
          </div>
        </div>
      </el-popover>
      <span v-if="store.sessionId" class="pill mono session-pill" :title="store.sessionId">
        SESSION {{ store.sessionId }}
      </span>
    </div>
  </header>
</template>

<style scoped>
.topbar {
  position: sticky;
  top: 0;
  z-index: 10;
  background: rgba(250, 249, 245, 0.88);
  backdrop-filter: blur(10px);
  border-bottom: 1px solid var(--line-soft);
  padding: 13px 32px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.crumb {
  font-size: 12.5px;
  color: var(--ink-2);
}

.crumb b {
  color: var(--ink);
  font-weight: 500;
}

.sep-c {
  color: var(--ink-3);
  margin: 0 4px;
}

.top-actions {
  display: flex;
  gap: 8px;
  align-items: center;
}

.pill .d.is-err {
  background: var(--danger);
}

.pill .d.is-warn {
  background: var(--amber);
}

.session-pill {
  letter-spacing: 0.04em;
  max-width: 180px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.kb-pill {
  cursor: pointer;
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.kb-pill:hover {
  border-color: var(--accent-line);
  color: var(--accent);
}

.kb-pop-head {
  display: flex;
  align-items: baseline;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 6px;
}

.kb-pop-id {
  font-size: 10px;
  color: var(--text-3);
}

.kb-pop-hint {
  margin: 0 0 8px;
  font-size: 11.5px;
  line-height: 1.6;
  color: var(--text-2);
}

.kb-pop-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
  max-height: 200px;
  overflow-y: auto;
}

.kb-pop-empty {
  font-size: 11px;
  color: var(--text-3);
}

.kb-pop-foot {
  display: flex;
  justify-content: flex-end;
  margin-top: 10px;
  padding-top: 8px;
  border-top: 1px solid var(--hair-soft);
}
</style>
