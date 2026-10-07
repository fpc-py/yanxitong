<script setup lang="ts">
// 数据分析 Dossier：画像① / 规划②③ / 知识引用 / 校验⑦ / 运行与产出包⑨
import { computed } from 'vue'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import type {
  AnalysisRun,
  DataProfile,
  KnowledgeRecall,
  TaskPlan,
  ValidationReport,
} from '@/api/types'

const props = defineProps<{
  sessionId: string
  profile?: DataProfile | null
  plan?: TaskPlan | null
  recall?: KnowledgeRecall | null
  validation?: ValidationReport | null
  run?: AnalysisRun | null
}>()

const hasAnything = computed(
  () => !!(props.profile || props.plan || props.recall || props.validation || props.run),
)

const warnings = computed(() => {
  const out: string[] = []
  if (props.profile?.degraded) out.push(`数据画像降级：${props.profile.error || '沙箱不可用'}`)
  if (props.plan?.fallback) out.push('任务规划降级：LLM 规划失败，使用启发式单步计划')
  if (props.recall?.degraded) out.push('知识召回降级：向量检索不可用，改用关键词扫描种子库')
  if (props.run?.degraded) out.push(`执行降级（${props.run.degrade_reason || '代码执行失败'}）：已运行静态兜底脚本，结论有限`)
  if (!props.validation && props.run) out.push('LLM 复核未执行（deep_verify 关闭或调用失败）')
  return out
})

const profileIssues = computed(() => props.profile?.quality?.issues ?? [])

const recallGroups = computed(() => {
  const libs = props.recall?.libraries ?? {}
  return Object.entries(libs)
    .map(([key, value]) => ({ key, label: value?.label || key, chunks: value?.chunks ?? [] }))
    .filter((g) => g.chunks.length)
})

const stages = computed(() => props.run?.stages ?? [])
const files = computed(() => props.run?.files ?? props.run?.artifacts ?? [])

function statusIcon(status?: string): string {
  if (status === 'ok') return '✓'
  if (status === 'warn') return '⚠'
  if (status === 'fail') return '✗'
  return '·'
}

