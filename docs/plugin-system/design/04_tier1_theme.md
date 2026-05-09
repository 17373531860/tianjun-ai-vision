# 04 — 档位 1：主题包加载器（Theme Plugin）

> 适用版本：基于 `feat/plugin-config` v0.1
> 本文目的：把档位 1（纯主题包）从"manifest 写法"到"运行时注入"全链路定义到**可复制运行**的程度。
>
> 阅读前置：design 01 §3.5（frontend.theme/i18n 字段）、design 03 §二（plugins 表）、inventory/01（前端启动时序）。
>
> 配套：design 06（后端加载器）、design 07（打包工具）。

---

## 一、范围与目标

### 1.1 档位 1 能做什么 / 不能做什么

| 维度 | 能做 | 不能做 |
|---|---|---|
| 颜色 / 风格 | ✅ CSS 变量覆盖 | ❌ 重写 Element Plus 整套组件 |
| Logo / Favicon / App Title | ✅ 运行时替换 | ❌ 替换 index.html 静态结构 |
| 启动 Splash | ✅ 静态图替换（Electron） | ❌ 自定义动效 |
| 多语言 | ✅ 增量合并 / 覆盖 key | ❌ 新加 locale（zh-CN/zh-TW/en-US/ja-JP/ko-KR 5 种之外） |
| 隐藏菜单 | ✅ CSS `display:none`（白名单 path） | ❌ 隐藏 `/monitor`（核心视图） |
| 字体 | ✅ 自带 woff2 + 注册到 CSS | ❌ 改 html font-size 自适应算法 |
| 加新菜单/路由 | ❌ → 升级到档位 2 | — |
| 加 Vue 组件/Pinia store | ❌ → 升级到档位 2 | — |
| 后端能力 | ❌ → 升级到档位 3 | — |

### 1.2 典型用例

| 场景 | 复杂度 | manifest 字段 |
|---|---|---|
| ACME 公司白标（黄色 + ACME logo + "ACME 视觉检测系统"标题） | 1~2 天 | `theme.{css,logo,favicon,app_title,css_variables}` |
| 单机部署版（隐藏 `/cluster` `/mes/cluster`）+ 简化标题 | 0.5 天 | `theme.app_title` + `hidden_menus` |
| 海外客户（默认 en-US，覆盖部分中文术语为英文） | 1 天 | `i18n.en-US` + `theme.app_title` |
| 完全本地化 ja-JP（覆盖所有菜单项 + 标题） | 2~3 天 | `i18n.ja-JP` 完整覆盖 |

### 1.3 核心约束（与现有代码对齐）

- 现有 `frontend/src/style.css` 大量颜色**硬编码**（`#06b6d4` `#1e293b` `#0f172a` 等）→ tier 1 要在不改这些硬编码的前提下覆盖：用**更高 CSS 选择器优先级**或用 `!important` 注入
- 现有 `frontend/src/layout/index.vue` 菜单是**硬编码**`<router-link>` → tier 1 不动 layout，用 **CSS 隐藏**实现"隐藏菜单"
- 现有 i18n 在 `main.js` 静态 import 5 种 locale → tier 1 用 `mergeLocaleMessage` 增量合并，**在 createI18n 之后、mount 之前**

---

## 二、整体架构与运行时序

### 2.1 前端启动时序（含主题加载）

```
1. index.html 加载 main.js
2. (NEW) await loadActivePluginTheme()
   ├─ GET /api/v1/plugins/active/manifest   ← 拿当前激活插件 manifest
   ├─ 没插件激活 → 跳过, 走默认主题
   ├─ 插件 tier ≥ 1 → 处理 frontend.theme
   ├─ 插件 hidden_menus → 注入隐藏 CSS
   ├─ 插件 i18n → 拉每个 locale 文件
   ├─ 插件 favicon → 替换 <link rel=icon>
   └─ 插件 app_title → 替换 document.title
3. createApp(App)
4. createI18n(messages={zhCN, zhTW, enUS, jaJP, koKR})
5. (NEW) for locale in plugin_messages:
       i18n.global.mergeLocaleMessage(locale, plugin_messages[locale])
6. app.use(pinia / router / ElementPlus / i18n / icons)
7. app.mount('#app')
   ↑ 此时主题 CSS / logo / i18n 全部就位, 无 FOUC
```

> **"在 createApp 之前先 await"** 是关键。
> 现有 `main.js` 第 56~85 行没有 await，我们要在第 56 行前**插入一段 async IIFE 等待主题准备好**。

### 2.2 后端职责

```
GET /api/v1/plugins/active/manifest
    → 200 {manifest_json}  / 404 {无激活插件}

GET /api/v1/plugins/active/assets/{path:path}
    → StaticFiles 代理到 plugins/{cc}/

GET /api/v1/plugins/active/i18n/{locale}
    → 200 JSON  / 404
```

后端只做"读 plugins 表 → 路径校验 → 返回文件"——零业务逻辑。

---

## 三、主题资源四大件

### 3.1 CSS 变量覆盖（color tokens）

#### 3.1.1 主题变量 contract（主程序声明的可覆盖变量）

