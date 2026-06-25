/**
 * sensor-clean 前端面板 v1.1.0（host 注入风格，对齐 tier2-ui 样板）。
 *
 * 主程序 plugin loader 调:
 *   const mod = await import(blobUrl);
 *   await mod.default.register({ host: { vue, pinia, router, i18n, api }, registry });
 *
 * 面板职责:
 *   - 棉签耗材状态实时显示 + 手动换棉签 + 一键应用 demo 项目模板（保留）。
 *   - v1.1.0 新增「三判定 → 事件」配置 Tab: 假擦拭 / 棉签寿命超限 / 操作员离开超时
 *     各自选连到哪个主程序事件（默认系统 NG=2）+ 阈值, 保存走 /swab/config。
 *     判定命中后插件调 host.trigger_event 借主程序事件响应面（报警/计数器/Toast/主页）。
 *
 * 网络: 优先用 host.api（已配 baseURL=/api/v1 + 鉴权拦截器, 打包 file:// 下也工作）,
 *       host.api 不可用时回退 window.fetch（兼容老 loader）。
 */

const SWAB = "/plugins/sensor-clean/swab";

export default {
  async register({ host, registry }) {
    const { defineComponent, h, ref, onMounted, onUnmounted } = host.vue;
    const api = host && host.api ? host.api : null;

    // ---------- 网络封装（host.api 优先, fetch 兜底）----------
    async function apiGet(path) {
      if (api) {
        const { data } = await api.get(path);
        return data;
      }
      const r = await fetch(`/api/v1${path}`);
      return r.json();
    }
    async function apiPost(path, body) {
      if (api) {
        const { data } = await api.post(path, body || {});
        return data;
      }
      const r = await fetch(`/api/v1${path}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: body ? JSON.stringify(body) : undefined,
      });
      return r.json();
    }

    const PanelView = defineComponent({
      name: "SensorCleanPanel",
      setup() {
        const state = ref({ swab_used: 0, max_uses_per_swab: 11, remaining: 11, locked: false, absent_remaining: -1 });
        const cfg = ref({
          swab_over_limit_event_id: 2,
          fake_wipe_event_id: 2,
          fake_wipe_still_time: 2.0,
          fake_wipe_still_disp: 0.0058,
          operator_absent_enabled: false,
          operator_absent_event_id: 2,
          operator_absent_timeout_sec: 600,
          max_uses_per_swab: 11,
        });
        const eventOptions = ref([{ id: 1, name: "合格(OK)" }, { id: 2, name: "不良(NG)" }]);
        const saveMsg = ref("");
        const saving = ref(false);
        let timer = null;

        async function refresh() {
          try {
            state.value = await apiGet(`${SWAB}/state`);
          } catch (e) { /* 隔离 */ }
        }
        async function loadConfig() {
          try {
            const c = await apiGet(`${SWAB}/config`);
            if (c && typeof c === "object") cfg.value = { ...cfg.value, ...c };
          } catch (e) { /* 隔离 */ }
        }
        async function loadEvents() {
          try {
            const data = await apiGet("/projects");
            const items = (data && data.items) || data || [];
            const seen = new Map();
            seen.set(1, "合格(OK)");
            seen.set(2, "不良(NG)");
            items.forEach((p) => (p.events_config || []).forEach((e) => {
              if (e && e.id != null && !seen.has(e.id)) seen.set(e.id, e.name || `事件${e.id}`);
            }));
            eventOptions.value = [...seen].map(([id, name]) => ({ id, name }));
          } catch (e) { /* 隔离: 至少有 OK/NG 兜底 */ }
        }

        onMounted(() => {
          refresh();
          loadConfig();
          loadEvents();
          timer = setInterval(refresh, 1000);
        });
        onUnmounted(() => {
          if (timer) clearInterval(timer);
        });

        async function onReset() {
          try { state.value = await apiPost(`${SWAB}/reset`); } catch (e) { /* 隔离 */ }
        }
        async function onApply() {
          try {
            const res = await apiPost(`${SWAB}/apply-preset`);
            if (res && res.state) state.value = res.state;
            await loadConfig();
            const v1 = (res && res.view1_project) || {};
            const v2 = (res && res.view2_project) || {};
            window.alert(
              "已一键应用对齐 demo 的默认配置（计数/耗材/三判定参数已写入并热加载，可随时再改）。\n\n" +
                "视角1：" + (v1.name || "") + "\n  模型：" + (v1.model_path || "(请在项目页指定)") + "\n" +
                "视角2：" + (v2.name || "") + "\n  模型：" + (v2.model_path || "(请在项目页指定)") + "\n\n" +
                "请到「项目」页用上述模型新建两个项目并分别激活到视角1/视角2 通道。"
            );
          } catch (e) { /* 隔离 */ }
        }
        async function onSave() {
          saving.value = true;
          saveMsg.value = "";
          try {
            const patch = {
              swab_over_limit_event_id: Number(cfg.value.swab_over_limit_event_id) || 0,
              fake_wipe_event_id: Number(cfg.value.fake_wipe_event_id) || 0,
              fake_wipe_still_time: Number(cfg.value.fake_wipe_still_time) || 2.0,
              fake_wipe_still_disp: Number(cfg.value.fake_wipe_still_disp) || 0.0058,
              operator_absent_enabled: !!cfg.value.operator_absent_enabled,
              operator_absent_event_id: Number(cfg.value.operator_absent_event_id) || 0,
              operator_absent_timeout_sec: Number(cfg.value.operator_absent_timeout_sec) || 600,
              max_uses_per_swab: Number(cfg.value.max_uses_per_swab) || 11,
            };
            const res = await apiPost(`${SWAB}/config`, patch);
            if (res && typeof res === "object") cfg.value = { ...cfg.value, ...res };
            saveMsg.value = "✓ 已保存并热加载";
          } catch (e) {
            saveMsg.value = "保存失败: " + (e && e.message ? e.message : e);
          } finally {
            saving.value = false;
          }
        }

        // ---------- 渲染辅助 ----------
        const labelStyle = "display:block;color:#94a3b8;font-size:0.8rem;margin-bottom:0.25rem;";
        const inputStyle = "padding:0.4rem 0.6rem;border-radius:0.4rem;border:1px solid #334155;background:#0f172a;color:#e2e8f0;width:180px;box-sizing:border-box;";
        const hintStyle = "display:block;color:#64748b;font-size:0.7rem;margin-top:0.2rem;";

        function eventSelect(fieldKey) {
          return h(
            "select",
            {
              style: inputStyle,
              onChange: (ev) => { cfg.value[fieldKey] = Number(ev.target.value); },
            },
            [
              h("option", { value: "0", selected: Number(cfg.value[fieldKey]) === 0 }, "（关闭此判定）"),
              ...eventOptions.value.map((o) =>
                h("option", { value: String(o.id), selected: Number(cfg.value[fieldKey]) === o.id }, `${o.id} · ${o.name}`)
              ),
            ]
          );
        }
        function numInput(fieldKey, step) {
          return h("input", {
            type: "number",
            step: step || "1",
            style: inputStyle,
            value: cfg.value[fieldKey],
            onInput: (ev) => { cfg.value[fieldKey] = ev.target.value; },
          });
        }
        function toggleSelect(fieldKey) {
          return h(
            "select",
            {
              style: inputStyle,
              onChange: (ev) => { cfg.value[fieldKey] = ev.target.value === "1"; },
            },
            [
              h("option", { value: "0", selected: !cfg.value[fieldKey] }, "关闭"),
              h("option", { value: "1", selected: !!cfg.value[fieldKey] }, "启用"),
            ]
          );
        }
        function field(labelText, control, hint) {
          return h("div", { style: "margin-bottom:0.75rem;" }, [
            h("label", { style: labelStyle }, labelText),
            control,
            hint ? h("span", { style: hintStyle }, hint) : null,
          ]);
        }
        function block(title, children) {
          return h(
            "article",
            { style: "padding:1rem;background:#1e293b;border:1px solid #334155;border-radius:0.5rem;flex:1;min-width:260px;" },
            [h("h3", { style: "font-size:1rem;margin-bottom:0.75rem;color:#e2e8f0;" }, title), ...children]
          );
        }
        function card(title, val) {
          return h(
            "article",
            { style: "padding:1rem;background:#1e293b;border:1px solid #334155;border-radius:0.5rem;min-width:150px;" },
            [
              h("strong", { style: "display:block;color:#94a3b8;font-size:0.875rem;margin-bottom:0.5rem;" }, title),
              h("span", { style: "font-size:1.75rem;color:var(--tj-primary,#38bdf8);font-weight:bold;" }, val),
            ]
          );
        }

        return () => {
          const s = state.value;
          const locked = s.locked;
          const cards = [card("棉签已用", `${s.swab_used} / ${s.max_uses_per_swab}`), card("剩余", String(s.remaining))];
          if (cfg.value.operator_absent_enabled && s.absent_remaining >= 0) {
            cards.push(card("离岗倒计时(秒)", String(s.absent_remaining)));
          }

          return h("section", { style: "padding:1.5rem;color:var(--tj-text-main,#e2e8f0);" }, [
            h("h2", { style: "font-size:1.5rem;margin-bottom:1rem;" }, "传感器清洁工序"),
            h(
              "div",
              {
                style: `padding:0.75rem 1rem;border-radius:0.5rem;margin-bottom:1.5rem;font-weight:bold;color:#fff;background:${locked ? "#7f1d1d" : "#14532d"};`,
              },
              locked ? "棉签已用满，请更换棉签！（计数已锁定）" : "状态：正常"
            ),
            h("div", { style: "display:flex;gap:1rem;flex-wrap:wrap;margin-bottom:1.5rem;" }, cards),
            h("div", { style: "display:flex;gap:1rem;margin-bottom:2rem;" }, [
              h("button", {
                style: "padding:0.5rem 1.25rem;border-radius:0.5rem;border:none;background:#38bdf8;color:#0f172a;font-weight:bold;cursor:pointer;",
                onClick: onReset,
              }, "更换棉签（重置）"),
              h("button", {
                style: "padding:0.5rem 1.25rem;border-radius:0.5rem;border:1px solid #334155;background:transparent;color:var(--tj-text-main,#e2e8f0);cursor:pointer;",
                onClick: onApply,
              }, "一键应用 demo 默认配置"),
            ]),

            // ---------- 三判定 → 事件 配置 ----------
            h("h2", { style: "font-size:1.25rem;margin-bottom:0.5rem;" }, "三判定 → 事件 配置"),
            h("p", { style: "color:#94a3b8;font-size:0.8rem;margin-bottom:1rem;line-height:1.5;" },
              "每个判定命中后借「对应通道激活项目」里所选事件的响应面联动报警/计数器/Toast/主页（不结算检测周期）。" +
              "事件、报警、计数器请在主程序「项目」页 / 「报警」页配置；新计数器勾「显示」即可上检测主页。默认连系统 NG（id=2）。"),
            h("div", { style: "display:flex;gap:1rem;flex-wrap:wrap;margin-bottom:1rem;" }, [
              block("① 假擦拭（视角1）", [
                field("连接事件", eventSelect("fake_wipe_event_id"), "默认 不良(NG)；选「关闭此判定」即停用"),
                field("静止超时(秒)", numInput("fake_wipe_still_time", "0.5"), "擦拭框停留超此秒数仍几乎不动 → 命中"),
                field("位移阈值(归一化)", numInput("fake_wipe_still_disp", "0.001"), "位移小于此视为不动（10px/1728≈0.0058）"),
              ]),
              block("② 棉签寿命超限", [
                field("连接事件", eventSelect("swab_over_limit_event_id"), "棉签擦满 K 个产品后触发"),
                field("棉签寿命 K（个/根）", numInput("max_uses_per_swab", "1"), "一根棉签最多擦几个产品"),
              ]),
              block("③ 操作员离开超时（视角2）", [
                field("启用", toggleSelect("operator_absent_enabled"), "默认关闭（避免误报）"),
                field("连接事件", eventSelect("operator_absent_event_id"), "视角2 长时间无人 → 触发"),
                field("离开超时(秒)", numInput("operator_absent_timeout_sec", "60"), "持续无任何检测目标超此秒数 → 命中"),
              ]),
            ]),
            h("div", { style: "display:flex;align-items:center;gap:1rem;" }, [
              h("button", {
                style: `padding:0.5rem 1.5rem;border-radius:0.5rem;border:none;background:${saving.value ? "#475569" : "#22c55e"};color:#0f172a;font-weight:bold;cursor:${saving.value ? "default" : "pointer"};`,
                disabled: saving.value,
                onClick: onSave,
              }, saving.value ? "保存中…" : "保存配置"),
              saveMsg.value ? h("span", { style: "color:#86efac;font-size:0.85rem;" }, saveMsg.value) : null,
            ]),
          ]);
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
