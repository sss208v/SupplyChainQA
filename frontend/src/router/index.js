/**
 * SmartQA Pro - 路由配置
 *
 * 【学习要点】
 * 1. Vue Router 4 使用 createRouter + createWebHistory
 * 2. 路由懒加载：() => import(...) 实现按需加载，减少首屏体积
 * 3. 每个路由的 meta 字段可以存储页面标题等信息
 */
import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  {
    path: '/',
    redirect: '/chat',
  },
  {
    path: '/chat',
    name: 'Chat',
    component: () => import('@/views/Chat/index.vue'),
    meta: { title: '智能对话' },
  },
  {
    path: '/knowledge',
    name: 'Knowledge',
    component: () => import('@/views/Knowledge/index.vue'),
    meta: { title: '知识库管理' },
  },
  {
    path: '/tools',
    name: 'Tools',
    component: () => import('@/views/Tools/index.vue'),
    meta: { title: '工具管理' },
  },
  {
    path: '/evaluate',
    name: 'Evaluate',
    component: () => import('@/views/Evaluate/index.vue'),
    meta: { title: 'RAG 评估' },
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

// 更新页面标题
router.beforeEach((to) => {
  document.title = `${to.meta.title || '供应链助手'} - 供应链智能助手`
})

export default router