> 现有 `style.css` 用了一组 `--el-*` Element Plus 变量，但没有自家"语义层变量"。我们**要补一组**，让插件可以**只覆盖语义层**而不动 Element Plus。

**新增 `frontend/src/assets/css/theme-tokens.css`**（v3.7 加）：

```css
/* 主题令牌 — 任何颜色/尺寸都从这里取
 * 插件通过覆盖 :root 来变主题
 */
:root {
  /* === 品牌色 === */
  --tj-primary: #06b6d4;          /* 主色 (cyan-500) */
  --tj-primary-hover: #0891b2;
  --tj-primary-active: #0e7490;
  --tj-primary-rgb: 6, 182, 212;  /* alpha 透明度用 */

  --tj-secondary: #94a3b8;
  --tj-accent: #facc15;            /* 警告色 */

  /* === 状态色 === */
  --tj-success: #22c55e;
  --tj-warning: #facc15;
  --tj-danger:  #ef4444;
  --tj-info:    #06b6d4;

  /* === 背景层级 === */
  --tj-bg-base:    #0f172a;        /* 最底色 */
  --tj-bg-panel:   #1e293b;        /* 面板/卡片 */
  --tj-bg-elev:    #334155;        /* 浮起层 */
  --tj-bg-overlay: rgba(15, 23, 42, 0.8);

  /* === 文字层级 === */
  --tj-text-primary:   #e2e8f0;
  --tj-text-secondary: #cbd5e1;
  --tj-text-muted:     #94a3b8;
  --tj-text-disabled:  #64748b;

  /* === 边框 === */
  --tj-border-base:  #334155;
  --tj-border-light: #1e293b;
  --tj-border-hover: var(--tj-primary);

  /* === 字号 / 间距 === */
  --tj-font-family-base: 'Inter', 'PingFang SC', 'Microsoft YaHei', sans-serif;
  --tj-radius-sm: 0.25rem;
  --tj-radius-md: 0.375rem;
  --tj-radius-lg: 0.5rem;

  /* === Logo / Favicon / 标题 === */
  --tj-app-title: '天骏 AI 视觉检测系统';
  --tj-logo-url:    url('/static/default-logo.svg');
  --tj-favicon-url: url('/static/default-favicon.png');

  /* === 同步到 Element Plus（让 EP 跟随品牌色） === */
  --el-color-primary:    var(--tj-primary);
  --el-color-primary-light-3: rgba(var(--tj-primary-rgb), 0.7);
  --el-color-primary-light-5: rgba(var(--tj-primary-rgb), 0.5);
  --el-color-primary-light-7: rgba(var(--tj-primary-rgb), 0.3);
  --el-color-primary-light-9: rgba(var(--tj-primary-rgb), 0.1);
  --el-color-primary-dark-2:  var(--tj-primary-active);
  --el-color-success: var(--tj-success);
  --el-color-warning: var(--tj-warning);
  --el-color-danger:  var(--tj-danger);
  --el-color-info:    var(--tj-info);
}
```

> ⚠️ **这一步对现有 style.css 是渐进重构**：
> - **第 1 阶段 (v3.7 发版前)**：新增 theme-tokens.css，但不动现有 style.css 硬编码（**插件能改的颜色有限**——只能改 Element Plus 跟随的部分）
> - **第 2 阶段 (v3.8+)**：把 style.css 里硬编码 `#06b6d4` `#1e293b` 等渐进替换成 `var(--tj-primary)` `var(--tj-bg-panel)` 等。现有功能不受影响
> - **第 3 阶段 (v4.0)**：插件可以彻底定制主题

> design 00 第十节 8 milestones 中"M3 档位 1 加载器"包含的工作就是**第 1 阶段 + 注入机制**，第 2/3 阶段是后续 update 任务，不阻塞插件系统首发。

#### 3.1.2 客户主题 CSS 写法（manifest.frontend.theme.css）

```css
/* plugins/acme/frontend/theme.css */
:root {
  --tj-primary: #FFEB3B;
  --tj-primary-hover: #FBC02D;
  --tj-primary-active: #F57F17;
  --tj-primary-rgb: 255, 235, 59;

  --tj-bg-base:  #1A1A1A;
  --tj-bg-panel: #212121;
  --tj-bg-elev:  #2D2D2D;

  --tj-app-title: 'ACME 视觉检测系统';
}

/* 客户也可以自定义局部样式 */
.acme-corner-watermark {
  position: fixed;
  bottom: 1rem;
  right: 1rem;
  background: rgba(255, 235, 59, 0.1);
  padding: 0.5rem 1rem;
  border-radius: 0.25rem;
  color: var(--tj-primary);
}
```

**约束**：
- 客户 CSS 只能用 **`:root` 选择器**或以 `.plugin-acme-` 前缀的类（design 04 §五命名空间）
- 不允许用 `*` 全局选择器（性能 + 安全）
- 不允许 `body { ... }` 直接覆盖（用 `:root`）
- 文件大小 ≤ 200 KB（防 DoS）

签名工具会在 §五打包时校验。

