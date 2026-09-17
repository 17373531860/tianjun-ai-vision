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
    // 投影光引导输出窗 (feat/light-sensor): 独立全屏路由, 给投影仪副屏用。
    // 不进 Layout / 不进路由记忆 (REMEMBERABLE_NAMES), 与 Monitor 互不影响。
    path: '/projection',
    name: 'Projection',
    component: () => import('@/views/Projection/index.vue'),
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
      // 2026-09 「AI 能力试用」页已下线: 能力试用/记忆库管理/VLM 配置
      // 全量迁入模型仓库 (/model) 内置能力模型分区的「试一试」抽屉
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
  route?.query?.kiosk === '1' || route?.query?.station_view === '1' || route?.query?.multi_monitor === '1';

// kiosk = 工位屏形态（Electron 副屏子窗，或一拖多里一体机浏览器全屏开的工位页）。
const isKioskRoute = (route) => route?.query?.kiosk === '1';

const isHandsCropRoute = (route) =>
  route?.query?.kiosk === '1'
  && route?.query?.video_only === '1'
  && route?.query?.hands_crop === '1';

// 登录/激活是 kiosk 也必须能到的页（否则鉴权一开，工位屏没法登录就彻底打不开）
const isEscapeHatchRoute = (route) =>
  route?.name === 'Login' || route?.name === 'Activation';

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

/**
 * 业务页放行前的账号侧守卫：强制登录 + 按权限拦深链。
 *
 * 返回 undefined = 放行；返回路由对象 = 改道。
 *
 * 权限拦截是"和菜单同一套判据"（useAuthStore.canAccessRoute → ROUTE_PERM_MAP），
 * 不是按浏览器/kiosk 硬编码封路。所以：
 *   - 鉴权未启用（出厂默认）→ canAccessRoute 恒 true，存量客户零差异；
 *   - 工位账号没有 project.view → 手输 #/project 被弹回监控页；
 *   - 日后想放开一体机进项目页，只要给那个账号加权限即可，不用改代码。
 */
async function resolveAuthRedirect(to) {
  if (isEscapeHatchRoute(to)) return undefined;
  await ensureAuthInitialized();
  try {
    const { useAuthStore } = await import('@/store/useAuthStore');
    const authStore = useAuthStore();
    if (authStore.requiresLogin()) {
      console.log('[⬛ Router] 匿名兜底已关, 强制跳登录页');
      dbg('auth.ops', '匿名访问被拦截 → 跳登录页', `target=${to.fullPath}`);
      return { name: 'Login', query: { redirect: to.fullPath } };
    }
    // /monitor 自己不拦, 否则没有 monitor.view 的账号会被弹成死循环
    if (to.path !== '/monitor' && !authStore.canAccessRoute(to.path)) {
      console.log(`[⬛ Router] 权限不足, 拦下 ${to.fullPath} → /monitor`);
      dbg('auth.ops', '无权限页面被拦截 → 回监控页', `target=${to.fullPath}`);
      return { path: '/monitor' };
    }
  } catch (e) {
    console.warn('[⬛ Router] 账号守卫失败 (不阻断导航):', e?.message || e);
  }
  return undefined;
}

router.beforeEach(async (to, from) => {
  console.log(`[⬛ Router] 导航: ${from.fullPath} → ${to.fullPath} (name: ${to.name})`);

  // 普通导航不能丢掉工位身份；只读窗不能通过菜单/插件路由切换绕过操作限制。
  // Login / Activation 仍由原有鉴权守卫处理，桌面重载可应用新的只读配置。
  if (isMultiMonitorRoute(from) && !isHandsCropRoute(from) && from.path !== '/projection'
      && to.name !== 'Login' && to.name !== 'Activation') {
    if (from.query.readonly !== '0' && to.fullPath !== from.fullPath) return false;
    // 独立工位窗保留绑定；总控复用的 station_view 仍能通过侧栏返回总览管理。
    if (from.query.kiosk === '1') {
      const context = Object.fromEntries(['kiosk', 'channel', 'readonly', 'multi_monitor']
        .filter(key => from.query[key] !== undefined).map(key => [key, from.query[key]]));
      if (Object.entries(context).some(([key, value]) => to.query[key] !== value)) {
        return { path: to.path, query: { ...to.query, ...context }, hash: to.hash, replace: true };
      }
    }
  }

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

  // 工位屏钉死本工位：kiosk 页面一旦打开，任何跳转（误触、插件菜单、深链）都拒绝。
  // 一体机现场没有键盘鼠标退路，漂到别的页面等于这台工位屏当场报废。
  // 只留登录/激活两个出口，其余一律留在原地。
  if (isKioskRoute(from) && !isKioskRoute(to) && !isEscapeHatchRoute(to)) {
    console.log(`[⬛ Router] kiosk 工位屏钉死当前页, 拒绝跳转 → ${to.fullPath}`);
    return false;
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
    return await resolveAuthRedirect(to);
  }

  if (!window.electronAPI?.isElectron) {
    console.log('[⬛ Router] 非Electron环境，跳过授权检查');
    licenseChecked = true;
    return await resolveAuthRedirect(to);
  }

  try {
    console.log('[⬛ Router] 开始授权检查...');
    const t0 = Date.now();
    const status = await window.electronAPI.getLicenseStatus();
    console.log(`[⬛ Router] 授权检查完成 (${Date.now() - t0}ms): valid=${status.valid}`);
    licenseChecked = true;
    if (!status.valid) return { name: 'Activation' };
    // License 通过 → 顺便初始化用户系统状态 + 账号侧守卫
    return await resolveAuthRedirect(to);
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
