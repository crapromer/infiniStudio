import { createRouter, createWebHistory } from 'vue-router'
import Overview from '../views/Overview.vue'
import Brands from '../views/Brands.vue'
import BrandDetail from '../views/BrandDetail.vue'
import Models from '../views/Models.vue'
import Servers from '../views/Servers.vue'
import Services from '../views/Services.vue'
import ServiceChat from '../views/ServiceChat.vue'
import Tasks from '../views/Tasks.vue'

const routes = [
  {
    path: '/',
    name: 'overview',
    component: Overview
  },
  {
    path: '/brands',
    name: 'brands',
    component: Brands
  },
  {
    path: '/brands/:id',
    name: 'brandDetail',
    component: BrandDetail
  },
  {
    path: '/models',
    name: 'models',
    component: Models
  },
  {
    path: '/servers',
    name: 'servers',
    component: Servers
  },
  {
    path: '/services',
    name: 'services',
    component: Services
  },
  {
    path: '/services/:id/chat',
    name: 'serviceChat',
    component: ServiceChat
  },
  {
    path: '/tasks',
    name: 'tasks',
    component: Tasks
  }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

// 路由守卫：确保刷新时路由和菜单状态同步
router.beforeEach((to, from, next) => {
  // 如果路由名称不存在且路径不是根路径，重定向到总览页面
  if (!to.name && to.path !== '/') {
    next({ name: 'overview', replace: true })
    return
  }
  next()
})

export default router