#### 3.1.3 css_variables inline 写法（轻量场景）

如果客户只想改 2~3 个颜色，可以**不用 css 文件**，直接写 manifest：

```json
"frontend": {
  "theme": {
    "css_variables": {
      "--tj-primary": "#FFEB3B",
      "--tj-app-title": "'ACME 视觉检测系统'"
    }
  }
}
```

加载器把它转成 `:root { ... }` 注入 `<style>` 标签。

#### 3.1.4 注入实现

新增 `frontend/src/plugin/themeLoader.js`：

```js
// frontend/src/plugin/themeLoader.js

/**
 * 加载激活插件的主题资源
 * 必须在 createApp 之前 await
 *
 * @returns {Promise<{i18nMessages: object, manifestLoaded: boolean}>}
 */
export async function loadActivePluginTheme() {
  const result = { i18nMessages: {}, manifestLoaded: false };

  let manifest;
  try {
    const resp = await fetch('/api/v1/plugins/active/manifest', {
      cache: 'no-store',
    });
    if (resp.status === 404) {
      console.log('[Theme] 无激活插件, 走默认主题');
      return result;
    }
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    manifest = await resp.json();
  } catch (e) {
    console.warn('[Theme] 拉取插件 manifest 失败, 走默认主题', e);
    return result;  // 容错: 失败也别阻塞主程序
  }

  result.manifestLoaded = true;
  const cc = manifest.customer_code;
  const theme = manifest.frontend?.theme;

  // === 1) CSS 变量 inline ===
  if (theme?.css_variables) {
    injectCssVariables(cc, theme.css_variables);
  }

  // === 2) 主题 CSS 文件 ===
  if (theme?.css) {
    await injectCssFile(cc, theme.css);
  }

  // === 3) Logo / Favicon ===
  if (theme?.favicon) {
    setFavicon(`/api/v1/plugins/active/assets/${theme.favicon}`);
  }

  // === 4) App Title ===
  if (theme?.app_title) {
    document.title = theme.app_title;
    // 也写到 CSS 变量, layout 模板用 var() 引用
    document.documentElement.style.setProperty(
      '--tj-app-title', JSON.stringify(theme.app_title)
    );
  }

  // === 5) Logo URL 写到 CSS 变量, 让 layout 通过 var(--tj-logo-url) 引用 ===
  if (theme?.logo) {
    const url = `/api/v1/plugins/active/assets/${theme.logo}`;
    document.documentElement.style.setProperty(
      '--tj-logo-url', `url("${url}")`
    );
    // 同时给 <img src> 用的: 暴露成 window.__pluginLogoUrl
    window.__pluginLogoUrl = url;
  }

  // === 6) Hidden Menus ===
  if (manifest.frontend?.hidden_menus?.length) {
    injectHiddenMenuCss(cc, manifest.frontend.hidden_menus);
  }

  // === 7) i18n ===
  if (manifest.frontend?.i18n) {
    result.i18nMessages = await loadI18nMessages(manifest.frontend.i18n);
  }

  return result;
}

function injectCssVariables(cc, vars) {
  const cssText = Object.entries(vars)
    .map(([k, v]) => `  ${k}: ${v};`)
    .join('\n');
  const styleEl = document.createElement('style');
  styleEl.id = `plugin-${cc}-vars`;
  styleEl.textContent = `:root {\n${cssText}\n}`;
  document.head.appendChild(styleEl);
}

async function injectCssFile(cc, relPath) {
  const url = `/api/v1/plugins/active/assets/${relPath}`;
  // 用 link 标签而非 fetch+inline, 利于 DevTools 调试
  const link = document.createElement('link');
  link.id = `plugin-${cc}-css`;
  link.rel = 'stylesheet';
  link.href = url;
  // 等 link 加载完才 resolve, 防止 mount 后 FOUC
  await new Promise((resolve) => {
    link.onload = resolve;
    link.onerror = () => {
      console.warn(`[Theme] CSS 加载失败: ${url}`);
      resolve();  // 失败也走 (跟主程序一起跑)
    };
    document.head.appendChild(link);
  });
}

function setFavicon(url) {
  let link = document.querySelector('link[rel~=icon]');
  if (!link) {
    link = document.createElement('link');
    link.rel = 'icon';
    document.head.appendChild(link);
  }
  link.href = url;
}

function injectHiddenMenuCss(cc, paths) {
  const RESERVED = new Set(['/monitor']);  // 不可隐藏
  const allowed = paths.filter((p) => !RESERVED.has(p));
  if (!allowed.length) return;

  // 用属性选择器: <router-link to="/cluster"> 渲染成 <a href="/cluster">
  const selectors = allowed.map((p) => `a[href$="${cssEscape(p)}"]`).join(',\n');
  const css = `
    /* plugin-${cc} hidden menus */
    ${selectors} {
      display: none !important;
    }
  `;
  const styleEl = document.createElement('style');
  styleEl.id = `plugin-${cc}-hidden-menus`;
  styleEl.textContent = css;
  document.head.appendChild(styleEl);
}

function cssEscape(s) {
  return s.replace(/[^a-zA-Z0-9\/\-_]/g, (c) => '\\' + c);
}

async function loadI18nMessages(i18nMap) {
  const messages = {};
  for (const [locale, relPath] of Object.entries(i18nMap)) {
    try {
      const url = `/api/v1/plugins/active/assets/${relPath}`;
      const resp = await fetch(url, { cache: 'no-store' });
      if (resp.ok) {
        messages[locale] = await resp.json();
      }
    } catch (e) {
      console.warn(`[Theme] i18n 加载失败 ${locale}:`, e);
    }
  }
  return messages;
}
```

