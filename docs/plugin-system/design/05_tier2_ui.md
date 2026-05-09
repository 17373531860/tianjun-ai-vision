# 05 — 档位 2：动态组件 + 路由 / 菜单注入（UI Plugin）

> 适用版本：基于 `feat/plugin-config` v0.1
> 本文目的：把档位 2（UI 插件）从"加载 Vue 组件"到"挂到 vue-router + Pinia + 菜单"全链路定义到**Electron file:// 生产环境可运行**的程度。
>
> 阅读前置：design 01 §3.5（frontend.routes / stores / menus）、design 04（tier 1 基础设施）、inventory/01（前端启动时序）。
>
> 配套：design 06（后端 register_plugin）、design 07（vite.lib build 配置）。

---

## 一、范围与目标

### 1.1 档位 2 比档位 1 多了什么

| 能力 | 档位 1 | 档位 2 |
|---|---|---|
| 主题色 / Logo / Title | ✅ | ✅（继承） |
| i18n 合并 | ✅ | ✅（继承） |
| CSS 隐藏菜单 | ✅ | ✅（继承） |
| **动态加载 Vue 组件** | ❌ | ✅ |
| **添加新路由** | ❌ | ✅ |
| **添加 Pinia store** | ❌ | ✅ |
| **菜单从 routes 表生成** | ❌ | ✅（layout 改造） |
| **路由级权限守卫**（不只是 CSS 隐藏） | ❌ | ✅ |
| **挂载 ESM 模块** | ❌ | ✅ |
| 后端能力 | ❌ | ❌ → 升档位 3 |

### 1.2 典型用例

| 场景 | 复杂度 | manifest 字段 |
|---|---|---|
| 加 ACME 自家报表页（一个新视图） | 1~2 天 | `frontend.routes[1]` |
| 加班次管理（视图 + Pinia store） | 2~3 天 | `frontend.routes[1]` + `frontend.stores[1]` |
| 多页面 ACME 系统（报表 + 班次 + 操作日志） | 5~7 天 | `frontend.routes[3]` + `frontend.stores[2]` + i18n + theme |
| 替换主程序 Data 页为客户专属版（高危） | ⚠️ 不支持 | 需要 design 06 加 view override 能力 |

### 1.3 三条强约束

1. **不能修改主程序源码**：插件"装"上来，主程序代码（`router/index.js` / `layout/index.vue` / `main.js`）**只在 v3.7 一次性改造完成**，之后任何插件**纯加载机制**
2. **生产环境必须能跑**：Electron 用 `file://` 协议加载 `dist/index.html`，浏览器原生 `import('http://...')` 在 file:// 下表现不一致 → 必须用 **Blob URL 方案**
3. **错误必须隔离**：插件视图组件抛错 → Vue 顶层 errorHandler 捕获 → fallback 显示错误 + console，但**主程序其他页面继续可用**

---

## 二、整体架构与启动时序

### 2.1 加载时序（接续 design 04）

```
1. index.html → main.js
2. await loadActivePluginTheme()                ← design 04 (tier 1)
3. createApp(App)
4. createI18n(messages={...})  → mergeLocaleMessage(plugin_i18n)
5. app.use(pinia / router / ElementPlus / i18n / icons)
6. (NEW) await loadActivePluginUI({ app, router, pinia, i18n })  ← tier 2
   ├─ 已有 manifest（在 tier 1 步 2 已拉过, 通过 window.__pluginManifest 复用）
   ├─ tier ≥ 2 → 处理 frontend.routes / frontend.stores / frontend.entry
   ├─ fetch 插件 entry.js (ESM bundle) → blob URL → import()
   ├─ import 后调 plugin.register({ app, router, pinia, i18n, ctx })
   ├─ register 内部:
   │    ├─ pinia 注册 stores
   │    ├─ router.addRoute (parent='Layout', 子路由)
   │    └─ pluginRegistry.menus 推入菜单项
7. app.mount('#app')
   ↑ mount 时, 路由 / 菜单 / store 全部就绪
```

### 2.2 与 tier 1 的复用

design 04 §3.1.4 `themeLoader.js` 拉过的 `manifest_json` 缓存到 `window.__pluginManifest`，tier 2 直接读，**不再发第二次 manifest 请求**。

```js
// frontend/src/plugin/themeLoader.js 改造
export async function loadActivePluginTheme() {
  const result = { i18nMessages: {}, manifestLoaded: false };
  // ... 拉 manifest ...
  window.__pluginManifest = manifest;  // ← NEW: 缓存给 tier 2 用
  return result;
}
```

---

## 三、插件入口契约

### 3.1 register 函数签名

每个 tier 2/3 插件**必须**导出一个 `register(ctx)`：

```js
// plugins/acme/frontend/src/index.js (开发源码)
import shiftStore from './stores/shift';
import AcmeReport from './views/AcmeReport.vue';
import AcmeShift from './views/AcmeShift.vue';

export default {
  /**
   * @param {Object} ctx
   * @param {App}     ctx.app           - Vue app 实例
   * @param {Router}  ctx.router        - vue-router 实例
   * @param {Pinia}   ctx.pinia         - Pinia 实例
   * @param {I18n}    ctx.i18n          - i18n 实例
   * @param {String}  ctx.customer_code - 'acme'
   * @param {Object}  ctx.registry      - 注册表 (见 §3.2)
   * @param {Object}  ctx.host          - 主程序暴露的部分 API (见 §3.3)
   */
  async register(ctx) {
    const { router, pinia, registry, customer_code } = ctx;

    // 1. 注册 Pinia store
    registry.stores.register('plugin-acme-shift', shiftStore);

    // 2. 注册路由 (子路由会被自动加到 Layout 下)
    registry.routes.register({
      path: 'report',
      name: 'report',
      component: AcmeReport,
      menu: { label: 'plugin.acme.menu.report', icon: 'DataLine', order: 100 },
    });

    registry.routes.register({
      path: 'shift',
      name: 'shift',
      component: AcmeShift,
      menu: { label: 'plugin.acme.menu.shift', icon: 'Calendar', order: 110 },
    });

    // 3. (可选) 监听 ctx.host 提供的事件
    // ctx.host.systemStore.$onAction(...);
  },

  // (可选) 卸载钩子, 主要清后台 setInterval 之类
  async unregister(ctx) {
    // 不强制实现, 默认 no-op
  },
};
```

