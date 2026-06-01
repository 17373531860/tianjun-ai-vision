/**
 * G2 ADR-0002 — Tier 2/3 前端代码加载器.
 *
 * 客户视角:
 *   ACME 客户激活 Tier 2 "Factory Dashboard UI" 插件 + 重启应用后, 期待:
 *     1. 侧边菜单新增「客户看板」项
 *     2. 点击跳到 /factory-dashboard, 渲染 Dashboard 组件
 *     3. Pinia store plugin-internal-demo-dashboard 真挂上
 *     4. 停用 + 重启 → 全部回退
 *
 * 设计:
 *   - 拉 active manifest, 拿 frontend.entry 路径
 *   - fetch /api/v1/plugins/active/assets/{entry} 拿 ESM 文本
 *   - 动态 import 该 ESM (三级兜底: blob → data → http, 见下)
 *   - 调 module.default.register({ host, registry })
 *     - host = { vue, pinia, router, i18n, echarts }   ← 让插件不需要 import "vue" / "echarts"
 *     - registry = { routes / menus / stores / slots / tabs }
 *   - 失败静默 fallback, 主程序不崩
 *
 * ⚠️ v3.15.4 关键修复 (现场事故复盘):
 *   打包后主窗口走 file:// 协议, Chromium 对 file:// 源下 `import(blob:...)` 动态导入
 *   有安全限制 → 插件 ESM 加载静默失败 → 双工位 UI 等前端定制完全不生效, 而本地
 *   开发是 http://localhost 不复现. 修法:
 *     1. import 改成「三级兜底」: blob → data:URL → 后端 http URL, 任一成功即用.
 *     2. 全过程日志双通道输出: console (带 ⬛ 前缀, Electron 主进程转发进终端) +
 *        POST /plugins/client-log 回传后端 logger (终端可见 + 落盘 plugin 日志文件).
 *        从此前端插件加载问题无需开 DevTools / 不再黑盒.
 *
 * 局限 (留给后续 PR):
 *   - 没做 SES sandbox / iframe 隔离 — 插件代码有完全 host 访问权
 *   - 没做卸载/切换的运行时热刷新 — 客户切插件必须重启 (与后端 PluginManager 保持一致)
 */
import * as Vue from "vue";
import * as Pinia from "pinia";
import * as VueI18n from "vue-i18n";
import * as ECharts from "echarts";
import api from "@/api/index";
import { usePluginThemeStore } from "@/store/usePluginThemeStore";

const _loadedBlobUrls = new Set();
let _i18nInstance = null;

export function setPluginLoaderI18n(i18n) {
  _i18nInstance = i18n;
}

// ==================== 诊断日志双通道 ====================
// 现场没法开 DevTools, 所以每一步都:
//   1) console.log 带 [⬛ 前缀 → Electron 主进程 console-message 钩子转发进启动终端
//   2) POST /plugins/client-log → 后端 logger 打印 (终端 [Backend] 行) + 落盘
// 任一通道挂掉都不影响主流程 (全 try 包住).
function _safeStr(v) {
  if (v === undefined || v === null) return "";
  if (typeof v === "string") return v;
  try { return JSON.stringify(v); } catch (e) { return String(v); }
}

function plog(stage, detail, level) {
  const d = _safeStr(detail);
  const line = `[⬛ PluginLoader] ${stage}${d ? " :: " + d : ""}`;
  try {
    // eslint-disable-next-line no-console
    (level === "error" ? console.error : level === "warn" ? console.warn : console.log)(line);
  } catch (e) { /* ignore */ }
  try {
    api.post("/plugins/client-log", {
      source: "plugin-loader",
      level: level || "info",
      stage,
      detail: d,
      ts: Date.now(),
    }).catch(() => {});
  } catch (e) { /* ignore */ }
}

/**
 * 主入口 — main.js 在 Vue mount 前调一次.
 * @param {import('vue-router').Router} router
 */
