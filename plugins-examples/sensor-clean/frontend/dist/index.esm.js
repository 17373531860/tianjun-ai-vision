/**
 * sensor-clean 前端面板（host 注入风格，对齐 tier2-ui 样板）。
 *
 * 主程序 plugin loader 调:
 *   const mod = await import(blobUrl);
 *   await mod.default.register({ host: { vue, pinia, router, i18n }, registry });
 *
 * 面板职责：棉签耗材状态实时显示 + 手动换棉签 + 查看对齐 demo 的项目配置模板。
 * 检测/计数视频在主程序 Monitor 页，本面板聚焦插件增值（耗材约束 + 跨视角联动状态）。
 */

const API = "/api/v1/plugins/sensor-clean/swab";

async function fetchState() {
  const r = await fetch(`${API}/state`);
  return r.json();
}
async function postReset() {
  const r = await fetch(`${API}/reset`, { method: "POST" });
  return r.json();
}
async function postApplyPreset() {
  const r = await fetch(`${API}/apply-preset`, { method: "POST" });
  return r.json();
}

export default {
  async register({ host, registry }) {
    const { defineComponent, h, ref, onMounted, onUnmounted } = host.vue;

    const PanelView = defineComponent({
      name: "SensorCleanPanel",
      setup() {
        const state = ref({ swab_used: 0, max_uses_per_swab: 11, remaining: 11, locked: false });
        let timer = null;

        async function refresh() {
          try {
            state.value = await fetchState();
          } catch (e) {
            /* 隔离：拉取失败不影响面板存活 */
          }
        }

        onMounted(() => {
          refresh();
          timer = setInterval(refresh, 1000);
        });
        onUnmounted(() => {
          if (timer) clearInterval(timer);
        });

        async function onReset() {
          state.value = await postReset();
        }
        async function onApply() {
          const res = await postApplyPreset();
          if (res && res.state) state.value = res.state;
          const v1 = res?.view1_project || {};
          const v2 = res?.view2_project || {};
          window.alert(
            "已一键应用对齐 demo 的默认配置（计数/耗材参数已写入并热加载，可随时再改）。\n\n" +
              "视角1：" + (v1.name || "") + "\n  模型：" + (v1.model_path || "(请在项目页指定)") + "\n" +
              "视角2：" + (v2.name || "") + "\n  模型：" + (v2.model_path || "(请在项目页指定)") + "\n\n" +
              "请到「项目」页用上述模型新建两个项目并分别激活到视角1/视角2 通道，计数即对齐 demo。"
          );
        }

        return () => {
          const s = state.value;
          const locked = s.locked;
          const card = (title, val) =>
            h(
              "article",
              {
                style:
                  "padding:1rem;background:#1e293b;border:1px solid #334155;border-radius:0.5rem;min-width:160px;",
              },
              [
                h(
                  "strong",
                  { style: "display:block;color:#94a3b8;font-size:0.875rem;margin-bottom:0.5rem;" },
                  title
                ),
                h(
                  "span",
                  { style: "font-size:1.75rem;color:var(--tj-primary,#38bdf8);font-weight:bold;" },
                  val
                ),
              ]
            );

          return h(
            "section",
            { style: "padding:1.5rem;color:var(--tj-text-main,#e2e8f0);" },
            [
              h("h2", { style: "font-size:1.5rem;margin-bottom:1rem;" }, "传感器清洁工序"),
              h(
                "div",
                {
                  style: `padding:0.75rem 1rem;border-radius:0.5rem;margin-bottom:1.5rem;font-weight:bold;color:#fff;background:${
                    locked ? "#7f1d1d" : "#14532d"
                  };`,
                },
                locked ? "棉签已用满，请更换棉签！（计数已锁定）" : "状态：正常"
              ),
              h(
                "div",
                { style: "display:flex;gap:1rem;flex-wrap:wrap;margin-bottom:1.5rem;" },
                [card("棉签已用", `${s.swab_used} / ${s.max_uses_per_swab}`), card("剩余", String(s.remaining))]
              ),
              h("div", { style: "display:flex;gap:1rem;" }, [
                h(
                  "button",
                  {
                    style:
                      "padding:0.5rem 1.25rem;border-radius:0.5rem;border:none;background:#38bdf8;color:#0f172a;font-weight:bold;cursor:pointer;",
                    onClick: onReset,
                  },
                  "更换棉签（重置）"
                ),
                h(
                  "button",
                  {
                    style:
                      "padding:0.5rem 1.25rem;border-radius:0.5rem;border:1px solid #334155;background:transparent;color:var(--tj-text-main,#e2e8f0);cursor:pointer;",
                    onClick: onApply,
                  },
                  "一键应用 demo 默认配置"
                ),
              ]),
            ]
          );
        };
      },
    });

    registry.routes.add({
      path: "/sensor-clean",
      name: "sensor-clean",
      component: PanelView,
      meta: { title: "传感器清洁", plugin: true },
    });
    registry.menus.add({
      path: "/sensor-clean",
      label: "传感器清洁",
      icon: "Brush",
      order: 80,
    });

    return { route: "sensor-clean", menu: "/sensor-clean" };
  },

  async unregister({ registry }) {
    if (registry?.routes?.remove) registry.routes.remove("sensor-clean");
    if (registry?.menus?.remove) registry.menus.remove("/sensor-clean");
  },
};
