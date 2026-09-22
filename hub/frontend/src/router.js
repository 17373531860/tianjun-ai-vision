// hash 路由: dist 由枢纽后端 StaticFiles 分发, 无需服务器 rewrite
import { createRouter, createWebHashHistory } from 'vue-router'
import { authState } from './auth'

const routes = [
  { path: '/login', name: 'login', component: () => import('./views/LoginView.vue') },
  { path: '/', name: 'wall', component: () => import('./views/WallView.vue') },
  {
    path: '/station/:nodeId/:channelId',
    name: 'station',
    component: () => import('./views/StationView.vue'),
  },
  { path: '/alarms', name: 'alarms', component: () => import('./views/AlarmsView.vue') },
  { path: '/data', name: 'data', component: () => import('./views/DataView.vue') },
  { path: '/users', name: 'users', component: () => import('./views/UsersView.vue') },
]

const router = createRouter({ history: createWebHashHistory(), routes })

router.beforeEach((to) => {
  if (to.name !== 'login' && !authState.token) return { name: 'login' }
  if (to.name === 'login' && authState.token) return { name: 'wall' }
})

export default router
