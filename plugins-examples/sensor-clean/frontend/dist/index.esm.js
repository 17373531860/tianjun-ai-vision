/**
 * sensor-clean 前端 v1.4.0 — 监控整页覆盖 + 项目配置 Tab，
 * 三判定 → 事件配置并入项目 Tab（不再单独占左侧导航）。
 *
 * 主程序 plugin loader 调:
 *   const mod = await import(blobUrl);
 *   await mod.default.register({ host: { vue, pinia, router, i18n, api }, registry });
 *
 * 设计:
 *   - monitor.layout.body 整页覆盖「实时监控」→ 双工位左右视频 + 棉签看板
 *   - registry.tabs.register('project', ...) → 「传感器清洁配置」专属 Tab
 *   - v1.1.0 三判定（假擦拭 / 棉签超限 / 操作员离开）在项目 Tab 配置
 *   - v1.4.0 工位1 计数许可标签（detect9(1) 语义: 许可标签出现过即解锁,
 *     不必与锚框同帧, 计一件消费一次）+ 按标签 ROI 区域（画面快照上框多边形,
 *     区域外的检出不算数, 复用主程序 /snapshot 快照与归一化多边形约定）
 */

// host.api 的 baseURL 已含 /api/v1, 故路径不带前缀; 统一用主程序已鉴权 axios
// 而非裸 fetch —— 打包后客户机主窗口是 file:// 源, 相对路径 fetch 会 Failed to fetch
// (AGENTS 第 12 条铁律)。_api 在 register() 时由 host.api 注入。
const API = "/plugins/sensor-clean/swab";
let _api = null;

// 后端 host 根地址（/snapshot 等非 /api/v1 路径用）。绝对 baseURL 时抽 host；
// 相对时返回空串 = 同源（dev 下 vite 已代理 /snapshot）。
function backendHost() {
  const b = (_api && _api.defaults && _api.defaults.baseURL) || "";
  if (/^https?:\/\//i.test(b)) {
    try {
      const u = new URL(b);
      return `${u.protocol}//${u.host}`;
    } catch (e) { /* fallthrough */ }
  }
  return "";
}

async function jget(path) {
  const r = await _api.get(`${API}${path}`);
  return r.data;
}
async function jpost(path) {
  const r = await _api.post(`${API}${path}`);
  return r.data;
}

// 工位角色定义（固定双工位）
const STATION1 = { ch: 0, title: "工位1 · 产品计数", accent: "#34d399" };
const STATION2 = { ch: 1, title: "工位2 · 更换棉签", accent: "#f87171" };

