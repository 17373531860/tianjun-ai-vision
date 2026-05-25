/**
 * 用户系统 Pinia store (v3.10.0+)
 *
 * 职责:
 * - 持久化 token (localStorage 'tianjun:auth_token')
 * - 缓存当前身份 (currentUser) 和总开关状态 (authEnabled)
 * - 提供 init / login / logout / refresh / hasPermission / hasRole 等 actions
 *
 * 设计契约:
 * - authEnabled=false → currentUser 永远是 __system__ 隐式管理员, hasPermission 永远 true
 * - authEnabled=true && 无 token → currentUser 是 __anonymous__ 匿名 operator, 仅 monitor.view / monitor.detection.control
 * - authEnabled=true && 有 token → currentUser 是真实账号, permissions 是其角色并集
 *
 * 初始化时机:
 * - main.js / App.vue 启动后调 store.init() → 拉 /auth/status + /auth/me
 * - 后续路由守卫 / Navbar / 各视图只读 store 不再发请求
 */
import { defineStore } from 'pinia';
import {
  getAuthStatus,
  getMe,
  login as apiLogin,
  logout as apiLogout,
} from '@/api/auth';

const TOKEN_KEY = 'tianjun:auth_token';

// ============================================================
// 路由权限映射 — 左侧菜单 / 路由守卫共用
//
// 约定:
// - 未在表里的路由 → 默认放行 (新加业务路由不需要改 store)
// - 表里的路由 → 当前用户必须拥有指定 permission 才能进入
// - 用 *.view 这种粒度: "能看到此页" 即可, 写操作单独标 require_perm 在 API 层
//
// 操作员 (operator) 仅有 monitor.* → 只能看 Monitor; 其他菜单全部隐藏.
// 工程师 (engineer) 有 monitor/project/source/model/alarm/data/mes/settings.* →
//   看除"账号鉴权 Tab"之外全部.
// 管理员 (admin) 权限 ['*'] → 全开.
// ============================================================
const ROUTE_PERM_MAP = {
  '/monitor': 'monitor.view',
  '/project': 'project.view',
  '/model': 'model.view',
  '/source': 'source.view',
  '/data': 'data.view',
  '/mes': 'mes.view',
  '/alarm': 'alarm.view',
  '/settings': 'settings.view',
};

// 匹配 permission key (与后端 core/permissions.py: match_permission 同语义)
function matchPermission(required, userPerms) {
  if (!required) return true;
  if (!userPerms || userPerms.length === 0) return false;
  for (const p of userPerms) {
    if (!p) continue;
    if (p === '*') return true;
    if (p === required) return true;
    if (p.endsWith('.*')) {
      const prefix = p.slice(0, -2);
      if (required === prefix || required.startsWith(prefix + '.')) return true;
    }
  }
  return false;
}

function readTokenFromStorage() {
  // v3.10+ 阶段 7+: 优先 sessionStorage (非持久模式), 再回退 localStorage
  // 这样 session_persist=false 时关 tab 立即失效, 同时兼容上次 localStorage 留下的 token
  try {
    const s = sessionStorage.getItem(TOKEN_KEY);
    if (s) return s;
    return localStorage.getItem(TOKEN_KEY) || '';
  } catch {
    return '';
  }
}

function writeTokenToStorage(token, persist = true) {
  // persist=true  → 写 localStorage (跨重启)
  // persist=false → 写 sessionStorage (仅当前 tab)
  try {
    if (!token) {
      localStorage.removeItem(TOKEN_KEY);
      sessionStorage.removeItem(TOKEN_KEY);
      return;
    }
    if (persist) {
      localStorage.setItem(TOKEN_KEY, token);
      sessionStorage.removeItem(TOKEN_KEY);
    } else {
      sessionStorage.setItem(TOKEN_KEY, token);
      localStorage.removeItem(TOKEN_KEY);
    }
  } catch {}
}

