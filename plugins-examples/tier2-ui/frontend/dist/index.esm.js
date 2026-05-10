import { defineComponent, h, ref } from "vue";
import { defineStore } from "pinia";

export const usePluginDashboardStore = defineStore("plugin-internal-demo-dashboard", () => {
  const shiftTarget = ref(1200);
  const defectThreshold = ref(8);

  function updateConfig(next) {
    if (typeof next?.shiftTarget === "number") shiftTarget.value = next.shiftTarget;
    if (typeof next?.defectThreshold === "number") defectThreshold.value = next.defectThreshold;
  }

  return { shiftTarget, defectThreshold, updateConfig };
});

export const DashboardView = defineComponent({
  name: "PluginInternalDemoDashboard",
  setup() {
    const store = usePluginDashboardStore();
    return () => h("section", { class: "plugin-dashboard" }, [
      h("h2", "客户看板"),
      h("p", "这是 Tier 2 UI 插件示例。真实环境中可通过 host API 读取主程序状态。"),
      h("div", { class: "plugin-dashboard__cards" }, [
        h("article", [h("strong", "当班目标"), h("span", String(store.shiftTarget))]),
        h("article", [h("strong", "缺陷阈值"), h("span", `${store.defectThreshold}%`)]),
      ]),
    ]);
  },
});

export default {
  async register(ctx) {
    ctx.registry?.stores?.register?.("plugin-internal-demo-dashboard", usePluginDashboardStore);
    ctx.registry?.routes?.register?.({
      path: "factory-dashboard",
      name: "factory-dashboard",
      component: DashboardView,
      meta: { title: "客户看板", plugin: true },
    });
  },
  async unregister(ctx) {
    ctx.registry?.routes?.unregister?.("factory-dashboard");
    ctx.registry?.stores?.unregister?.("plugin-internal-demo-dashboard");
  },
};
