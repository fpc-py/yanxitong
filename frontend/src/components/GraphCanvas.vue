<script setup lang="ts">
// 通用图谱画布：ECharts 力导向渲染 {nodes, edges}（后端 /api/kg/* 子图结构）
// 复用 KgGraph 的选项模式：类别配色 / 滚轮缩放 / 点击节点高亮邻接 + 详情面板
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts'
import type { KgEdge, KgNode } from '@/api/types'

const props = defineProps<{
  nodes: KgNode[]
  edges: KgEdge[]
  height?: string
}>()

const emit = defineEmits<{
  (e: 'select', node: KgNode | null): void
}>()

const chartEl = ref<HTMLDivElement | null>(null)
const selectedId = ref<string | null>(null)
let chart: echarts.ECharts | null = null

/** 类别配色：Paper 用主强调色，实体按类型区分（与封闭 schema 对齐） */
const CATEGORY_COLORS: Record<string, string> = {
  Paper: '#B8543A',
  Method: '#5C7A6A',
  Dataset: '#B98A33',
  Metric: '#5A6E8C',
  Model: '#7A5C8C',
  ResearchProblem: '#3F7E7A',
  Finding: '#8C6B4A',
  Author: '#8A8578',
  Venue: '#A8A296',
  Entity: '#8A8578',
}

function colorOf(type: string): string {
  return CATEGORY_COLORS[type] || CATEGORY_COLORS.Entity
}

interface GNode {
  id: string
  name: string
  shortName: string
  category: string
  symbolSize: number
  year?: string
  arxivId?: string
  degree: number
}

interface GLink {
  source: string
  target: string
  type: string
  evidence?: string | null
}

function edgeEvidence(e: KgEdge): string | null {
  return (e.evidence as string) || (e.properties?.evidence as string) || null
}

function truncate(text: string, max = 18): string {
  const t = (text || '').replace(/\s+/g, ' ').trim()
  return t.length > max ? `${t.slice(0, max)}…` : t
}

const graphData = computed<{ nodes: GNode[]; links: GLink[] }>(() => {
  const inEdges = new Set(props.edges.map((e) => e.target))
  const outEdges = new Set(props.edges.map((e) => e.source))
  const nodes: GNode[] = props.nodes.map((n) => {
    const type = n.type || (n.kind === 'paper' ? 'Paper' : 'Entity')
    const degree = (inEdges.has(n.id) ? 1 : 0) + (outEdges.has(n.id) ? 1 : 0)
    return {
      id: n.id,
      name: n.name || n.id,
      shortName: truncate(n.name || n.id),
      category: type,
      symbolSize: type === 'Paper' ? 18 : 12 + Math.min(degree, 6) * 2.5,
      year: n.year ? String(n.year) : undefined,
      arxivId: n.arxiv_id || undefined,
      degree,
    }
  })
  const known = new Set(nodes.map((n) => n.id))
  const links: GLink[] = props.edges
    .filter((e) => known.has(e.source) && known.has(e.target))
    .map((e) => ({ source: e.source, target: e.target, type: e.type, evidence: edgeEvidence(e) }))
  return { nodes, links }
})

const isEmpty = computed(() => graphData.value.nodes.length === 0)
const categories = computed(() => [...new Set(graphData.value.nodes.map((n) => n.category))])

const selected = computed(() => {
  if (!selectedId.value) return null
  return graphData.value.nodes.find((n) => n.id === selectedId.value) ?? null
})

const selectedLinks = computed(() => {
  if (!selectedId.value) return { from: [] as GLink[], to: [] as GLink[] }
  return {
    from: graphData.value.links.filter((l) => l.target === selectedId.value),
    to: graphData.value.links.filter((l) => l.source === selectedId.value),
  }
})

function nameOf(id: string): string {
  return graphData.value.nodes.find((n) => n.id === id)?.shortName ?? id
}

