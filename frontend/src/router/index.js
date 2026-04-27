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
let lastRouteRestored = false;

const LAST_ROUTE_KEY = 'tianjun:lastRoute';
// 允许被记忆的路由 (Activation 不在内, 不能恢复到激活页)
const REMEMBERABLE_NAMES = new Set([
  'Monitor', 'Project', 'Model', 'Data', 'Source', 'Settings', 'Alarm', 'MES'
]);

router.beforeEach(async (to, from) => {
  console.log(`[⬛ Router] 导航: ${from.fullPath} → ${to.fullPath} (name: ${to.name})`);

  // 冷启动恢复: 第一次进入且目标是默认 /monitor 时, 尝试取上次路由
  if (!lastRouteRestored && from.name === undefined && to.path === '/monitor') {
    lastRouteRestored = true;
    try {
      const last = localStorage.getItem(LAST_ROUTE_KEY);
      if (last && last !== '/monitor' && last.startsWith('/')) {
        console.log(`[⬛ Router] 恢复上次路由: ${last}`);
        // 让授权检查继续走 (下一跳进入 beforeEach 时 from.name 已存在)
        if (licenseChecked || !window.electronAPI?.isElectron) {
          return last;
        }
        // 先做授权检查再恢复
        try {
          const status = await window.electronAPI.getLicenseStatus();
          licenseChecked = true;
          if (!status.valid) return { name: 'Activation' };
          return last;
        } catch (e) {
          console.error('[⬛ Router] 授权检查异常:', e);
          // 授权状态未知时走保守策略：回到激活页，避免误放行
          licenseChecked = false;
          return { name: 'Activation' };
        }
      }
    } catch (e) {
      console.warn('[⬛ Router] 读取上次路由失败:', e);
    }
  }

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
    // 授权状态未知时不放行业务页，防止“异常即放行”
    licenseChecked = false;
    return { name: 'Activation' };
  }
});

router.afterEach((to) => {
  console.log(`[⬛ Router] ✓ 导航完成: ${to.fullPath}`);
  // 只记可恢复的页面
  if (to.name && REMEMBERABLE_NAMES.has(to.name)) {
    try {
      localStorage.setItem(LAST_ROUTE_KEY, to.fullPath);
    } catch (e) {
      // localStorage 不可用就算了
    }
  }
});

router.onError((error) => {
  console.error('[⬛ Router] 路由错误（可能是懒加载组件失败）:', error);
});

export default router;