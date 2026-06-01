import { defineStore } from 'pinia';
import { markRaw } from 'vue';
import api from '@/api/index';

/**
 * 插件主题 Pinia store — 消费 active 插件 manifest.frontend.theme + frontend.hidden_menus。
 *
 * 设计意图 (对应 ISSUES.md §G3):
 * - main.js 启动后调 `apply()`，主程序拉 `/api/v1/plugins/active/manifest`
 *   - 没有 active 插件 → 字段保持 null/空数组，前端按默认天均品牌显示
 *   - 有 active 插件 → 应用 css_variables 到 document.documentElement, 替换 title/favicon/logo
 * - Navbar / Layout 用 getter 优先取插件值，fallback 主程序默认
 * - 插件停用 / 切换后 main.js 不会自动重 apply，所以 Settings 提示客户重启应用
 *   （和后端 PluginManager 一致：单 active + 重启才能完整切换）
 *
 * 字段:
 *   appTitle:        manifest.frontend.theme.app_title (string|null)
 *   logoUrl:         /api/v1/plugins/active/assets/<theme.logo>  (string|null, 已转 absolute)
 *   faviconUrl:      同上, theme.favicon
 *   cssVariables:    { '--tj-primary': '#...', ... }   已经 apply 到 :root
 *   hiddenMenus:     ['/alarm', ...]
 *   themeCssText:    fetch 进来的 theme.css 内容 (string|null)
 *   activeCustomerCode: 当前 active 插件 customer_code (诊断用)
 *
 * 用法:
 *   const themeStore = usePluginThemeStore()
 *   onMounted(async () => { await themeStore.apply() })
 *   // 在 .vue 里:  themeStore.isMenuHidden('/alarm')
 */
