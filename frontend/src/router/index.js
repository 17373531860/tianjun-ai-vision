import { createRouter, createWebHashHistory } from 'vue-router';
import Layout from '@/layout/index.vue';

const routes = [
  {
    path: '/',
    component: Layout,
    redirect: '/monitor',
    children: [
      {
        path: 'monitor',
        name: 'Monitor',
        component: () => import('@/views/Monitor/index.vue'),
      },
      {
        path: 'project',
        name: 'Project',
        component: () => import('@/views/Project/index.vue'),
      },
      {
        path: 'model',
        name: 'Model',
        component: () => import('@/views/Model/index.vue'),
      },
      {
        path: 'data',
        name: 'Data',
        component: () => import('@/views/Data/index.vue'),
      },
      {
        path: 'source',
        name: 'Source',
        component: () => import('@/views/Source/index.vue'),
      },
      {
        path: 'settings',
        name: 'Settings',
        component: () => import('@/views/Settings/index.vue'),
      },
      {
        path: 'alarm',
        name: 'Alarm',
        component: () => import('@/views/Alarm/index.vue'),
      }
    ]
  }
];

export default createRouter({
  history: createWebHashHistory(),
  routes
});