### 3.2 Logo / Favicon 替换

#### 3.2.1 Logo 替换链路

现有 `frontend/src/layout/index.vue:11-16` 显示 logo 是写死文字 `<span>VISION SYSTEM</span>`。

**轻量改造**：把这一行改成可被插件替换的：

```vue
<!-- frontend/src/layout/index.vue -->
<div class="p-6 text-xl font-bold text-tech-blue border-b border-gray-800 flex justify-between items-center">
  <!-- 优先用 logo 图, 否则用 app_title 文字 -->
  <img v-if="logoUrl" :src="logoUrl" :alt="appTitle" class="h-8" />
  <span v-else>{{ appTitle }}</span>
  <button @click="sidebarOpen = false" class="text-gray-500 hover:text-white transition p-1">
    <el-icon class="text-[1.125rem]"><Close /></el-icon>
  </button>
</div>

<script setup>
import { computed } from 'vue';
const logoUrl = computed(() => window.__pluginLogoUrl || null);
const appTitle = computed(() => {
  // 从 CSS 变量读 (themeLoader 已写)
  const v = getComputedStyle(document.documentElement).getPropertyValue('--tj-app-title');
  return (v || "'VISION SYSTEM'").replace(/^['"]|['"]$/g, '');
});
</script>
```

> **改动量**：layout/index.vue + Navbar.vue + 任何显示"VISION SYSTEM" / 默认 logo 的地方
> **测试用例**：无插件 / 有插件主题包 / 主题包 logo 加载失败 三种场景都能正常显示

#### 3.2.2 Favicon

`themeLoader.injectCssFile` 已经处理（替换 `<link rel=icon>`）。

#### 3.2.3 Splash 图（Electron）

启动 Splash 在 `electron/src/main/splash.html`，用本地 PNG。**插件无法替换 Splash**（Electron 主进程在加载插件之前就已经显示 Splash）。

> 如果客户必须改 Splash → 走另一条路：自定义安装包替换 Splash 文件（不在插件系统范围内）。

### 3.3 多语言文案合并

#### 3.3.1 i18n 文件格式

插件 i18n JSON 与现有 `frontend/src/locales/zh-CN.js` 结构**完全一致**：

```json
// plugins/acme/frontend/i18n/zh-CN.json
{
  "navbar": {
    "title": "ACME 智能视觉系统"
  },
  "menu": {
    "monitor": "ACME 监控",
    "data": "生产数据"
  },
  "plugin": {
    "acme": {
      "menu": {
        "report": "ACME 报表"
      },
      "tooltip": {
        "shift": "切换班次"
      }
    }
  }
}
```

**两类 key**：

| 类别 | 示例 | 行为 |
|---|---|---|
| **覆盖型**（主程序已有的 key） | `navbar.title` / `menu.monitor` | mergeLocaleMessage 直接覆盖现有值 |
| **新增型**（插件自家命名空间） | `plugin.acme.menu.report` | 加新 key |

> 命名约定：客户**自家**新加的 key **必须**放在 `plugin.{customer_code}.*` 下（design 01 §九命名空间）。
> **覆盖主程序 key**是允许的（这就是档位 1 的核心能力之一）。

#### 3.3.2 合并时机（main.js 改造）

```js
// frontend/src/main.js（v3.7 改造）

import { loadActivePluginTheme } from './plugin/themeLoader';

(async () => {  // ← 整个改成 async IIFE

  // ===== 早期: 拉插件主题（在 createApp 之前）=====
  let pluginI18n = {};
  try {
    const themeResult = await loadActivePluginTheme();
    pluginI18n = themeResult.i18nMessages;
  } catch (e) {
    console.warn('[Boot] 主题加载失败, 走默认主题:', e);
  }

  // ===== 现有逻辑 =====
  // ... (initResponsive 不动)

  const i18n = createI18n({
    legacy: false,
    globalInjection: true,
    locale: 'zh-CN',
    messages: { 'zh-CN': zhCN, 'zh-TW': zhTW, 'en-US': enUS, 'ja-JP': jaJP, 'ko-KR': koKR }
  });

  // ===== NEW: 合并插件 i18n =====
  for (const [locale, msgs] of Object.entries(pluginI18n)) {
    try {
      i18n.global.mergeLocaleMessage(locale, msgs);
      console.log(`[Boot] ✓ 合并插件 i18n ${locale}`);
    } catch (e) {
      console.warn(`[Boot] 合并 i18n ${locale} 失败:`, e);
    }
  }

  // ===== 现有 createApp / use / mount =====
  const app = createApp(App);
  // ... (errorHandler / use / mount 不动)
})();
```