### 3.2 registry 接口

`registry` 是主程序提供的注册表，做"插件想做的事"和"实际怎么改变 app 状态"的中间层。这样**未来要换实现也不动插件代码**。

```js
// frontend/src/plugin/registry.js (新增, 由 loadActivePluginUI 创建并传给插件)
export function createPluginRegistry(ctx) {
  const customer_code = ctx.customer_code;
  const registeredRoutes = [];
  const registeredStores = [];
  const registeredMenus = [];

  return {
    /** Pinia store 注册 */
    stores: {
      register(id, defineStoreFn) {
        // 校验命名空间
        if (!id.startsWith(`plugin-${customer_code}-`)) {
          throw new Error(`store id 必须以 plugin-${customer_code}- 开头, 实际: ${id}`);
        }
        // 注: 主程序不强制提前注册, store 用到时 defineStore() 才生效
        // 但可以 eager call 一次让它创建
        try {
          defineStoreFn(ctx.pinia)();
          registeredStores.push(id);
        } catch (e) {
          throw new Error(`store ${id} 初始化失败: ${e.message}`);
        }
      },
      list() { return [...registeredStores]; },
    },

    /** Vue Router 注册 */
    routes: {
      register(routeDef) {
        // 路径命名空间: /plugin/{cc}/{path}
        if (routeDef.path.startsWith('/')) {
          throw new Error('路由 path 必须是相对路径, 实际: ' + routeDef.path);
        }
        if (!/^[a-z][a-z0-9-]*$/.test(routeDef.path)) {
          throw new Error('路由 path 必须是 kebab-case');
        }
        if (!/^[a-z][a-z0-9-]*$/.test(routeDef.name)) {
          throw new Error('路由 name 必须是 kebab-case');
        }

        const fullName = `plugin-${customer_code}-${routeDef.name}`;
        const fullPath = `plugin/${customer_code}/${routeDef.path}`;

        // 包装组件以做错误隔离
        const wrappedComponent = wrapPluginView(routeDef.component, customer_code, fullName);

        ctx.router.addRoute('Layout', {
          path: fullPath,
          name: fullName,
          component: wrappedComponent,
          meta: {
            plugin: customer_code,
            originalName: routeDef.name,
            menu: routeDef.menu,
          },
        });

        registeredRoutes.push(fullName);

        if (routeDef.menu) {
          registeredMenus.push({
            path: '/' + fullPath,                       // /plugin/acme/report
            name: fullName,
            label: routeDef.menu.label,                  // i18n key 或字面量
            icon: routeDef.menu.icon,
            order: routeDef.menu.order ?? 999,
            plugin: customer_code,
          });
        }
      },
      list() { return [...registeredRoutes]; },
    },

    /** 菜单查询 (供 Layout 用) */
    menus: {
      list() { return [...registeredMenus].sort((a, b) => a.order - b.order); },
    },

    /** i18n 增量合并 (供 register 内部需要时调用) */
    i18n: {
      merge(locale, msgs) {
        ctx.i18n.global.mergeLocaleMessage(locale, msgs);
      },
    },
  };
}
```

### 3.3 host 接口（主程序暴露给插件的"窗口"）

> 这是**主程序对插件开放的最小 API 表面**。设计原则：
> - 仅暴露**只读 store 引用** + **少量方法**
> - 不暴露 axios 实例（让插件用 fetch 自己调）
> - 不暴露 ElMessage 等 ElementPlus（插件自己 import）

```js
// frontend/src/plugin/host.js
import { useSystemStore } from '@/store/useSystemStore';
import { useProjectStore } from '@/store/useProjectStore';

export function createHost(ctx) {
  return {
    // 系统状态 (只读)
    get systemStore() { return useSystemStore(); },
    get projectStore() { return useProjectStore(); },

    // 主程序版本
    main_version: window.__mainVersion || '3.7.0',

    // 主程序导航 (插件可以编程导航)
    navigate(target) {
      ctx.router.push(target);
    },

    // Electron API (脚下功能, 仅 Electron 环境有)
    electron: window.electronAPI || null,

    // 自家 KV 配置读 (仅 plugin.{cc}.* 命名空间)
    async getConfig(key) {
      if (!key.startsWith(`plugin.${ctx.customer_code}.`)) {
        throw new Error('只能读自家命名空间下的 config');
      }
      const resp = await fetch(`/api/v1/system-config/${encodeURIComponent(key)}`);
      if (!resp.ok) return null;
      const data = await resp.json();
      return data.value;
    },
    async setConfig(key, value) {
      if (!key.startsWith(`plugin.${ctx.customer_code}.`)) {
        throw new Error('只能写自家命名空间下的 config');
      }
      await fetch(`/api/v1/system-config/${encodeURIComponent(key)}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ value }),
      });
    },
  };
}
```

> ⚠️ host 接口是**契约性 API**——一旦发布，**不能 break 升级**。任何字段变更需要新发 manifest_version。

---

## 四、Vue 组件动态加载（最难一节）

### 4.1 问题

Electron 生产环境是 `file://` 协议，加载 `dist/index.html`：

```
file:///C:/Program Files/TianJun/dist/index.html
```

此时浏览器的 `import('http://localhost:8001/api/v1/plugins/.../entry.js')` 行为：

| 浏览器/Electron | 结果 |
|---|---|
| Chrome 80+ file:// → http: | ❌ 被阻止（CORS / mixed content） |
| Electron 32+ Chromium | ⚠️ 默认阻止；可改 `webPreferences.webSecurity=false`（**强烈不推荐**） |
| 开发模式（vite dev server, http://localhost:5174）| ✅ 跨域 OK |

**结论**：不能用 dynamic `import('http://...')`。

### 4.2 解决方案：Blob URL

```js
// 1. fetch ESM 源码字节
const resp = await fetch('/api/v1/plugins/active/assets/frontend/dist/entry.js');
const code = await resp.text();

// 2. 创建 Blob URL（同源，受 file:// 信任）
const blob = new Blob([code], { type: 'text/javascript' });
const url = URL.createObjectURL(blob);

// 3. import (file:// → blob:// 是允许的)
const module = await import(/* @vite-ignore */ url);

// 4. 用完释放
URL.revokeObjectURL(url);
```

