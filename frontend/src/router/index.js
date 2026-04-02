import { createRouter, createWebHashHistory } from 'vue-router';
import Layout from '@/layout/index.vue';

const routes = [
  {
    path: '/activation',
    name: 'Activation',
    component: () => import('@/views/Activation/index.vue'),
  },
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
      },
      {
        path: 'mes',
        name: 'MES',
        component: () => import('@/views/MES/index.vue'),
      },
    ]
  }
];

const router = createRouter({
  history: createWebHashHistory(),
  routes
});

let licenseChecked = false;

router.beforeEach(async (to, from) => {
  console.log(`[⬛ Router] 导航: ${from.fullPath} → ${to.fullPath} (name: ${to.name})`);

  if (licenseChecked || to.name === 'Activation') {
    console.log(`[⬛ Router] 直接放行 (licenseChecked=${licenseChecked}, target=${to.name})`);
    return;
  }

  if (!window.electronAPI?.isElectron) {
    console.log('[⬛ Router] 非Electron环境，跳过授权检查');
    licenseChecked = true;
    return;
  }

  try {
    console.log('[⬛ Router] 开始授权检查...');
    const t0 = Date.now();
    const status = await window.electronAPI.getLicenseStatus();
    console.log(`[⬛ Router] 授权检查完成 (${Date.now() - t0}ms): valid=${status.valid}`);
    licenseChecked = true;
    if (!status.valid) return { name: 'Activation' };
  } catch (e) {
    console.error('[⬛ Router] 授权检查异常:', e);
    licenseChecked = true;
  }
});

router.afterEach((to) => {
  console.log(`[⬛ Router] ✓ 导航完成: ${to.fullPath}`);
});

router.onError((error) => {
  console.error('[⬛ Router] 路由错误（可能是懒加载组件失败）:', error);
});

export default router;