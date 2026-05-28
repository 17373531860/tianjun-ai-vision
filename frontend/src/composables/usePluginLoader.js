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
 *   - new Blob → URL.createObjectURL → dynamic import
 *   - 调 module.default.register({ host, registry })
 *     - host = { vue, pinia, router, i18n }   ← 让插件不需要 import "vue"
 *     - registry = {
 *         routes: { add(routeOpts), remove(name) }   → vue-router 4 addRoute/removeRoute
 *         menus:  { add(menuOpts), remove(path) }    → usePluginThemeStore.pluginMenus
 *         stores: { register(id, factory) }          → 调一次 factory 让 Pinia 自然挂载
 *       }
 *   - 失败静默 fallback, 主程序不崩
 *
 * 局限 (留给后续 PR):
 *   - 没做 SES sandbox / iframe 隔离 — 插件代码有完全 host 访问权
 *   - 没做卸载/切换的运行时热刷新 — 客户切插件必须重启 (与后端 PluginManager 保持一致)
 */
import * as Vue from "vue";
import * as Pinia from "pinia";
import * as VueI18n from "vue-i18n";
import api from "@/api/index";
import { usePluginThemeStore } from "@/store/usePluginThemeStore";

const _loadedBlobUrls = new Set();
let _i18nInstance = null;

export function setPluginLoaderI18n(i18n) {
  _i18nInstance = i18n;
}

/**
 * 主入口 — main.js 在 Vue mount 前调一次.
 * @param {import('vue-router').Router} router
 */
export async function loadActivePluginFrontend(router) {
  let manifest;
  try {
    const { data } = await api.get("/plugins/active/manifest");
    manifest = data;
  } catch (e) {
    console.warn("[PluginLoader] 拉 active manifest 失败:", e?.message);
    return { loaded: false, reason: "manifest-fetch-failed" };
  }

  if (!manifest || !manifest.customer_code) {
    return { loaded: false, reason: "no-active-plugin" };
  }

  const entry = manifest?.frontend?.entry;
  if (!entry) {
    return { loaded: false, reason: "no-frontend-entry" };
  }

  const themeStore = usePluginThemeStore();
  const url = `${api.defaults.baseURL}/plugins/active/assets/${entry}`;

  let text;
  try {
    const resp = await fetch(url, { credentials: "omit" });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    text = await resp.text();
  } catch (e) {
    console.warn("[PluginLoader] 拉 ESM entry 失败:", e?.message);
    return { loaded: false, reason: "entry-fetch-failed" };
  }

  let mod;
  let blobUrl;
  try {
    const blob = new Blob([text], { type: "application/javascript" });
    blobUrl = URL.createObjectURL(blob);
    _loadedBlobUrls.add(blobUrl);
    mod = await import(/* @vite-ignore */ blobUrl);
  } catch (e) {
    console.warn("[PluginLoader] dynamic import 失败:", e);
    if (blobUrl) URL.revokeObjectURL(blobUrl);
    return { loaded: false, reason: "import-failed", error: e?.message };
  }

  const exported = mod?.default ?? mod;
  if (!exported || typeof exported.register !== "function") {
    console.warn("[PluginLoader] entry 没导出 register():", entry);
    return { loaded: false, reason: "no-register-export" };
  }

  const host = {
    vue: Vue,
    pinia: Pinia,
    i18n: _i18nInstance ? VueI18n : null,
    router,
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
          console.log(`[PluginLoader] route added: ${finalRoute.path} (${finalRoute.name})`);
        } catch (e) {
          console.warn(`[PluginLoader] route add 失败 ${finalRoute.path}:`, e);
        }
      },
      remove(name) {
        try {
          router.removeRoute(name);
          themeStore.removePluginRoute(name);
        } catch (e) {
          console.warn(`[PluginLoader] route remove 失败 ${name}:`, e);
        }
      },
    },
    menus: {
      add(menuOpts) {
        try {
          themeStore.addPluginMenu(menuOpts);
          console.log(`[PluginLoader] menu added: ${menuOpts.path} (${menuOpts.label})`);
        } catch (e) {
          console.warn("[PluginLoader] menu add 失败:", e);
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
            console.log(`[PluginLoader] store registered: ${id}`);
          }
        } catch (e) {
          console.warn(`[PluginLoader] store register 失败 ${id}:`, e);
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
          console.warn("[PluginLoader] slot register 失败: slotName 必填");
          return;
        }
        if (!component) {
          console.warn(`[PluginLoader] slot register 失败 ${slotName}: component 必填`);
          return;
        }
        try {
          themeStore.addPluginSlot(slotName, component);
          console.log(`[PluginLoader] slot registered: ${slotName}`);
        } catch (e) {
          console.warn(`[PluginLoader] slot register 失败 ${slotName}:`, e);
        }
      },
      unregister(slotName) {
        try {
          themeStore.removePluginSlot(slotName);
        } catch (e) {
          console.warn(`[PluginLoader] slot unregister 失败 ${slotName}:`, e);
        }
      },
    },
  };

  try {
    const result = await exported.register({ host, registry });
    return { loaded: true, customerCode: manifest.customer_code, result };
  } catch (e) {
    console.warn("[PluginLoader] register() 抛错:", e);
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
