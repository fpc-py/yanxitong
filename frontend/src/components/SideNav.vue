<script setup lang="ts">
// 左侧导航：品牌 + 新研究任务 + 分组条目 + 历史会话 + 用户卡片（对应 v3.0 原型）
import { computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useSessionStore } from '@/stores/session'
import { useSystemStore } from '@/stores/system'

/** count：导航右侧显示真实计数（取自当前会话），无则回落到英文标签 */
type CountKey = 'papers' | 'kg' | 'claims'

interface NavItem {
  path: string
  label: string
  hint: string
  icon: string
  count?: CountKey
}

interface NavGroup {
  name: string
  items: NavItem[]
}

// 原型两组结构：总览 + 研究工作流（找→读→算→写→审）
const groups: NavGroup[] = [
  {
    name: '总览',
    items: [
      { path: '/overview', label: '工作台', hint: 'Overview', icon: 'M3 3h7v7H3zM14 3h7v7h-7zM14 14h7v7h-7zM3 14h7v7H3z' },
    ],
  },
  {
    name: '研究工作流 · 找→读→算→写→审',
    items: [
      { path: '/chat', label: '研究对话', hint: 'Deep Research', icon: 'M21 12a8 8 0 0 1-8 8H4l2-3a8 8 0 0 1 15-5z' },
      {
        path: '/literature',
        label: '文献库',
        hint: 'LIBRARY',
        count: 'papers',
        icon: 'M4 19.5A2.5 2.5 0 0 1 6.5 17H20M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z',
      },
      {
        path: '/kg',
        label: '知识图谱',
        hint: 'GRAPH',
        count: 'kg',
        icon: 'M18 8a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM6 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM18 22a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM8.6 13.5l6.8 4M15.4 6.5l-6.8 4',
      },
      { path: '/analyze', label: '数据分析', hint: 'ANALYZE', icon: 'M12 20V10M18 20V4M6 20v-4M3 20h18' },
      {
        path: '/design',
        label: '实验设计',
        hint: 'DESIGN',
        icon: 'M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6',
      },
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
        count: 'claims',
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

const props = defineProps<{ collapsed?: boolean }>()
const emit = defineEmits<{ (e: 'toggle'): void }>()

const route = useRoute()
const router = useRouter()
const store = useSessionStore()
const system = useSystemStore()

const activePath = computed(() => route.path)

// ---- 真实计数：导航右侧提示 & 用户卡片成本 ----
function countOf(key: CountKey): number {
  if (key === 'papers') return store.papersCount
  if (key === 'kg') return store.status?.kg_entities_count ?? 0
  return store.claims.length
}

function hintText(item: NavItem): string {
  return item.count ? String(countOf(item.count)) : item.hint
}

const costText = computed(() => {
  const c = system.metrics?.cost_cents
  if (c === undefined || c === null) return '成本统计中'
  return `累计 ¥${(Number.isFinite(c) ? c / 100 : 0).toFixed(2)}`
})

const toggleTitle = computed(() => (props.collapsed ? '展开侧边栏' : '收起侧边栏'))

// 新建任务：重置会话并回到研究对话
async function newTask(): Promise<void> {
  await store.resetSession()
  if (route.path !== '/chat') void router.push('/chat')
}

// 切换历史会话：加载该会话状态并回到研究对话
async function switchTo(id: string): Promise<void> {
  await store.switchSession(id)
  if (route.path !== '/chat') void router.push('/chat')
}

// 删除历史会话（按钮已 stopPropagation，不会触发上方的切换）
function onDeleteSession(id: string): void {
  void store.removeSession(id)
}

onMounted(() => {
  // 后端可能离线，loadSessions 内部静默失败即可
  void store.loadSessions()
  // 成本数据由常驻的 Inspector 面板统一拉取（见 InspectorPanel 的环境轮询），此处只读
})
</script>

<template>
  <nav class="side-nav" :class="{ 'is-collapsed': props.collapsed }">
    <div class="side-top">
      <div class="brand">
        <span class="brand-mark">研析通</span>
        <span class="brand-dot" />
      </div>
      <button class="icon-btn" :title="toggleTitle" @click="emit('toggle')">
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <rect x="3" y="3" width="18" height="18" rx="2" />
          <path d="M9 3v18" />
        </svg>
      </button>
    </div>

    <button class="new-btn" @click="newTask">
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14" /></svg>
      <span class="new-text">新研究任务</span>
    </button>

    <div class="side-scroll">
      <div v-for="(group, gi) in groups" :key="group.name" class="nav-group" :style="{ animationDelay: `${gi * 0.06}s` }">
        <div class="nav-label">{{ group.name }}</div>
        <router-link
          v-for="item in group.items"
          :key="item.path"
          :to="item.path"
          class="nav-item"
          :class="{ active: activePath === item.path }"
          :title="item.label"
        >
          <svg class="nav-icon" viewBox="0 0 24 24" aria-hidden="true">
            <path :d="item.icon" />
          </svg>
          <span class="nav-text">{{ item.label }}</span>
          <span class="n mono">{{ hintText(item) }}</span>
        </router-link>
      </div>

      <div class="nav-group session-group">
        <div class="nav-label">历史会话</div>
        <template v-if="store.sessionList.length > 0">
          <div
            v-for="item in store.sessionList"
            :key="item.session_id"
            class="session-item"
            :class="{ active: item.session_id === store.sessionId }"
            @click="switchTo(item.session_id)"
          >
            <span class="session-topic">{{ item.topic || '未命名会话' }}</span>
            <span class="session-count">{{ item.papers_count }}篇</span>
            <button class="session-del" title="删除会话" @click.stop="onDeleteSession(item.session_id)">
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12" /></svg>
            </button>
          </div>
        </template>
        <div v-else-if="store.sessionLoaded" class="session-empty">暂无历史会话</div>
      </div>
    </div>

    <div class="side-foot">
      <div class="avatar">研</div>
      <div class="user">
        <div class="user-name">研究生 · 材料学院</div>
        <div class="user-plan">Pro · {{ costText }}</div>
      </div>
    </div>
  </nav>
</template>

<style scoped>
.side-nav {
  grid-area: side;
  display: flex;
  flex-direction: column;
  border-right: 1px solid var(--line);
  background: var(--bg);
  overflow: hidden;
  min-width: 0;
}

.side-top {
  padding: 18px 18px 12px;
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.brand {
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.brand-mark {
  font-family: var(--font-display);
  font-style: italic;
  font-size: 20px;
  font-weight: 500;
  color: var(--ink);
}

.brand-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--accent);
}

.icon-btn {
  width: 30px;
  height: 30px;
  border-radius: 8px;
  display: grid;
  place-items: center;
  cursor: pointer;
  color: var(--ink-2);
  border: 1px solid transparent;
  background: transparent;
  transition: background 0.15s;
}

.icon-btn:hover {
  background: var(--soft);
}

.icon-btn svg {
  width: 16px;
  height: 16px;
  fill: none;
  stroke: currentColor;
  stroke-width: 1.8;
  stroke-linecap: round;
  stroke-linejoin: round;
}

.new-btn {
  margin: 8px 14px;
  padding: 10px 14px;
  border: 1.5px solid var(--ink);
  border-radius: 10px;
  font-size: 13.5px;
  font-weight: 500;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 8px;
  transition: all 0.15s;
  background: transparent;
  color: var(--ink);
  font-family: var(--font-body);
}

.new-btn:hover {
  background: var(--ink);
  color: var(--bg);
}

.new-btn svg {
  width: 14px;
  height: 14px;
  fill: none;
  stroke: currentColor;
  stroke-width: 2;
  stroke-linecap: round;
}

.side-scroll {
  flex: 1 1 auto;
  overflow-y: auto;
  padding: 8px 10px;
}

.nav-group {
  margin-top: 14px;
  animation: fadeIn 0.5s var(--ease) both;
}

.nav-label {
  font-size: 10.5px;
  color: var(--ink-3);
  padding: 6px 10px;
  font-weight: 500;
  letter-spacing: 0.04em;
}

.nav-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  border-radius: 8px;
  cursor: pointer;
  color: var(--ink-1);
  font-size: 13.5px;
  text-decoration: none;
  transition: all 0.12s;
}

.nav-item:hover {
  background: var(--soft);
  color: var(--ink);
}

.nav-item.active {
  background: var(--soft);
  color: var(--ink);
  font-weight: 500;
}

.nav-icon {
  width: 15px;
  height: 15px;
  stroke: currentColor;
  fill: none;
  stroke-width: 1.8;
  stroke-linecap: round;
  stroke-linejoin: round;
  flex: 0 0 auto;
}

.nav-text {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.n {
  font-size: 10px;
  color: var(--ink-3);
  flex: 0 0 auto;
}

.session-group {
  margin-top: 14px;
}

.session-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  border-radius: 8px;
  cursor: pointer;
  color: var(--ink-1);
  font-size: 13px;
  transition: all 0.12s;
}

.session-item:hover {
  background: var(--soft);
  color: var(--ink);
}

.session-item.active {
  background: var(--soft);
  color: var(--ink);
  font-weight: 500;
}

.session-topic {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.session-count {
  flex: 0 0 auto;
  font-size: 10px;
  color: var(--ink-3);
  background: var(--soft);
  border-radius: 999px;
  padding: 1px 7px;
}

.session-del {
  flex: 0 0 auto;
  width: 20px;
  height: 20px;
  display: grid;
  place-items: center;
  border: none;
  background: transparent;
  border-radius: 6px;
  color: var(--ink-3);
  cursor: pointer;
  transition: background 0.12s, color 0.12s;
}

.session-del:hover {
  background: var(--soft);
  color: var(--ink);
}

.session-del svg {
  width: 12px;
  height: 12px;
  fill: none;
  stroke: currentColor;
  stroke-width: 2;
  stroke-linecap: round;
  stroke-linejoin: round;
}

.session-empty {
  padding: 6px 10px;
  font-size: 12px;
  color: var(--ink-3);
}

.side-foot {
  padding: 14px;
  border-top: 1px solid var(--line);
  display: flex;
  align-items: center;
  gap: 10px;
}

.avatar {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  background: var(--ink);
  color: var(--bg);
  display: grid;
  place-items: center;
  font-family: var(--font-display);
  font-style: italic;
  font-size: 13px;
  flex: 0 0 auto;
}

.user {
  min-width: 0;
  line-height: 1.35;
}

.user-name {
  font-size: 13px;
  font-weight: 500;
  color: var(--ink);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.user-plan {
  font-size: 11px;
  color: var(--ink-3);
}

/* ---- 收起态：56px 图标栏（品牌区按钮切换） ---- */
.side-nav.is-collapsed .side-top {
  justify-content: center;
  padding: 16px 0 10px;
}

.side-nav.is-collapsed .brand {
  display: none;
}

.side-nav.is-collapsed .new-btn {
  justify-content: center;
  margin: 6px 10px;
  padding: 10px 0;
}

.side-nav.is-collapsed .new-text {
  display: none;
}

.side-nav.is-collapsed .side-scroll {
  padding: 6px;
}

.side-nav.is-collapsed .nav-label {
  display: none;
}

.side-nav.is-collapsed .nav-group {
  margin-top: 6px;
}

.side-nav.is-collapsed .nav-item {
  justify-content: center;
  padding: 9px 0;
  gap: 0;
}

.side-nav.is-collapsed .nav-text,
.side-nav.is-collapsed .n {
  display: none;
}

/* 收起时历史会话与用户文字不再有空间，隐藏但保留展开入口 */
.side-nav.is-collapsed .session-group,
.side-nav.is-collapsed .user {
  display: none;
}

.side-nav.is-collapsed .side-foot {
  justify-content: center;
  padding: 14px 0;
}
</style>