export const useAuthStore = defineStore('auth', {
  state: () => ({
    // 总开关
    authEnabled: false,
    // 匿名 operator 兜底开关 (默认 true; false 时未登录必须先去登录页)
    allowAnonymous: true,
    // 重启后保持登录 (默认 true; false 时关 tab/重启即失效)
    sessionPersist: true,
    // 已有账号数 (启用引导用: 启用后 user_count 必 ≥1)
    userCount: 0,
    // 当前身份 (来自 /auth/me 的 user 字段)
    currentUser: {
      id: null,
      username: '__system__',
      display_name: '系统超级管理员 (鉴权未启用)',
      roles: ['admin'],
      permissions: ['*'],
      is_anonymous: false,
      is_superuser: true,
    },
    // 当前 token (从 localStorage 启动时读入)
    token: readTokenFromStorage(),
    // 初始化已完成 (避免重复拉 /me)
    initialized: false,
    // 正在加载身份信息中 (避免并发 init)
    loading: false,
  }),

  getters: {
    /** 是否已登录真实账号 (有 id 且非匿名).
     *  注意: admin 角色权限是 ["*"], 后端 is_superuser=true,
     *  但他仍然是真实登录用户; is_superuser 只用来判"隐式管理员" (id=null 时).
     */
    isLoggedIn(state) {
      return !state.currentUser.is_anonymous &&
        state.currentUser.id !== null &&
        state.currentUser.id !== undefined;
    },
    /** 当前是否匿名 operator 身份 (只能按 3 个按钮) */
    isAnonymous(state) {
      return !!state.currentUser.is_anonymous;
    },
    /** 当前是否隐式管理员 (开关关闭) */
    isImplicitAdmin(state) {
      return !!state.currentUser.is_superuser && state.currentUser.id === null;
    },
    /** 显示用的"当前身份" 短描述 */
    displayLabel(state) {
      if (!state.authEnabled) return '鉴权未启用';
      if (state.currentUser.is_anonymous) return '操作员 (未登录)';
      return state.currentUser.display_name || state.currentUser.username;
    },
  },

  actions: {
    /**
     * 启动时调用 — 拉 status + me, 装载身份.
     *
     * force=true 时强制重拉 (登录/登出/启用-关闭开关后调)
     */
    async init(force = false) {
      if (this.initialized && !force) return;
      if (this.loading) return;
      this.loading = true;
      try {
        // 串行: 先 status 再 me, 让 me 走在 token 已注入之后 (但 axios 拦截器
        // 已经做了, 这里其实可以并行, 顺序无关. 保持串行更易追踪)
        const statusRes = await getAuthStatus();
        this.authEnabled = !!statusRes.data?.auth_enabled;
        this.userCount = Number(statusRes.data?.user_count) || 0;
        // 后端默认 true; 仅当显式返 false 时关闭兜底
        this.allowAnonymous = statusRes.data?.allow_anonymous_operator !== false;
        this.sessionPersist = statusRes.data?.session_persist !== false;

        const meRes = await getMe();
        this.currentUser = meRes.data?.user || this.currentUser;
        // /auth/me 也返回 auth_enabled, 以它为准 (status 和 me 之间客户可能切换)
        if (typeof meRes.data?.auth_enabled === 'boolean') {
          this.authEnabled = meRes.data.auth_enabled;
        }

        this.initialized = true;
      } catch (e) {
        // 后端没起来或网络错误: 静默, 保留 init=false 让下次重试
        console.warn('[useAuthStore] init 失败:', e?.message || e);
      } finally {
        this.loading = false;
      }
    },

    /**
     * 登录 — 调 /auth/login, 拿到 token 后写 store + localStorage, 然后刷新身份
     */
    async login(username, password) {
      const res = await apiLogin(username, password);
      const token = res.data?.token || '';
      if (!token) throw new Error('登录响应缺少 token');

      this.token = token;
      // v3.10+ 阶段 7+: 后端 login 响应里告诉前端 session_persist 当前值
      // true → localStorage (跨重启), false → sessionStorage (仅当前 tab)
      const persist = res.data?.session_persist !== false;
      this.sessionPersist = persist;
      writeTokenToStorage(token, persist);
      // 强制重拉 /me 装载真实角色
      await this.init(true);
      return this.currentUser;
    },

    /**
     * 登出 — 调 /auth/logout, 清 token, 回到匿名状态
     */
    async logout() {
      try {
        await apiLogout();
      } catch (e) {
        // 即使后端登出失败, 前端也要清干净
        console.warn('[useAuthStore] logout 后端调用失败, 仍清前端 token:', e?.message);
      }
      this.token = '';
      // 登出清两种 storage, persist 参数无关紧要
      writeTokenToStorage('', true);
      // 强制重拉 /me, 应该变成 anonymous
      await this.init(true);
    },

    /**
     * 设置总开关状态 (启用 / 关闭账号鉴权后调) — 不发请求, 只更新 state
     * 调用方 (AuthPanel) 自己负责发 enable-auth / disable-auth, 然后调本方法
     */
    setAuthEnabled(enabled) {
      this.authEnabled = !!enabled;
    },

    /**
     * 权限检查 — 单个 perm key, 通配符匹配
     */
    hasPermission(perm) {
      if (!this.currentUser) return false;
      return matchPermission(perm, this.currentUser.permissions || []);
    },

    /**
     * 权限检查 — 任一匹配即可
     */
    hasAnyPermission(perms = []) {
      return perms.some((p) => this.hasPermission(p));
    },

    /**
     * 角色检查 — is_superuser 永远 true
     */
    hasRole(role) {
      if (!this.currentUser) return false;
      if (this.currentUser.is_superuser) return true;
      return (this.currentUser.roles || []).includes(role);
    },

    /**
     * 路由级访问检查 — 给左侧菜单 / 路由守卫用.
     * 鉴权未启用时永远放行 (用户系统对客户零差异).
     * 未在 ROUTE_PERM_MAP 配置的路径默认放行.
     */
    canAccessRoute(path) {
      if (!this.authEnabled) return true;
      const perm = ROUTE_PERM_MAP[path];
      if (!perm) return true;
      return this.hasPermission(perm);
    },

    /**
     * 是否必须强制登录 — 给路由守卫用.
     * 鉴权启用 + 匿名兜底关 + 当前匿名 → 必须登录
     */
    requiresLogin() {
      return this.authEnabled && !this.allowAnonymous && this.isAnonymous;
    },
  },
});