export const usePluginThemeStore = defineStore('plugin-theme', {
  state: () => ({
    appTitle: null,
    logoUrl: null,
    faviconUrl: null,
    cssVariables: {},
    hiddenMenus: [],
    themeCssText: null,
    activeCustomerCode: null,
    lastError: null,
    _appliedStyleEl: null,
    // G2: 插件动态注入的菜单 + 路由 (Tier 2/3)
    // pluginMenus: [{ path, label, icon, order }]
    pluginMenus: [],
    // pluginRoutes: [{ path, name, ... }] (仅记录, vue-router 真挂在 router 实例)
    pluginRoutes: [],
    // v3.13 M2.2a: 插件注册的 slot 组件 (slot name → Vue Component).
    // 单 active 插件设计 = 每个 slot name 只允许一个组件; 同名后注册覆盖前注册.
    // 用 markRaw 防 Vue reactive proxy 包 Component 引用 (避免性能损耗).
    pluginSlots: {},
    // v3.13 M2.2a: 插件 manifest.frontend.ui_hidden — slot name 列表.
    // 命中时 <TjSlot> 渲染 null (连默认内容都不渲染), 实现"硬隐藏"语义.
    uiHidden: [],
    // v3.13 M2.2b/M3.4: 客户插件注入的 Settings / Project tab 列表.
    // 元素: { key, label, component? } — component 用 plugin loader 注册的 slot 也可以,
    // 直接挂 component 引用更常用 (经 markRaw 防 reactive 代理).
    settingsTabs: [],
    projectTabs: [],
  }),

  getters: {
    isActive: (s) => Boolean(s.activeCustomerCode),
    isMenuHidden: (s) => (path) => Array.isArray(s.hiddenMenus) && s.hiddenMenus.includes(path),
    sortedPluginMenus: (s) => [...s.pluginMenus].sort((a, b) => (a.order ?? 999) - (b.order ?? 999)),
    // v3.13 M2.2a: TjSlot 内部用 — slot 是否在 ui_hidden 列表 / 是否有插件覆盖
    isSlotHidden: (s) => (name) => Array.isArray(s.uiHidden) && s.uiHidden.includes(name),
    getSlotComponent: (s) => (name) => s.pluginSlots[name] || null,
    registeredSlotNames: (s) => Object.keys(s.pluginSlots),
  },

  actions: {
    /**
     * 拉 active manifest 并应用主题。无 active 插件时清空状态。
     * 调用方应在应用启动后调一次 (main.js 启 Vue 挂载前 / 挂载后皆可)。
     */
    async apply() {
      this.lastError = null;
      try {
        const { data } = await api.get('/plugins/active/manifest');
        if (!data || Object.keys(data).length === 0) {
          this._resetTheme();
          return;
        }
        const cc = data.customer_code;
        const theme = data?.frontend?.theme || {};
        const hidden = data?.frontend?.hidden_menus || [];

        this.activeCustomerCode = cc || null;
        this.appTitle = theme.app_title || null;
        this.cssVariables = theme.css_variables || {};
        this.hiddenMenus = Array.isArray(hidden) ? hidden : [];

        // v3.13 M2.2a: 把 manifest.frontend.ui_hidden 应用到 store
        const uiHidden = data?.frontend?.ui_hidden || [];
        this.uiHidden = Array.isArray(uiHidden) ? uiHidden : [];

        // assets 路径：相对于插件包根目录, 通过 /api/v1/plugins/active/assets/{path}
        if (theme.logo) {
          this.logoUrl = this._buildAssetUrl(theme.logo);
        } else {
          this.logoUrl = null;
        }
        if (theme.favicon) {
          this.faviconUrl = this._buildAssetUrl(theme.favicon);
        } else {
          this.faviconUrl = null;
        }

        // 应用副作用到 DOM
        this._applyCssVariables();
        this._applyDocumentTitle();
        this._applyFavicon();

        // CSS 文件（如果声明了 theme.css 路径）
        if (theme.css) {
          await this._fetchAndInjectThemeCss(theme.css);
        } else {
          this._removeInjectedThemeCss();
        }
      } catch (e) {
        this.lastError = e?.message || 'unknown';
        // 不抛 — 插件主题失败不能拖垮主程序
        // eslint-disable-next-line no-console
        console.warn('[PluginTheme] apply 失败:', this.lastError);
      }
    },

    addPluginMenu(menu) {
      if (!menu || !menu.path) return;
      const exists = this.pluginMenus.find((m) => m.path === menu.path);
      if (exists) return;
      this.pluginMenus.push({
        path: menu.path,
        label: menu.label || menu.path,
        icon: menu.icon || null,
        order: typeof menu.order === "number" ? menu.order : 999,
      });
    },

    removePluginMenu(path) {
      this.pluginMenus = this.pluginMenus.filter((m) => m.path !== path);
    },

    addPluginRoute(route) {
      if (!route || !route.name) return;
      const exists = this.pluginRoutes.find((r) => r.name === route.name);
      if (exists) return;
      this.pluginRoutes.push({
        path: route.path,
        name: route.name,
        meta: route.meta || {},
      });
    },

    removePluginRoute(name) {
      this.pluginRoutes = this.pluginRoutes.filter((r) => r.name !== name);
    },

    // v3.13 M2.2a: slot 注册/注销 (plugin loader 用)
    addPluginSlot(name, component) {
      if (!name || !component) return;
      // markRaw 避免 Pinia 把 Vue Component 当 reactive data 来代理
      // (Component 引用本身是不变的, 不需要 deep watch)
      // ⚠️ v3.15.4: 必须用顶部静态 import 的 markRaw — 之前用 import('vue').then()
      // 异步动态导入, 在 file:// 打包环境下动态 import 不稳, 会让插槽永远设不上
      // (插件 register 已成功调到这里, 但 slot 还是空 → 双工位 UI 不显示的元凶之一).
      // 新增 key 触发 Vue3 reactive, getSlotComponent getter 会重算 → Monitor 的
      // layoutBodyOverride computed 自动更新.
      this.pluginSlots[name] = markRaw(component);
    },

    removePluginSlot(name) {
      if (this.pluginSlots[name]) {
        delete this.pluginSlots[name];
      }
    },

    // v3.13 M2.2b/M3.4: tab 注入 (plugin loader 用)
    addPluginTab(scope, tab) {
      if (!scope || !tab || !tab.key || !tab.label) return;
      const target = scope === 'settings' ? this.settingsTabs : (scope === 'project' ? this.projectTabs : null);
      if (!target) return;
      // 同 key 去重 (新覆盖旧)
      const idx = target.findIndex((t) => t.key === tab.key);
      const payload = {
        key: tab.key,
        label: tab.label,
        component: tab.component || null,
      };
      // 把 component 标 raw 避免 Pinia reactive 代理 Vue Component
      // ⚠️ v3.15.4: 同 addPluginSlot, 用静态 import 的 markRaw (不再 import('vue').then)
      if (payload.component) {
        payload.component = markRaw(payload.component);
      }
      if (idx >= 0) target.splice(idx, 1, payload);
      else target.push(payload);
    },

    removePluginTab(scope, key) {
      const target = scope === 'settings' ? this.settingsTabs : (scope === 'project' ? this.projectTabs : null);
      if (!target) return;
      const idx = target.findIndex((t) => t.key === key);
      if (idx >= 0) target.splice(idx, 1);
    },

    /** 主程序退出 / 测试用：恢复默认外观。 */
    _resetTheme() {
      // 撤销 CSS 变量
      const root = document?.documentElement;
      if (root) {
        for (const key of Object.keys(this.cssVariables)) {
          root.style.removeProperty(key);
        }
      }
      this.cssVariables = {};
      this.appTitle = null;
      this.logoUrl = null;
      this.faviconUrl = null;
      this.hiddenMenus = [];
      this.themeCssText = null;
      this.activeCustomerCode = null;
      this.pluginMenus = [];
      this.pluginRoutes = [];
      this.pluginSlots = {};
      this.uiHidden = [];
      this.settingsTabs = [];
      this.projectTabs = [];
      this._removeInjectedThemeCss();
      this._applyDocumentTitle();
      this._applyFavicon();
    },

    _buildAssetUrl(assetPath) {
      // api 实例 baseURL 已含 /api/v1, 这里直接拼后端 absolute 路径
      const base = api?.defaults?.baseURL || '';
      const clean = (assetPath || '').replace(/^\/+/, '');
      return `${base}/plugins/active/assets/${clean}`;
    },

    _applyCssVariables() {
      const root = document?.documentElement;
      if (!root || !this.cssVariables) return;
      for (const [k, v] of Object.entries(this.cssVariables)) {
        root.style.setProperty(k, v);
      }
    },

    _applyDocumentTitle() {
      if (!document) return;
      document.title = this.appTitle || '视觉AI行为引导系统';
    },

    _applyFavicon() {
      if (!document) return;
      let link = document.querySelector("link[rel='icon']");
      if (!link) {
        link = document.createElement('link');
        link.rel = 'icon';
        document.head.appendChild(link);
      }
      link.href = this.faviconUrl || '/vite.svg';
    },

    async _fetchAndInjectThemeCss(cssPath) {
      try {
        const url = this._buildAssetUrl(cssPath);
        const resp = await fetch(url, { credentials: 'omit' });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const text = await resp.text();
        this.themeCssText = text;
        this._injectThemeCss(text);
      } catch (e) {
        // eslint-disable-next-line no-console
        console.warn('[PluginTheme] theme.css 拉取失败:', e?.message);
      }
    },

    _injectThemeCss(text) {
      this._removeInjectedThemeCss();
      const style = document.createElement('style');
      style.setAttribute('data-plugin-theme', this.activeCustomerCode || 'unknown');
      style.textContent = text;
      document.head.appendChild(style);
      this._appliedStyleEl = style;
    },

    _removeInjectedThemeCss() {
      if (this._appliedStyleEl && this._appliedStyleEl.parentNode) {
        this._appliedStyleEl.parentNode.removeChild(this._appliedStyleEl);
      }
      this._appliedStyleEl = null;
    },
  },
});
