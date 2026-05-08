import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  {
    path: '/login',
    name: 'Login',
    component: () => import('@/views/Login/index.vue'),
    meta: { title: '登录', public: true },
  },
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

// 路由守卫
router.beforeEach((to) => {
  document.title = `${to.meta.title || '供应链助手'} - 供应链智能助手`

  // 未登录且非公开页面，跳转登录
  const token = localStorage.getItem('token')
  if (!token && !to.meta.public) {
    return '/login'
  }
})

export default router
