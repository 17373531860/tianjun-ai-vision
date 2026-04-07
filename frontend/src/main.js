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