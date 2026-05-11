/**
 * Tier 2 UI 插件示例 — host 注入风格 (G2 ADR-0002 修订版).
 *
 * 设计变更:
 *   原版本: `import { defineComponent, h, ref } from "vue"` — Vite dev/prod 都解不到 bare specifier
 *   新版本: register({ host }) 接 host.vue / host.pinia, 一份代码 dev/prod 通用
 *
 * 主程序 plugin loader 调:
 *   const mod = await import(blobUrl);
 *   await mod.default.register({
 *     host: { vue, pinia, router, i18n },
 *     registry: { routes, menus, stores },
 *   });
 */

let _registeredRouteName = null;
let _registeredMenuPath = null;

export default {
  async register({ host, registry }) {
    const { defineComponent, h, ref } = host.vue;
    const { defineStore } = host.pinia;

    const usePluginDashboardStore = defineStore("plugin-internal-demo-dashboard", () => {
      const shiftTarget = ref(1200);
      const defectThreshold = ref(8);

      function updateConfig(next) {
        if (typeof next?.shiftTarget === "number") shiftTarget.value = next.shiftTarget;
        if (typeof next?.defectThreshold === "number") defectThreshold.value = next.defectThreshold;
      }

      return { shiftTarget, defectThreshold, updateConfig };
    });

    const DashboardView = defineComponent({
      name: "PluginInternalDemoDashboard",
      setup() {
        const store = usePluginDashboardStore();
        return () =>
          h("section", { class: "plugin-dashboard", style: "padding:1.5rem;color:var(--tj-text-main,#e2e8f0);" }, [
            h("h2", { style: "font-size:1.5rem;margin-bottom:1rem;" }, "客户看板"),
            h("p", { style: "margin-bottom:1.5rem;color:#94a3b8;" },
              "这是 Tier 2 UI 插件示例。真实环境中可通过 host API 读取主程序状态。"),
            h("div", { class: "plugin-dashboard__cards", style: "display:flex;gap:1rem;flex-wrap:wrap;" }, [
              h("article",
                { style: "padding:1rem;background:#1e293b;border:1px solid #334155;border-radius:0.5rem;min-width:160px;" },
                [
                  h("strong", { style: "display:block;color:#94a3b8;font-size:0.875rem;" }, "当班目标"),
                  h("span", { style: "font-size:1.5rem;color:var(--tj-primary,#38bdf8);font-weight:bold;" },
                    String(store.shiftTarget)),
                ]),
              h("article",
                { style: "padding:1rem;background:#1e293b;border:1px solid #334155;border-radius:0.5rem;min-width:160px;" },
                [
                  h("strong", { style: "display:block;color:#94a3b8;font-size:0.875rem;" }, "缺陷阈值"),
                  h("span", { style: "font-size:1.5rem;color:var(--tj-primary,#38bdf8);font-weight:bold;" },
                    `${store.defectThreshold}%`),
                ]),
            ]),
          ]);
      },
    });

    registry.routes.add({
      path: "/factory-dashboard",
      name: "factory-dashboard",
      component: DashboardView,
      meta: { title: "客户看板", plugin: true },
    });
    _registeredRouteName = "factory-dashboard";

    registry.menus.add({
      path: "/factory-dashboard",
      label: "客户看板",
      icon: "DataAnalysis",
      order: 80,
    });
    _registeredMenuPath = "/factory-dashboard";

    if (registry.stores?.register) {
      registry.stores.register("plugin-internal-demo-dashboard", usePluginDashboardStore);
    } else {
      usePluginDashboardStore();
    }

    return { route: _registeredRouteName, menu: _registeredMenuPath };
  },

  async unregister({ registry }) {
    if (_registeredRouteName && registry?.routes?.remove) {
      registry.routes.remove(_registeredRouteName);
    }
    if (_registeredMenuPath && registry?.menus?.remove) {
      registry.menus.remove(_registeredMenuPath);
    }
    _registeredRouteName = null;
    _registeredMenuPath = null;
  },
};
