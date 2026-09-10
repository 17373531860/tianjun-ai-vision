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

// 手部副屏是只读图片终端：初始路由命中时不启动鉴权/插件网络引导，
// 避免除 /snapshot?view=hands 外的任何业务 API 与写通路。
const handsCropBootstrap = (() => {
  try {
    const hash = window.location.hash || '';
    const queryIndex = hash.indexOf('?');
    const query = new URLSearchParams(queryIndex >= 0 ? hash.slice(queryIndex + 1) : '');
    return query.get('kiosk') === '1'
      && query.get('video_only') === '1'
      && query.get('hands_crop') === '1';
  } catch {
    return false;
  }
})();

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
  if (handsCropBootstrap) return;
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
//
// ⚠️ v3.15.5 关键修复 (现场事故复盘):
//   前端页面加载远早于后端就绪 (后端要 CUDA 预热 + 模型加载好几秒). 旧逻辑一上来就拉
//   插件清单 → Network Error → 一次性放弃 → 插件前端定制全程不加载 (现场日志精准抓到
//   "[⬛ PluginLoader] 拿 active manifest 失败 :: Network Error"). 修法: 先轮询探活
//   等后端就绪再拉, 拉不到就重试, 覆盖后端冷启动窗口.
(async () => {
  if (handsCropBootstrap) return;
  const api = (await import('./api/index')).default;

  // 用插件清单端点探活 (无 active 插件也返回 200), 轮询直到后端就绪, 最多等 90s.
  // 90s 覆盖工控机冷启动 + CUDA 预热 + 大模型加载的最坏情况.
  async function waitBackendReady(maxWaitMs = 90000, intervalMs = 1000) {
    const start = Date.now();
    let attempt = 0;
    while (Date.now() - start < maxWaitMs) {
      attempt += 1;
      try {
        await api.get('/plugins/active/manifest');
        console.log(`[⬛ PluginBootstrap] 后端就绪 (探测#${attempt}, 耗时 ${Date.now() - start}ms)`);
        return true;
      } catch (e) {
        if (attempt === 1 || attempt % 5 === 0) {
          console.log(`[⬛ PluginBootstrap] 等后端就绪... 探测#${attempt} (${e?.message || e})`);
        }
        await new Promise((r) => setTimeout(r, intervalMs));
      }
    }
    console.warn(`[⬛ PluginBootstrap] 等后端就绪超时 ${maxWaitMs}ms, 仍尝试加载插件 (可能 Network Error)`);
    return false;
  }

  try {
    await waitBackendReady();

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

// v3.13 M2.2a: 全局注册 TjSlot 组件, 让主程序 .vue 文件能用 <TjSlot name="..."> 暴露
// 插件可覆盖的 UI 位置. 默认渲染 default slot (主程序原生内容); 插件用
// registry.slots.register(name, component) 覆盖时改渲染插件组件 + 透传主程序 props.
import TjSlot from './components/TjSlot.vue';
app.component('TjSlot', TjSlot);

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
