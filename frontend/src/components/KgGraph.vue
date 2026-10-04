<script setup lang="ts">
// 知识图谱：ECharts 力导向图，把“论文 → 结论”关系可视化
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts'
import type { Citation } from '@/api/types'

interface ClaimNode {
  text: string
  confidence: number
  citations: Citation[]
}

const props = defineProps<{
  claims: ClaimNode[]
  citations: Citation[]
}>()

const chartEl = ref<HTMLDivElement | null>(null)
const selectedId = ref<string | null>(null)
let chart: echarts.ECharts | null = null

interface GraphNode {
  id: string
  name: string
  shortName: string
  category: number
  symbolSize: number
  confidence?: number
  degree?: number
  authors?: string
  source?: string
}

interface GraphLink {
  source: string
  target: string
}

function truncate(text: string, max = 20): string {
  const t = (text || '').replace(/\s+/g, ' ').trim()
  return t.length > max ? `${t.slice(0, max)}…` : t
}

/** 由 claims + citations 构建图数据 */
const graphData = computed<{ nodes: GraphNode[]; links: GraphLink[] }>(() => {
  const nodes: GraphNode[] = []
  const paperMap = new Map<string, GraphNode>()
  const links: GraphLink[] = []
  const linkKeys = new Set<string>()

  props.claims.forEach((c, i) => {
    const id = `claim-${i}`
    nodes.push({
      id,
      name: c.text,
      shortName: truncate(c.text, 16),
      category: 0,
      symbolSize: 20 + Math.max(0, Math.min(1, c.confidence)) * 24,
      confidence: c.confidence,
      degree: c.citations.length,
    })
    c.citations.forEach((cit) => {
      const pid = `paper-${cit.title}`
      if (!paperMap.has(pid)) {
        paperMap.set(pid, {
          id: pid,
          name: cit.title,
          shortName: truncate(cit.title, 18),
          category: 1,
          symbolSize: 14,
          degree: 0,
          authors: (cit.authors || []).slice(0, 2).join('、'),
          source: [cit.source, cit.year].filter(Boolean).join(' · '),
        })
      }
      const p = paperMap.get(pid) as GraphNode
      p.degree = (p.degree ?? 0) + 1
      p.symbolSize = 14 + Math.min(p.degree, 6) * 3
      const key = `${pid}->${id}`
      if (!linkKeys.has(key)) {
        linkKeys.add(key)
        links.push({ source: pid, target: id })
      }
    })
  })

  // 未被结论引用的独立文献同样入图
  props.citations.forEach((cit) => {
    const pid = `paper-${cit.title}`
    if (!paperMap.has(pid)) {
      paperMap.set(pid, {
        id: pid,
        name: cit.title,
        shortName: truncate(cit.title, 18),
        category: 1,
        symbolSize: 14,
        degree: 0,
        authors: (cit.authors || []).slice(0, 2).join('、'),
        source: [cit.source, cit.year].filter(Boolean).join(' · '),
      })
    }
  })

  return { nodes: [...nodes, ...paperMap.values()], links }
})

const isEmpty = computed(() => graphData.value.nodes.length === 0)

const selected = computed(() => {
  if (!selectedId.value) return null
  return graphData.value.nodes.find((n) => n.id === selectedId.value) ?? null
})

const selectedLinks = computed(() => {
  if (!selectedId.value) return { from: [] as GraphNode[], to: [] as GraphNode[] }
  const map = new Map(graphData.value.nodes.map((n) => [n.id, n]))
  const from: GraphNode[] = []
  const to: GraphNode[] = []
  graphData.value.links.forEach((l) => {
    if (l.target === selectedId.value) {
      const n = map.get(l.source)
      if (n) from.push(n)
    }
    if (l.source === selectedId.value) {
      const n = map.get(l.target)
      if (n) to.push(n)
    }
  })
  return { from, to }
})