#### 3.3.3 冲突策略

如果同一 locale 下，主程序 key 和插件 key 同名：

```
主程序 zh-CN.menu.monitor = '检测中心'
插件   zh-CN.menu.monitor = '生产监控'
↓
mergeLocaleMessage 后: '生产监控'  (插件覆盖)
```

> 这是预期行为——客户希望覆盖才会写。
> 如果客户**不想**覆盖，写在 `plugin.{cc}.*` 自家命名空间即可。

### 3.4 隐藏菜单

design 04 §3.1.4 `injectHiddenMenuCss` 已实现：CSS `display:none` 路径选择器。

**保护项**（`RESERVED` 集合）：

```js
const RESERVED = new Set([
  '/monitor',  // 核心检测视图
  // 其他: 见下表评审后补
]);
```

**评审清单**（design 01 §3.5 提到的"暂定 /monitor，待 design 04 评审")：

| 路径 | 是否保护 | 理由 |
|---|---|---|
| `/monitor` | ✅ 保护 | 核心检测视图，隐藏=应用无意义 |
| `/project` | ❌ 不保护 | 客户可能用激活的项目就够了，不开放管理 |
| `/model` | ❌ 不保护 | 同上 |
| `/source` | ❌ 不保护 | 同上 |
| `/data` | ❌ 不保护 | 客户可能纯看监控不看数据 |
| `/mes` | ❌ 不保护 | 单机版必隐 |
| `/alarm` | ❌ 不保护 | — |
| `/settings` | ⚠️ 保护 | 隐藏后无法卸载/管理插件，**强制保护** |
| `/activation` | ⚠️ 保护 | License 激活页，**强制保护** |

> 修订：`RESERVED = { '/monitor', '/settings', '/activation' }`

> ⚠️ **CSS 隐藏 ≠ 路由禁止**：用户输入 URL `/cluster` 仍能直接进。tier 1 不阻止，**真正的访问控制等到 tier 2**（路由 beforeEach 级别）。

---

## 四、后端三个端点

### 4.1 GET `/api/v1/plugins/active/manifest`

```python
# backend/api/plugins.py 新增
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from backend.db.database import get_db
from backend.models.plugin_models import PluginInstall

router = APIRouter(prefix="/plugins", tags=["plugins"])


@router.get("/active/manifest")
def get_active_plugin_manifest(db: Session = Depends(get_db)):
    """返回当前激活插件的 manifest（前端启动时调）"""
    p = db.query(PluginInstall).filter_by(state="active").first()
    if not p:
        raise HTTPException(status_code=404, detail="no active plugin")
    # 返回时去除敏感字段（如 install_path 不暴露）
    return {
        "customer_code": p.customer_code,
        "name": p.name,
        "plugin_version": p.plugin_version,
        "tier": p.tier,
        "manifest_json": p.manifest_json,
        "frontend": p.manifest_json.get("frontend"),
    }
```

### 4.2 GET `/api/v1/plugins/active/assets/{path:path}`

```python
import os
from pathlib import Path
from fastapi.responses import FileResponse


# 危险路径正则（额外防御 path traversal）
import re
_SAFE_PATH = re.compile(r"^[a-zA-Z0-9_./\-]+$")


@router.get("/active/assets/{path:path}")
def get_active_plugin_asset(path: str, db: Session = Depends(get_db)):
    """返回插件静态资源（CSS / 图片 / i18n JSON）"""
    p = db.query(PluginInstall).filter_by(state="active").first()
    if not p:
        raise HTTPException(404, "no active plugin")

    # ===== 安全防御: path traversal =====
    if ".." in path or path.startswith("/"):
        raise HTTPException(400, "invalid path")
    if not _SAFE_PATH.match(path):
        raise HTTPException(400, "path contains illegal chars")

    # 拼接 + resolve, 检查是否还在 plugin_dir 内
    plugin_dir = Path(p.install_path).resolve()
    full = (plugin_dir / path).resolve()
    try:
        full.relative_to(plugin_dir)
    except ValueError:
        raise HTTPException(400, "path escape")

    if not full.is_file():
        raise HTTPException(404, "file not found")

    # 大小限制
    if full.stat().st_size > 50 * 1024 * 1024:  # 50 MB
        raise HTTPException(413, "file too large")

    return FileResponse(str(full))
```

#### 安全清单

| 风险 | 防御 |
|---|---|
| Path traversal `../` | `if ".." in path` + `relative_to` 双保险 |
| 绝对路径 `/etc/passwd` | `path.startswith("/")` 拒绝 |
| Windows `\` | `_SAFE_PATH` 正则 |
| URL 编码 `%2e%2e%2f` | FastAPI 已 decode，检查在 decode 后 |
| 软链接逃逸 | `Path.resolve()` 解一次，仍在 plugin_dir 才放行 |
| 读 plugin.json / signature.bin | **额外拒绝**（见下） |
| DoS 大文件 | 50 MB 限制 |

**额外拒绝**（敏感文件不能通过 assets 端点暴露）：

```python
SENSITIVE_FILES = {"plugin.json", "signature.bin"}
if full.name in SENSITIVE_FILES:
    raise HTTPException(404, "file not found")