function formatBytes(size: number): string {
  if (size >= 1024 * 1024) return `${(size / 1024 / 1024).toFixed(2)} MB`
  if (size >= 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${size} B`
}

function fileUrl(name: string): string {
  return api.analysisFileUrl(props.sessionId, props.run?.run_id ?? '', name)
}

async function downloadPackage(): Promise<void> {
  if (!props.run?.run_id) return
  try {
    const blob = await api.downloadAnalysisPackage(props.sessionId, props.run.run_id)
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `analysis_${props.run.run_id}.zip`
    a.click()
    URL.revokeObjectURL(url)
  } catch {
    ElMessage.error('产出包下载失败，请稍后重试')
  }
}
</script>

<template>
  <section v-if="hasAnything" class="panel dossier">
    <header class="panel-head">
      <span class="panel-title serif">分析档案 Dossier</span>
      <span class="panel-sub">PROFILE · PLAN · RECALL · VERIFY · PACKAGE</span>
    </header>
    <div class="panel-body stack-inner">
      <!-- 降级警示 -->
      <div v-if="warnings.length" class="warn-box">
        <span class="warn-title">⚠ 降级提示</span>
        <ul>
          <li v-for="(w, i) in warnings" :key="i">{{ w }}</li>
        </ul>
      </div>

      <!-- ① 数据画像 -->
      <div v-if="profile" class="zone">
        <div class="zone-head">
          <span class="zone-title">① 数据画像</span>
          <span class="chip">{{ profile.format || '未知格式' }}</span>
          <span v-if="profile.rows != null" class="chip mono">{{ profile.rows }} 行 × {{ profile.cols }} 列</span>
          <span v-if="profile.sandbox" class="chip mono">{{ profile.sandbox }}</span>
        </div>
        <p v-if="profile.degraded" class="hint-line">{{ profile.error || '画像不可用' }}</p>
        <template v-else>
          <table v-if="profile.columns?.length" class="mini-table">
            <thead>
              <tr>
                <th>列名</th>
                <th>类型</th>
                <th>缺失</th>
                <th>唯一值</th>
                <th>范围 / 示例</th>
                <th>异常值</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="col in profile.columns" :key="col.name">
                <td class="mono">{{ col.name }}</td>
                <td class="mono">{{ col.dtype }}</td>
                <td>{{ col.missing_pct }}%</td>
                <td>{{ col.unique }}</td>
                <td class="cell-sample">
                  <span v-if="col.min != null && col.max != null" class="mono">[{{ col.min }}, {{ col.max }}]</span>
                  <span v-else>{{ col.sample ?? '' }}</span>
                </td>
                <td>{{ col.outliers_iqr ?? '—' }}</td>
              </tr>
            </tbody>
          </table>
          <ul v-if="profileIssues.length" class="issue-list">
            <li v-for="(issue, i) in profileIssues" :key="i">
              <span class="chip tiny">{{ issue.kind }}</span>
              <span v-if="issue.column" class="mono">{{ issue.column }}</span>
              <span class="issue-text">{{ issue.detail }}</span>
            </li>
          </ul>
        </template>
      </div>

      <!-- ② 任务规划 -->
      <div v-if="plan" class="zone">
        <div class="zone-head">
          <span class="zone-title">② 任务规划</span>
          <span v-if="plan.fallback" class="chip warn-chip">启发式兜底</span>
        </div>
        <p v-if="plan.intent_summary" class="plan-summary">{{ plan.intent_summary }}</p>
        <ol v-if="plan.steps?.length" class="step-list">
          <li v-for="(step, i) in plan.steps" :key="i">
            <span class="chip tiny">{{ step.type }}</span>
            <span class="step-desc">{{ step.description }}</span>
            <span v-if="step.output" class="step-out mono">→ {{ step.output }}</span>
          </li>
        </ol>
        <div v-if="plan.methods?.length" class="sub-zone">
          <span class="sub-title">推荐统计方法</span>
          <div v-for="(m, i) in plan.methods" :key="i" class="method-row">
            <span class="method-name">{{ m.name }}</span>
            <span class="method-src mono">{{ m.source || '' }}</span>
            <p class="method-why">{{ m.why }}</p>
            <p v-if="m.assumptions?.length" class="method-assume">前提：{{ m.assumptions.join('；') }}</p>
          </div>
        </div>
        <div v-if="plan.templates?.length" class="sub-zone">
          <span class="sub-title">绘图模板</span>
          <div v-for="(t, i) in plan.templates" :key="i" class="method-row">
            <span class="method-name">{{ t.name }}</span>
            <span class="method-src mono">{{ t.source || '' }}</span>
            <p v-if="t.when" class="method-why">{{ t.when }}<span v-if="t.params">｜{{ t.params }}</span></p>
          </div>
        </div>
        <div v-if="plan.journal_rules?.length" class="sub-zone">
          <span class="sub-title">期刊规范</span>
          <ul class="rule-list">
            <li v-for="(r, i) in plan.journal_rules" :key="i">{{ r }}</li>
          </ul>
        </div>
      </div>

      <!-- ③ 知识引用 -->
      <div v-if="recallGroups.length" class="zone">
        <div class="zone-head">
          <span class="zone-title">③ 知识引用</span>
          <span class="chip mono">{{ recall?.sources ?? 0 }} 条</span>
          <span v-if="recall?.degraded" class="chip warn-chip">关键词召回</span>
        </div>
        <div v-for="group in recallGroups" :key="group.key" class="sub-zone">
          <span class="sub-title">{{ group.label }}</span>
          <div v-for="(c, i) in group.chunks" :key="i" class="chunk-row">
            <div class="chunk-head">
              <span class="chunk-section">{{ c.section }}</span>
              <span class="chunk-source mono">{{ c.source }}</span>
              <span class="chip tiny mono">{{ c.similarity.toFixed(2) }}</span>
            </div>
            <p class="chunk-text">{{ c.text }}</p>
          </div>
        </div>
      </div>

      <!-- ⑦ 结果校验 -->
      <div v-if="validation" class="zone">
        <div class="zone-head">
          <span class="zone-title">⑦ 结果校验</span>
          <span class="chip" :class="validation.overall === 'pass' ? '' : validation.overall === 'fail' ? 'warn-chip' : 'chip'">
            {{ validation.overall === 'pass' ? '通过' : validation.overall === 'fail' ? '不通过' : '注意' }}
          </span>
        </div>
        <ul class="check-list">
          <li v-for="(c, i) in validation.deterministic?.checks ?? []" :key="i" :class="`st-${c.status}`">
            <span class="check-icon">{{ statusIcon(c.status) }}</span>
            <span class="check-name">{{ c.check }}</span>
            <span class="check-detail">{{ c.detail }}</span>
            <span v-if="c.suggestion" class="check-suggest">建议：{{ c.suggestion }}</span>
          </li>
        </ul>
        <div v-if="validation.llm" class="sub-zone">
          <span class="sub-title">LLM 复核（假设适用性 / 叙述一致性）</span>
          <ul class="check-list">
            <li
              v-for="(a, i) in validation.llm.assumptions ?? []"
              :key="`a${i}`"
              :class="a.status === 'ok' ? 'st-ok' : a.status === 'violated' ? 'st-fail' : 'st-warn'"
            >
              <span class="check-icon">{{ statusIcon(a.status === 'ok' ? 'ok' : a.status === 'violated' ? 'fail' : 'warn') }}</span>
              <span class="check-name">{{ a.test }} · {{ a.assumption }}</span>
              <span class="check-detail">{{ a.note }}</span>
            </li>
            <li
              v-for="(n, i) in validation.llm.narrative ?? []"
              :key="`n${i}`"
              :class="n.status === 'consistent' ? 'st-ok' : n.status === 'inconsistent' ? 'st-fail' : 'st-warn'"
            >
              <span class="check-icon">{{ statusIcon(n.status === 'consistent' ? 'ok' : n.status === 'inconsistent' ? 'fail' : 'warn') }}</span>
              <span class="check-name">叙述</span>
              <span class="check-detail">{{ n.claim }}<span v-if="n.note"> · {{ n.note }}</span></span>
            </li>
          </ul>
        </div>
        <ul v-if="validation.revision_suggestions?.length" class="rule-list">
          <li v-for="(s, i) in validation.revision_suggestions" :key="i">改进建议：{{ s }}</li>
        </ul>
      </div>

      <!-- ⑨ 运行与产出包 -->
      <div v-if="run" class="zone">
        <div class="zone-head">
          <span class="zone-title">⑨ 运行与产出包</span>
          <span class="chip mono">RUN {{ run.run_id }}</span>
          <span class="chip mono">{{ run.attempts }} 次执行</span>
          <el-button size="small" @click="downloadPackage">下载产出包 zip</el-button>
        </div>
        <div class="stage-row">
          <span v-for="s in stages" :key="s.stage" class="chip tiny" :class="s.ok ? '' : 'warn-chip'">
            {{ s.stage }}<span v-if="s.ms != null" class="mono"> {{ Math.round(s.ms) }}ms</span>
          </span>
        </div>
        <ul v-if="files.length" class="file-list">
          <li v-for="f in files" :key="f.name">
            <a :href="fileUrl(f.name)" target="_blank" rel="noopener" class="mono">{{ f.name }}</a>
            <span class="mono file-size">{{ formatBytes(f.size) }}</span>
          </li>
        </ul>
        <p v-else class="hint-line">本次运行未收集到文件产物</p>
      </div>
    </div>
  </section>
</template>

<style scoped>
.stack-inner {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.warn-box {
  padding: 10px 12px;
  border: 1px solid rgba(201, 151, 60, 0.45);
  border-radius: var(--radius-sm);
  background: rgba(240, 228, 66, 0.14);
}

.warn-title {
  font-size: 12px;
  color: var(--text-1);
}

.warn-box ul {
  margin: 6px 0 0;
  padding-left: 16px;
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.warn-box li {
  font-size: 11.5px;
  color: var(--text-2);
  line-height: 1.6;
}

.zone {
  padding-top: 12px;
  border-top: 1px solid var(--hair-soft);
}

.zone:first-of-type {
  border-top: none;
  padding-top: 0;
}

.zone-head {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 9px;
}

.zone-title {
  font-size: 13px;
  color: var(--text-1);
}

.warn-chip {
  border-color: rgba(201, 151, 60, 0.5);
  color: #8a6a1f;
}

.mini-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 11px;
}

.mini-table th,
.mini-table td {
  padding: 5px 8px;
  border-bottom: 1px solid var(--hair-soft);
  text-align: left;
  color: var(--text-2);
  vertical-align: top;
}

.mini-table th {
  color: var(--text-3);
  font-weight: 500;
  letter-spacing: 0.04em;
}

.cell-sample {
  max-width: 220px;
  word-break: break-all;
}

.issue-list,
.rule-list,
.check-list,
.file-list {
  list-style: none;
  margin: 8px 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.issue-list li,
.rule-list li {
  display: flex;
  align-items: baseline;
  gap: 7px;
  font-size: 11.5px;
  color: var(--text-2);
  line-height: 1.6;
}

.issue-text {
  color: var(--text-3);
}

.plan-summary {
  margin: 0 0 8px;
  font-size: 12px;
  color: var(--text-2);
}

.step-list {
  margin: 0;
  padding-left: 18px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.step-list li {
  font-size: 12px;
  color: var(--text-2);
  line-height: 1.65;
}

.step-desc {
  margin-left: 6px;
}

.step-out {
  margin-left: 8px;
  font-size: 10.5px;
  color: var(--text-3);
}

.sub-zone {
  margin-top: 10px;
}

.sub-title {
  display: block;
  margin-bottom: 5px;
  font-size: 11px;
  color: var(--text-3);
  letter-spacing: 0.05em;
}

.method-row {
  padding: 7px 9px;
  margin-bottom: 6px;
  border: 1px solid var(--hair-soft);
  border-radius: var(--radius-sm);
  background: rgba(255, 255, 255, 0.85);
}

.method-name {
  font-size: 12px;
  color: var(--text-1);
}

.method-src {
  margin-left: 8px;
  font-size: 10px;
  color: var(--text-3);
}

.method-why {
  margin: 4px 0 0;
  font-size: 11.5px;
  color: var(--text-2);
  line-height: 1.6;
}

.method-assume {
  margin: 2px 0 0;
  font-size: 11px;
  color: var(--text-3);
}

.chunk-row {
  padding: 7px 9px;
  margin-bottom: 6px;
  border: 1px solid var(--hair-soft);
  border-radius: var(--radius-sm);
  background: rgba(255, 255, 255, 0.85);
}

.chunk-head {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 7px;
}

.chunk-section {
  font-size: 12px;
  color: var(--text-1);
}

.chunk-source {
  font-size: 10px;
  color: var(--text-3);
}

.chunk-text {
  margin: 4px 0 0;
  font-size: 11px;
  color: var(--text-2);
  line-height: 1.65;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.check-list li {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 6px;
  font-size: 11.5px;
  color: var(--text-2);
  line-height: 1.6;
}

.check-icon {
  width: 14px;
  text-align: center;
}

.st-ok .check-icon {
  color: #3e7d4f;
}

.st-warn .check-icon {
  color: #b58a2c;
}

.st-fail .check-icon {
  color: #b0483f;
}

.check-name {
  color: var(--text-1);
}

.check-detail {
  color: var(--text-2);
}

.check-suggest {
  color: var(--text-3);
}

.stage-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 8px;
}

.file-list li {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px;
  font-size: 11px;
}

.file-list a {
  color: var(--accent);
  text-decoration: none;
  word-break: break-all;
}

.file-list a:hover {
  text-decoration: underline;
}

.file-size {
  color: var(--text-3);
  flex-shrink: 0;
}
</style>
