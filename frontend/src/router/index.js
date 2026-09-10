import { createRouter, createWebHashHistory } from 'vue-router';
import Layout from '@/layout/index.vue';
import { dbg } from '@/utils/debug';

const routes = [
  {
    path: '/activation',
    name: 'Activation',
    component: () => import('@/views/Activation/index.vue'),
  },
  {
    // v3.10.0 用户系统: 独立登录页, 不受 License 守卫干扰 (License 先过, 才能进登录页)
    path: '/login',
    name: 'Login',
    component: () => import('@/views/Login/index.vue'),
  },
  {
    path: '/',
    component: Layout,
    redirect: '/monitor',
    children: [
      {
        path: 'monitor',
        name: 'Monitor',
        component: () => import('@/views/Monitor/MonitorRoute.vue'),
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
      {
        // v3.47 训练平台互连 (YoloVision)
        path: 'interconnect',
        name: 'Interconnect',
        component: () => import('@/views/Interconnect/index.vue'),
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
let authInitialized = false;

const LAST_ROUTE_KEY = 'tianjun:lastRoute';
// 允许被记忆的路由 (Activation / Login 不在内, 不能恢复到这两个页)
const REMEMBERABLE_NAMES = new Set([
  'Monitor', 'Project', 'Model', 'Data', 'Source', 'Settings', 'Alarm', 'MES', 'Interconnect'
]);

// 工位子窗与主窗口共用 /monitor；子窗 query 不得读写主窗口的路由记忆。
const isMultiMonitorRoute = (route) =>
  route?.query?.kiosk === '1' || route?.query?.multi_monitor === '1';

const isHandsCropRoute = (route) =>
  route?.query?.kiosk === '1'
  && route?.query?.video_only === '1'
  && route?.query?.hands_crop === '1';

/**
 * v3.10.0 用户系统: 在路由守卫中确保 useAuthStore 已 init.
 *
 * - 失败时仅打日志, 不阻断导航 (后端可能正在启动)
 * - 多次调用幂等 (store 内部 initialized flag 自己挡住)
 * - import 用 dynamic 避免与 main.js 启动时序冲突
 */
async function ensureAuthInitialized() {
  if (authInitialized) return;
  try {
    const { useAuthStore } = await import('@/store/useAuthStore');
    const authStore = useAuthStore();
    await authStore.init();
    authInitialized = true;
  } catch (e) {
    console.warn('[⬛ Router] AuthStore 初始化失败 (不阻断导航):', e?.message || e);
  }
}

router.beforeEach(async (to, from) => {
  console.log(`[⬛ Router] 导航: ${from.fullPath} → ${to.fullPath} (name: ${to.name})`);

  // 副屏窗口由 Electron 的 License 守门后创建；这里只保留一次本地 IPC 复核，
  // 不初始化 AuthStore/插件，确保页面网络只剩 hands snapshot 短轮询。
  if (isHandsCropRoute(to)) {
    if (!licenseChecked && window.electronAPI?.isElectron) {
      try {
        const status = await window.electronAPI.getLicenseStatus();
        licenseChecked = true;
        if (!status.valid) return { name: 'Activation' };
      } catch (e) {
        console.error('[⬛ Router] 副屏授权检查异常:', e);
        return { name: 'Activation' };
      }
    }
    return;
  }

  // 冷启动恢复: 第一次进入且目标是默认 /monitor 时, 尝试取上次路由
  if (!lastRouteRestored && from.name === undefined && to.path === '/monitor' && !isMultiMonitorRoute(to)) {
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

  // License 已通过 / 目标本身就是激活页 / 目标是登录页 → 跳过 License 校验
  // (Login 是登录前可达页, 不能要求 License 已过)
  if (licenseChecked || to.name === 'Activation' || to.name === 'Login') {
    console.log(`[⬛ Router] 跳过 License (licenseChecked=${licenseChecked}, target=${to.name})`);
    // 业务页放行前先同步用户系统状态 (登录/激活页内部自己 init, 这里不重复)
    if (to.name !== 'Activation' && to.name !== 'Login') {
      await ensureAuthInitialized();
      // v3.10+ 强制登录守卫: 鉴权启用 + 匿名兜底关 + 当前匿名 → 跳登录页
      try {
        const { useAuthStore } = await import('@/store/useAuthStore');
        const authStore = useAuthStore();
        if (authStore.requiresLogin()) {
          console.log('[⬛ Router] 匿名兜底已关, 强制跳登录页');
          dbg('auth.ops', '匿名访问被拦截 → 跳登录页', `target=${to.fullPath}`);
          return { name: 'Login', query: { redirect: to.fullPath } };
        }
      } catch (e) {
        console.warn('[⬛ Router] 强制登录守卫失败:', e?.message || e);
      }
    }
    return;
  }

  if (!window.electronAPI?.isElectron) {
    console.log('[⬛ Router] 非Electron环境，跳过授权检查');
    licenseChecked = true;
    await ensureAuthInitialized();
    return;
  }

  try {
    console.log('[⬛ Router] 开始授权检查...');
    const t0 = Date.now();
    const status = await window.electronAPI.getLicenseStatus();
    console.log(`[⬛ Router] 授权检查完成 (${Date.now() - t0}ms): valid=${status.valid}`);
    licenseChecked = true;
    if (!status.valid) return { name: 'Activation' };
    // License 通过 → 顺便初始化用户系统状态
    await ensureAuthInitialized();
  } catch (e) {
    console.error('[⬛ Router] 授权检查异常:', e);
    // 授权状态未知时不放行业务页，防止"异常即放行"
    licenseChecked = false;
    return { name: 'Activation' };
  }
});

router.afterEach((to, from) => {
  console.log(`[⬛ Router] ✓ 导航完成: ${to.fullPath}`);
  // 调试设置 'page.nav': 每次页面切换留痕 (开关关闭时零开销)
  if (!isHandsCropRoute(to)) {
    dbg('page.nav', `页面切换 ${from.fullPath} → ${to.fullPath}`, `name=${String(to.name || '')}`);
  }
  // 只记可恢复的页面
  if (to.name && REMEMBERABLE_NAMES.has(to.name) && !isMultiMonitorRoute(to)) {
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
