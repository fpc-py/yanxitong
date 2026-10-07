<script setup lang="ts">
// 证据链面板（规格⑥）：从节点出发，沿带原文引文的边追溯到论文与出处
// 数据源 /api/kg/session/{sid}/subgraph（节点候选）+ /api/kg/evidence-path（追溯链）
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api, { extractErrorMessage } from '@/api/client'
import type { KgEvidenceRow, KgNode } from '@/api/types'
import { useSessionStore } from '@/stores/session'

const store = useSessionStore()

const target = ref('')
const rows = ref<KgEvidenceRow[]>([])
const loading = ref(false)

/** 可追溯目标：本会话子图节点（论文优先，实体在后） */
const candidates = ref<KgNode[]>([])

const paperOptions = computed(() => candidates.value.filter((n) => n.kind === 'paper' || n.type === 'Paper'))
const entityOptions = computed(() => candidates.value.filter((n) => !(n.kind === 'paper' || n.type === 'Paper')))

async function loadCandidates(): Promise<void> {
  if (!store.sessionId) return
  try {
    const sub = await api.getKgSessionSubgraph(store.sessionId)
    candidates.value = sub.nodes ?? []
    if (!target.value && candidates.value.length) {
      target.value = candidates.value[0].id
      await loadPath()
    }
  } catch {
    candidates.value = []
  }
}

async function loadPath(): Promise<void> {
  const t = target.value.trim()
  if (!t) {
    ElMessage.info('请选择或输入要追溯的节点')
    return
  }
  loading.value = true
  try {
    const res = await api.getKgEvidencePath(t, 50)
    rows.value = res.path
  } catch (err) {
    ElMessage.error(extractErrorMessage(err))
  } finally {
    loading.value = false
  }
}

function nodeLabel(n: KgNode): string {
  const prefix = n.type === 'Paper' || n.kind === 'paper' ? '[论文]' : `[${n.type || '实体'}]`
  return `${prefix} ${n.name}`
}

onMounted(() => {
  if (store.hasSession) void loadCandidates()
})

defineExpose({ loadCandidates })
</script>

<template>
  <div class="evidence-chain stack">
    <section class="panel">
      <header class="panel-head">
        <span class="panel-title serif">证据链追溯</span>
        <span class="panel-sub">节点 → 带原文引文的边 → 论文出处</span>
      </header>
      <div class="panel-body">
        <div class="ec-search">
          <el-select
            v-model="target"
            filterable
            allow-create
            default-first-option
            placeholder="选择本会话节点，或输入 paper_id / entity_id"
            class="ec-select"
          >
            <el-option-group v-if="paperOptions.length" label="论文">
              <el-option v-for="n in paperOptions" :key="n.id" :label="nodeLabel(n)" :value="n.id" />
            </el-option-group>
            <el-option-group v-if="entityOptions.length" label="实体">
              <el-option v-for="n in entityOptions" :key="n.id" :label="nodeLabel(n)" :value="n.id" />
            </el-option-group>
          </el-select>
          <el-button type="primary" :loading="loading" @click="loadPath">追溯</el-button>
        </div>

        <div v-if="rows.length" class="ec-list">
          <article v-for="(r, i) in rows" :key="i" class="ec-item">
            <div class="ec-head">
              <span class="ec-paper">
                {{ r.paper_title || r.paper_id }}
                <span v-if="r.arxiv_id" class="ec-arxiv mono">arXiv:{{ r.arxiv_id }}</span>
              </span>
              <span class="ec-rel mono">--{{ r.relation }}--&gt;</span>
              <span class="ec-entity">
                <span class="chip">{{ r.entity_type || 'Entity' }}</span>
                {{ r.entity_name }}
              </span>
            </div>
            <p v-if="r.evidence" class="ec-evidence">“{{ r.evidence }}”</p>
            <div class="ec-meta mono">
              <template v-if="r.evidence_source">来源 {{ r.evidence_source }}</template>
              <template v-if="r.value"> · 值 {{ r.value }}</template>
              <template v-if="r.sessions?.length"> · {{ r.sessions.length }} 个会话贡献</template>
            </div>
          </article>
        </div>
        <div v-else-if="!loading" class="empty">
          <span class="empty-icon mono">◌</span>
          <span>暂无证据链</span>
          <span class="hint-line">
            选择本会话的论文或实体节点进行追溯；每条边携带写入时经 verify_quote 校验的原文引文。
          </span>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.ec-search {
  display: flex;
  gap: 8px;
  margin-bottom: 14px;
}

.ec-select {
  flex: 1 1 auto;
}

.ec-list {
  display: flex;
  flex-direction: column;
  gap: 9px;
}

.ec-item {
  padding: 13px 15px;
  border: 1px solid var(--hair-soft);
  border-radius: var(--radius);
  background: rgba(255, 255, 255, 0.9);
  transition: border-color 0.2s var(--ease);
}

.ec-item:hover {
  border-color: var(--accent-line);
}

.ec-head {
  display: grid;
  grid-template-columns: 1fr auto 1fr;
  gap: 12px;
  align-items: baseline;
}

.ec-paper {
  font-size: 13px;
  color: var(--text-1);
  line-height: 1.55;
}

.ec-arxiv {
  font-size: 10px;
  color: var(--text-3);
  margin-left: 6px;
}

.ec-rel {
  font-size: 10.5px;
  color: var(--accent);
  letter-spacing: 0.04em;
  flex: 0 0 auto;
}

.ec-entity {
  font-size: 13px;
  color: var(--text-1);
  display: flex;
  gap: 7px;
  align-items: baseline;
}

.ec-evidence {
  margin: 10px 0 0;
  padding: 9px 12px;
  font-size: 12px;
  line-height: 1.65;
  color: var(--text-2);
  background: var(--ink-750);
  border-radius: 6px;
}

.ec-meta {
  margin-top: 8px;
  font-size: 10px;
  color: var(--text-3);
  letter-spacing: 0.03em;
}
</style>