```

### 4.3 路由挂载

```python
# backend/main.py 新增
from backend.api.plugins import router as plugins_router
app.include_router(plugins_router, prefix=settings.API_V1_STR)
```

> 与 inventory/04 §一所列 19 个 API prefix 一致：新加的 `/api/v1/plugins/*` 前缀放在 19 个之外，**不冲突**。

---

## 五、客户主题 CSS 命名空间约束

### 5.1 允许的选择器

| 选择器 | 允许 | 说明 |
|---|---|---|
| `:root { ... }` | ✅ | 仅用于覆盖 CSS 变量 |
| `html { ... }` | ⚠️ 警告 | 不影响功能但破坏 main.js initResponsive |
| `body { ... }` | ❌ | 用 `:root` 替代 |
| `* { ... }` | ❌ | 性能炸弹 |
| `.plugin-{cc}-*` | ✅ | 客户自家组件 |
| `.el-button { ... }` | ⚠️ 警告 | 全局影响 Element Plus, 容易翻车 |
| `[class^="el-"] { ... }` | ❌ | 同上, 更危险 |
| `#app { ... }` | ⚠️ 警告 | 影响主容器 |
| `@font-face { ... }` | ✅ | 字体文件 |
| `@media { ... }` | ✅ | 响应式 |
| `@keyframes { ... }` | ✅ | 客户自家动效 |

### 5.2 签名工具的 CSS 静态检查（design 07 实现）

```python
# scripts/sign-plugin.py 中新增
def check_css_namespace(css_path, customer_code):
    import re
    css = css_path.read_text(encoding="utf-8")

    # 检查危险全局选择器
    DANGER_PATTERNS = [
        (r"\*\s*\{", "全局 * 选择器禁用"),
        (r"\bbody\s*\{", "禁用 body 全局选择器, 用 :root"),
        (r'\[class[\^*~|$]?="el-', "禁用 [class*='el-'] 全局覆盖"),
    ]
    for pat, msg in DANGER_PATTERNS:
        if re.search(pat, css):
            raise BuildError(f"CSS 静态检查失败: {msg} [{css_path}]")

    # 检查自家 class 是否符合命名规范
    pat = re.compile(r"\.([a-zA-Z][a-zA-Z0-9_-]*)\s*[\{,]")
    for m in pat.finditer(css):
        cls = m.group(1)
        # 跳过 .el-* / .tj-* / 跟随 :root 的(无意义)
        if cls.startswith(("el-", "tj-")):
            continue
        if not cls.startswith(f"plugin-{customer_code}-"):
            print(f"[WARN] CSS 类 .{cls} 建议加 plugin-{customer_code}- 前缀")
```

**严格 vs 宽松**：
- **严格**（默认）：危险全局选择器**直接报错**，自家 class 命名规范**警告**
- **宽松**（`--no-css-check`）：仅警告

---

## 六、卸载 / 切换主题流程

### 6.1 卸载主题（清理 DOM）

切换插件或卸载时，前端要把已注入的元素去掉：

```js
// frontend/src/plugin/themeLoader.js
export function unloadActivePluginTheme(customer_code) {
  const ids = [
    `plugin-${customer_code}-vars`,
    `plugin-${customer_code}-css`,
    `plugin-${customer_code}-hidden-menus`,
  ];
  ids.forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.remove();
  });
  // i18n 不能卸载（vue-i18n 没 unmerge API），需重启页面
  window.__pluginLogoUrl = undefined;
  document.documentElement.style.removeProperty('--tj-logo-url');
  document.documentElement.style.removeProperty('--tj-app-title');
  document.title = '天骏 AI 视觉检测系统';
}
```

> ⚠️ **i18n 不能热卸载** —— vue-i18n `mergeLocaleMessage` 是不可逆的。所以**切换主题包必须重启 Electron**。

### 6.2 加载新主题（切换插件）

Settings 页"激活插件"按钮：

```js
async function activatePlugin(plugin_id) {
  await axios.post(`/api/v1/plugins/${plugin_id}/activate`);
  ElMessageBox.confirm('插件已激活, 需要重启应用以完全生效', '提示', {
    confirmButtonText: '立即重启',
    cancelButtonText: '稍后重启',
  }).then(() => {
    window.electronAPI?.relaunch?.();  // Electron IPC 重启
  });
}
```

> **未来优化**（v3.8+）：tier 1 主题包可热切换不重启，前提是改造 i18n 注册方式。

---

## 七、错误处理与降级

### 7.1 错误降级策略

| 错误 | 降级 | 用户感知 |
|---|---|---|
| `/api/v1/plugins/active/manifest` 404 | 走默认主题 | 默认皮肤 |
| `/api/v1/plugins/active/manifest` 500 | 走默认主题 + console.warn | 默认皮肤 + 后台 log |
| theme.css 文件不存在（404） | 跳过该 css，其他主题资源仍生效 | logo / 标题正常，颜色是默认 |
| Logo 文件不存在 | layout 走文字 fallback | 显示 app_title 文字 |
| i18n JSON 拉取失败 | 跳过该 locale，主程序原 locale 生效 | 看到主程序默认文案 |
| Favicon 加载失败 | 浏览器走默认 favicon | 浏览器图标默认 |

> **核心原则**：tier 1 主题包加载失败**绝不阻塞主程序启动**。永远是"插件优雅降级，不影响主功能"。

### 7.2 错误日志

```js
// frontend 侧
console.warn('[Theme] xxx');  // 用户能看到的（DevTools）

// 后端
logger.warning("[Plugin] active manifest failed: %s", e);  // 后端日志
```

### 7.3 后端的二次校验

即使前端已经验过了，后端 `/api/v1/plugins/active/manifest` 端点**仍然要再校验**：

- 插件 state == 'active'（不是 failed / quarantined）
- 验签状态 signature_status == 'ok'（不放过期/吊销）

```python
@router.get("/active/manifest")
def get_active_plugin_manifest(db: Session = Depends(get_db)):
    p = db.query(PluginInstall).filter_by(state="active").first()
    if not p:
        raise HTTPException(404, "no active plugin")
    if p.signature_status != "ok":
        raise HTTPException(403, "plugin signature invalid")
    return ...
```

---

## 八、目录结构（档位 1 完整 demo）

```
plugins/acme/
├── plugin.json                       # design 01 §6.1 完整示例
├── signature.bin                     # design 02 RSA + HMAC
├── frontend/
│   ├── theme.css                     # CSS 变量覆盖
│   ├── assets/
│   │   ├── acme-logo.svg
│   │   ├── acme-favicon.png
│   │   └── fonts/                    # 自带字体（可选）
│   │       └── ACME-Regular.woff2
│   └── i18n/
│       ├── zh-CN.json
│       └── en-US.json
└── README.md                         # 客户文档（design 08）
```

### 8.1 ACME 主题包完整示例（可直接打包）

```css
/* plugins/acme/frontend/theme.css */
@font-face {
  font-family: 'ACME';
  src: url('/api/v1/plugins/active/assets/frontend/assets/fonts/ACME-Regular.woff2') format('woff2');
  font-display: swap;
}

