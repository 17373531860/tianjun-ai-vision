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

const app = createApp(App);

app.config.errorHandler = (err, vm, info) => {
  console.error('[Vue Error]', err, info);
};

window.addEventListener('unhandledrejection', (event) => {
  console.error('[Unhandled Promise]', event.reason);
});

app.use(createPinia());
app.use(router);
app.use(ElementPlus);
app.use(i18n);

import * as ElementPlusIconsVue from '@element-plus/icons-vue'
for (const [key, component] of Object.entries(ElementPlusIconsVue)) {
  app.component(key, component)
}

app.mount('#app');