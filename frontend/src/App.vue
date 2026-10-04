<script setup lang="ts">
// 应用外壳：顶部状态条 + 左侧导航 + 非对称主区 + 右侧可折叠 Inspector
import { onMounted, onUnmounted, ref } from 'vue'
import TopStatusBar from '@/components/TopStatusBar.vue'
import SideNav from '@/components/SideNav.vue'
import InspectorPanel from '@/components/InspectorPanel.vue'
import { useSessionStore } from '@/stores/session'

const store = useSessionStore()
const inspectorOpen = ref(true)
let healthTimer: number | null = null

onMounted(() => {
  void store.refreshHealth()
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
  <div class="app-shell">
    <TopStatusBar />

    <div class="app-body">
      <SideNav />

      <main class="app-main">
        <router-view v-slot="{ Component }">
          <transition name="panel-fade" mode="out-in">
            <component :is="Component" />
          </transition>
        </router-view>
      </main>

      <InspectorPanel v-model:open="inspectorOpen" />
    </div>
  </div>
</template>

<style scoped>
.app-shell {
  display: flex;
  flex-direction: column;
  height: 100vh;
  overflow: hidden;
}

.app-body {
  flex: 1 1 auto;
  display: flex;
  min-height: 0;
}

.app-main {
  flex: 1 1 auto;
  min-width: 0;
  overflow-y: auto;
  overflow-x: hidden;
  padding: 22px 26px 40px;
}
</style>