:root {
  /* 品牌色 */
  --tj-primary: #FFEB3B;
  --tj-primary-hover: #FBC02D;
  --tj-primary-active: #F57F17;
  --tj-primary-rgb: 255, 235, 59;

  /* 背景层级 */
  --tj-bg-base:  #1A1A1A;
  --tj-bg-panel: #2A2A2A;
  --tj-bg-elev:  #3A3A3A;

  /* 文字 */
  --tj-text-primary:   #FFFFFF;
  --tj-text-secondary: #DDDDDD;

  /* 字体 */
  --tj-font-family-base: 'ACME', 'Inter', sans-serif;

  /* 标题 */
  --tj-app-title: 'ACME 视觉检测系统';
}

/* 客户局部水印 */
.plugin-acme-watermark {
  position: fixed;
  bottom: 1rem;
  right: 1rem;
  background: rgba(255, 235, 59, 0.1);
  padding: 0.5rem 1rem;
  border-radius: 0.25rem;
  color: var(--tj-primary);
  pointer-events: none;
  z-index: 9999;
  user-select: none;
}
```

```json
// plugins/acme/frontend/i18n/zh-CN.json
{
  "navbar": {
    "title": "ACME"
  },
  "menu": {
    "monitor": "ACME 监控",
    "data": "生产数据"
  }
}
```

### 8.2 manifest（同 design 01 §6.1）

```json
{
  "manifest_version": 1,
  "name": "ACME 白标主题",
  "customer_code": "acme",
  "plugin_version": "1.0.0",
  "tier": 1,
  "capabilities": [
    "frontend.theme",
    "frontend.replace_logo",
    "frontend.replace_app_title",
    "frontend.i18n",
    "frontend.hide_menus"
  ],
  ...
  "frontend": {
    "theme": {
      "css": "frontend/theme.css",
      "logo": "frontend/assets/acme-logo.svg",
      "favicon": "frontend/assets/acme-favicon.png",
      "app_title": "ACME 视觉检测系统"
    },
    "i18n": {
      "zh-CN": "frontend/i18n/zh-CN.json"
    },
    "hidden_menus": ["/cluster", "/mes"]
  }
}
```

---

## 九、测试策略

### 9.1 单元测试

| 测试 | 覆盖 |
|---|---|
| `injectCssVariables` 注入 `<style>`，重复调用替换不重复 | 1 |
| `injectCssFile` 加载成功 / 404 fallback | 2 |
| `setFavicon` 已有 link 时替换 / 没 link 时新建 | 2 |
| `injectHiddenMenuCss` 过滤 RESERVED | 1 |
| `loadI18nMessages` 部分 locale 失败仍返回其他 | 1 |
| `unloadActivePluginTheme` 清理所有 ID | 1 |
| `cssEscape` 处理特殊字符 | 1 |

### 9.2 集成测试

| 测试 | 覆盖 |
|---|---|
| 无激活插件启动 | 默认主题，console.log 显示"无激活插件" |
| 激活插件 + theme.css 正常 | 颜色变化 |
| 激活插件 + theme.css 404 | 颜色不变，但其他资源正常 |
| 激活插件 + signature_status=invalid | 后端拒绝返回 manifest，前端走默认 |
| 激活插件 + i18n 部分缺失 | 已加载的 locale 生效，缺失的不影响 |
| 切换插件后重启 | 新主题完全生效 |
| 卸载插件后 | 默认主题恢复 |

### 9.3 性能基准

| 场景 | 期望 |
|---|---|
| 拉 manifest（无插件） | ≤ 50 ms |
| 拉 manifest（含 i18n × 5 locale） | ≤ 200 ms |
| theme.css 注入 → render 完成 | ≤ 300 ms 总 |
| Logo 大图 (500 KB) → 首次绘制 | ≤ 500 ms |

> **总目标**：从浏览器开始到 `app.mount()` 完成，主题加载额外开销 ≤ 500 ms。

### 9.4 视觉回归（手工）

- 激活 ACME 主题 → 截图对比
- 激活 ACME 主题 + zh-TW → 截图对比
- 卸载 → 截图对比

---

## 十、与 design 05/06 的接口契约

### 10.1 档位 1 → 档位 2 的复用

档位 2（design 05）会**完全复用** themeLoader：

```
档位 1: 仅 frontend.theme + frontend.i18n + frontend.hidden_menus
档位 2: 上面 + frontend.routes + frontend.menus + frontend.stores + frontend.entry
档位 3: 上面 + backend.* 全部
```

`themeLoader.loadActivePluginTheme()` 在档位 2/3 时**也调用同一个**，只是 manifest 字段更多。

### 10.2 档位 1 → 后端无新依赖

档位 1 不需要：
- 加载器跑 register_plugin
- ORM 表（除 plugins / plugin_state）
- hooks
- adapters

**档位 1 启用就是改了 plugins.state = 'active', 重启前端即可**。

### 10.3 与档位 3 的协作

如果客户买了档位 3 插件，**档位 1 资源也都生效**（向下兼容）：

```
plugins/acme/plugin.json: tier=3
   frontend.theme: { ... }    ← tier 1 的工作
   frontend.routes: [...]     ← tier 2 的工作
   backend: { ... }           ← tier 3 的工作

