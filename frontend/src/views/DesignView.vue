<script setup lang="ts">
// 实验设计：证据驱动方案优化引擎（配置解析→证据检索→瓶颈诊断→候选→Optuna
// 多目标优化→排序→验证计划→校验→产出包→闭环反馈）
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import MarkdownView from '@/components/MarkdownView.vue'
import ResultCard from '@/components/ResultCard.vue'
import DesignerDossier from '@/components/design/DesignerDossier.vue'
import api from '@/api/client'
import type {
  DesignConfig,
  DesignDiagnosis,
  DesignEvidence,
  DesignOptimization,
  DesignRun,
  DesignValidation,
  QueryResponse,
} from '@/api/types'
import { useSessionStore } from '@/stores/session'

const store = useSessionStore()
const router = useRouter()

const query = ref('设计一套验证「图神经网络能提升分子性质预测精度」的实验方案。')
const configText = ref('')
const answer = ref('')
const confidence = ref(0)
const elapsed = ref<number | null>(null)

const engineConfig = ref<DesignConfig | null>(null)
const diagnosis = ref<DesignDiagnosis | null>(null)
const evidence = ref<DesignEvidence | null>(null)
const candidates = ref<QueryResponse['design_candidates']>(null)
const optimization = ref<DesignOptimization | null>(null)
const validation = ref<DesignValidation | null>(null)
const designRun = ref<DesignRun | null>(null)

const CONFIG_PLACEHOLDER =
  '{\n' +
  '  "task": "图像分类",\n' +
  '  "model": { "name": "resnet50", "family": "cnn", "params_m": 25 },\n' +
  '  "hyperparams": { "learning_rate": 0.01, "batch_size": 128, "epochs": 100, "optimizer": "sgd" },\n' +
  '  "data": { "name": "CIFAR-10", "n_samples": 50000, "n_classes": 10 },\n' +
  '  "resources": { "gpu_hours": 12, "memory_gb": 16 },\n' +
  '  "metrics": ["accuracy"]\n' +
  '}'

function parseConfig(): DesignConfig | null | undefined {
  const text = configText.value.trim()
  if (!text) return null
  try {
    const parsed = JSON.parse(text)
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('not an object')
    return parsed as DesignConfig
  } catch {
    ElMessage.warning('实验配置需为合法 JSON 对象；留空则由意图解析自动补齐')
    return undefined
  }
}

function applyEngine(res: QueryResponse): void {
  engineConfig.value = res.experiment_config ?? null
  diagnosis.value = res.design_diagnosis ?? null
  evidence.value = res.design_evidence ?? null
  candidates.value = res.design_candidates ?? null
  optimization.value = res.design_optimization ?? null
  validation.value = res.design_validation ?? null
  designRun.value = res.design_run ?? null
}

async function run(): Promise<void> {
  if (!store.sessionId || !query.value.trim() || store.loading) return
  const cfg = parseConfig()
  if (cfg === undefined) return
  const res = await store.runTask('实验设计', () => api.design(store.sessionId as string, query.value.trim(), cfg))
  if (!res) {
    if (store.error) ElMessage.error(store.error)
    return
  }
  answer.value = res.answer
  confidence.value = res.confidence
  elapsed.value = store.lastLatencyMs
  applyEngine(res)
  await store.refreshStatus()
}

async function copy(): Promise<void> {
  if (!answer.value) return
  try {
    await navigator.clipboard.writeText(answer.value)
    ElMessage.success('设计方案已复制到剪贴板')
  } catch {
    ElMessage.error('复制失败，请手动选择文本')
  }
}
</script>

<template>
  <div class="page">
    <header class="page-head">
      <div>
        <span class="eyebrow page-kicker">EXPERIMENT / DESIGN</span>
        <h1 class="page-title">实验设计</h1>
        <p class="page-desc">
          输入研究问题（可选带入当前实验配置），后端证据驱动引擎输出瓶颈诊断、候选方案、
          Pareto 优化与推荐配置，并打包训练脚本与复现材料。
        </p>
      </div>
      <div class="page-actions">
        <span v-if="confidence" class="chip is-accent">置信度 {{ (confidence * 100).toFixed(0) }}%</span>
      </div>
    </header>

    <div v-if="!store.hasSession" class="empty big-empty">
      <span class="empty-icon mono">◌</span>
      <span>实验设计需要先建立会话</span>
      <el-button type="primary" size="small" @click="router.push('/chat')">前往建立会话</el-button>
    </div>

    <div v-else class="stack stagger">
      <section class="panel">
        <header class="panel-head">
          <span class="panel-title serif">研究问题</span>
        </header>
        <div class="panel-body">
          <el-input v-model="query" type="textarea" :rows="4" resize="none" placeholder="描述要验证的假设或研究问题" />
          <el-collapse class="config-collapse">
            <el-collapse-item title="当前实验配置（可选，JSON：模型/超参/数据/资源 → 瓶颈诊断与优化依据）" name="cfg">
              <el-input
                v-model="configText"
                type="textarea"
                :rows="8"
                resize="none"
                :placeholder="CONFIG_PLACEHOLDER"
                class="mono-input"
              />
            </el-collapse-item>
          </el-collapse>
          <div class="run-row">
            <span class="hint-line">
              产出台账：假设/变量/统计方法 + 证据链/候选方案/Pareto 优化/分层校验/产出包/闭环反馈
            </span>
            <el-button type="primary" :loading="store.loading" :disabled="store.loading || !query.trim()" @click="run">
              {{ store.loading ? '设计中…' : '生成实验设计' }}
            </el-button>
          </div>
        </div>
      </section>

      <ResultCard
        title="设计方案"
        eyebrow="DESIGN OUTPUT"
        :loading="store.loading"
        loading-text="配置解析 → 证据检索 → 瓶颈诊断 → 候选生成 → 多目标优化 → 校验 → 报告，请稍候…"
        :error="store.error"
        :elapsed-ms="elapsed"
      >
        <template #actions>
          <el-button v-if="answer" size="small" @click="copy">复制</el-button>
        </template>
        <MarkdownView :content="answer" empty-text="尚未生成设计方案。" />
      </ResultCard>

      <DesignerDossier
        v-if="store.sessionId"
        :session-id="store.sessionId"
        :config="engineConfig"
        :diagnosis="diagnosis"
        :evidence="evidence"
        :candidates="candidates"
        :optimization="optimization"
        :validation="validation"
        :run="designRun"
      />
    </div>
  </div>
</template>

<style scoped>
.big-empty {
  padding: 64px 20px;
  gap: 14px;
}

.config-collapse {
  margin-top: 12px;
  --el-collapse-header-font-size: 12px;
}

.config-collapse :deep(.el-collapse-item__header) {
  height: auto;
  min-height: 34px;
  padding: 6px 2px;
  font-size: 12px;
  color: var(--text-2);
  border-bottom-color: var(--hair-soft);
}

.config-collapse :deep(.el-collapse-item__wrap) {
  border-bottom-color: var(--hair-soft);
}

.mono-input :deep(.el-textarea__inner) {
  font-family: var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace);
  font-size: 11.5px;
  line-height: 1.6;
}

.run-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  margin-top: 14px;
  padding-top: 13px;
  border-top: 1px solid var(--hair-soft);
}
</style>
