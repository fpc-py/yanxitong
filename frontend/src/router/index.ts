import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'

// 路由表：每个视图对应一类科研任务
export const routes: RouteRecordRaw[] = [
  { path: '/', redirect: '/overview' },
  {
    path: '/overview',
    name: 'overview',
    component: () => import('@/views/OverviewView.vue'),
    meta: { title: '工作台概览', group: '工作台' },
  },
  {
    path: '/chat',
    name: 'chat',
    component: () => import('@/views/ChatView.vue'),
    meta: { title: '会话问答', group: '工作台' },
  },
  {
    path: '/literature',
    name: 'literature',
    component: () => import('@/views/LiteratureView.vue'),
    meta: { title: '文献与引用', group: '文献情报' },
  },
  {
    path: '/kg',
    name: 'kg',
    component: () => import('@/views/KgView.vue'),
    meta: { title: '知识图谱', group: '文献情报' },
  },
  {
    path: '/analyze',
    name: 'analyze',
    component: () => import('@/views/AnalyzeView.vue'),
    meta: { title: '数据分析', group: '研究执行' },
  },
  {
    path: '/design',
    name: 'design',
    component: () => import('@/views/DesignView.vue'),
    meta: { title: '实验设计', group: '研究执行' },
  },
  {
    path: '/write',
    name: 'write',
    component: () => import('@/views/WriteView.vue'),
    meta: { title: '论文写作', group: '成果产出' },
  },
  {
    path: '/review',
    name: 'review',
    component: () => import('@/views/ReviewView.vue'),
    meta: { title: '学术审阅', group: '成果产出' },
  },
  {
    path: '/bibliography',
    name: 'bibliography',
    component: () => import('@/views/BibliographyView.vue'),
    meta: { title: '参考文献', group: '成果产出' },
  },
  { path: '/:pathMatch(.*)*', redirect: '/overview' },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
  scrollBehavior: () => ({ top: 0 }),
})

router.afterEach((to) => {
  const title = (to.meta.title as string) || ''
  document.title = title ? `${title} · 研析通` : '研析通 · 科研全链路工作台'
})

export default router
