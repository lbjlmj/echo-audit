/**
 * 路由（PRD §12 六界面模块）。
 * 权限以后端为准，前端守卫仅兜底：无会话令牌时重定向到 /hub（登录入口），
 * 逐页权限码校验由后端裁决（AGENTS.md 硬约束 5）。
 */
import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      redirect: '/hub',
    },
    {
      path: '/hub',
      component: () => import('@/views/hub/index.vue'),
      meta: { title: '统一入口 audit-hub' },
    },
    {
      path: '/project',
      component: () => import('@/views/project/index.vue'),
      meta: { title: '项目工作台', perm: 'project.approve' },
    },
    {
      path: '/worksheet',
      component: () => import('@/views/worksheet/index.vue'),
      meta: { title: '底稿工作台', perm: 'worksheet.edit' },
    },
    {
      path: '/review',
      component: () => import('@/views/review/index.vue'),
      meta: { title: '复核工作台', perm: 'worksheet.review_sign' },
    },
    {
      path: '/quality',
      component: () => import('@/views/quality/index.vue'),
      meta: { title: '质控驾驶舱', perm: 'archive.access' },
    },
    {
      path: '/knowledge',
      component: () => import('@/views/knowledge/index.vue'),
      meta: { title: '知识库问答', perm: 'knowledge.write' },
    },
  ],
})

router.beforeEach((to) => {
  document.title = `${String(to.meta.title ?? '')} - Echo 审计作业平台`
  // 兜底：无令牌仅放行登录入口；有令牌时页面级权限由后端 403 兜底
  const token = localStorage.getItem('echo_token')
  if (!token && to.path !== '/hub') return '/hub'
})

export default router