function buildOption(): echarts.EChartsOption {
  const { nodes, links } = graphData.value
  return {
    backgroundColor: 'transparent',
    animationDuration: 700,
    animationEasingUpdate: 'quinticInOut',
    tooltip: {
      backgroundColor: 'rgba(255,255,255,0.97)',
      borderColor: '#E8E4D9',
      borderWidth: 1,
      padding: [8, 11],
      textStyle: { color: '#1A1814', fontSize: 12, fontFamily: 'IBM Plex Sans, sans-serif' },
      formatter: (p: any) => {
        if (p && p.dataType === 'edge') {
          const d = p.data as GLink
          return [
            '<div style="max-width:300px;white-space:normal;line-height:1.55">',
            `<b style="color:#B8543A">${d.type}</b>`,
            d.evidence ? `<br/><span style="color:#7A7468">“${d.evidence}”</span>` : '',
            '</div>',
          ].join('')
        }
        const d = p && p.data ? (p.data as GNode) : null
        if (!d) return ''
        return [
          '<div style="max-width:280px;white-space:normal;line-height:1.5">',
          `<b style="color:${colorOf(d.category)}">${d.category}</b><br/>${d.name}`,
          d.arxivId ? `<br/><span style="color:#7A7468">arXiv:${d.arxivId}</span>` : '',
          d.year ? `<br/><span style="color:#A8A296">${d.year}</span>` : '',
          '</div>',
        ].join('')
      },
    },
    legend: [
      {
        right: 8,
        top: 4,
        icon: 'circle',
        itemWidth: 8,
        itemHeight: 8,
        textStyle: { color: '#7A7468', fontSize: 11, fontFamily: 'IBM Plex Mono, monospace' },
        data: categories.value,
      },
    ],
    series: [
      {
        type: 'graph',
        layout: 'force',
        roam: true,
        draggable: true,
        cursor: 'pointer',
        top: 30,
        categories: categories.value.map((name) => ({ name, itemStyle: { color: colorOf(name) } })),
        force: {
          repulsion: 300,
          edgeLength: [70, 160],
          gravity: 0.08,
          friction: 0.15,
          layoutAnimation: true,
        },
        label: {
          show: true,
          position: 'right',
          distance: 6,
          color: '#4A453C',
          fontSize: 10.5,
          formatter: (p: any) => (p && p.data ? (p.data as GNode).shortName : ''),
        },
        itemStyle: {
          borderColor: 'rgba(255,255,255,.9)',
          borderWidth: 1.5,
          shadowBlur: 12,
          shadowColor: 'rgba(184,84,58,.15)',
        },
        lineStyle: {
          color: '#D8D2C4',
          width: 1,
          curveness: 0.12,
          opacity: 0.72,
        },
        edgeSymbol: ['none', 'arrow'],
        edgeSymbolSize: 5,
        emphasis: {
          focus: 'adjacency',
          scale: 1.1,
          label: { color: '#1A1814', fontSize: 11.5 },
          lineStyle: { width: 2, opacity: 0.95, color: '#B8543A' },
          itemStyle: { borderColor: '#B8543A', shadowBlur: 22, shadowColor: 'rgba(184,84,58,.5)' },
        },
        data: nodes,
        links,
      },
    ],
  }
}

function render(): void {
  if (!chart) return
  chart.setOption(buildOption(), true)
  chart.resize()
}

function handleClick(params: any): void {
  const id = params && params.data ? (params.data as { id?: string }).id : undefined
  if (params && params.dataType === 'node' && id) {
    selectedId.value = selectedId.value === id ? null : id
  } else {
    selectedId.value = null
  }
  emit('select', selected.value ? (props.nodes.find((n) => n.id === selectedId.value) ?? null) : null)
}

function handleResize(): void {
  chart?.resize()
}

let observer: ResizeObserver | null = null

