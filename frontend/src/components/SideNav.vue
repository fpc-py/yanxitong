<script setup lang="ts">
// 左侧窄导航：图标 + 文字，按科研链路分组
import { computed } from 'vue'
import { useRoute } from 'vue-router'

interface NavItem {
  path: string
  label: string
  hint: string
  icon: string
}

interface NavGroup {
  name: string
  items: NavItem[]
}

const groups: NavGroup[] = [
  {
    name: '工作台',
    items: [
      { path: '/overview', label: '概览', hint: 'OVERVIEW', icon: 'M3 3h7v7H3zM14 3h7v7h-7zM14 14h7v7h-7zM3 14h7v7H3z' },
      {
        path: '/chat',
        label: '会话问答',
        hint: 'DIALOGUE',
        icon: 'M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z',
      },
    ],
  },
  {
    name: '文献情报',
    items: [
      {
        path: '/literature',
        label: '文献与引用',
        hint: 'CITATIONS',
        icon: 'M4 19.5A2.5 2.5 0 0 1 6.5 17H20M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z',
      },
      {
        path: '/kg',
        label: '知识图谱',
        hint: 'GRAPH',
        icon: 'M18 8a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM6 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM18 22a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM8.6 13.5l6.8 4M15.4 6.5l-6.8 4',
      },
    ],
  },
  {
    name: '研究执行',
    items: [
      { path: '/analyze', label: '数据分析', hint: 'ANALYZE', icon: 'M12 20V10M18 20V4M6 20v-4M3 20h18' },
      {
        path: '/design',
        label: '实验设计',
        hint: 'DESIGN',
        icon: 'M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6',
      },
    ],
  },
  {
    name: '成果产出',
    items: [
      {
        path: '/write',
        label: '论文写作',
        hint: 'WRITE',
        icon: 'M12 20h9M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4 12.5-12.5z',
      },
      {
        path: '/review',
        label: '学术审阅',
        hint: 'REVIEW',
        icon: 'M9 11l3 3L22 4M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11',
      },
      {
        path: '/bibliography',
        label: '参考文献',
        hint: 'BIBLIO',
        icon: 'M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01',
      },
    ],
  },
]

const route = useRoute()
const activePath = computed(() => route.path)
</script>

<template>
  <nav class="side-nav">
    <div class="nav-scroll">
      <div v-for="(group, gi) in groups" :key="group.name" class="nav-group" :style="{ animationDelay: `${gi * 0.06}s` }">
        <div class="group-label eyebrow">{{ group.name }}</div>
        <router-link
          v-for="item in group.items"
          :key="item.path"
          :to="item.path"
          class="nav-item"
          :class="{ 'is-active': activePath === item.path }"
        >
          <span class="nav-rail" />
          <svg class="nav-icon" viewBox="0 0 24 24" aria-hidden="true">
            <path :d="item.icon" />
          </svg>
          <span class="nav-text">
            <span class="nav-label">{{ item.label }}</span>
            <span class="nav-hint mono">{{ item.hint }}</span>
          </span>
        </router-link>
      </div>
    </div>

    <div class="nav-foot">
      <div class="foot-line mono">KG × RAG · MULTI-AGENT</div>
      <div class="foot-line mono dim">5-LINK CHAIN / find→read→compute→write→review</div>
    </div>
  </nav>
</template>

<style scoped>
.side-nav {
  flex: 0 0 var(--nav-w);
  width: var(--nav-w);
  display: flex;
  flex-direction: column;
  border-right: 1px solid var(--hair);
  background: linear-gradient(180deg, rgba(14, 20, 32, 0.72), rgba(11, 15, 20, 0.55));
  min-height: 0;
}

.nav-scroll {
  flex: 1 1 auto;
  overflow-y: auto;
  padding: 18px 12px;
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.nav-group {
  display: flex;
  flex-direction: column;
  gap: 3px;
  animation: fadeIn 0.5s var(--ease) both;
}

.group-label {
  padding: 0 8px 7px;
}

.nav-item {
  position: relative;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 10px 8px 14px;
  border-radius: var(--radius-sm);
  color: var(--text-2);
  text-decoration: none;
  transition: background 0.2s var(--ease), color 0.2s var(--ease);
}

.nav-item:hover {
  background: rgba(36, 48, 67, 0.34);
  color: var(--text-1);
}

.nav-item.is-active {
  background: linear-gradient(90deg, var(--accent-soft), rgba(61, 214, 196, 0.02));
  color: var(--accent);
}

.nav-rail {
  position: absolute;
  left: 4px;
  top: 50%;
  width: 2px;
  height: 0;
  border-radius: 2px;
  background: var(--accent);
  transform: translateY(-50%);
  transition: height 0.24s var(--ease);
}

.nav-item.is-active .nav-rail {
  height: 20px;
  box-shadow: 0 0 10px var(--accent-glow);
}

.nav-icon {
  width: 16px;
  height: 16px;
  flex: 0 0 auto;
  fill: none;
  stroke: currentColor;
  stroke-width: 1.6;
  stroke-linecap: round;
  stroke-linejoin: round;
}

.nav-text {
  display: flex;
  flex-direction: column;
  line-height: 1.2;
  min-width: 0;
}

.nav-label {
  font-size: 13px;
}

.nav-hint {
  font-size: 9px;
  letter-spacing: 0.14em;
  color: var(--text-3);
  opacity: 0.75;
}

.nav-item.is-active .nav-hint {
  color: var(--accent);
  opacity: 0.7;
}

.nav-foot {
  flex: 0 0 auto;
  padding: 12px 16px 14px;
  border-top: 1px solid var(--hair-soft);
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.foot-line {
  font-size: 9px;
  letter-spacing: 0.1em;
  color: var(--text-3);
}

.foot-line.dim {
  opacity: 0.6;
  letter-spacing: 0.04em;
}
</style>