export async function loadActivePluginFrontend(router) {
  plog("开始加载前端插件", { protocol: (typeof location !== "undefined" ? location.protocol : "?") });

  let manifest;
  try {
    const { data } = await api.get("/plugins/active/manifest");
    manifest = data;
  } catch (e) {
    plog("拉 active manifest 失败", e?.message, "warn");
    return { loaded: false, reason: "manifest-fetch-failed" };
  }

  if (!manifest || !manifest.customer_code) {
    plog("无 active 插件 (manifest 空)", null, "warn");
    return { loaded: false, reason: "no-active-plugin" };
  }
  plog("active 插件", { customer_code: manifest.customer_code, version: manifest.plugin_version, tier: manifest.tier });

  const entry = manifest?.frontend?.entry;
  if (!entry) {
    plog("manifest 无 frontend.entry, 此包不含前端 UI", null, "warn");
    return { loaded: false, reason: "no-frontend-entry" };
  }

  const themeStore = usePluginThemeStore();
  const url = `${api.defaults.baseURL}/plugins/active/assets/${entry}`;
  plog("准备拉取 ESM", url);

  let text;
  try {
    const resp = await fetch(url, { credentials: "omit" });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    text = await resp.text();
    plog("ESM 文本已拉到", { bytes: text.length });
  } catch (e) {
    plog("拉 ESM entry 失败", e?.message, "error");
    return { loaded: false, reason: "entry-fetch-failed", error: e?.message };
  }

  // ===== 三级兜底动态 import (修 file:// 下 blob import 被拦) =====
  // 数据驱动避免 if/else 分支堆叠: 依次尝试 blob → data → http, 谁先成功用谁.
  const strategies = [
    {
      name: "blob",
      make: () => {
        const blob = new Blob([text], { type: "application/javascript" });
        const u = URL.createObjectURL(blob);
        _loadedBlobUrls.add(u);
        return u;
      },
    },
    {
      name: "data",
      make: () => "data:text/javascript;charset=utf-8," + encodeURIComponent(text),
    },
    {
      // 直接 import 后端真实 http URL. Electron 主窗口 webSecurity:false,
      // 允许 file:// 页面跨源 import http://localhost:8001 的 module.
      name: "http",
      make: () => url,
    },
  ];

  let mod = null;
  let usedStrategy = null;
  for (const st of strategies) {
    try {
      const importUrl = st.make();
      // eslint-disable-next-line no-unsanitized/method
      mod = await import(/* @vite-ignore */ importUrl);
      usedStrategy = st.name;
      plog(`import 成功`, { strategy: st.name });
      break;
    } catch (e) {
      plog(`import 策略失败`, { strategy: st.name, error: e?.message, name: e?.name }, "warn");
    }
  }
  if (!mod) {
    plog("三级 import 全部失败 — 前端插件无法加载", null, "error");
    return { loaded: false, reason: "import-failed" };
  }

  const exported = mod?.default ?? mod;
  if (!exported || typeof exported.register !== "function") {
    plog("entry 没导出 register()", { entry, exportKeys: exported ? Object.keys(exported) : null }, "error");
    return { loaded: false, reason: "no-register-export" };
  }
  plog("拿到 register(), 准备注册", { strategy: usedStrategy });

  const host = {
    vue: Vue,
    pinia: Pinia,
    i18n: _i18nInstance ? VueI18n : null,
    router,
    echarts: ECharts,
    // 已配 baseURL + 鉴权拦截器的 axios 实例, 让插件前端能调自己的后端路由
    // (/api/v1/plugins/<cc>/...). 纯附加能力, 不改任何既有行为.
    api,
    customerCode: manifest.customer_code,
    pluginVersion: manifest.plugin_version,
  };

  const registry = {
    routes: {
      add(routeOpts) {
        const path = routeOpts.path?.startsWith("/") ? routeOpts.path : `/${routeOpts.path}`;
        const finalRoute = { ...routeOpts, path };
        try {
          router.addRoute(finalRoute);
          themeStore.addPluginRoute(finalRoute);
          plog("route added", { path: finalRoute.path, name: finalRoute.name });
        } catch (e) {
          plog("route add 失败", { path: finalRoute.path, error: e?.message }, "warn");
        }
      },
      remove(name) {
        try {
          router.removeRoute(name);
          themeStore.removePluginRoute(name);
        } catch (e) {
          plog("route remove 失败", { name, error: e?.message }, "warn");
        }
      },
    },
    menus: {
      add(menuOpts) {
        try {
          themeStore.addPluginMenu(menuOpts);
          plog("menu added", { path: menuOpts.path, label: menuOpts.label });
        } catch (e) {
          plog("menu add 失败", e?.message, "warn");
        }
      },
      remove(path) {
        themeStore.removePluginMenu(path);
      },
    },
    stores: {
      register(id, useStoreFn) {
        try {
          if (typeof useStoreFn === "function") {
            useStoreFn();
            plog("store registered", id);
          }
        } catch (e) {
          plog("store register 失败", { id, error: e?.message }, "warn");
        }
      },
    },
    // v3.13 M2.2a: 主程序 UI Slot 注册接口
    // 插件用 registry.slots.register('monitor.step-cell.duration', MyCellComponent) 覆盖
    // 主程序对应 <TjSlot name="monitor.step-cell.duration" :duration="..."> 的默认实现.
    // 同名后注册覆盖前注册 (单 active plugin 设计).
    slots: {
      register(slotName, component) {
        if (!slotName) {
          plog("slot register 失败: slotName 必填", null, "warn");
          return;
        }
        if (!component) {
          plog("slot register 失败: component 必填", slotName, "warn");
          return;
        }
        try {
          themeStore.addPluginSlot(slotName, component);
          plog("slot registered", slotName);
        } catch (e) {
          plog("slot register 失败", { slotName, error: e?.message }, "warn");
        }
      },
      unregister(slotName) {
        try {
          themeStore.removePluginSlot(slotName);
        } catch (e) {
          plog("slot unregister 失败", { slotName, error: e?.message }, "warn");
        }
      },
    },
    // v3.13 M2.2b/M3.4: 配置面板 tab 注入 API
    //   registry.tabs.register('settings', { key: 'customer-rule', label: '客户规则', component: MyComp })
    //   scope: 'settings' | 'project'
    tabs: {
      register(scope, tab) {
        if (!scope || !tab || !tab.key || !tab.label) {
          plog("tab register 失败: scope/key/label 必填", { scope, tab }, "warn");
          return;
        }
        if (scope !== "settings" && scope !== "project") {
          plog("tab register 失败: scope 必须是 settings/project", scope, "warn");
          return;
        }
        try {
          themeStore.addPluginTab(scope, tab);
          plog(`${scope} tab registered`, { key: tab.key, label: tab.label });
        } catch (e) {
          plog("tab register 失败", { key: tab.key, error: e?.message }, "warn");
        }
      },
      unregister(scope, key) {
        try {
          themeStore.removePluginTab(scope, key);
        } catch (e) {
          plog("tab unregister 失败", { scope, key, error: e?.message }, "warn");
        }
      },
    },
  };

  try {
    const result = await exported.register({ host, registry });
    plog("register() 完成, 前端插件加载成功", { customer_code: manifest.customer_code, result });
    return { loaded: true, customerCode: manifest.customer_code, result };
  } catch (e) {
    plog("register() 抛错", { error: e?.message, stack: e?.stack }, "error");
    return { loaded: false, reason: "register-threw", error: e?.message };
  }
}

/**
 * 测试 / 切换时回收 Blob URL.
 */
export function cleanupPluginBlobs() {
  for (const u of _loadedBlobUrls) {
    URL.revokeObjectURL(u);
  }
  _loadedBlobUrls.clear();
}