加载顺序:
   1) 后端: register_plugin → 注册 router/adapter/hook (design 06)
   2) 前端: themeLoader (本文 §三)
   3) 前端: 动态注册 routes/menus (design 05)
```

---

## 十一、待 design 05/06 确认

| 议题 | 当前态度 |
|---|---|
| 档位 1 是否要支持运行时切换（不重启 Electron）| 不支持（v3.7）。i18n 限制。v3.8+ 评估 |
| `--tj-*` 变量是否要扩到所有 Element Plus tokens | 当前只扩 `--el-color-primary`，余下渐进重构 |
| logo 是否支持 dark/light 双图 | 不支持（暗色为底就一张） |
| theme.css 是否支持 import 其他文件 | **不支持**（CSS @import 路径会指向插件内部文件，但 fetch 时会跨插件目录边界）。要求客户**单文件交付** |
| 是否提供主题预览页（Settings 内嵌缩略图） | 当前不做，文档化为"建议未来"|

---

## 十二、本文决策摘要

| 决策点 | 值 |
|---|---|
| 档位 1 唯一要改主程序的地方 | `main.js` async IIFE / `style.css` 加 theme-tokens / `layout/index.vue` logo 用 var |
| CSS 变量入口 | `:root` 选择器 |
| 主题令牌前缀 | `--tj-*` |
| 同步给 Element Plus | `--el-color-primary` 等关键变量从 `--tj-*` 派生 |
| 客户 CSS 大小限制 | 200 KB |
| Logo 文件大小限制 | 50 MB（assets 端点限制） |
| 客户 CSS 静态检查 | 禁止 `*` / `body` / `[class*=el-]` |
| 隐藏菜单实现 | CSS `display:none`（属性选择器匹配 `<a href>`） |
| RESERVED 不可隐藏 | `/monitor` / `/settings` / `/activation` |
| 路由保护 | tier 1 仅 CSS 隐藏，URL 直访不防（tier 2 才防） |
| i18n 合并方式 | `mergeLocaleMessage` 在 createI18n 后 mount 前 |
| i18n 限制 | 只能在 zh-CN/zh-TW/en-US/ja-JP/ko-KR 5 种内 |
| i18n 卸载 | 不支持（必须重启） |
| Splash 替换 | 不支持 |
| 错误降级 | 永远不阻塞主程序启动 |
| 后端端点数 | 3 个（manifest / assets / i18n） |

---

**本文最后更新**：2026-05-08
**事实校验**：基于 `frontend/src/main.js` / `style.css` / `layout/index.vue` / `i18n` 现状
**下一文档**：design/05_tier2_ui.md（动态组件 + 路由/菜单注入）