onMounted(() => {
  if (!chartEl.value) return
  chart = echarts.init(chartEl.value, undefined, { renderer: 'canvas' })
  chart.on('click', handleClick)
  render()
  window.addEventListener('resize', handleResize)
  // 容器从隐藏（v-show 空态）转为可见 / 面板尺寸变化时重新按实际宽度布局，
  // 否则 ECharts 会停留在初始化时的 100px 兜底宽度
  observer = new ResizeObserver(() => handleResize())
  observer.observe(chartEl.value)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', handleResize)
  observer?.disconnect()
  observer = null
  chart?.dispose()
  chart = null
})

watch(graphData, async () => {
  selectedId.value = null
  emit('select', null)
  await nextTick()
  render()
})
</script>

<template>
  <div class="canvas-wrap" :style="{ minHeight: height || '480px' }">
    <div v-show="!isEmpty" ref="chartEl" class="canvas" :style="{ height: height || '480px' }" />

    <div v-if="isEmpty" class="empty canvas-empty" :style="{ minHeight: height || '480px' }">
      <span class="empty-icon mono">◌</span>
      <slot name="empty">
        <span>暂无图谱数据</span>
      </slot>
    </div>

    <transition name="panel-fade">
      <aside v-if="selected" class="canvas-detail">
        <div class="detail-head">
          <span class="chip" :style="{ borderColor: colorOf(selected.category), color: colorOf(selected.category) }">
            {{ selected.category }}
          </span>
          <button class="close" @click="selectedId = null">×</button>
        </div>
        <p class="detail-title">{{ selected.name }}</p>
        <div class="detail-meta mono">
          <template v-if="selected.arxivId">arXiv:{{ selected.arxivId }} · </template>
          <template v-if="selected.year">{{ selected.year }} · </template>
          关联 {{ selected.degree }} 条
        </div>

        <template v-if="selectedLinks.from.length">
          <div class="detail-label eyebrow">入边</div>
          <ul class="detail-list">
            <li v-for="l in selectedLinks.from.slice(0, 8)" :key="`${l.source}-${l.type}`">
              <span>{{ nameOf(l.source) }}</span>
              <span class="rel mono">--{{ l.type }}--&gt;</span>
            </li>
          </ul>
        </template>

        <template v-if="selectedLinks.to.length">
          <div class="detail-label eyebrow">出边</div>
          <ul class="detail-list">
            <li v-for="l in selectedLinks.to.slice(0, 8)" :key="`${l.target}-${l.type}`">
              <span class="rel mono">--{{ l.type }}--&gt;</span>
              <span>{{ nameOf(l.target) }}</span>
            </li>
          </ul>
        </template>
      </aside>
    </transition>
  </div>
</template>

<style scoped>
.canvas-wrap {
  position: relative;
  width: 100%;
}

.canvas {
  width: 100%;
  border-radius: var(--radius);
}

.canvas-empty {
  height: 100%;
  gap: 14px;
}

.canvas-detail {
  position: absolute;
  right: 10px;
  bottom: 10px;
  width: 272px;
  max-height: 74%;
  overflow-y: auto;
  padding: 12px 14px;
  border: 1px solid var(--hair);
  border-radius: var(--radius);
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.98), rgba(243, 241, 234, 0.98));
  box-shadow: var(--inner-glow), 0 12px 32px rgba(0, 0, 0, 0.45);
}

.detail-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}

.close {
  background: none;
  border: 0;
  color: var(--text-3);
  font-size: 17px;
  line-height: 1;
  cursor: pointer;
  padding: 0 2px;
}

.close:hover {
  color: var(--accent);
}

.detail-title {
  margin: 0 0 6px;
  font-size: 13px;
  line-height: 1.55;
  color: var(--text-1);
}

.detail-meta {
  font-size: 10.5px;
  line-height: 1.7;
  color: var(--text-2);
  letter-spacing: 0.03em;
}

.detail-label {
  margin: 12px 0 5px;
}

.detail-list {
  margin: 0;
  padding-left: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 11.5px;
  color: var(--text-2);
  line-height: 1.5;
}

.detail-list li {
  display: flex;
  gap: 5px;
  align-items: baseline;
}

.rel {
  color: var(--accent);
  font-size: 10px;
  flex: 0 0 auto;
}
</style>
