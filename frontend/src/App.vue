<script setup lang="ts">
// 应用外壳：左侧导航 + 主区（顶栏 + 视图）+ 右侧可折叠 Inspector（对应 v3.0 原型）
import { onMounted, onUnmounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import TopStatusBar from '@/components/TopStatusBar.vue'
import SideNav from '@/components/SideNav.vue'
import InspectorPanel from '@/components/InspectorPanel.vue'
import GlobalComposer from '@/components/GlobalComposer.vue'
import AuthModal from '@/components/AuthModal.vue'
import { useSessionStore } from '@/stores/session'
import { useAuthStore } from '@/stores/auth'

const store = useSessionStore()
const auth = useAuthStore()
const route = useRoute()
const inspectorOpen = ref(false)
const navCollapsed = ref(false)
let healthTimer: number | null = null

onMounted(() => {
  void store.refreshHealth()
  // 恢复身份：本地 token 有效则回到登录态，否则同步匿名配额（顶栏/侧栏展示）
  void auth.refreshMe()
  // 从 localStorage 恢复了会话时，回填会话状态与引用链，
  // 避免刷新后各面板显示为空白（会话本身仍在后端）。
  if (store.sessionId) {
    void store.refreshStatus()
    void store.loadCitationChain()
  }
  // 轮询刷新后端健康状态
  healthTimer = window.setInterval(() => void store.refreshHealth(), 15000)
})

onUnmounted(() => {
  if (healthTimer !== null) window.clearInterval(healthTimer)
})
</script>

<template>
  <div class="app-shell" :class="{ 'insp-hidden': !inspectorOpen, 'nav-hidden': navCollapsed }">
    <SideNav :collapsed="navCollapsed" @toggle="navCollapsed = !navCollapsed" />

    <main class="app-main">
      <TopStatusBar />
      <div class="main-inner">
        <router-view v-slot="{ Component }">
          <transition name="panel-fade" mode="out-in">
            <component :is="Component" />
          </transition>
        </router-view>
      </div>

      <!-- 全局底部输入条（置于主区滚动容器内，sticky 定位自然居中于内容区；聊天页自带输入区故不重复展示） -->
      <GlobalComposer v-if="route.path !== '/chat'" />
    </main>

    <InspectorPanel v-model:open="inspectorOpen" />

    <!-- 全局唯一的账号弹窗（左下角身份卡 / 配额提示唤起） -->
    <AuthModal />
  </div>
</template>

<style scoped>
.app-shell {
  display: grid;
  grid-template-columns: var(--nav-w) minmax(0, 1fr) var(--inspector-w);
  grid-template-areas: 'side main insp';
  height: 100vh;
  overflow: hidden;
  transition: grid-template-columns 0.25s ease;
}

.app-shell.insp-hidden {
  /* 收缩后保留 34px 竖条：与 InspectorPanel 的 is-closed 宽度一致，
     使展开按钮始终可见可点 */
  grid-template-columns: var(--nav-w) minmax(0, 1fr) 34px;
}

/* 侧边栏收起为 56px 图标栏（品牌区按钮切换） */
.app-shell.nav-hidden,
.app-shell.nav-hidden.insp-hidden {
  grid-template-columns: 56px minmax(0, 1fr) 34px;
}

.app-shell.nav-hidden:not(.insp-hidden) {
  grid-template-columns: 56px minmax(0, 1fr) var(--inspector-w);
}

.app-main {
  grid-area: main;
  min-width: 0;
  overflow-y: auto;
  overflow-x: hidden;
  display: flex;
  flex-direction: column;
}

.main-inner {
  flex: 1 1 auto;
  max-width: 980px;
  width: 100%;
  margin: 0 auto;
  padding: 36px 32px 160px;
}
</style>