function buildOption(): echarts.EChartsOption {
  const { nodes, links } = graphData.value
  return {
    backgroundColor: 'transparent',
    animationDuration: 700,
    animationEasingUpdate: 'quinticInOut',
    tooltip: {
      backgroundColor: 'rgba(10,15,22,.96)',
      borderColor: '#243043',
      borderWidth: 1,
      padding: [8, 11],
      textStyle: { color: '#E6EDF6', fontSize: 12, fontFamily: 'IBM Plex Sans, sans-serif' },
      formatter: (p: any) => {
        const d = p && p.data ? (p.data as GraphNode) : null
        if (!d) return ''
        if (p.dataType === 'edge') return '证据引用'
        if (d.category === 0) {
          return [
            '<div style="max-width:280px;white-space:normal;line-height:1.5">',
            `<b style="color:#3DD6C4">结论</b><br/>${d.name}`,
            `<br/><span style="color:#8FA0B5">置信度 ${((d.confidence ?? 0) * 100).toFixed(0)}%　证据 ${d.degree ?? 0} 条</span>`,
            '</div>',
          ].join('')
        }
        return [
          '<div style="max-width:280px;white-space:normal;line-height:1.5">',
          `<b style="color:#E8B04B">论文</b><br/>${d.name}`,
          d.authors ? `<br/><span style="color:#8FA0B5">${d.authors}</span>` : '',
          d.source ? `<br/><span style="color:#5C6B7E">${d.source}</span>` : '',
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
        textStyle: { color: '#8FA0B5', fontSize: 11, fontFamily: 'IBM Plex Mono, monospace' },
        data: ['结论', '论文'],
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
        categories: [
          { name: '结论', itemStyle: { color: '#3DD6C4' } },
          { name: '论文', itemStyle: { color: '#E8B04B' } },
        ],
        force: {
          repulsion: 320,
          edgeLength: [80, 170],
          gravity: 0.06,
          friction: 0.14,
          layoutAnimation: true,
        },
        label: {
          show: true,
          position: 'right',
          distance: 6,
          color: '#8FA0B5',
          fontSize: 10.5,
          formatter: (p: any) => (p && p.data ? (p.data as GraphNode).shortName : ''),
        },
        itemStyle: {
          borderColor: 'rgba(11,15,20,.9)',
          borderWidth: 1.5,
          shadowBlur: 12,
          shadowColor: 'rgba(61,214,196,.25)',
        },
        lineStyle: {
          color: '#33445e',
          width: 1,
          curveness: 0.12,
          opacity: 0.55,
        },
        edgeSymbol: ['none', 'arrow'],
        edgeSymbolSize: 5,
        emphasis: {
          focus: 'adjacency',
          scale: 1.12,
          label: { color: '#E6EDF6', fontSize: 11.5 },
          lineStyle: { width: 2, opacity: 0.95, color: '#3DD6C4' },
          itemStyle: { borderColor: '#3DD6C4', shadowBlur: 22, shadowColor: 'rgba(61,214,196,.55)' },
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
}

function handleResize(): void {
  chart?.resize()
}

onMounted(() => {
  if (!chartEl.value) return
  chart = echarts.init(chartEl.value, undefined, { renderer: 'canvas' })
  chart.on('click', handleClick)
  render()
  window.addEventListener('resize', handleResize)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', handleResize)
  chart?.dispose()
  chart = null
})

watch(graphData, () => {
  selectedId.value = null
  render()
})
</script>

<template>
  <div class="kg-wrap">
    <div v-show="!isEmpty" ref="chartEl" class="kg-canvas" />

    <div v-if="isEmpty" class="empty kg-empty">
      <span class="empty-icon mono">◌</span>
      <span>暂无图谱数据</span>
      <span class="hint">先在「会话问答」中提问，产生引用链后此处将渲染「论文—结论」关系图。</span>
    </div>

    <transition name="panel-fade">
      <aside v-if="selected" class="kg-detail">
        <div class="detail-head">
          <span class="chip" :class="selected.category === 0 ? 'is-accent' : 'is-amber'">
            {{ selected.category === 0 ? '结论' : '论文' }}
          </span>
          <button class="close" @click="selectedId = null">×</button>
        </div>
        <p class="detail-title">{{ selected.name }}</p>
        <div v-if="selected.category === 0" class="detail-meta mono">
          置信度 {{ ((selected.confidence ?? 0) * 100).toFixed(0) }}% · 证据 {{ selected.degree ?? 0 }} 条
        </div>
        <div v-else class="detail-meta mono">
          {{ selected.authors || '作者未标注' }}<br />
          {{ selected.source || '来源未标注' }}
        </div>

        <template v-if="selectedLinks.from.length">
          <div class="detail-label eyebrow">支撑证据</div>
          <ul class="detail-list">
            <li v-for="n in selectedLinks.from" :key="n.id">{{ n.shortName }}</li>
          </ul>
        </template>

        <template v-if="selectedLinks.to.length">
          <div class="detail-label eyebrow">被引结论</div>
          <ul class="detail-list">
            <li v-for="n in selectedLinks.to" :key="n.id">{{ n.shortName }}</li>
          </ul>
        </template>
      </aside>
    </transition>
  </div>
</template>

<style scoped>
.kg-wrap {
  position: relative;
  width: 100%;
  height: 100%;
  min-height: 420px;
}

.kg-canvas {
  width: 100%;
  height: 100%;
  min-height: 420px;
  border-radius: var(--radius);
}

.kg-empty {
  height: 100%;
  min-height: 420px;
}

.hint {
  font-size: 12px;
  color: var(--text-3);
  max-width: 340px;
}

.kg-detail {
  position: absolute;
  right: 10px;
  bottom: 10px;
  width: 268px;
  max-height: 72%;
  overflow-y: auto;
  padding: 12px 14px;
  border: 1px solid var(--hair);
  border-radius: var(--radius);
  background: linear-gradient(180deg, rgba(20, 27, 38, 0.97), rgba(11, 15, 20, 0.97));
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
  padding-left: 15px;
  font-size: 12px;
  color: var(--text-2);
  line-height: 1.6;
}

.detail-list li::marker {
  color: var(--accent);
}
</style>
