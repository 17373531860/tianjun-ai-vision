import { createApp } from 'vue';
import { createPinia } from 'pinia';
import App from './App.vue';
import router from './router';
import ElementPlus from 'element-plus';
import 'element-plus/dist/index.css';
import './style.css';
import { createI18n } from 'vue-i18n';
import zhCN from './locales/zh-CN.js';
import zhTW from './locales/zh-TW.js';
import enUS from './locales/en-US.js';
import jaJP from './locales/ja-JP.js';
import koKR from './locales/ko-KR.js';

// 生产构建下静默 console.log/debug，保留 warn/error 用于线上排错
// 开发模式下保持原样；console.error/warn 始终保留以便定位问题
if (!import.meta.env.DEV) {
  // eslint-disable-next-line no-console
  console.log = () => {};
  // eslint-disable-next-line no-console
  console.debug = () => {};
}

// 屏幕自适应：根据视口宽度动态设置 html font-size，
// Tailwind 的 rem 单位会跟着等比缩放
;(function initResponsive() {
  const BASE_WIDTH = 1920;
  const BASE_FONT_SIZE = 16;
  const MIN_FONT_SIZE = 12;  // 防止 12 寸小屏字太小
  const MAX_FONT_SIZE = 24;  // 防止 50 寸大屏字太大

  function setRootFontSize() {
    const w = document.documentElement.clientWidth || window.innerWidth;
    const fs = Math.max(MIN_FONT_SIZE, Math.min(MAX_FONT_SIZE, (w / BASE_WIDTH) * BASE_FONT_SIZE));
    document.documentElement.style.fontSize = fs + 'px';
    window.__uiScale = fs / BASE_FONT_SIZE;
  }

  setRootFontSize();
  window.addEventListener('resize', setRootFontSize);
})();

const i18n = createI18n({
  legacy: false,
  globalInjection: true, // Allow $t globally
  locale: 'zh-CN',
  messages: {
    'zh-CN': zhCN,
    'zh-TW': zhTW,
    'en-US': enUS,
    'ja-JP': jaJP,
    'ko-KR': koKR
  }
});

console.log(`[⬛ Boot] main.js 开始执行 ${new Date().toLocaleTimeString()}`);

const app = createApp(App);

app.config.errorHandler = (err, vm, info) => {
  console.error(`[⬛ Vue Fatal] 组件错误 — info: ${info}`, err);
  console.error('[⬛ Vue Fatal] 组件:', vm?.$options?.name || vm?.$options?.__name || '未知');
  console.error('[⬛ Vue Fatal] 堆栈:', err?.stack);
};

window.addEventListener('unhandledrejection', (event) => {
  console.error('[⬛ Promise] 未处理的 Promise 拒绝:', event.reason);
  if (event.reason?.stack) console.error('[⬛ Promise] 堆栈:', event.reason.stack);
});

window.addEventListener('error', (event) => {
  console.error(`[⬛ Global] 全局错误: ${event.message}`, event.filename, '行:', event.lineno);
});

console.log('[⬛ Boot] 注册插件...');
try {
  app.use(createPinia());
  console.log('[⬛ Boot] ✓ Pinia');
  app.use(router);
  console.log('[⬛ Boot] ✓ Router');
  app.use(ElementPlus);
  console.log('[⬛ Boot] ✓ ElementPlus');
  app.use(i18n);
  console.log('[⬛ Boot] ✓ i18n');
} catch (e) {
  console.error('[⬛ Boot] 插件注册失败:', e);
}

// v3.10.0 用户系统: 启动时拉一次 /auth/status + /auth/me 让 store 就位
// (失败静默 — 后端可能正在启动, 路由守卫和 Settings 页都会再 init 一次)
(async () => {
  try {
    const { useAuthStore } = await import('./store/useAuthStore.js');
    const authStore = useAuthStore();
    await authStore.init();
    console.log(`[⬛ AuthStore] 初始化完成: enabled=${authStore.authEnabled}, user=${authStore.displayLabel}`);
  } catch (e) {
    console.warn('[⬛ AuthStore] 初始化失败 (不影响主流程):', e?.message || e);
  }
})();

// G3 + G2: active 插件主题 + Tier2/3 ESM loader — 在 Vue mount 前 fire-and-forget
// (失败静默 fallback, 主程序继续走默认外观与默认路由表)
(async () => {
  try {
    const { usePluginThemeStore } = await import('./store/usePluginThemeStore.js');
    const themeStore = usePluginThemeStore();
    await themeStore.apply();
    if (themeStore.isActive) {
      console.log(`[⬛ PluginTheme] 已应用: ${themeStore.activeCustomerCode}, title="${themeStore.appTitle}", hiddenMenus=${JSON.stringify(themeStore.hiddenMenus)}`);
    } else {
      console.log('[⬛ PluginTheme] 无 active 插件, 走天均默认外观');
    }

    const { loadActivePluginFrontend, setPluginLoaderI18n } = await import('./composables/usePluginLoader.js');
    setPluginLoaderI18n(i18n);
    const loadResult = await loadActivePluginFrontend(router);
    if (loadResult.loaded) {
      console.log(`[⬛ PluginLoader] Tier2/3 ESM 加载成功: customer=${loadResult.customerCode}`);
    } else {
      console.log(`[⬛ PluginLoader] 未加载 ESM 入口: ${loadResult.reason}`);
    }
  } catch (e) {
    console.warn('[⬛ PluginBootstrap] 失败:', e);
  }
})();

import * as ElementPlusIconsVue from '@element-plus/icons-vue'
for (const [key, component] of Object.entries(ElementPlusIconsVue)) {
  app.component(key, component)
}

try {
  app.mount('#app');
  console.log(`[⬛ Boot] ✓ Vue 挂载成功 ${new Date().toLocaleTimeString()}`);
} catch (e) {
  console.error('[⬛ Boot] ✗ Vue 挂载失败:', e);
}

setInterval(() => {
  const mem = performance?.memory;
  if (mem) {
    const used = (mem.usedJSHeapSize / 1048576).toFixed(1);
    const total = (mem.totalJSHeapSize / 1048576).toFixed(1);
    const limit = (mem.jsHeapSizeLimit / 1048576).toFixed(1);
    if (mem.usedJSHeapSize / mem.jsHeapSizeLimit > 0.7) {
      console.warn(`[⬛ Memory] ⚠ 内存偏高: ${used}MB / ${total}MB (上限 ${limit}MB)`);
    }
  }
}, 30000);