**已验证**：Electron 32+ 在 file:// 下加载 blob:// ES Module 是 OK 的。

### 4.3 子模块导入怎么办？

如果 entry.js 内部有 `import './stores/shift.js'`，浏览器会去 `blob://` 相对路径找子模块——**找不到**。

**解决方案**：插件用 vite 打包时配 `format: 'es'` + **bundle 所有子模块**到一个文件：

```js
// plugins/acme/frontend/vite.lib.config.js (插件作者写)
import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';
import path from 'path';

export default defineConfig({
  plugins: [vue()],
  build: {
    lib: {
      entry: path.resolve(__dirname, 'src/index.js'),
      formats: ['es'],
      fileName: () => 'entry.js',
    },
    rollupOptions: {
      // 把这些 externals 留给主程序提供
      external: [
        'vue', 'vue-router', 'pinia', 'vue-i18n',
        'element-plus', '@element-plus/icons-vue',
        'echarts', 'axios',
      ],
      output: {
        // 用全局变量名（在主程序 importmap / 注入时绑定）
        // 我们不用 globals, 直接保留 import 语句, 后面用 import shim 处理 (见 §4.4)
        format: 'es',
      },
    },
    cssCodeSplit: false,  // 把 CSS 也合到一起 (插件作者打 entry.css)
    sourcemap: false,
  },
});
```

打出来的 `entry.js` 长这样：

```js
import { defineComponent, ref } from 'vue';
import { useRouter } from 'vue-router';
import { defineStore } from 'pinia';
// ... bundle 内其他代码 ...
const _sfc_main = defineComponent({ ... });
const shiftStore = defineStore('plugin-acme-shift', { ... });
export default {
  register(ctx) { ... }
};
```

这些 `import 'vue'` 语句在 file://blob:// 下会失败——因为 blob:// 没有 `vue` 包。

### 4.4 importmap 解决 externals

