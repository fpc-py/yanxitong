<script setup lang="ts">
// 文献矩阵：N×M 六字段对比表（研究问题/方法含基线/数据集含样本量/结果/局限性/相关度）
// + 证据 popover（字段级 span 溯源）+ CSV 导出（Excel 中文不乱码）
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api, { extractErrorMessage } from '@/api/client'
import { useSessionStore } from '@/stores/session'
import { downloadCsv } from '@/utils/csv'
import type { MatrixRow } from '@/api/types'

const store = useSessionStore()
const exporting = ref(false)

const rows = computed(() => store.matrixRows)

const SOURCE_LABEL: Record<string, string> = {
  arxiv: 'arXiv',
  semantic_scholar: 'S2',
  openalex: 'OpenAlex',
  knowledge: 'KB',
}

function sourceLabel(s: string): string {
  return SOURCE_LABEL[s] ?? s
}

/** 相关度 1-6 → el-tag 色阶（6 最高） */
function relTagType(score: number): 'success' | 'warning' | 'info' {
  if (score >= 5) return 'success'
  if (score >= 3) return 'warning'
  return 'info'
}

function joinMethods(m: MatrixRow['methods']): string {
  return (m ?? [])
    .filter((x) => x?.name)
    .map((x) => (x.baseline ? `${x.name} (基线: ${x.baseline})` : x.name))
    .join('；')
}

function joinDatasets(d: MatrixRow['datasets']): string {
  return (d ?? [])
    .filter((x) => x?.name)
    .map((x) => (x.sample_size ? `${x.name} (n=${x.sample_size})` : x.name))
    .join('；')
}

function joinResults(r: MatrixRow['results']): string {
  return (r ?? [])
    .map((x) => `${x.metric ?? ''}: ${x.value ?? ''}${x.unit ?? ''}`.trim().replace(/^:\s*/, ''))
    .filter(Boolean)
    .join('；')
}

const FIELD_LABEL: Record<string, string> = {
  research_problem: '研究问题',
  methods: '方法',
  datasets: '数据集',
  results: '结果',
  limitations: '局限性',
}

interface EvidenceLine {
  field: string
  quote: string
  page: string
}

function evidenceLines(row: MatrixRow): EvidenceLine[] {
  const out: EvidenceLine[] = []
  Object.entries(row.evidence ?? {}).forEach(([field, entries]) => {
    ;(entries ?? []).forEach((e) => {
      if (e?.quote) {
        out.push({
          field: FIELD_LABEL[field] ?? field,
          quote: e.quote,
          page: e.page === null || e.page === undefined || e.page === '' ? '' : `p${e.page}`,
        })
      }
    })
  })
  return out
}

async function exportCsv(): Promise<void> {
  if (!store.sessionId || exporting.value) return
  exporting.value = true
  try {
    const blob = await api.exportMatrixCsv(store.sessionId)
    await downloadCsv(blob, `文献矩阵_${store.sessionId.slice(0, 8)}.csv`)
  } catch (e) {
    ElMessage.error(extractErrorMessage(e))
  } finally {
    exporting.value = false
  }
}
</script>

<template>
  <section class="panel">
    <header class="panel-head">
      <span class="panel-title serif">文献矩阵</span>
      <span class="panel-sub">{{ rows.length }} PAPERS × 6 FIELDS</span>
      <el-button class="export-btn" size="small" :disabled="!rows.length" :loading="exporting" @click="exportCsv">
        导出 CSV
      </el-button>
    </header>

    <div class="panel-body matrix-body">
      <el-table v-if="rows.length" :data="rows" size="small" class="matrix-table" max-height="560" border>
        <el-table-column prop="index" label="#" width="52" align="center" />
        <el-table-column label="标题" min-width="240">
          <template #default="{ row }">
            <a v-if="row.url" class="mx-title" :href="row.url" target="_blank" rel="noopener noreferrer">{{ row.title }}</a>
            <span v-else class="mx-title">{{ row.title }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="year" label="年" width="64" align="center" />
        <el-table-column label="来源" width="120">
          <template #default="{ row }">
            <el-tag v-for="s in row.sources" :key="s" size="small" effect="plain" class="src-tag">{{ sourceLabel(s) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="相关度" width="86" align="center">
          <template #default="{ row }">
            <el-popover placement="top" width="300" trigger="hover" :content="row.relevance?.reason || '未给出理由'" :disabled="!row.relevance?.reason">
              <template #reference>
                <el-tag :type="relTagType(row.relevance?.score ?? 0)" size="small" class="rel-tag">
                  {{ row.relevance?.score ?? 0 }}/6
                </el-tag>
              </template>
            </el-popover>
          </template>
        </el-table-column>
        <el-table-column label="研究问题" min-width="200" show-overflow-tooltip>
          <template #default="{ row }">{{ row.research_problem || '—' }}</template>
        </el-table-column>
        <el-table-column label="方法（基线）" min-width="200" show-overflow-tooltip>
          <template #default="{ row }">{{ joinMethods(row.methods) || '—' }}</template>
        </el-table-column>
        <el-table-column label="数据集（样本量）" min-width="180" show-overflow-tooltip>
          <template #default="{ row }">{{ joinDatasets(row.datasets) || '—' }}</template>
        </el-table-column>
        <el-table-column label="关键指标" min-width="180" show-overflow-tooltip>
          <template #default="{ row }">{{ joinResults(row.results) || '—' }}</template>
        </el-table-column>
        <el-table-column label="局限性" min-width="170" show-overflow-tooltip>
          <template #default="{ row }">{{ (row.limitations ?? []).join('；') || '—' }}</template>
        </el-table-column>
        <el-table-column label="证据" width="76" align="center">
          <template #default="{ row }">
            <el-popover v-if="evidenceLines(row).length" placement="left" width="380" trigger="click">
              <template #reference>
                <el-button size="small" text type="primary">溯源 {{ evidenceLines(row).length }}</el-button>
              </template>
              <div class="ev-pop">
                <div v-for="(e, i) in evidenceLines(row)" :key="i" class="ev-line">
                  <span class="ev-field">{{ e.field }}</span>
                  <span class="ev-quote">“{{ e.quote }}”</span>
                  <span v-if="e.page" class="ev-page mono">{{ e.page }}</span>
                </div>
              </div>
            </el-popover>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
      </el-table>

      <div v-else class="empty">
        <span class="empty-icon mono">▦</span>
        <span>矩阵为空</span>
        <span class="hint-line">完成一次提问后，后端会对论文做六字段结构化抽取并填充矩阵。</span>
      </div>
    </div>
  </section>
</template>

<style scoped>
.panel-head {
  display: flex;
  align-items: baseline;
  gap: 10px;
}

.export-btn {
  margin-left: auto;
}

.matrix-body {
  padding-top: 10px;
}

.mx-title {
  color: var(--text-1);
  text-decoration: none;
}

a.mx-title:hover {
  color: var(--accent);
}

.src-tag,
.rel-tag {
  margin-right: 4px;
}

.rel-tag {
  cursor: default;
}

.ev-pop {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 320px;
  overflow-y: auto;
}

.ev-line {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: baseline;
  font-size: 12px;
  line-height: 1.6;
}

.ev-field {
  flex: 0 0 auto;
  font-size: 10.5px;
  padding: 1px 6px;
  border-radius: 3px;
  background: var(--ink-750);
  color: var(--text-2);
}

.ev-quote {
  color: var(--text-1);
}

.ev-page {
  font-size: 10.5px;
  color: var(--text-3);
}

.muted {
  color: var(--text-3);
}
</style>