export default {
  async register({ host, registry }) {
    const { defineComponent, h, ref, onMounted, onUnmounted, nextTick } = host.vue;
    const api = host.api;
    _api = host.api;

    // ==================== 整页覆盖：双工位左右视频 + 棉签看板 ====================
    const MonitorOverride = defineComponent({
      name: "SensorCleanMonitor",
      props: {
        channelCount: { type: Number, default: 2 },
        multiChannelData: { type: Object, default: () => ({}) },
        selectedChannel: { type: Number, default: 0 },
        channelModelStats: { type: Object, default: () => ({}) },
        currentProject: { type: Object, default: null },
        actions: { type: Object, default: () => ({}) },
        streamUrlBuilder: { type: Function, default: null },
      },
      setup(props) {
        const swab = ref({
          total_products: 0,
          ng_count: 0,
          swab_used: 0,
          remaining: 11,
          max_uses_per_swab: 11,
          over_limit: false,
        });
        // MJPEG 流地址一次性算好，避免每次重渲染换 src 导致断流重连
        const streamUrls = ref({});
        let timer = null;

        async function refresh() {
          try {
            swab.value = await jget("/state");
          } catch (e) {
            /* 隔离: 拉取失败不影响面板存活 */
          }
        }

        onMounted(() => {
          if (typeof props.streamUrlBuilder === "function") {
            streamUrls.value = {
              [STATION1.ch]: props.streamUrlBuilder(STATION1.ch),
              [STATION2.ch]: props.streamUrlBuilder(STATION2.ch),
            };
          }
          refresh();
          timer = setInterval(refresh, 1000);
        });
        onUnmounted(() => {
          if (timer) clearInterval(timer);
        });

        async function onChangeSwab() {
          swab.value = await jpost("/reset");
        }
        async function onResetAll() {
          swab.value = await jpost("/reset-counts");
        }
        async function startAll() {
          const fn = props.actions?.startDetectionForChannel;
          if (!fn) return;
          for (const st of [STATION1, STATION2]) {
            try { await fn(st.ch); } catch (e) { /* 隔离 */ }
          }
        }
        async function stopAll() {
          const fn = props.actions?.stopDetectionForChannel;
          if (!fn) return;
          for (const st of [STATION1, STATION2]) {
            try { await fn(st.ch); } catch (e) { /* 隔离 */ }
          }
        }

        // hover 提亮（纯 JS, 不依赖 Tailwind hover: 是否被 purge）
        const lift = (e) => { e.target.style.filter = "brightness(1.15)"; };
        const drop = (e) => { e.target.style.filter = ""; };

        // ── 单个工位视频窗格（img 持续推流 + 主程序自动画框的 overlay canvas）──
        const videoPane = (st) =>
          h(
            "div",
            {
              class: "relative rounded-xl overflow-hidden bg-black min-h-0",
              style: `box-shadow:0 0 0 2px ${st.accent}66, 0 6px 22px rgba(0,0,0,.55);`,
            },
            [
              streamUrls.value[st.ch]
                ? h("img", {
                    src: streamUrls.value[st.ch],
                    style: "width:100%;height:100%;object-fit:contain;display:block;",
                  })
                : h(
                    "div",
                    {
                      class: "w-full h-full flex items-center justify-center text-gray-600 text-sm",
                      style: "letter-spacing:2px;",
                    },
                    "等待视频信号..."
                  ),
              // 主程序 paintPluginOverlay 按 querySelectorAll('canvas.fjjl-det-overlay')[ch] 取画框
              h("canvas", {
                class: "fjjl-det-overlay",
                style: "position:absolute;inset:0;width:100%;height:100%;pointer-events:none;",
              }),
              // 左上工位徽章 + LIVE 脉冲点
              h(
                "div",
                {
                  class: "absolute top-2 left-2 flex items-center gap-2 px-3 py-1 rounded-full font-bold text-sm",
                  style: `background:rgba(8,12,20,.72);backdrop-filter:blur(6px);color:${st.accent};`,
                },
                [
                  h("span", {
                    class: "rounded-full animate-pulse",
                    style: `display:block;width:8px;height:8px;background:${st.accent};box-shadow:0 0 8px ${st.accent};`,
                  }),
                  st.title,
                ]
              ),
            ]
          );

        // ── 看板数值卡片（顶部 accent 条 + 图标 + 大数字 + 单位）──
        const statCard = (icon, title, val, unit, color) =>
          h(
            "div",
            {
              class: "relative flex-1 bg-slate-800 border border-slate-700 rounded-lg overflow-hidden",
              style: "padding:14px 18px;",
            },
            [
              h("div", {
                class: "absolute top-0 left-0 right-0",
                style: `height:3px;background:${color};`,
              }),
              h(
                "div",
                { class: "text-xs text-gray-400 flex items-center gap-1", style: "margin-bottom:4px;" },
                `${icon} ${title}`
              ),
              h(
                "div",
                {
                  class: "font-bold",
                  style: `font-size:2.4rem;line-height:1.1;color:${color};font-variant-numeric:tabular-nums;`,
                },
                [String(val), h("span", { class: "text-gray-500", style: "font-size:.9rem;margin-left:6px;font-weight:500;" }, unit)]
              ),
            ]
          );

        // ── 棉签用量卡片（数字 + 进度条可视化）──
        const swabCard = (used, maxu, pct, over) => {
          const color = over ? "#f87171" : pct >= 80 ? "#fbbf24" : "#34d399";
          return h(
            "div",
            {
              class: "relative flex-1 bg-slate-800 border border-slate-700 rounded-lg overflow-hidden",
              style: "padding:14px 18px;",
            },
            [
              h("div", { class: "absolute top-0 left-0 right-0", style: `height:3px;background:${color};` }),
              h(
                "div",
                { class: "text-xs text-gray-400 flex items-center gap-1", style: "margin-bottom:4px;" },
                "🧹 棉签用量"
              ),
              h(
                "div",
                {
                  class: "font-bold",
                  style: `font-size:2.4rem;line-height:1.1;color:${color};font-variant-numeric:tabular-nums;`,
                },
                [String(used), h("span", { class: "text-gray-500", style: "font-size:.9rem;margin-left:6px;font-weight:500;" }, `/ ${maxu}`)]
              ),
              h(
                "div",
                { class: "rounded-full overflow-hidden", style: "margin-top:10px;height:7px;background:#0f172a;" },
                [h("i", { style: `display:block;height:100%;border-radius:9999px;transition:width .4s ease;width:${pct}%;background:${color};` })]
              ),
            ]
          );
        };

        const btn = (label, onClick, bg, fg) =>
          h(
            "button",
            {
              class: "rounded-lg font-bold text-sm",
              style: `padding:9px 22px;background:${bg};color:${fg};border:none;cursor:pointer;transition:filter .15s;`,
              onClick,
              onMouseenter: lift,
              onMouseleave: drop,
            },
            label
          );

        return () => {
          const s = swab.value;
          const over = s.over_limit;
          const used = s.swab_used || 0;
          const maxu = s.max_uses_per_swab || 11;
          const pct = maxu > 0 ? Math.min(100, Math.round((used / maxu) * 100)) : 0;
          // 操作员离开: 启用且剩余倒计时已归零 → 已离岗告警中
          const absentEnabled = !!s.operator_absent_enabled;
          const absentRemain = typeof s.absent_remaining === "number" ? s.absent_remaining : -1;
          const absentFired = absentEnabled && absentRemain === 0;
          return h(
            "div",
            {
              class: "flex flex-col gap-3 p-3",
              style:
                "height:100%;box-sizing:border-box;color:#e2e8f0;" +
                "background:radial-gradient(circle at 50% -10%,#13203a,#060a11 72%);",
            },
            [
              // ── 双工位视频（左右并排, 铺满）──
              h(
                "div",
                { class: "grid grid-cols-2 gap-3", style: "flex:1;min-height:0;" },
                [videoPane(STATION1), videoPane(STATION2)]
              ),
              // ── 棉签耗材看板 ──
              h(
                "div",
                {
                  class: "bg-slate-900 border border-slate-700 rounded-xl flex flex-col gap-3",
                  style: "flex-shrink:0;padding:14px;",
                },
                [
                  absentFired
                    ? h(
                        "div",
                        {
                          class: "rounded-lg text-center font-bold animate-pulse",
                          style: "padding:10px 16px;background:linear-gradient(90deg,#5c3a14,#915a1d);color:#ffe9c4;letter-spacing:.5px;",
                        },
                        `⚠ 操作员已离开岗位超时（工位2）— 请尽快返回`
                      )
                    : null,
                  over
                    ? h(
                        "div",
                        {
                          class: "rounded-lg text-center font-bold animate-pulse",
                          style: "padding:10px 16px;background:linear-gradient(90deg,#5c1414,#911d1d);color:#ffd7d4;letter-spacing:.5px;",
                        },
                        `⚠ 棉签已达上限 ${maxu} 件仍在擦拭 — 请立即更换棉签！累计不良 ${s.ng_count}`
                      )
                    : (!absentFired
                        ? h(
                            "div",
                            {
                              class: "rounded-lg text-center font-bold",
                              style: "padding:10px 16px;background:linear-gradient(90deg,#0f3d24,#14532d);color:#86efac;letter-spacing:.5px;",
                            },
                            "● 运行正常 — 棉签状态良好"
                          )
                        : null),
                  h("div", { class: "flex gap-3" }, [
                    statCard("📦", "总产量", s.total_products, "件", "#38bdf8"),
                    statCard("⚠", "不良 NG", s.ng_count, "件", over ? "#f87171" : "#64748b"),
                    swabCard(used, maxu, pct, over),
                  ]),
                  h("div", { class: "flex gap-2 justify-center" }, [
                    btn("▶ 开始", startAll, "#16a34a", "#f0fdf4"),
                    btn("■ 停止", stopAll, "#dc2626", "#fef2f2"),
                    btn("🧹 换棉签", onChangeSwab, "#d97706", "#fffbeb"),
                    btn("↺ 重置计数", onResetAll, "#334155", "#e2e8f0"),
                  ]),
                ]
              ),
            ]
          );
        };
      },
    });

    registry.slots.register("monitor.layout.body", MonitorOverride);

    // ==================== 整页覆盖：数据中心 → 棉签使用记录 ====================
    // 一条记录 = 一根棉签生命周期(擦第1件 → 换棉签/手动/整批重置清零)。后端 /records 落库，
    // 这里自绘 统计卡 + 轻量柱图 + 记录表 + 工位/今日筛选 + CSV 导出（零依赖，不引图表库）。
    const REASON_LABEL = { change: "换棉签", manual: "手动重置", batch_reset: "整批重置" };
    const fmtTime = (ts) => {
      if (!ts) return "-";
      try { return new Date(ts * 1000).toLocaleString("zh-CN", { hour12: false }); }
      catch (e) { return "-"; }
    };
    const csvCell = (v) => {
      const s = v == null ? "" : String(v);
      return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
    };

    const DataOverride = defineComponent({
      name: "SensorCleanData",
      setup() {
        const payload = ref({ records: [], summary: {} });
        const loading = ref(false);
        const channel = ref("");      // ""=全部工位
        const todayOnly = ref(false);
        let timer = null;

        async function load() {
          loading.value = true;
          try {
            const params = { limit: 500 };
            if (channel.value !== "") params.channel = Number(channel.value);
            if (todayOnly.value) {
              const d = new Date(); d.setHours(0, 0, 0, 0);
              params.since = d.getTime() / 1000;
            }
            const r = await _api.get(`${API}/records`, { params });
            payload.value = r.data || { records: [], summary: {} };
          } catch (e) {
            /* 隔离: 拉取失败保留上次数据 */
          } finally {
            loading.value = false;
          }
        }
        onMounted(() => { load(); timer = setInterval(load, 8000); });
        onUnmounted(() => { if (timer) clearInterval(timer); });

        function exportCsv() {
          const rows = payload.value.records || [];
          const header = ["序号", "工位", "起始时间", "结束时间", "时长(秒)", "产品数", "不良", "超限", "结束原因"];
          const lines = [header.join(",")];
          for (const r of rows) {
            lines.push([
              r.swab_seq, "通道" + r.channel, fmtTime(r.start_ts), fmtTime(r.end_ts),
              (r.duration_sec || 0).toFixed(1), r.products, r.ng,
              r.over_limit ? "是" : "否", REASON_LABEL[r.end_reason] || r.end_reason || "",
            ].map(csvCell).join(","));
          }
          const blob = new Blob(["\uFEFF" + lines.join("\n")], { type: "text/csv;charset=utf-8" });
          const url = URL.createObjectURL(blob);
          const a = document.createElement("a");
          a.href = url;
          a.download = "棉签记录_" + new Date().toISOString().slice(0, 10) + ".csv";
          document.body.appendChild(a); a.click(); a.remove();
          setTimeout(() => URL.revokeObjectURL(url), 1000);
        }

        const card = (label, value, accent) => h("div", {
          style: "flex:1;min-width:120px;padding:14px 16px;border-radius:10px;" +
            "background:#0d1b2a;border:1px solid #1f2d3d;",
        }, [
          h("div", { style: "color:#94a3b8;font-size:0.78rem;margin-bottom:6px;" }, label),
          h("div", { style: `color:${accent || "#e2e8f0"};font-size:1.5rem;font-weight:bold;` }, String(value)),
        ]);

        return () => {
          const recs = payload.value.records || [];
          const sm = payload.value.summary || {};
          // 柱图: 取最近 40 根反转成时间正序
          const bars = recs.slice(0, 40).slice().reverse();
          const maxP = Math.max(1, ...bars.map((b) => b.products || 0));
          return h("div", {
            style: "padding:1rem 1.25rem;color:#e2e8f0;height:100%;overflow-y:auto;box-sizing:border-box;",
          }, [
            h("div", { style: "display:flex;align-items:center;flex-wrap:wrap;gap:12px;margin-bottom:1rem;" }, [
              h("h2", { style: "font-size:1.15rem;font-weight:bold;color:#58a6ff;margin:0;" }, "棉签使用记录"),
              h("span", { style: "color:#94a3b8;font-size:0.8rem;" }, "一条 = 一根棉签（擦第1件 → 换棉签/重置清零）"),
              h("div", { style: "flex:1;" }),
              h("select", {
                style: "padding:6px 10px;border-radius:6px;background:#0d1b2a;color:#e2e8f0;border:1px solid #1f2d3d;",
                value: channel.value,
                onChange: (ev) => { channel.value = ev.target.value; load(); },
              }, [
                h("option", { value: "" }, "全部工位"),
                h("option", { value: "0" }, "工位1·计数(通道0)"),
                h("option", { value: "1" }, "工位2·换棉签(通道1)"),
              ]),
              h("button", {
                style: "padding:6px 14px;border-radius:6px;border:1px solid #1f6feb;" +
                  `background:${todayOnly.value ? "#1f6feb" : "transparent"};color:#f0f6fc;cursor:pointer;`,
                onClick: () => { todayOnly.value = !todayOnly.value; load(); },
              }, todayOnly.value ? "今日" : "全部时段"),
              h("button", {
                style: "padding:6px 14px;border-radius:6px;border:none;background:#238636;color:#fff;cursor:pointer;",
                onClick: exportCsv,
              }, "导出 CSV"),
              h("button", {
                style: "padding:6px 14px;border-radius:6px;border:1px solid #30363d;background:transparent;color:#c9d1d9;cursor:pointer;",
                onClick: load,
              }, loading.value ? "刷新中…" : "刷新"),
            ]),
            h("div", { style: "display:flex;gap:12px;flex-wrap:wrap;margin-bottom:1.25rem;" }, [
              card("棉签根数", sm.swab_count || 0, "#58a6ff"),
              card("产品总数", sm.total_products || 0, "#3fb950"),
              card("不良总数", sm.total_ng || 0, "#f85149"),
              card("超限根数", sm.over_limit_count || 0, "#d29922"),
              card("均产品/根", sm.avg_products || 0, "#a371f7"),
              card("均时长(秒)", sm.avg_duration_sec || 0, "#79c0ff"),
            ]),
            h("div", {
              style: "margin-bottom:1.25rem;padding:14px;border-radius:10px;background:#0d1b2a;border:1px solid #1f2d3d;",
            }, [
              h("div", { style: "color:#94a3b8;font-size:0.8rem;margin-bottom:10px;" },
                "每根棉签擦拭产品数（最近 40 根 · 红色=曾超限）"),
              bars.length === 0
                ? h("div", { style: "color:#64748b;font-size:0.85rem;padding:20px 0;text-align:center;" }, "暂无记录")
                : h("div", { style: "display:flex;align-items:flex-end;gap:3px;height:120px;" },
                    bars.map((b) => h("div", {
                      title: `#${b.swab_seq} 产品${b.products} 不良${b.ng}`,
                      style: "flex:1;min-width:4px;border-radius:2px 2px 0 0;" +
                        `height:${Math.round(((b.products || 0) / maxP) * 100)}%;` +
                        `background:${b.over_limit ? "#f85149" : "#1f6feb"};`,
                    }))),
            ]),
            h("div", { style: "border-radius:10px;overflow:hidden;border:1px solid #1f2d3d;" }, [
              h("table", { style: "width:100%;border-collapse:collapse;font-size:0.83rem;" }, [
                h("thead", {}, h("tr", { style: "background:#0d1b2a;color:#94a3b8;text-align:left;" },
                  ["序号", "工位", "起始", "结束", "时长(s)", "产品", "不良", "超限", "结束原因"].map(
                    (c) => h("th", { style: "padding:8px 10px;font-weight:600;border-bottom:1px solid #1f2d3d;" }, c)))),
                h("tbody", {}, recs.length === 0
                  ? [h("tr", {}, h("td", { colspan: 9, style: "padding:20px;text-align:center;color:#64748b;" }, "暂无棉签记录"))]
                  : recs.map((r) => h("tr", { style: "border-bottom:1px solid #161b22;" }, [
                      h("td", { style: "padding:7px 10px;" }, "#" + r.swab_seq),
                      h("td", { style: "padding:7px 10px;" }, "通道" + r.channel),
                      h("td", { style: "padding:7px 10px;color:#94a3b8;" }, fmtTime(r.start_ts)),
                      h("td", { style: "padding:7px 10px;color:#94a3b8;" }, fmtTime(r.end_ts)),
                      h("td", { style: "padding:7px 10px;" }, (r.duration_sec || 0).toFixed(1)),
                      h("td", { style: "padding:7px 10px;color:#3fb950;font-weight:600;" }, r.products),
                      h("td", { style: `padding:7px 10px;font-weight:600;color:${r.ng > 0 ? "#f85149" : "#8b949e"};` }, r.ng),
                      h("td", { style: "padding:7px 10px;" }, r.over_limit
                        ? h("span", { style: "color:#d29922;" }, "超限")
                        : h("span", { style: "color:#3fb950;" }, "正常")),
                      h("td", { style: "padding:7px 10px;color:#8b949e;" }, REASON_LABEL[r.end_reason] || r.end_reason || "-"),
                    ]))),
              ]),
            ]),
          ]);
        };
      },
    });

    registry.slots.register("data.layout.body", DataOverride);

    // 列级 Toast 分流：只屏蔽「内置 合格(1)/不良(2)」——这类由插件自绘红横幅作权威展示，
    // 主程序按周期判的合格/不良提示对清洁工序无意义且会盖在视频上闪；
    // 其它（自定义）事件仍渲染标准提示框，保留主程序事件提示能力（计数/报警本就一直有效）。
    // 老主程序未透传 eventId 时回退按 OK/NG 标识判断，保持原有屏蔽行为。
    const _isBuiltinOkNg = (t) => {
      if (!t) return true;
      // 后端事件日志里 event_id 可能是数字也可能是字符串("1"/"2")，统一归一为数字再判。
      const eid = t.eventId == null || t.eventId === "" ? null : Number(t.eventId);
      if (eid === 1 || eid === 2) return true;
      // 老主程序未透传 eventId 时回退按 OK/NG 标识判断，保持原有屏蔽行为。
      if (eid == null && (t.toastId === "ok" || t.toastId === "ng")) return true;
      return false;
    };
    registry.slots.register(
      "cycle-result.indicator",
      defineComponent({
        name: "SensorCleanCycleToast",
        props: {
          toast: { type: Object, default: null },
          channelId: { type: Number, default: 0 },
        },
        setup(props) {
          return () => {
            const t = props.toast;
            if (_isBuiltinOkNg(t)) return null;
            return h(
              "div",
              {
                class: "px-4 py-3 rounded-xl shadow-2xl text-white font-bold text-center pointer-events-auto",
                style: {
                  backgroundColor: t.color || "#2563eb",
                  fontSize: ((t.fontSize || 16) / 16) + "rem",
                },
              },
              [
                h("div", { class: "font-bold" }, t.title || "事件"),
                t.subtitle ? h("div", { class: "text-sm opacity-80" }, t.subtitle) : null,
              ]
            );
          };
        },
      })
    );

    // ==================== 项目配置 tab：双工位参数 ====================
    // 配置状态提到 register 作用域: ConfigTab 渲染用它, saveSensorCleanConfig 也用它。
    // 这样主程序「保存配置」按钮触发的 onSave 能拿到 Tab 里编辑的最新值
    // (即便已切走未卸载、残留在内存里), 插件不再需要自带保存按钮。
    const cfg = ref({});
    async function saveSensorCleanConfig() {
      // Tab 从未打开 (cfg 空) 时跳过, 避免拿空配置覆盖已存
      if (!cfg.value || Object.keys(cfg.value).length === 0) return { skipped: true };
      const body = {
        max_uses_per_swab: Number(cfg.value.max_uses_per_swab) || 11,
        count_anchor_label: cfg.value.count_anchor_label || "",
        count_require_label: cfg.value.count_require_label || "",
        label_rois: cfg.value.label_rois || {},
        swap_label: cfg.value.swap_label || "",
        move_threshold: Number(cfg.value.move_threshold),
        lock_spatial: Number(cfg.value.lock_spatial),
        lock_time: Number(cfg.value.lock_time),
        move_confirm_frames: Number(cfg.value.move_confirm_frames),
        lost_frame_thresh: Number(cfg.value.lost_frame_thresh),
        force_lock_frames: Number(cfg.value.force_lock_frames),
        dist_y_weight: Number(cfg.value.dist_y_weight) || 1.0,
        lost_gone_sec: Number(cfg.value.lost_gone_sec) || 0,
        force_lock_sec: Number(cfg.value.force_lock_sec) || 0,
        min_confidence: Number(cfg.value.min_confidence) || 0,
        alarm_event: cfg.value.alarm_event || "",
        swab_over_limit_event_id: Number(cfg.value.swab_over_limit_event_id) || 0,
        fake_wipe_event_id: Number(cfg.value.fake_wipe_event_id) || 0,
        fake_wipe_still_time: Number(cfg.value.fake_wipe_still_time) || 2.0,
        fake_wipe_still_disp: Number(cfg.value.fake_wipe_still_disp) || 0.0058,
        operator_absent_enabled: !!cfg.value.operator_absent_enabled,
        operator_absent_event_id: Number(cfg.value.operator_absent_event_id) || 0,
        operator_absent_timeout_sec: Number(cfg.value.operator_absent_timeout_sec) || 600,
        normal_count_event_id: Number(cfg.value.normal_count_event_id) || 0,
        suppress_main_settle_alarm: !!cfg.value.suppress_main_settle_alarm,
      };
      const res = await _api.post(`${API}/config`, body);
      if (res.data?.config) cfg.value = res.data.config;
      return { saved: !!res.data?.saved };
    }

    const ConfigTab = defineComponent({
      name: "SensorCleanConfig",
      setup() {
        const importing = ref(false);
        const importMsg = ref("");
        const eventOptions = ref([{ id: 1, name: "合格(OK)" }, { id: 2, name: "不良(NG)" }]);

        async function loadEvents() {
          try {
            const data = await api.get("/projects");
            const items = (data?.data?.items) || data?.data || data?.items || data || [];
            const seen = new Map([[1, "合格(OK)"], [2, "不良(NG)"]]);
            (Array.isArray(items) ? items : []).forEach((p) =>
              (p.events_config || []).forEach((e) => {
                if (e && e.id != null && !seen.has(e.id)) seen.set(e.id, e.name || `事件${e.id}`);
              })
            );
            eventOptions.value = [...seen].map(([id, name]) => ({ id, name }));
          } catch (e) { /* 隔离: 至少有 OK/NG 兜底 */ }
        }

        async function onImport() {
          importing.value = true;
          importMsg.value = "";
          try {
            const res = await api.post(`${API}/import-project`);
            const data = res.data;
            if (data?.ok) {
              const names = (data.projects || []).map((p) => p.name).join("、");
              importMsg.value = `已导入并激活到双工位：${names}（去监控页配好摄像头即对真推理计数）`;
            } else {
              importMsg.value = "导入失败：" + (data?.msg || "见后端日志");
            }
          } catch (e) {
            importMsg.value = "导入出错：" + (e?.message || e);
          } finally {
            importing.value = false;
          }
        }

        async function load() {
          try {
            cfg.value = await jget("/config");
          } catch (e) {
            /* 隔离 */
          }
        }
        onMounted(() => {
          load();
          loadEvents();
        });

        // ==================== v1.4.0 按标签 ROI 编辑器 ====================
        // 画面快照 (主程序 /snapshot) 上单击加顶点画多边形, 归一化坐标存
        // cfg.label_rois[role], 随右上角「保存配置」一并落库。
        const ROI_ROLES = [
          { key: "count_anchor", label: "工位1 产品计数标签", ws: 1 },
          { key: "count_require", label: "工位1 计数许可标签", ws: 1 },
          { key: "fake_wipe", label: "工位1 假擦拭标签", ws: 1 },
          { key: "swap", label: "工位2 换棉签标签", ws: 2 },
        ];
        const roiEditing = ref(null);   // 当前编辑的 role 对象, null=关闭
        const roiPoints = ref([]);      // [[nx,ny],...] 归一化顶点
        const roiCanvas = ref(null);
        const roiImgFailed = ref(false);
        let roiImg = null;

        function roleChannel(role) {
          if (role.key === "swap") return Number(cfg.value.swap_channel ?? 1);
          const chs = cfg.value.count_channels;
          return Number(Array.isArray(chs) && chs.length ? chs[0] : 0);
        }

        function roiRedraw() {
          const canvas = roiCanvas.value;
          if (!canvas) return;
          const ctx = canvas.getContext("2d");
          if (roiImg) {
            ctx.drawImage(roiImg, 0, 0, canvas.width, canvas.height);
          } else {
            ctx.fillStyle = "#1e293b";
            ctx.fillRect(0, 0, canvas.width, canvas.height);
            ctx.fillStyle = "#94a3b8";
            ctx.font = "16px sans-serif";
            ctx.fillText("无画面快照（通道未连接），仍可按比例框区域", 20, 40);
          }
          const pts = roiPoints.value;
          if (!pts.length) return;
          const px = pts.map((p) => [p[0] * canvas.width, p[1] * canvas.height]);
          ctx.lineWidth = 2;
          ctx.strokeStyle = "#22d3ee";
          ctx.fillStyle = "rgba(34,211,238,0.15)";
          ctx.beginPath();
          ctx.moveTo(px[0][0], px[0][1]);
          for (let i = 1; i < px.length; i++) ctx.lineTo(px[i][0], px[i][1]);
          if (px.length >= 3) ctx.closePath();
          ctx.fill();
          ctx.stroke();
          px.forEach(([x, y], i) => {
            ctx.beginPath();
            ctx.arc(x, y, 5, 0, Math.PI * 2);
            ctx.fillStyle = i === 0 ? "#4ade80" : "#22d3ee";
            ctx.fill();
          });
        }

        function roiOpen(role) {
          roiEditing.value = role;
          const saved = (cfg.value.label_rois || {})[role.key];
          roiPoints.value = Array.isArray(saved) ? saved.map((p) => [p[0], p[1]]) : [];
          roiImg = null;
          roiImgFailed.value = false;
          nextTick(() => {
            const canvas = roiCanvas.value;
            if (!canvas) return;
            const img = new Image();
            img.crossOrigin = "anonymous";
            img.src = `${backendHost()}/snapshot?channel=${roleChannel(role)}&t=${Date.now()}`;
            img.onload = () => {
              roiImg = img;
              canvas.width = img.naturalWidth;
              canvas.height = img.naturalHeight;
              roiRedraw();
            };
            img.onerror = () => {
              roiImgFailed.value = true;
              canvas.width = 960;
              canvas.height = 540;
              roiRedraw();
            };
          });
        }

        function roiClick(ev) {
          const canvas = roiCanvas.value;
          if (!canvas) return;
          const rect = canvas.getBoundingClientRect();
          const nx = (ev.clientX - rect.left) / rect.width;
          const ny = (ev.clientY - rect.top) / rect.height;
          roiPoints.value = [...roiPoints.value,
            [Math.min(1, Math.max(0, nx)), Math.min(1, Math.max(0, ny))]];
          roiRedraw();
        }

        function roiUndo() {
          roiPoints.value = roiPoints.value.slice(0, -1);
          roiRedraw();
        }

        function roiSaveAndClose() {
          const role = roiEditing.value;
          if (!role) return;
          const rois = { ...(cfg.value.label_rois || {}) };
          if (roiPoints.value.length >= 3) {
            rois[role.key] = roiPoints.value.map((p) =>
              [Math.round(p[0] * 10000) / 10000, Math.round(p[1] * 10000) / 10000]);
          } else {
            delete rois[role.key];
          }
          cfg.value = { ...cfg.value, label_rois: rois };
          roiEditing.value = null;
        }

        function roiClearRole(role) {
          const rois = { ...(cfg.value.label_rois || {}) };
          delete rois[role.key];
          cfg.value = { ...cfg.value, label_rois: rois };
        }

        const roiBtn = (text, onClick, opts = {}) =>
          h("button", {
            style: "padding:5px 14px;border-radius:6px;border:1px solid #334155;" +
              `background:${opts.bg || "#1e293b"};color:${opts.color || "#e2e8f0"};` +
              "font-size:0.8rem;cursor:pointer;" + (opts.style || ""),
            disabled: !!opts.disabled,
            onClick,
          }, text);

        const roiRow = (role) => {
          const saved = (cfg.value.label_rois || {})[role.key];
          const active = Array.isArray(saved) && saved.length >= 3;
          return h("div", {
            style: "display:flex;align-items:center;gap:10px;margin-bottom:10px;",
          }, [
            h("label", {
              style: "width:200px;color:#94a3b8;font-size:0.875rem;flex-shrink:0;",
            }, `${role.label} ROI`),
            h("span", {
              style: `font-size:0.8rem;font-weight:bold;color:${active ? "#4ade80" : "#64748b"};` +
                "width:110px;flex-shrink:0;",
            }, active ? `已框定 ${saved.length} 点` : "未限制(全画面)"),
            roiBtn(active ? "重新绘制" : "绘制区域", () => roiOpen(role),
              { bg: "#1f6feb", color: "#f0f6fc" }),
            active ? roiBtn("清除", () => roiClearRole(role),
              { bg: "#3f1d1d", color: "#fca5a5" }) : null,
          ]);
        };

        const roiEditorOverlay = () => {
          const role = roiEditing.value;
          if (!role) return null;
          return h("div", {
            style: "position:fixed;inset:0;z-index:9000;background:rgba(2,6,23,0.85);" +
              "display:flex;align-items:center;justify-content:center;",
          }, [
            h("div", {
              style: "background:#0f172a;border:1px solid #334155;border-radius:12px;" +
                "padding:16px;max-width:90vw;max-height:92vh;display:flex;" +
                "flex-direction:column;gap:10px;",
            }, [
              h("div", { style: "display:flex;align-items:center;gap:12px;" }, [
                h("span", { style: "color:#e2e8f0;font-weight:bold;" },
                  `绘制「${role.label}」ROI 区域（工位${role.ws} 画面快照）`),
                h("span", { style: "color:#64748b;font-size:0.78rem;flex:1;" },
                  "单击添加顶点，至少 3 点；保存后区域外的该标签检出不算数"),
              ]),
              roiImgFailed.value
                ? h("div", { style: "color:#fbbf24;font-size:0.78rem;" },
                    "快照获取失败（通道未连接视频）— 画布按 16:9 比例，仍可框定归一化区域")
                : null,
              h("div", {
                style: "overflow:auto;max-height:70vh;background:#000;border-radius:8px;",
              }, [
                h("canvas", {
                  ref: roiCanvas,
                  style: "max-width:86vw;max-height:68vh;cursor:crosshair;display:block;",
                  onClick: roiClick,
                }),
              ]),
              h("div", { style: "display:flex;gap:10px;justify-content:flex-end;" }, [
                h("span", { style: "color:#94a3b8;font-size:0.8rem;flex:1;align-self:center;" },
                  `顶点数: ${roiPoints.value.length}${roiPoints.value.length >= 3 ? "（已可保存）" : ""}`),
                roiBtn("撤销上一点", roiUndo, { disabled: !roiPoints.value.length }),
                roiBtn("清空", () => { roiPoints.value = []; roiRedraw(); },
                  { disabled: !roiPoints.value.length }),
                roiBtn("取消", () => { roiEditing.value = null; }),
                roiBtn(roiPoints.value.length >= 3 ? "保存区域" : "保存(不限制)",
                  roiSaveAndClose, { bg: "#238636", color: "#f0f6fc" }),
              ]),
            ]),
          ]);
        };

        const row = "display:flex;align-items:center;gap:10px;margin-bottom:10px;";
        const lbl = "width:200px;color:#94a3b8;font-size:0.875rem;flex-shrink:0;";
        const inp =
          "flex:1;max-width:240px;padding:6px 10px;border:1px solid #334155;" +
          "border-radius:6px;background:#0f172a;color:#e2e8f0;font-size:0.875rem;";
        const sel = inp + "cursor:pointer;";

        const numField = (label, key, step, hint) =>
          h("div", { style: row }, [
            h("label", { style: lbl }, label),
            h("input", {
              type: "number",
              step: step || "any",
              value: cfg.value[key],
              style: inp,
              onInput: (e) => { cfg.value[key] = e.target.value; },
            }),
            hint ? h("span", { style: "color:#64748b;font-size:0.75rem;" }, hint) : null,
          ]);
        const txtField = (label, key, hint) =>
          h("div", { style: row }, [
            h("label", { style: lbl }, label),
            h("input", {
              type: "text",
              value: cfg.value[key],
              style: inp,
              onInput: (e) => { cfg.value[key] = e.target.value; },
            }),
            hint ? h("span", { style: "color:#64748b;font-size:0.75rem;" }, hint) : null,
          ]);
        const eventField = (label, key, hint, closeLabel = "（关闭此判定）") =>
          h("div", { style: row }, [
            h("label", { style: lbl }, label),
            h(
              "select",
              {
                style: sel,
                value: String(Number(cfg.value[key]) || 0),
                onChange: (ev) => { cfg.value[key] = Number(ev.target.value); },
              },
              [
                h("option", { value: "0" }, closeLabel),
                ...eventOptions.value.map((o) =>
                  h("option", {
                    value: String(o.id),
                  }, `${o.id} · ${o.name}`)
                ),
              ]
            ),
            hint ? h("span", { style: "color:#64748b;font-size:0.75rem;" }, hint) : null,
          ]);
        const boolField = (label, key, hint) => {
          const on = cfg.value[key] === true || cfg.value[key] === 1 || cfg.value[key] === "1"
            || String(cfg.value[key]).toLowerCase() === "true";
          return h("div", { style: row }, [
            h("label", { style: lbl }, label),
            h(
              "select",
              {
                style: sel,
                value: on ? "1" : "0",
                onChange: (ev) => { cfg.value[key] = ev.target.value === "1"; },
              },
              [
                h("option", { value: "0" }, "关闭"),
                h("option", { value: "1" }, "启用"),
              ]
            ),
            hint ? h("span", { style: "color:#64748b;font-size:0.75rem;" }, hint) : null,
          ]);
        };
        const group = (title, children) =>
          h("section", { style: "margin-bottom:1.5rem;" }, [
            h(
              "h3",
              { style: "color:#58a6ff;font-size:1rem;margin-bottom:0.75rem;font-weight:bold;" },
              title
            ),
            ...children,
          ]);

        return () =>
          h("div", { style: "padding:1rem;color:#e2e8f0;max-width:720px;height:100%;overflow-y:auto;box-sizing:border-box;" }, [
            h(
              "p",
              { style: "color:#94a3b8;margin-bottom:1.25rem;font-size:0.875rem;" },
              "双工位传感器清洁参数（保存即热加载；工位1=产品计数·通道0，工位2=换棉签·通道1，固定）。"
            ),
            h("section", {
              style: "margin-bottom:1.5rem;padding:14px;border:1px solid #1f6feb;" +
                "border-radius:8px;background:#0d1b2a;",
            }, [
              h("h3", { style: "color:#58a6ff;font-size:1rem;margin-bottom:6px;font-weight:bold;" },
                "一键导入项目"),
              h("p", { style: "color:#94a3b8;font-size:0.8rem;margin-bottom:10px;" },
                "创建视角1/视角2 两个正式项目 + 登记模型并激活到双工位（幂等，可重复点）。" +
                "导入后通道即挂传感器清洁项目，无需手动建项目。"),
              h("div", { style: "display:flex;align-items:center;gap:14px;" }, [
                h("button", {
                  style: "padding:8px 20px;border-radius:6px;border:none;background:#1f6feb;" +
                    "color:#f0f6fc;font-weight:bold;cursor:pointer;",
                  disabled: importing.value,
                  onClick: onImport,
                }, importing.value ? "导入中..." : "一键导入传感器清洁项目"),
                importMsg.value
                  ? h("span", { style: "color:#3fb950;font-size:0.8rem;" }, importMsg.value)
                  : null,
              ]),
            ]),
            group("耗材约束", [
              numField("单棉签最大产品数 (K)", "max_uses_per_swab", "1", "擦满 K 后继续擦记不良"),
            ]),
            group("识别标签", [
              txtField("工位1 产品计数标签", "count_anchor_label", "模型输出的产品类别名"),
              txtField("工位1 计数许可标签", "count_require_label",
                "留空=不启用；它出现过即解锁计数(不必同帧)，计一件需再见一次"),
              txtField("工位2 换棉签标签", "swap_label", "模型输出的换棉签类别名"),
            ]),
            group("标签 ROI 区域（区域外的检出不算数；不画=全画面）",
              ROI_ROLES.map(roiRow)),
            group("计数防抖 (决定计数准不准)", [
              numField("移动判定阈值 (归一化)", "move_threshold", "0.0001"),
              numField("位置锁范围 (归一化)", "lock_spatial", "0.0001"),
              numField("位置锁/冷却 (秒)", "lock_time", "0.1"),
              numField("移动确认帧数", "move_confirm_frames", "1"),
              numField("丢失确认帧数", "lost_frame_thresh", "1"),
              numField("计数后强制锁定帧", "force_lock_frames", "1", "源帧率高于推理需调大; 强锁秒>0时本项不生效"),
              numField("纵向位移权重", "dist_y_weight", "0.0001", "=画面高/宽(1728x1080填0.625, 16:9填0.5625); 1=等权老行为"),
              numField("离场确认秒", "lost_gone_sec", "0.01", "锚缺席≥N秒确认离开(抗丢帧); 0=按丢失帧数"),
              numField("计数后强锁秒", "force_lock_sec", "0.1", "计数后锁定N秒防重复(抗丢帧); 0=按锁定帧数"),
              numField("插件置信度地板", "min_confidence", "0.05", "低于此置信度的检出不进计数/许可/换棉签判定; 0=跟随监控页滑条"),
            ]),
            group("计件 → 事件（正常擦一件联动主程序报警/计数器/Toast）", [
              eventField(
                "正常计件 · 连接事件",
                "normal_count_event_id",
                "默认 合格(OK)；选关闭则仅插件看板计数",
                "（关闭，计件不连事件）"
              ),
              boolField(
                "抑制主程序周期结算塔灯",
                "suppress_main_settle_alarm",
                "默认启用：主程序并行结算不再闪灯，只留本页三判定+计件事件"
              ),
            ]),
            group("三判定 → 事件（命中后联动报警/计数器/Toast，不结算周期）", [
              eventField("① 假擦拭 · 连接事件", "fake_wipe_event_id", "默认 不良(NG)；选关闭即停用"),
              numField("假擦拭 · 静止超时(秒)", "fake_wipe_still_time", "0.5"),
              numField("假擦拭 · 位移阈值(归一化)", "fake_wipe_still_disp", "0.001"),
              eventField("② 棉签寿命超限 · 连接事件", "swab_over_limit_event_id", "擦满 K 个产品后触发"),
              boolField("③ 操作员离开 · 启用", "operator_absent_enabled", "默认关闭，避免误报"),
              eventField("③ 操作员离开 · 连接事件", "operator_absent_event_id"),
              numField("③ 操作员离开 · 超时(秒)", "operator_absent_timeout_sec", "60"),
            ]),
            group("报警（兼容老配置）", [
              txtField("超限硬件报警事件类型", "alarm_event", "留空=不触发；推荐用上节「连接事件」"),
            ]),
            h(
              "p",
              {
                style: "margin-top:0.5rem;padding:10px 14px;border-radius:6px;" +
                  "background:#0d2818;border:1px solid #238636;color:#86efac;font-size:0.85rem;",
              },
              "● 以上参数随页面右上角【保存配置】按钮一并保存，无需单独保存。"
            ),
            roiEditorOverlay(),
          ]);
      },
    });

    if (registry?.tabs?.register) {
      registry.tabs.register("project", {
        key: "sensor-clean",
        label: "传感器清洁配置",
        component: ConfigTab,
        // 主程序「保存配置」按钮触发: 本 Tab 参数随项目一并落库 (v3.27.x 平台能力)
        onSave: saveSensorCleanConfig,
      });
    }

    // ==================== 锁定双工位（输入源页固定为 2 通道）====================
    try {
      const ws = await api.get("/workstations/");
      if ((ws?.data?.channel_count || 1) !== 2) {
        await api.post("/workstations/mode", { channel_count: 2 });
      }
    } catch (e) {
      /* 隔离: 锁通道失败不影响插件其余能力 */
    }

    return {
      slots: ["monitor.layout.body", "data.layout.body", "cycle-result.indicator"],
      tab: "project/sensor-clean",
      channels: 2,
    };
  },

  async unregister({ registry }) {
    if (registry?.slots?.unregister) {
      registry.slots.unregister("monitor.layout.body");
      registry.slots.unregister("data.layout.body");
      registry.slots.unregister("cycle-result.indicator");
    }
    if (registry?.tabs?.unregister) registry.tabs.unregister("project", "sensor-clean");
  },
};