Electron 32+ 支持 [Import Maps](https://developer.mozilla.org/en-US/docs/Web/HTML/Element/script/type/importmap)：

```html
<!-- frontend/index.html -->
<script type="importmap">
{
  "imports": {
    "vue": "/__vendor/vue.esm.js",
    "vue-router": "/__vendor/vue-router.esm.js",
    "pinia": "/__vendor/pinia.esm.js",
    "vue-i18n": "/__vendor/vue-i18n.esm.js",
    "element-plus": "/__vendor/element-plus.esm.js",
    "@element-plus/icons-vue": "/__vendor/element-plus-icons.esm.js"
  }
}
</script>
```

但这要求主程序**预先**把这些库以 ESM 形式释放到 `dist/__vendor/`。

#### 4.4.1 vendor bundle 工程

新增 `frontend/vite.vendor.config.js`：

```js
// frontend/vite.vendor.config.js
import { defineConfig } from 'vite';

export default defineConfig({
  build: {
    outDir: 'dist/__vendor',
    lib: {
      entry: {
        vue: '/path/to/node_modules/vue/dist/vue.esm-browser.prod.js',
        'vue-router': '...',
        // ...
      },
      formats: ['es'],
    },
    rollupOptions: {
      output: {
        // 关键: 全部输出为可被 importmap 引用的 ESM
        entryFileNames: '[name].esm.js',
        format: 'es',
      },
    },
  },
});
```

> **此时主程序 Vite 双 build**：
> 1. 主入口 `index.html` (现有 `vite build`)
> 2. `__vendor/*.esm.js` (`vite build --config vite.vendor.config.js`)
>
> 这俩都要 ship 到 dist/，安装时一起拷贝。

#### 4.4.2 简化方案：window 全局注入（**推荐！**）

importmap 工程化复杂，更简单的方案是**主程序把 vendor 挂在 window 上，插件构建时把 import 改写为 `window.__vendor.xxx` 引用**。

主程序（在 main.js 早期）：

```js
// frontend/src/main.js
import * as vue from 'vue';
import * as vueRouter from 'vue-router';
import * as pinia from 'pinia';
import * as vueI18n from 'vue-i18n';
import * as elementPlus from 'element-plus';
import * as elPlusIcons from '@element-plus/icons-vue';

window.__pluginVendor = {
  'vue': vue,
  'vue-router': vueRouter,
  'pinia': pinia,
  'vue-i18n': vueI18n,
  'element-plus': elementPlus,
  '@element-plus/icons-vue': elPlusIcons,
  'axios': window.axios || (await import('axios')),
};
```

插件 vite build 配置用 **`output.globals` + `format: 'system'`** 或 **CommonJS 风格**：

```js
// plugins/acme/frontend/vite.lib.config.js (改写)
export default defineConfig({
  build: {
    lib: { entry: 'src/index.js', formats: ['umd'], name: 'PluginAcme', fileName: () => 'entry.umd.js' },
    rollupOptions: {
      external: ['vue', 'vue-router', 'pinia', 'vue-i18n', 'element-plus', '@element-plus/icons-vue', 'axios'],
      output: {
        format: 'umd',
        globals: {
          'vue': '__pluginVendor["vue"]',
          'vue-router': '__pluginVendor["vue-router"]',
          'pinia': '__pluginVendor["pinia"]',
          'vue-i18n': '__pluginVendor["vue-i18n"]',
          'element-plus': '__pluginVendor["element-plus"]',
          '@element-plus/icons-vue': '__pluginVendor["@element-plus/icons-vue"]',
          'axios': '__pluginVendor["axios"]',
        },
      },
    },
  },
});
```

**加载方式简化**：

```js
// frontend/src/plugin/uiLoader.js
async function loadPluginEntry(url) {
  const resp = await fetch(url);
  const code = await resp.text();
  // 用 Function 执行（有 eval 的味道, 但是 file:// 同源，配合签名验证可控）
  const factory = new Function('window', `${code}; return window.PluginAcme;`);
  // 或者:
  const blob = new Blob([code], { type: 'text/javascript' });
  const objUrl = URL.createObjectURL(blob);
  const module = await import(/* @vite-ignore */ objUrl);
  URL.revokeObjectURL(objUrl);
  return module.default || module;
}
```

> ⚠️ **此方案的安全考虑**：插件代码**已经过 RSA 签名验证**（design 02），可视为可信。eval / new Function 在签名验证后是可接受风险，与 dynamic import 等价。

#### 4.4.3 推荐技术栈

**最终选择**：**format=es + Blob URL + importmap（vendor.esm.js）**

理由：
- **format=es** 是 Vue 3 / vite 的"原生"输出，代码可读、source map 友好
- **importmap** Electron 32+ 原生支持，不依赖 hack
- vendor bundle 一次性产出，主程序版本固定不变（除非升级 Vue 等核心库）

```
dist/
├── index.html              ← <script type="importmap">
├── assets/                 ← 主程序代码
└── __vendor/
    ├── vue.esm.js
    ├── vue-router.esm.js
    ├── pinia.esm.js
    ├── element-plus.esm.js
    ├── element-plus.css
    └── ...
```

```js
// 插件加载
const url = `/api/v1/plugins/active/assets/frontend/dist/entry.js`;
const resp = await fetch(url);
const code = await resp.text();

// 重写相对 import 路径? 不需要, vite 会 bundle 所有相对路径
// import 'vue' / 'vue-router' 等 bare specifier → importmap 解析

const blob = new Blob([code], { type: 'text/javascript' });
const objUrl = URL.createObjectURL(blob);
const module = await import(/* @vite-ignore */ objUrl);
URL.revokeObjectURL(objUrl);

const plugin = module.default;
await plugin.register(ctx);
```

**已知约束**：
- Electron ≥ 32（已在 v3.x 主版本）
- Chromium ≥ 89 importmap 原生支持

### 4.5 完整 uiLoader 实现

```js
// frontend/src/plugin/uiLoader.js
import { createPluginRegistry } from './registry';
import { createHost } from './host';

const PLUGIN_LOAD_TIMEOUT_MS = 10_000;

/**
 * 加载档位 2 / 3 插件 UI 部分
 * 必须在 createApp + use(...) 之后, mount 之前调用
 *
 * @param {Object} opts
 * @param {App}    opts.app
 * @param {Router} opts.router
 * @param {Pinia}  opts.pinia
 * @param {I18n}   opts.i18n
 * @returns {Promise<{loaded: boolean, customer_code?: string, error?: Error}>}
 */
export async function loadActivePluginUI(opts) {
  const manifest = window.__pluginManifest;
  if (!manifest) return { loaded: false };

  if (manifest.tier < 2) {
    // tier 1 only: 没有 UI 加载, themeLoader 已处理
    return { loaded: false };
  }

  const cc = manifest.customer_code;
  const entry = manifest.frontend?.entry;
  if (!entry) {
    console.warn(`[Plugin UI] manifest 缺 frontend.entry`);
    return { loaded: false };
  }

  const ctx = {
    app: opts.app,
    router: opts.router,
    pinia: opts.pinia,
    i18n: opts.i18n,
    customer_code: cc,
  };
  ctx.registry = createPluginRegistry(ctx);
  ctx.host = createHost(ctx);

  let plugin;
  try {
    plugin = await Promise.race([
      _fetchAndImportPlugin(entry),
      new Promise((_, rej) => setTimeout(() => rej(new Error('timeout')), PLUGIN_LOAD_TIMEOUT_MS)),
    ]);
  } catch (e) {
    console.error(`[Plugin UI] ${cc} 加载失败:`, e);
    _reportPluginError(cc, 'fetch_or_import', e);
    return { loaded: false, error: e };
  }

  // 校验 register 函数存在
  if (!plugin || typeof plugin.register !== 'function') {
    const err = new Error('plugin module 必须 export default { register }');
    console.error(`[Plugin UI] ${cc} 入口契约不符:`, err);
    _reportPluginError(cc, 'invalid_entry', err);
    return { loaded: false, error: err };
  }

  try {
    await plugin.register(ctx);
    console.log(`[Plugin UI] ✓ ${cc} 注册完成`,
      `routes=${ctx.registry.routes.list().length}`,
      `stores=${ctx.registry.stores.list().length}`,
      `menus=${ctx.registry.menus.list().length}`);
    // 暴露给 layout 用
    window.__pluginRegistry = ctx.registry;
    return { loaded: true, customer_code: cc };
  } catch (e) {
    console.error(`[Plugin UI] ${cc} register 失败:`, e);
    _reportPluginError(cc, 'register', e);
    return { loaded: false, error: e };
  }
}

async function _fetchAndImportPlugin(entryPath) {
  const url = `/api/v1/plugins/active/assets/${entryPath}`;
  const resp = await fetch(url, { cache: 'no-store' });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  const code = await resp.text();
  const blob = new Blob([code], { type: 'text/javascript' });
  const objUrl = URL.createObjectURL(blob);
  try {
    return await import(/* @vite-ignore */ objUrl);
  } finally {
    URL.revokeObjectURL(objUrl);
  }
}

function _reportPluginError(customer_code, stage, err) {
  fetch('/api/v1/plugins/runtime-error', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      customer_code,
      stage,
      error_message: String(err?.message || err),
      stack: err?.stack || '',
      ts: new Date().toISOString(),
    }),
  }).catch(() => {});  // 上报失败也别炸
}
```

### 4.6 main.js 接入

```js
// frontend/src/main.js (在 design 04 改造基础上, 再加一段)
(async () => {
  // ===== 早期: tier 1 =====
  let pluginI18n = {};
  try {
    const r = await loadActivePluginTheme();
    pluginI18n = r.i18nMessages;
  } catch (e) { console.warn('[Boot] 主题加载失败:', e); }

  // ===== createApp + 插件 + i18n =====
  const app = createApp(App);
  // errorHandler / use(pinia, router, ElementPlus) 不变
  // ...

  // ===== 合并插件 i18n =====
  for (const [locale, msgs] of Object.entries(pluginI18n)) {
    i18n.global.mergeLocaleMessage(locale, msgs);
  }

  // ===== NEW: tier 2 UI 加载 =====
  await loadActivePluginUI({ app, router, pinia, i18n });

  // ===== 挂载 =====
  app.mount('#app');
})();
```

---

## 五、动态注册路由

### 5.1 vue-router addRoute 用法

```js
ctx.router.addRoute('Layout', {
  path: 'plugin/acme/report',         // 注意没有前导 /
  name: 'plugin-acme-report',
  component: AcmeReport,
  meta: { plugin: 'acme', menu: { ... } },
});
```

> 注：'Layout' 是父路由的 name。我们要给 router/index.js 中第二个 `{ path: '/', component: Layout, ... }` 加一个 `name: 'Layout'`。

### 5.2 v3.7 router/index.js 改造

```js
// frontend/src/router/index.js (v3.7 改造)
const routes = [
  {
    path: '/activation',
    name: 'Activation',
    component: () => import('@/views/Activation/index.vue'),
  },
  {
    path: '/',
    name: 'Layout',                    // ← NEW: 让 addRoute 找得到父
    component: Layout,
    redirect: '/monitor',
    children: [
      // ... 现有 8 个 children 不变 ...
    ]
  }
];
```

### 5.3 路由命名约定（强约束）

| 资源 | 命名空间 |
|---|---|
| 路由 path | `plugin/{cc}/{path}`（自动拼装） |
| 路由 name | `plugin-{cc}-{name}` |
| 完整 URL | `/#/plugin/acme/report`（hash mode） |

加载器在 `registry.routes.register` 中**自动拼装**，插件作者写**相对**的 path / name。

### 5.4 路由级权限守卫（升级 hidden_menus）

design 04 §3.4 的 hidden_menus 只是 CSS 隐藏（用户输入 URL 仍能进）。tier 2 提供**真正的访问控制**：

```js
// frontend/src/router/index.js (v3.7 守卫扩展)
router.beforeEach(async (to, from) => {
  // ===== 现有 license 检查 (略) =====

  // ===== NEW: 插件 hidden_menus 强守卫 =====
  const manifest = window.__pluginManifest;
  if (manifest?.frontend?.hidden_menus?.length) {
    const blocked = manifest.frontend.hidden_menus.filter(p => to.path.startsWith(p));
    if (blocked.length) {
      console.warn(`[Router] 插件隐藏路径被访问: ${to.path}, 重定向到 /monitor`);
      return { path: '/monitor' };
    }
  }

  // ===== NEW: 插件路由的 license 校验 =====
  // 插件路由也走 license check, 已被现有逻辑覆盖
});
```

**策略**：
- hidden_menus 拦截：用 `startsWith` 而非 `===`，因为子路径也要拦
- 插件自家路由（`/plugin/acme/*`）和主程序路由一样走 license 校验
- 路由名命中 `plugin-{cc}-*` 的 → 在 `REMEMBERABLE_NAMES` 自动允许（见下）

### 5.5 REMEMBERABLE_NAMES 扩展

现有：

```js
const REMEMBERABLE_NAMES = new Set([
  'Monitor', 'Project', 'Model', 'Data', 'Source', 'Settings', 'Alarm', 'MES'
]);
```

改成：

```js
function isRememberable(name) {
  if (REMEMBERABLE_NAMES.has(name)) return true;
  // 插件路由 (plugin-{cc}-*) 全部可记忆
  if (typeof name === 'string' && name.startsWith('plugin-')) return true;
  return false;
}

router.afterEach((to) => {
  if (to.name && isRememberable(to.name)) {
    try { localStorage.setItem(LAST_ROUTE_KEY, to.fullPath); } catch (_) {}
  }
});
```

### 5.6 路由组件错误隔离（wrapPluginView）

`registry.routes.register` 在挂载前用 `wrapPluginView` 包一层：

```js
// frontend/src/plugin/wrapView.js
import { defineComponent, h, ref, onErrorCaptured } from 'vue';

export function wrapPluginView(Component, customer_code, route_name) {
  return defineComponent({
    name: `PluginViewBoundary-${route_name}`,
    setup() {
      const error = ref(null);

      onErrorCaptured((err, vm, info) => {
        console.error(`[Plugin View] ${route_name} 渲染错误`, err, info);
        error.value = err;
        // 上报后端
        fetch('/api/v1/plugins/runtime-error', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            customer_code,
            stage: 'render',
            route: route_name,
            error_message: String(err?.message),
            stack: err?.stack,
            info,
            ts: new Date().toISOString(),
          }),
        }).catch(() => {});
        return false;  // ← 关键: 阻止错误冒泡到主程序 Vue errorHandler
      });

      return () => {
        if (error.value) {
          return h('div', { class: 'plugin-view-error' }, [
            h('div', { class: 'icon' }, '⚠'),
            h('div', { class: 'msg' }, `插件 [${customer_code}] 此页面出错`),
            h('div', { class: 'hint' }, '请联系厂商或在 Settings 重启插件'),
            h('details', [
              h('summary', '错误详情'),
              h('pre', { style: 'white-space: pre-wrap' }, String(error.value?.stack || error.value)),
            ]),
          ]);
        }
        return h(Component);
      };
    },
  });
}
```

**效果**：
- 插件组件抛错 → 显示友好错误页 + 错误详情
- 主程序的 `app.config.errorHandler` **不被触发**（onErrorCaptured 返回 false 阻止冒泡）
- 主程序其他页面继续正常工作

---

## 六、Layout 改造（菜单生成）

### 6.1 现状问题

`frontend/src/layout/index.vue:18~43` 菜单是 8 个硬编码 `<router-link>`：

```vue
<router-link to="/monitor">{{ $t('menu.monitor') }}</router-link>
<router-link to="/project">{{ $t('menu.project') }}</router-link>
<router-link to="/model">{{ $t('menu.model') }}</router-link>
... 等等 ...
```

要支持插件菜单，**必须改成 v-for 渲染**。

### 6.2 v3.7 改造方案

```vue
<!-- frontend/src/layout/index.vue (v3.7 改造) -->
<nav class="flex-1 mt-4">
  <router-link
    v-for="item in displayMenuItems"
    :key="item.name"
    :to="item.path"
    class="nav-item"
    :class="{
      'nav-disabled': item.disableWhenDetecting && systemStore.isDetecting,
      'nav-plugin': item.plugin,
    }"
    @click.capture="handleNav(item, $event)"
  >
    <el-icon class="mr-2"><component :is="item.icon" /></el-icon>
    {{ resolveLabel(item.label) }}
  </router-link>
</nav>

<script setup>
import { ref, computed, onMounted } from 'vue';
import { useI18n } from 'vue-i18n';
import { Monitor, Folder, Cpu, DataLine, Setting, VideoCamera, Bell, Tickets, Calendar, Document } from '@element-plus/icons-vue';

const { t, te } = useI18n();

// 主程序内置菜单 (静态)
const BUILTIN_MENUS = [
  { path: '/monitor',  name: 'Monitor',  label: 'menu.monitor',  icon: 'Monitor',     order: 10,  disableWhenDetecting: false },
  { path: '/project',  name: 'Project',  label: 'menu.project',  icon: 'Folder',      order: 20,  disableWhenDetecting: true },
  { path: '/model',    name: 'Model',    label: 'menu.model',    icon: 'Cpu',         order: 30,  disableWhenDetecting: true },
  { path: '/source',   name: 'Source',   label: 'menu.source',   icon: 'VideoCamera', order: 40,  disableWhenDetecting: true },
  { path: '/data',     name: 'Data',     label: 'menu.data',     icon: 'DataLine',    order: 50,  disableWhenDetecting: true },
  { path: '/mes',      name: 'MES',      label: 'menu.mes',      icon: 'Tickets',     order: 60,  disableWhenDetecting: true },
  { path: '/alarm',    name: 'Alarm',    label: 'menu.alarm',    icon: 'Bell',        order: 70,  disableWhenDetecting: true },
  { path: '/settings', name: 'Settings', label: 'menu.settings', icon: 'Setting',     order: 80,  disableWhenDetecting: true },
];

const pluginMenus = ref([]);
onMounted(() => {
  // 从 window.__pluginRegistry 取插件菜单
  const reg = window.__pluginRegistry;
  if (reg) {
    pluginMenus.value = reg.menus.list().map(m => ({
      ...m,
      disableWhenDetecting: false,  // 插件菜单默认不锁
    }));
  }
});

const hiddenMenus = computed(() => {
  return new Set(window.__pluginManifest?.frontend?.hidden_menus || []);
});

const displayMenuItems = computed(() => {
  const all = [...BUILTIN_MENUS, ...pluginMenus.value];
  return all
    .filter(m => !hiddenMenus.value.has(m.path))
    .sort((a, b) => a.order - b.order);
});

function resolveLabel(label) {
  // 如果 label 是 i18n key 就翻译, 否则直接显示
  if (te(label)) return t(label);
  return label;
}

function handleNav(item, e) {
  if (item.disableWhenDetecting && systemStore.isDetecting) {
    e.preventDefault();
    e.stopPropagation();
    ElMessage.warning('检测运行中，请先停止检测再切换页面');
  } else {
    sidebarOpen.value = false;
  }
}
</script>
```

**改造影响**：
- BUILTIN_MENUS 数组成为唯一菜单源
- 现有 i18n key 一致（`menu.monitor` 等）
- **新加 i18n key**：`menu.source` / `menu.mes` / `menu.alarm` 之前是硬编码中文（"输入源设置" "MES 管理" "报警设置"），改造时**必须补到 zh-CN.js / zh-TW.js / en-US.js / ja-JP.js / ko-KR.js 5 个 locale**
- 顺序由 order 控制，**不再依赖书写顺序**

### 6.3 i18n 补 key（v3.7 改造清单）

```js
// frontend/src/locales/zh-CN.js (补)
menu: {
  monitor: '检测中心',
  project: '项目管理',
  model: '模型仓库',
  source: '输入源设置',   // ← NEW
  data: '数据管理',
  mes: 'MES 管理',         // ← NEW
  alarm: '报警设置',       // ← NEW
  settings: '显示设置',
},
```

5 个 locale 都要补，工作量约 30 分钟。

### 6.4 与 BottomBar / Navbar 的协同

`frontend/src/layout/Navbar.vue` 和 `BottomBar.vue` 当前不参与菜单。tier 2 也**不动这两个组件**。

> 未来 tier 2 增强：插件可以注入 Navbar / BottomBar 的"slot"。当前不做。

---

## 七、Pinia store 动态注册

### 7.1 store 写法

插件 store 写法和主程序一致，只多一个**id 命名空间约束**：

```js
// plugins/acme/frontend/src/stores/shift.js
import { defineStore } from 'pinia';

export const useShiftStore = defineStore('plugin-acme-shift', {
  state: () => ({
    currentShift: null,
    history: [],
  }),
  getters: {
    isActive: (state) => state.currentShift !== null,
  },
  actions: {
    async loadCurrentShift() {
      const resp = await fetch('/api/v1/plugins/acme/shifts/current');
      this.currentShift = await resp.json();
    },
  },
});

export default useShiftStore;
```

### 7.2 注册流程

`registry.stores.register('plugin-acme-shift', useShiftStore)` 内部：

1. 校验 id 命名空间
2. 不需要"挂"到 pinia——pinia 是惰性创建，组件里 `useShiftStore()` 才生效
3. 但要**eager call 一次**让插件能直接访问 `useShiftStore().state`，避免组件还没挂载之前 register 内部就要操作 store 报"必须在 setup 内调用"错

```js
register(id, defineStoreFn) {
  if (!id.startsWith(`plugin-${customer_code}-`)) {
    throw new Error(...);
  }
  try {
    // pinia 已通过 ctx 提供, defineStoreFn 内部会引用 active pinia
    const useStore = defineStoreFn;  // 已经是 use* 函数
    const store = useStore();         // eager 创建一次
    registeredStores.push(id);
    console.log(`[Plugin Store] ✓ ${id}`);
  } catch (e) {
    throw new Error(`store ${id} 初始化失败: ${e.message}`);
  }
},
```

### 7.3 跨插件 / 主程序 store 隔离

**主程序 store 是只读暴露**（通过 `ctx.host.systemStore`）。插件**不应直接修改**主程序 store——这是约定，没有强代码层防御（pinia 没法做 readonly）。

**插件 store 仅插件自家用**——主程序也不读插件 store。

---

## 八、错误隔离层次

### 8.1 三层防护

```
┌──────────────────────────────────────────────┐
│ Layer 1: 加载阶段                            │
│  loadActivePluginUI 内 try/catch             │
│  失败 → 主程序继续, 插件功能失效              │
├──────────────────────────────────────────────┤
│ Layer 2: register 阶段                       │
│  plugin.register(ctx) 内 try/catch           │
│  失败 → 主程序继续, 部分注册可能成功          │
│  (这一层不太干净, 后台手动清理)               │
├──────────────────────────────────────────────┤
│ Layer 3: 渲染阶段                            │
│  wrapPluginView 内 onErrorCaptured           │
│  失败 → 该路由组件显示错误页, 其他不受影响    │
└──────────────────────────────────────────────┘
```

### 8.2 上报通道

`/api/v1/plugins/runtime-error` 是**前端 → 后端**单向上报，写入 `plugin_audit_log`（design 03 §2.3）：

```python
# backend/api/plugins.py
from datetime import datetime
from backend.models.plugin_models import PluginAuditLog

@router.post("/runtime-error")
def report_runtime_error(
    customer_code: str,
    stage: str,
    error_message: str,
    stack: str = "",
    route: str | None = None,
    db: Session = Depends(get_db),
):
    db.add(PluginAuditLog(
        customer_code=customer_code,
        event_type="frontend_error",
        event_detail={
            "stage": stage,
            "error_message": error_message,
            "stack": stack[:5000],
            "route": route,
        },
    ))
    db.commit()
    return {"ok": True}
```

> 后端**不会因为前端上报错误而修改 plugins.state**——保留人工干预空间。

---

## 九、CSS 样式作用域

### 9.1 插件 .vue SFC `<style scoped>`

vue SFC 的 `<style scoped>` 编译成 `[data-v-xxx]` 选择器，**天然不污染主程序**。

### 9.2 全局样式（不 scoped）

如果插件作者写了 `<style>`（不 scoped），**vite build 会合并到 entry.css**。`registry` 加载 entry.js 时**也要加载 entry.css**：

```js
async function _fetchAndImportPlugin(entryPath) {
  // ... import JS ...

  // CSS sibling: entry.js → entry.css
  const cssPath = entryPath.replace(/\.js$/, '.css');
  const cssUrl = `/api/v1/plugins/active/assets/${cssPath}`;
  try {
    const cssResp = await fetch(cssUrl);
    if (cssResp.ok) {
      const link = document.createElement('link');
      link.rel = 'stylesheet';
      link.href = cssUrl;
      link.id = `plugin-${cc}-entry-css`;
      document.head.appendChild(link);
    }
  } catch (e) {
    // CSS 可选, 加载失败不阻止
  }
}
```

> ⚠️ 全局 CSS 无 scoped → 容易污染主程序。建议**插件作者**用 `<style scoped>` 或 `.plugin-{cc}-*` 类名前缀。
> design 07 打包工具会**警告**未 scoped 的全局样式。

---

## 十、打包工具（design 07 概要）

### 10.1 vite.lib.config.js 模板

每个 tier 2/3 插件应在 `plugins/{cc}/frontend/` 内自带 vite 配置：

```js
// plugins/acme/frontend/vite.lib.config.js
import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';
import path from 'path';

export default defineConfig({
  plugins: [vue()],
  build: {
    outDir: 'dist',
    lib: {
      entry: path.resolve(__dirname, 'src/index.js'),
      formats: ['es'],
      fileName: () => 'entry.js',
    },
    rollupOptions: {
      external: [
        'vue', 'vue-router', 'pinia', 'vue-i18n',
        'element-plus', '@element-plus/icons-vue',
        'echarts', 'axios',
      ],
      output: { format: 'es' },
    },
    cssCodeSplit: false,
    sourcemap: false,
  },
});
```

### 10.2 打包流程

```bash
cd plugins/acme/frontend
npm install
npx vite build --config vite.lib.config.js
# 产出: plugins/acme/frontend/dist/entry.js + entry.css
```

manifest 中：

```json
"frontend": {
  "entry": "frontend/dist/entry.js"
}
```

### 10.3 打包脚本检查

```python
# scripts/sign-plugin.py 中（design 07 详细）
def check_frontend_bundle(plugin_dir):
    entry_js = plugin_dir / "frontend/dist/entry.js"
    if not entry_js.exists():
        raise BuildError("frontend/dist/entry.js 不存在, 请先 vite build")

    code = entry_js.read_text(encoding='utf-8')

    # 校验 ESM 格式
    if 'export default' not in code and 'export{' not in code:
        raise BuildError("entry.js 不是 ESM (缺 export)")

    # 警告: 大小过大
    size = entry_js.stat().st_size
    if size > 2 * 1024 * 1024:
        print(f"[WARN] entry.js 大小 {size/1024:.0f} KB, 建议 < 2MB")

    # 校验: vendor externals 是否正确没被打入
    BAD_PATTERNS = [
        r'\bcreateApp\b.*from\s+[\'"]vue[\'"]',  # 主程序 createApp 不该被插件引用
    ]
    # (实际靠 rollupOptions.external 保证, 这里仅示意检查)
```

---

## 十一、完整 ACME 档位 2 示例

```
plugins/acme/
├── plugin.json                  # tier=2, frontend.routes×2 + stores×1
├── signature.bin
├── frontend/
│   ├── package.json             # vite + vue + element-plus(devDep)
│   ├── vite.lib.config.js
│   ├── src/
│   │   ├── index.js             # export default { register }
│   │   ├── views/
│   │   │   ├── AcmeReport.vue
│   │   │   └── AcmeShift.vue
│   │   ├── stores/
│   │   │   └── shift.js
│   │   └── api/
│   │       └── shift.js         # fetch wrapper
│   ├── i18n/
│   │   └── zh-CN.json
│   ├── theme.css
│   └── dist/                    # vite build 产物 (打包时生成)
│       ├── entry.js
│       └── entry.css
└── README.md
```

完整 `index.js`：

```js
// plugins/acme/frontend/src/index.js
import AcmeReport from './views/AcmeReport.vue';
import AcmeShift from './views/AcmeShift.vue';
import { useShiftStore } from './stores/shift';

export default {
  async register(ctx) {
    const { registry } = ctx;

    registry.stores.register('plugin-acme-shift', useShiftStore);

    registry.routes.register({
      path: 'report',
      name: 'report',
      component: AcmeReport,
      menu: { label: 'plugin.acme.menu.report', icon: 'DataLine', order: 100 },
    });

    registry.routes.register({
      path: 'shift',
      name: 'shift',
      component: AcmeShift,
      menu: { label: 'plugin.acme.menu.shift', icon: 'Calendar', order: 110 },
    });

    console.log('[Plugin ACME] 注册完成');
  },
};
```

---

## 十二、测试策略

### 12.1 单元测试

| 测试 | 覆盖 |
|---|---|
| `createPluginRegistry`：store id 命名空间校验 | 1 |
| `registry.routes.register`：path/name 命名空间校验 | 2 |
| `registry.routes.register`：自动拼装 fullName / fullPath | 1 |
| `wrapPluginView`：渲染错误展示 fallback | 1 |
| `wrapPluginView`：错误不冒泡到 app errorHandler | 1 |
| `loadActivePluginUI`：tier=1 时跳过 | 1 |
| `loadActivePluginUI`：fetch 失败 fallback | 1 |
| `loadActivePluginUI`：register 抛错 fallback | 1 |
| `_fetchAndImportPlugin`：blob URL revoke | 1 |
| Layout `displayMenuItems`：hidden_menus 过滤 | 1 |
| Layout `displayMenuItems`：order 排序 | 1 |
| `isRememberable`：plugin-* 路由可记忆 | 1 |

### 12.2 集成测试

| 场景 | 期望 |
|---|---|
| 加载 tier=1 主题包 | UI 不变 + 主题生效 |
| 加载 tier=2 UI 插件（成功） | 菜单加 2 项 + 路由可访问 |
| 加载 tier=2（fetch 404） | 菜单不变 + console error + Settings 显示 |
| 加载 tier=2（register 抛错） | 菜单不变 + audit_log 写入 |
| 加载 tier=2（视图组件渲染抛错） | 错误 fallback 页 + 主程序其他页可用 |
| URL 直访 hidden_menu 路径 | 重定向到 /monitor |
| 重启后冷启动恢复到插件路由 | localStorage 记得，正常恢复 |

### 12.3 跨平台测试

| 环境 | 测试点 |
|---|---|
| 开发模式 vite dev (localhost:5174) | 正常 |
| 生产 Electron file:// | Blob URL + importmap 都正常 |
| Electron 32 | importmap 原生支持 |
| Windows 10 | OK |
| Windows 11 | OK |

### 12.4 性能基准

| 场景 | 期望 |
|---|---|
| 加载 entry.js (200 KB) | ≤ 200 ms |
| 加载 entry.js (1 MB) | ≤ 500 ms |
| register 全部完成 | ≤ 100 ms |
| 总额外启动开销 (tier 2) | ≤ 800 ms |

---

## 十三、与 design 04/06 的协作

### 13.1 与 design 04 共用

- manifest 拉取（design 04 §3.1.4）
- `window.__pluginManifest` 共用
- i18n 合并（design 04 §3.3.2）
- 主题 CSS（design 04 §3.1.4）
- assets 端点（design 04 §4.2）

### 13.2 与 design 06 协作

档位 3 = 档位 2 + 后端能力。design 06 的后端 register_plugin 完成后，前端 register 调用顺序：

```
1. 后端 register_plugin → router/adapter/hook 就绪
2. 前端 loadActivePluginUI → fetch /api/v1/plugins/active/assets/frontend/dist/entry.js
3. 前端 register → 调用 ctx.host 时, 后端 API 已就绪
```

### 13.3 接口冻结

为避免下游 break，本文章的 ctx / registry / host 接口在 manifest_version=1 内**冻结**。任何升级需要 manifest_version=2。

---

## 十四、待确认与开放点

| 议题 | 当前态度 |
|---|---|
| **U1** vendor bundle 工程化 | 推荐 importmap，但要 Electron 32+ 验证 |
| **U2** vue/element-plus 版本对齐 | 主程序与插件**必须**同版本 → 在 manifest `requires.main_version_min` 强校验 |
| **U3** 是否给 Navbar/BottomBar 插槽 | v3.7 不做 |
| **U4** 是否允许覆盖主程序路由（如替换 Data 视图） | v3.7 不做（留 v4.0） |
| **U5** 多个插件共存 | 当前 design 00 单插件激活，不做。v4.0 评估 |
| **U6** 是否动态卸载（不重启）| 不做（vue-i18n 限制 + addRoute 反向 removeRoute 复杂） |
| **U7** 插件路由懒加载 | 默认不（直接 component），未来若 entry.js 太大可分模块 |
| **U8** 插件菜单是否支持子菜单（嵌套） | v3.7 不做（一层菜单足够） |

---

## 十五、本文决策摘要

| 决策点 | 值 |
|---|---|
| 加载时机 | createApp + use 之后, mount 之前 |
| 模块加载方式 | fetch + Blob URL + dynamic import |
| Vendor 共享方式 | importmap（推荐）/ window.__pluginVendor（备选） |
| 插件入口契约 | `export default { register(ctx), unregister?(ctx) }` |
| ctx 字段 | `{app, router, pinia, i18n, customer_code, registry, host}` |
| host 暴露的最小 API | systemStore / projectStore / navigate / electron / getConfig / setConfig |
| 路由命名 | path = `plugin/{cc}/{path}` / name = `plugin-{cc}-{name}` |
| 路由父挂载点 | `Layout`（要给 `/` 路由加 `name: 'Layout'`） |
| store id 命名 | `plugin-{cc}-*` |
| 菜单源 | BUILTIN_MENUS + window.__pluginRegistry.menus |
| 隐藏菜单守卫 | router beforeEach + startsWith 拦 URL |
| 错误隔离 | wrapPluginView + onErrorCaptured(返回 false) |
| 错误上报 | POST /api/v1/plugins/runtime-error → plugin_audit_log |
| 路由错误 fallback UI | "插件 [{cc}] 此页面出错 + 详情折叠" |
| 加载超时 | 10 秒 |
| 打包格式 | ESM（vite lib --formats=es） |
| 主程序双 build | dist/index.html + dist/__vendor/*.esm.js |
| Layout 改造工作量 | 1 PR（菜单数据驱动 + 5 locale 加 3 key） |
| router 改造工作量 | 1 PR（加 name=Layout + 守卫扩展） |
| main.js 改造工作量 | 1 PR（async IIFE + tier 2 加载步骤） |

---

**本文最后更新**：2026-05-08
**事实校验**：基于 `frontend/src/router/index.js` / `vite.config.js` / `package.json` / `main.js` / `layout/index.vue` 现状
**下一文档**：design/06_tier3_fullstack.md（FastAPI router 自动注册 + 错误隔离）
