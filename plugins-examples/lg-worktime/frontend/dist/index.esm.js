/**
 * LG 工时看板 — 前端插件 (Tier 3, v1.4.0)
 *   Monitor = LG 领导看板 (KPI 结构保留, 配色对齐展会 HUD)
 *   其它 7 页 = 展会 showcase-app.html iframe (项目/模型/源/数据/MES/报警/设置)
 *
 * 定位: 领导看板。v1.2 三种工位数三种布局 (客户明确要求各自设计, 不做复制粘贴):
 *
 *   [单工位 mode-single] 数据全量, 画面 16:9 不失衡, 三列:
 *   ┌ 顶栏 | KPI 行(含最短/最长副行) ─────────────────────────────────┐
 *   ├ [视频(16:9)+状态条+流程带] [当前周期独立列] [LEAN环/分解图列]    │
 *   ├ [步骤统计表]  [近 7 天趋势]                                      │
 *   └──────────────────────────────────────────────────────────────┘
 *
 *   [双工位 mode-dual] 两面板并排, 各自带画面+流程带 + 窄右栏 LEAN 图。
 *
 *   [三工位 mode-focus] 对齐 LG 原版"3 路缩略列+主画面"形态:
 *   ┌ [缩略列: 3 路小画面     ] [焦点工位: 大画面+状态条+流程带] [窄右栏] ┐
 *   │  点击切换焦点, 带状态点                                            │
 *   └ 底排同上 ─────────────────────────────────────────────────────────┘
 *
 *   顶栏选具体工位 → 任何模式下都退化为 mode-single 独占放大(数据全量)。
 *
 * 另注册 project.step-cell.durations: 项目步骤表里直接给每步选 VA/BVA/NVA
 * (存 steps_config[i].plugin_data["lg-worktime"].value_type, 经主程序
 *  PUT /projects/{id}/plugin-data 精准写入)。
 *
 * 数据面:
 *   /api/v1/plugins/lg-worktime/dashboard/live          1s  实时
 *   /api/v1/plugins/lg-worktime/dashboard/summary       5s  今日汇总
 *   /api/v1/plugins/lg-worktime/dashboard/step-averages 15s 步骤统计
 *   /api/v1/plugins/lg-worktime/dashboard/trend         60s 多日趋势
 *
 * 约束: ESM 单文件 / 无 bare import / host 注入 vue+echarts+api (不变量 12/13);
 *        host.echarts 缺失时图表区文字兜底, 不让主程序崩。
 */

const CC = "lg-worktime";
const BASE = `/plugins/${CC}/dashboard`;

const VT_META = {
  VA: { label: "VA 增值", short: "VA", color: "#10b981" },
  BVA: { label: "BVA 必要非增值", short: "BVA", color: "#eab308" },
  NVA: { label: "NVA 非增值", short: "NVA", color: "#ef4444" },
};

let _registeredSlots = [];

function fmtSec(v) {
  if (v == null || isNaN(v)) return "—";
  const n = Number(v);
  if (n < 60) return n.toFixed(1) + "s";
  const m = Math.floor(n / 60);
  const s = n - m * 60;
  if (m < 60) return `${m}m${Math.round(s)}s`;
  const hh = Math.floor(m / 60);
  return `${hh}h${m - hh * 60}m`;
}

function fmtPct(v) {
  if (v == null || isNaN(v)) return "—";
  return Number(v).toFixed(1) + "%";
}

// mm:ss (视频进度条时间), 1h+ 显示 h:mm:ss
function fmtClock(v) {
  if (v == null || isNaN(v)) return "--:--";
  const n = Math.max(0, Math.round(Number(v)));
  const s = String(n % 60).padStart(2, "0");
  const m = Math.floor(n / 60);
  if (m < 60) return `${m}:${s}`;
  return `${Math.floor(m / 60)}:${String(m % 60).padStart(2, "0")}:${s}`;
}

// HH:MM:SS (操作日志行时间)
function fmtLogTime(epochMs) {
  const d = new Date(epochMs);
  const p = (x) => String(x).padStart(2, "0");
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

function vtBadge(h, vt) {
  const meta = VT_META[vt] || VT_META.VA;
  return h("span", {
    class: "lgwt-vt-badge",
    style: { borderColor: meta.color, color: meta.color },
  }, meta.short);
}

// ==================== ECharts: LEAN 堆叠横条 (当前轮 vs 今日合格均值) ====================
function buildLeanStackChart(host) {
  const { defineComponent, h, ref, onMounted, onBeforeUnmount, watch } = host.vue;
  return defineComponent({
    name: "LgwtLeanStack",
    props: {
      live: { type: Object, default: null },     // {va,bva,nva_total} 当前轮实时
      avg: { type: Object, default: null },      // {va,bva,nva_total} 今日均值 (口径见 avgLabel)
      avgLabel: { type: String, default: "今日合格均值" },   // 统计范围可配 → 轴标签跟随
      nvaLabel: { type: String, default: "NVA 非增值(含等待)" }, // 等待归类可配 → 图例跟随
    },
    setup(props) {
      const domRef = ref(null);
      let inst = null;
      let ro = null;

      const buildOption = () => {
        const l = props.live || {};
        const a = props.avg || {};
        // 段内百分比标签 (对齐 LG 原版"动作价值分析"柱内 % 表达); 段太窄不标, 防重叠
        const totals = [
          (a.va || 0) + (a.bva || 0) + (a.nva_total || 0),   // dataIndex 0: 今日合格均值
          (l.va || 0) + (l.bva || 0) + (l.nva_total || 0),   // dataIndex 1: 当前轮(实时)
        ];
        const seg = (name, color, liveV, avgV) => ({
          name, type: "bar", stack: "lean",
          barWidth: 26,
          itemStyle: { color },
          label: {
            show: true, position: "inside",
            color: "#0f172a", fontSize: 9, fontWeight: 700,
            formatter: (p) => {
              const tot = totals[p.dataIndex];
              if (!tot || !p.value) return "";
              const pct = (p.value / tot) * 100;
              return pct >= 12 ? pct.toFixed(0) + "%" : "";
            },
          },
          data: [Number(avgV || 0), Number(liveV || 0)],
        });
        return {
          backgroundColor: "transparent",
          // bottom 留足两层: 秒数刻度 (~14px) + 图例 (~16px), 不再互相压叠
          grid: { left: 86, right: 24, top: 8, bottom: 44 },
          tooltip: {
            trigger: "axis", axisPointer: { type: "shadow" },
            valueFormatter: (v) => (Number(v) || 0).toFixed(1) + "s",
          },
          legend: {
            show: true, bottom: 0, itemWidth: 12, itemHeight: 8,
            textStyle: { color: "#94a3b8", fontSize: 10 },
          },
          xAxis: {
            type: "value",
            axisLabel: { color: "#64748b", fontSize: 10, formatter: "{value}s" },
            splitLine: { lineStyle: { color: "#1e293b" } },
          },
          yAxis: {
            type: "category",
            data: [props.avgLabel, "当前轮(实时)"],
            axisLabel: { color: "#cbd5e1", fontSize: 11 },
            axisLine: { lineStyle: { color: "#334155" } },
          },
          series: [
            seg("VA 增值", VT_META.VA.color, l.va, a.va),
            seg("BVA 必要非增值", VT_META.BVA.color, l.bva, a.bva),
            seg(props.nvaLabel, VT_META.NVA.color, l.nva_total, a.nva_total),
          ],
        };
      };

      const apply = () => { if (inst && !inst.isDisposed()) inst.setOption(buildOption(), true); };

      onMounted(() => {
        if (!domRef.value || !host.echarts) return;
        try {
          inst = host.echarts.init(domRef.value);
          apply();
          ro = new ResizeObserver(() => { if (inst && !inst.isDisposed()) inst.resize(); });
          ro.observe(domRef.value);
        } catch (e) { console.warn("[lg-worktime] lean chart init failed:", e); }
      });
      onBeforeUnmount(() => {
        if (ro) ro.disconnect();
        if (inst && !inst.isDisposed()) inst.dispose();
        inst = null;
      });
      watch(() => [props.live, props.avg, props.avgLabel, props.nvaLabel],
            apply, { deep: true, flush: "post" });

      return () => host.echarts
        ? h("div", { ref: domRef, class: "lgwt-chart-box" })
        : h("div", { class: "lgwt-chart-fallback" },
            `VA ${fmtSec(props.live?.va)} · BVA ${fmtSec(props.live?.bva)} · NVA ${fmtSec(props.live?.nva_total)}`);
    },
  });
}

// ==================== ECharts: LEAN 占比环图 (今日合格轮累计) ====================
function buildLeanDonutChart(host) {
  const { defineComponent, h, ref, onMounted, onBeforeUnmount, watch } = host.vue;
  return defineComponent({
    name: "LgwtLeanDonut",
    props: {
      lean: { type: Object, default: null },  // summary.lean {va,bva,nva_total,va_ratio,...}
    },
    setup(props) {
      const domRef = ref(null);
      let inst = null;
      let ro = null;

      const buildOption = () => {
        const L = props.lean || {};
        const total = (L.va || 0) + (L.bva || 0) + (L.nva_total || 0);
        // 无合格轮 / 全 0: 不画假彩色满环 (筛无数据工位时曾出现绿黄红空环)
        if (!total) {
          return {
            backgroundColor: "transparent",
            series: [],
            graphic: [{
              type: "text", left: "center", top: "middle",
              style: { text: "暂无合格轮", fill: "#64748b", fontSize: 13, textAlign: "center" },
            }],
          };
        }
        return {
          backgroundColor: "transparent",
          tooltip: { trigger: "item", valueFormatter: (v) => (Number(v) || 0).toFixed(1) + "s" },
          series: [{
            type: "pie", radius: ["58%", "88%"], center: ["50%", "50%"],
            avoidLabelOverlap: false,
            label: { show: false }, labelLine: { show: false },
            data: [
              { value: L.va || 0, name: "VA 增值", itemStyle: { color: VT_META.VA.color } },
              { value: L.bva || 0, name: "BVA 必要非增值", itemStyle: { color: VT_META.BVA.color } },
              { value: L.nva_total || 0, name: "NVA 非增值", itemStyle: { color: VT_META.NVA.color } },
            ],
          }],
          graphic: [{
            type: "text", left: "center", top: "42%",
            style: {
              text: L.va_ratio != null ? Number(L.va_ratio).toFixed(1) + "%" : "—",
              fill: VT_META.VA.color, fontSize: 22, fontWeight: "bold", textAlign: "center",
            },
          }, {
            type: "text", left: "center", top: "58%",
            style: { text: "VA 占比", fill: "#64748b", fontSize: 10, textAlign: "center" },
          }],
        };
      };

      const apply = () => { if (inst && !inst.isDisposed()) inst.setOption(buildOption(), true); };
      onMounted(() => {
        if (!domRef.value || !host.echarts) return;
        try {
          inst = host.echarts.init(domRef.value);
          apply();
          ro = new ResizeObserver(() => { if (inst && !inst.isDisposed()) inst.resize(); });
          ro.observe(domRef.value);
        } catch (e) { console.warn("[lg-worktime] donut init failed:", e); }
      });
      onBeforeUnmount(() => {
        if (ro) ro.disconnect();
        if (inst && !inst.isDisposed()) inst.dispose();
        inst = null;
      });
      watch(() => props.lean, apply, { deep: true, flush: "post" });

      return () => host.echarts
        ? h("div", { ref: domRef, class: "lgwt-chart-box" })
        : h("div", { class: "lgwt-chart-fallback" },
            `VA ${fmtPct(props.lean?.va_ratio)} / BVA ${fmtPct(props.lean?.bva_ratio)} / NVA ${fmtPct(props.lean?.nva_ratio)}`);
    },
  });
}

// ==================== ECharts: 多日趋势 (CT 均值 + 良率) ====================
function buildTrendChart(host) {
  const { defineComponent, h, ref, onMounted, onBeforeUnmount, watch } = host.vue;
  return defineComponent({
    name: "LgwtTrend",
    props: {
      days: { type: Array, default: () => [] },  // trend.days
    },
    setup(props) {
      const domRef = ref(null);
      let inst = null;
      let ro = null;

      const buildOption = () => {
        const days = props.days || [];
        return {
          backgroundColor: "transparent",
          grid: { left: 44, right: 44, top: 26, bottom: 22 },
          tooltip: { trigger: "axis" },
          legend: {
            top: 0, itemWidth: 14, itemHeight: 8,
            textStyle: { color: "#94a3b8", fontSize: 10 },
          },
          xAxis: {
            type: "category",
            data: days.map((d) => (d.date || "").slice(5)),
            axisLabel: { color: "#64748b", fontSize: 10 },
            axisLine: { lineStyle: { color: "#334155" } },
          },
          yAxis: [
            {
              type: "value", name: "CT(s)",
              nameTextStyle: { color: "#64748b", fontSize: 9 },
              axisLabel: { color: "#64748b", fontSize: 10 },
              splitLine: { lineStyle: { color: "#1e293b" } },
            },
            {
              type: "value", name: "良率%", min: 0, max: 100,
              nameTextStyle: { color: "#64748b", fontSize: 9 },
              axisLabel: { color: "#64748b", fontSize: 10 },
              splitLine: { show: false },
            },
            {
              // 产量独立隐轴, 避免与 CT(秒) 共轴比例失真
              type: "value", show: false,
            },
          ],
          series: [
            {
              name: "产量", type: "bar", yAxisIndex: 2,
              data: days.map((d) => d.total),
              itemStyle: { color: "rgba(99,102,241,0.35)" }, barWidth: 12,
            },
            {
              name: "CT 均值", type: "line", smooth: true,
              data: days.map((d) => d.ct_avg),
              itemStyle: { color: "#38bdf8" }, lineStyle: { width: 2 },
              connectNulls: true,
            },
            {
              name: "良率", type: "line", smooth: true, yAxisIndex: 1,
              data: days.map((d) => d.yield_rate),
              itemStyle: { color: "#10b981" }, lineStyle: { width: 2 },
              connectNulls: true,
            },
          ],
        };
      };

      const apply = () => { if (inst && !inst.isDisposed()) inst.setOption(buildOption(), true); };
      onMounted(() => {
        if (!domRef.value || !host.echarts) return;
        try {
          inst = host.echarts.init(domRef.value);
          apply();
          ro = new ResizeObserver(() => { if (inst && !inst.isDisposed()) inst.resize(); });
          ro.observe(domRef.value);
        } catch (e) { console.warn("[lg-worktime] trend init failed:", e); }
      });
      onBeforeUnmount(() => {
        if (ro) ro.disconnect();
        if (inst && !inst.isDisposed()) inst.dispose();
        inst = null;
      });
      watch(() => props.days, apply, { deep: true, flush: "post" });

      return () => host.echarts
        ? h("div", { ref: domRef, class: "lgwt-chart-box" })
        : h("div", { class: "lgwt-chart-fallback" }, "趋势图需要 host.echarts");
    },
  });
}

// ==================== 流程监控带 (对齐 LG 原版: 带图步骤卡 + 过长可拖 + 当前步骤跟随滑) ====================
// 数据合成:
//   宿主 chData.steps / 项目 steps_config → 步骤序 + 完成态 + 截图
//   插件 live (1s)                         → 当前步骤高亮 + 本轮逐步耗时
//   插件 step-averages                     → 今日平均耗时 / 平均等待 (等待 chip, 归 NVA)
function resolveFlowSteps(chData, stepsFallback, currentProject) {
  const hostSteps = Array.isArray(chData?.steps) ? chData.steps : [];
  const byLabel = {};
  hostSteps.forEach((s) => { if (s?.label) byLabel[s.label] = s; });

  // 宿主实时配置优先 (_pollProjectConfig), 再 chData.project, 最后全局 currentProject
  // 多工位绑不同项目时避免落到全局 active 项目串步骤/价值
  const conf = (
    (chData?.project && Array.isArray(chData.project.steps_config) && chData.project.steps_config)
    || (chData?._pollProjectConfig && Array.isArray(chData._pollProjectConfig.steps_config)
        && chData._pollProjectConfig.steps_config)
    || (currentProject && Array.isArray(currentProject.steps_config) && currentProject.steps_config)
    || []
  );
  const enabled = conf.filter((s) => s && s.enabled !== false && !s.is_backup && s.label);
  if (enabled.length) {
    return enabled.map((s) => {
      const host = byLabel[s.label] || {};
      return {
        label: s.label,
        name: s.displayLabel || s.name || host.name || s.label,
        status: host.status || "pending",
        screenshot: host.screenshot || null,
      };
    });
  }
  if (hostSteps.length) return hostSteps;
  return (stepsFallback || []).map((s) => ({
    label: s.label, name: s.name || s.label, status: s.status || "pending", screenshot: null,
  }));
}

function flowCardNodes(h, { steps, liveCh, statsMap, compact, vertical, valueMap, cardRefs }) {
  const doneDur = {};
  let curLabel = null, curElapsed = null, curVt = null;
  if (liveCh) {
    (liveCh.steps_done || []).forEach((s) => { doneDur[s.label] = s.duration; });
    if (liveCh.in_cycle && liveCh.current_step) {
      curLabel = liveCh.current_step.label;
      curElapsed = liveCh.current_step.elapsed;
      curVt = liveCh.current_step.value_type;
    }
  }
  const nodes = [];
  (steps || []).forEach((s, i) => {
    const st = (statsMap || {})[s.label] || {};
    if (i > 0) {
      nodes.push(h("div", { class: "lgwt-flow-gap", key: `gap-${s.label}` }, [
        h("span", { class: "lgwt-flow-arrow" }, vertical ? "↓" : "→"),
        // 无统计时显示 — 而不是 0.0s (无数据 ≠ 零等待)
        h("span", {
          class: "lgwt-flow-wait",
          title: `进入「${s.name || s.label}」前的平均等待 (归 NVA)`,
        }, `⏳ ${fmtSec(st.avg_wait)}`),
      ]));
    }
    const isCur = curLabel === s.label;
    const done = s.status === "completed";
    const cls = isCur ? "is-active" : done ? "is-done" : "is-pending";
    // 价值解析链: 实时步骤自带 > 该工位统计行 > 项目配置
    const vt = String((isCur && curVt) || st.value_type || (valueMap || {})[s.label] || "VA").toUpperCase();
    const meta = VT_META[vt] || VT_META.VA;
    const liveV = isCur ? curElapsed : doneDur[s.label];
    nodes.push(h("div", {
      class: `lgwt-flow-card ${cls}${compact ? " is-compact" : ""}`,
      key: s.label,
      "data-step": s.label,
      style: { "--vt-color": meta.color },
      ref: (el) => { if (el && cardRefs) cardRefs[s.label] = el; },
    }, [
      h("div", { class: "lgwt-flow-head" }, [
        h("span", { class: "lgwt-flow-name", title: s.name || s.label },
          `${i + 1}. ${s.name || s.label}`),
        vtBadge(h, vt),
      ]),
      // 图区必显 (对齐 LG 原版 / 主程序 SopStepPanel): 有截图就显示, 否则占位
      h("div", { class: "lgwt-flow-shot" }, [
        s.screenshot
          ? h("img", { src: s.screenshot, alt: "" })
          : h("div", { class: "lgwt-flow-shot-ph" }, "📷"),
        isCur ? h("div", { class: "lgwt-flow-pulse" }) : null,
      ]),
      h("div", { class: "lgwt-flow-foot" }, [
        h("span", { class: `lgwt-flow-live${isCur ? " is-cur" : ""}` },
          liveV != null ? `本轮 ${fmtSec(liveV)}`
            : (isCur ? "进行中" : done ? "已完成" : "待执行")),
        h("span", { class: "lgwt-flow-avg" }, `均 ${fmtSec(st.avg_duration)}`),
      ]),
      isCur ? h("div", { class: "lgwt-flow-curtag" }, "当前步骤") : null,
    ]));
  });
  return nodes;
}

// ==================== 工位面板 (大视频 + 状态条 + 流程监控带) ====================
function buildChannelPanel(host) {
  const { defineComponent, h, ref, computed, watch, nextTick, onMounted, onBeforeUnmount } = host.vue;
  return defineComponent({
    name: "LgwtChannelPanel",
    props: {
      chIdx: { type: Number, default: 0 },
      chData: { type: Object, default: null },
      liveCh: { type: Object, default: null },       // 插件 live.channels[chIdx]
      statsMap: { type: Object, default: () => ({}) }, // label -> step-averages 行
      stepsFallback: { type: Array, default: () => [] }, // 未在检的兜底步骤序
      streamUrlBuilder: { type: Function, default: null },
      actions: { type: Object, default: () => ({}) },
      currentProject: { type: Object, default: null },
      compact: { type: Boolean, default: false },    // 多工位并排时卡片紧凑化
      flowSide: { type: Boolean, default: false },   // 流程带竖排在画面右侧 (focus 模式吃掉 16:9 两侧空白)
      focused: { type: Boolean, default: false },    // dual/focus: 是否焦点工位 (堆叠图/本轮列归属)
    },
    emits: ["focus"],
    setup(props, { emit }) {
      const streamUrl = ref("");
      const overlayCanvasRef = ref(null);
      const imgRef = ref(null);
      const trackRef = ref(null);
      const cardRefs = {};
      let refreshTimer = null;
      let resizeObserver = null;
      let lastScrollLabel = null;

      // 主程序 paintPluginOverlay 按 canvas.fjjl-det-overlay 取画框; 插件侧也主动 paint
      // (对齐 fujian-jinlong / sensor-clean), 避免换页/时序丢帧。
      const paintOverlay = () => {
        const canvas = overlayCanvasRef.value;
        const fn = props.actions?.renderDetectionOverlay;
        if (canvas && typeof fn === "function") fn(props.chIdx, canvas);
      };
      const bindResizeObserver = () => {
        if (resizeObserver || typeof ResizeObserver === "undefined") return;
        const wrap = overlayCanvasRef.value?.parentElement;
        if (!wrap) return;
        resizeObserver = new ResizeObserver(() => paintOverlay());
        resizeObserver.observe(wrap);
      };

      // ==== fetch 流式 MJPEG (2026-08-26 重写, 治「画面冻帧但检测框还在跳」) ====
      // 旧方案 <img src=video_feed>: socket 半死时 Chrome 不触发 onError, 画面
      // 静默停在末帧, 只能靠 90s 定期换流兜底 (最长冻 90s, 现场肉眼可见)。
      // 新方案 fetch + reader 手动解 multipart JPEG → blob 喂 <img>:
      //   - 字节级掌握 lastFrameAt, 看门狗 3.5s 没新帧就重连 (真·自愈);
      //   - 每帧独立 blob 解码, 无 Chromium 原生 MJPEG 解码器内存增长, 不再需要
      //     90s 定期换流 (主程序 STREAM_SWAP 是 <img> 长连接才有的债)。
      let fetchAbort = null;
      let lastFrameAt = 0;
      let curObjUrl = null;

      const _findBytes = (buf, pat, from) => {
        outer: for (let i = from; i <= buf.length - pat.length; i++) {
          for (let j = 0; j < pat.length; j++) {
            if (buf[i + j] !== pat[j]) continue outer;
          }
          return i;
        }
        return -1;
      };

      const showFrame = (bytes) => {
        lastFrameAt = Date.now();
        const img = imgRef.value;
        if (!img) return;  // img 未挂载 (streamUrl 刚置位的同 tick), 丢这帧无妨
        const url = URL.createObjectURL(new Blob([bytes], { type: "image/jpeg" }));
        const prev = curObjUrl;
        curObjUrl = url;
        img.onload = () => {
          if (prev) URL.revokeObjectURL(prev);
          if (img.naturalWidth) {
            props.actions?.setFrameNaturalSize?.(
              props.chIdx, img.naturalWidth, img.naturalHeight,
            );
          }
          paintOverlay();
        };
        img.onerror = () => { if (prev) URL.revokeObjectURL(prev); };
        img.src = url;
      };

      const stopFetch = () => {
        if (fetchAbort) { try { fetchAbort.abort(); } catch (e) { /* 已结束 */ } }
        fetchAbort = null;
      };

      const startFetch = () => {
        if (typeof props.streamUrlBuilder !== "function") return;
        stopFetch();
        let url;
        try { url = props.streamUrlBuilder(props.chIdx); }
        catch (e) { console.warn("[lg-worktime] streamUrlBuilder 抛错:", e); return; }
        streamUrl.value = url;   // 仅用于渲染分支 (有源才挂 <img>)
        lastFrameAt = Date.now();
        const ctrl = new AbortController();
        fetchAbort = ctrl;
        (async () => {
          try {
            const resp = await fetch(url, { signal: ctrl.signal, cache: "no-store" });
            const reader = resp.body.getReader();
            const SOI = [0xff, 0xd8, 0xff];
            const EOI = [0xff, 0xd9];
            let buf = new Uint8Array(0);
            for (;;) {
              const { done, value } = await reader.read();
              if (done || ctrl.signal.aborted) break;
              const nb = new Uint8Array(buf.length + value.length);
              nb.set(buf); nb.set(value, buf.length);
              buf = nb;
              // 取尽 buf 里所有完整 JPEG; 半截留给下一轮拼
              for (;;) {
                const s = _findBytes(buf, SOI, 0);
                if (s < 0) { if (buf.length > 1024) buf = buf.slice(-8); break; }
                const e = _findBytes(buf, EOI, s + 3);
                if (e < 0) { if (s > 0) buf = buf.slice(s); break; }
                showFrame(buf.slice(s, e + 2));
                buf = buf.slice(e + 2);
              }
              if (buf.length > 16 * 1024 * 1024) buf = new Uint8Array(0);  // 异常散包防积压
            }
          } catch (e) { /* abort / 网络断: 交给看门狗重连, 不在此自旋 */ }
        })();
      };

      const forceReconnect = () => startFetch();

      // ---- 视频源进度条: 仅 sourceType=video 时轮询 video/info, 可点击 seek ----
      const videoInfo = ref(null);
      let vinfoTimer = null;
      let vinfoBusy = false;
      let lastVideoProgress = null;
      const pollVideoInfo = async () => {
        const st = (props.chData || {}).sourceType;
        if (st && st !== "video") { videoInfo.value = null; return; }
        if (vinfoBusy) return;
        vinfoBusy = true;
        try {
          const { data } = await host.api.get("/source/video/info",
            { params: { channel: props.chIdx } });
          const ok = data && data.status === "success";
          videoInfo.value = ok ? data : null;
          // 视频循环/重播 (进度从末尾跳回开头) → 旧 MJPEG socket 常冻在末帧, 强制换流
          if (ok && typeof data.progress === "number") {
            if (lastVideoProgress != null && lastVideoProgress > 0.85 && data.progress < 0.15) {
              forceReconnect();
            }
            lastVideoProgress = data.progress;
          }
        } catch (e) { videoInfo.value = null; }
        vinfoBusy = false;
      };
      const seekVideo = async (ev) => {
        ev.stopPropagation();  // 面板 onClick 是切焦点, 拖进度条不应触发
        const rect = ev.currentTarget.getBoundingClientRect();
        const p = Math.min(1, Math.max(0, (ev.clientX - rect.left) / rect.width));
        try {
          await host.api.post("/source/video/progress", { progress: p },
            { params: { channel: props.chIdx } });
          pollVideoInfo();
          forceReconnect();  // seek 后旧流常停在 seek 前一帧
        } catch (e) { console.warn("[lg-worktime] 视频 seek 失败:", e); }
      };
      const setSpeed = async (ev) => {
        ev.stopPropagation();
        const v = parseFloat(ev.target.value);
        if (!v || isNaN(v)) return;
        try {
          await host.api.post("/source/video/speed", { speed: v },
            { params: { channel: props.chIdx } });
          pollVideoInfo();
        } catch (e) { console.warn("[lg-worktime] 倍速设置失败:", e); }
      };

      watch(
        () => props.chData?.detections,
        () => { nextTick(() => paintOverlay()); },
        { deep: true },
      );
      // 工位切换到本面板当主画面时, 立刻抢回 MJPEG 独占权
      watch(() => props.chIdx, () => { forceReconnect(); });

      onMounted(() => {
        startFetch();
        // 冻流看门狗: 源在跑却 3.5s 没收到新帧 → 重连 (fetch 字节级真相,
        // 覆盖「socket 半死不触发 onError」的全部场景)
        refreshTimer = setInterval(() => {
          const c = props.chData || {};
          if ((c.isRunning || c.isDetecting) && Date.now() - lastFrameAt > 3500) {
            forceReconnect();
          }
        }, 2000);
        pollVideoInfo();
        vinfoTimer = setInterval(pollVideoInfo, 1000);
        nextTick(() => { bindResizeObserver(); paintOverlay(); });
      });
      onBeforeUnmount(() => {
        if (refreshTimer) clearInterval(refreshTimer);
        if (vinfoTimer) clearInterval(vinfoTimer);
        if (resizeObserver) { resizeObserver.disconnect(); resizeObserver = null; }
        stopFetch();
        if (curObjUrl) { URL.revokeObjectURL(curObjUrl); curObjUrl = null; }
      });

      const status = computed(() => {
        const c = props.chData;
        if (c?.isDetecting) return { label: "检测中", cls: "is-detecting" };
        if (c?.isRunning) return { label: "待机", cls: "is-standby" };
        return { label: "停止", cls: "is-stopped" };
      });

      // 当前步骤变化 → 滚动条跟着滑到该卡 (对齐主程序 SopStepPanel.scrollToCard)
      const curStepLabel = computed(() =>
        props.liveCh?.in_cycle && props.liveCh?.current_step
          ? props.liveCh.current_step.label : null);

      const scrollToCurrent = (label) => {
        if (!label || !trackRef.value) return;
        const el = cardRefs[label];
        if (!el || typeof el.scrollIntoView !== "function") return;
        try {
          el.scrollIntoView({
            behavior: lastScrollLabel == null ? "auto" : "smooth",
            block: "nearest",
            inline: "center",
          });
        } catch (e) { /* 老浏览器忽略 */ }
        lastScrollLabel = label;
      };

      watch(curStepLabel, (label) => {
        if (!label) return;
        nextTick(() => scrollToCurrent(label));
      });

      return () => {
        const a = props.actions || {};
        const c = props.chData || {};
        const lv = props.liveCh || null;
        const canStart = !!(c.project || props.currentProject) && !c.isDetecting;
        const btn = (label, cls, disabled, onClick) =>
          h("button", {
            class: `lgwt-btn ${cls}`, type: "button", disabled: !!disabled,
            onClick: (e) => { e.stopPropagation(); onClick(); },
          }, label);

        // 步骤序优先项目配置 (稳定顺序+显示名), 截图/完成态从宿主 SOP 按 label 合并
        const steps = resolveFlowSteps(c, props.stepsFallback, props.currentProject);
        const cur = lv?.in_cycle ? lv.current_step : null;

        const cell = (k, v, cls) => h("div", { class: `lgwt-sbar-cell ${cls || ""}` }, [
          h("div", { class: "lgwt-sbar-k" }, k),
          h("div", { class: "lgwt-sbar-v" }, v),
        ]);

        const videoNode = h("div", { class: "lgwt-video-wrap" }, [
          // src 由 fetch 流解出的 blob 逐帧喂 (showFrame), 不走 <img> 长连接
          streamUrl.value
            ? h("img", { ref: imgRef, class: "lgwt-video-img", alt: "检测画面" })
            : h("div", { class: "lgwt-video-none" }, "暂无视频源"),
          // 主程序 querySelectorAll('canvas.fjjl-det-overlay')[ch] 画检测框
          h("canvas", { ref: overlayCanvasRef, class: "fjjl-det-overlay lgwt-det-overlay" }),
          h("div", { class: `lgwt-video-status ${status.value.cls}` }, status.value.label),
          h("div", { class: "lgwt-video-tag" },
            `工位 ${props.chIdx + 1}` + (c.projectName ? ` · ${c.projectName}` : "")),
          // 视频源专属: 底部进度条 (点击跳转), 三种布局同一组件天然全有
          videoInfo.value
            ? h("div", { class: "lgwt-vbar" }, [
                h("span", { class: "lgwt-vbar-time" }, fmtClock(videoInfo.value.current_time)),
                h("div", { class: "lgwt-vbar-track", onClick: seekVideo }, [
                  h("div", {
                    class: "lgwt-vbar-fill",
                    style: { width: `${((videoInfo.value.progress || 0) * 100).toFixed(2)}%` },
                  }),
                  h("div", {
                    class: "lgwt-vbar-knob",
                    style: { left: `${((videoInfo.value.progress || 0) * 100).toFixed(2)}%` },
                  }),
                ]),
                h("span", { class: "lgwt-vbar-time" },
                  fmtClock(videoInfo.value.duration)
                  + (videoInfo.value.ended ? " · 已播完" : "")),
                h("select", {
                  class: "lgwt-vbar-speed",
                  value: String(videoInfo.value.speed ?? 1),
                  onChange: setSpeed,
                  onClick: (e) => e.stopPropagation(),
                }, [0.5, 1, 1.5, 2, 4].map((s) =>
                  h("option", { value: String(s) }, `${s}x`))),
              ])
            : null,
        ]);
        // 价值兜底: 同源 resolveFlowSteps 的配置优先级
        const valueMap = {};
        const stepsConf = (
          (c.project && Array.isArray(c.project.steps_config) && c.project.steps_config)
          || (c._pollProjectConfig && Array.isArray(c._pollProjectConfig.steps_config)
              && c._pollProjectConfig.steps_config)
          || (props.currentProject && Array.isArray(props.currentProject.steps_config)
              && props.currentProject.steps_config)
          || []
        );
        stepsConf.forEach((s) => {
          const vt = s && s.plugin_data && s.plugin_data["lg-worktime"]
            && s.plugin_data["lg-worktime"].value_type;
          if (s && s.label && vt) valueMap[s.label] = String(vt).toUpperCase();
        });
        // 每帧清空 cardRefs, 由本帧 ref 回调重新填充 (避免卸载残留)
        Object.keys(cardRefs).forEach((k) => { delete cardRefs[k]; });
        const flowNode = h("div", {
          class: `lgwt-flow-strip${props.flowSide ? " is-vertical" : ""}`,
        }, [
          h("div", { class: "lgwt-flow-strip-title" }, "流程监控"),
          h("div", { ref: trackRef, class: "lgwt-flow-track" },
            steps.length
              ? flowCardNodes(h, {
                  steps, liveCh: lv, statsMap: props.statsMap,
                  compact: props.compact, vertical: props.flowSide, valueMap, cardRefs,
                })
              : [h("div", { class: "lgwt-flow-empty" }, "未绑定项目 / 无步骤")]),
        ]);

        return h("div", {
          class: `lgwt-station${props.flowSide ? " is-flowside" : ""}${props.focused ? " is-focused" : ""}`,
          // dual 模式点面板切焦点 (堆叠图/本轮列归属跟着走)
          onClick: () => emit("focus", props.chIdx),
        }, [
          // ---- 画面区: flowSide 时 [视频 16:9 | 竖排流程带] 并排吃满宽度 ----
          props.flowSide
            ? h("div", { class: "lgwt-station-top" }, [videoNode, flowNode])
            : videoNode,
          // ---- 状态条: 轮号 / 本轮用时 / 当前步骤 + 控制按钮 ----
          h("div", { class: "lgwt-station-bar" }, [
            cell("轮号", lv?.in_cycle ? `#${lv.cycle_number ?? "—"}` : "—"),
            cell("本轮用时", lv?.in_cycle ? fmtSec(lv.cycle_elapsed) : "—", "is-hot"),
            h("div", { class: "lgwt-sbar-cell is-step" }, [
              h("div", { class: "lgwt-sbar-k" }, "当前步骤"),
              cur
                ? h("div", { class: "lgwt-sbar-v lgwt-sbar-step" }, [
                    vtBadge(h, cur.value_type),
                    h("span", { class: "lgwt-sbar-stepname" }, cur.name || cur.label),
                    h("span", { class: "lgwt-sbar-stepsec" }, fmtSec(cur.elapsed)),
                  ])
                : h("div", { class: "lgwt-sbar-v is-dim" },
                    lv?.in_cycle
                      ? "步骤间等待…"
                      : (lv?.last_cycle
                          ? [`上一轮 `, h("span", { class: lv.last_cycle.is_good ? "lgwt-ok" : "lgwt-ng" },
                              lv.last_cycle.is_good ? "OK" : "NG"),
                             ` · ${fmtSec(lv.last_cycle.duration)}`]
                          : "等待新周期")),
            ]),
            h("div", { class: "lgwt-video-controls" }, [
              btn("开始", "is-start", !canStart, () => a.startDetectionForChannel?.(props.chIdx)),
              btn("停止", "is-stop", !c.isRunning, () => a.stopDetectionForChannel?.(props.chIdx)),
              btn("待机", "is-standby", !c.isDetecting, () => a.standbyForChannel?.(props.chIdx)),
              btn("清零", "is-reset", !!c.isDetecting, () => a.resetCountersForChannel?.(props.chIdx)),
            ]),
          ]),
          // ---- 流程监控带 (LG 原版红框区): 横排模式挂在画面下方 ----
          props.flowSide ? null : flowNode,
        ]);
      };
    },
  });
}

// ==================== 工位缩略卡 (三工位焦点模式的左侧切换列, 对齐 LG 原版 SOURCE 列) ====================
// ⚠ 绝不能吃 /video_feed: 后端同通道只保留 1 条 MJPEG (新连接上位踢旧连接)。
// 缩略图 + 主面板同 channel 各挂一条 → 互相踢断 → 「画面冻帧、进度条/检测框还在动」
// (进度/框走独立 API 轮询, 不依赖 MJPEG)。缩略图改轮询 /snapshot 单帧, 主面板独占长连接。
function buildChannelThumb(host) {
  const { defineComponent, h, ref, computed, onMounted, onBeforeUnmount } = host.vue;

  const toSnapshotUrl = (feedUrl) => {
    try {
      const u = new URL(feedUrl, typeof location !== "undefined" ? location.origin : "http://localhost");
      u.pathname = u.pathname.replace(/\/video_feed\/?$/, "/snapshot");
      u.searchParams.set("t", String(Date.now()));
      return u.toString();
    } catch (e) {
      return String(feedUrl || "").replace("video_feed", "snapshot")
        + (String(feedUrl || "").includes("?") ? "&" : "?") + `t=${Date.now()}`;
    }
  };

  return defineComponent({
    name: "LgwtChannelThumb",
    props: {
      chIdx: { type: Number, default: 0 },
      chData: { type: Object, default: null },
      liveCh: { type: Object, default: null },
      focused: { type: Boolean, default: false },
      streamUrlBuilder: { type: Function, default: null },
    },
    emits: ["focus"],
    setup(props, { emit }) {
      const snapUrl = ref("");
      let snapTimer = null;
      const refreshSnap = () => {
        if (typeof props.streamUrlBuilder !== "function") return;
        try {
          snapUrl.value = toSnapshotUrl(props.streamUrlBuilder(props.chIdx));
        } catch (e) { /* 静默 */ }
      };
      onMounted(() => {
        refreshSnap();
        // 1.5s 一帧足够缩略预览, 且不占 MJPEG 长连接
        snapTimer = setInterval(refreshSnap, 1500);
      });
      onBeforeUnmount(() => { if (snapTimer) clearInterval(snapTimer); });

      const dotCls = computed(() => {
        const c = props.chData;
        if (c?.isDetecting) return "is-detecting";
        if (c?.isRunning) return "is-standby";
        return "is-stopped";
      });

      return () => {
        const lv = props.liveCh;
        return h("div", {
          class: `lgwt-thumb${props.focused ? " is-focused" : ""}`,
          onClick: () => emit("focus", props.chIdx),
        }, [
          h("div", { class: "lgwt-thumb-video" }, [
            snapUrl.value
              ? h("img", {
                  src: snapUrl.value, alt: "",
                  onError: () => { snapUrl.value = ""; },
                })
              : h("div", { class: "lgwt-thumb-none" }, "无信号"),
            props.focused ? h("div", { class: "lgwt-thumb-curtag" }, "当前主画面") : null,
          ]),
          h("div", { class: "lgwt-thumb-bar" }, [
            h("span", { class: `lgwt-thumb-dot ${dotCls.value}` }),
            h("span", { class: "lgwt-thumb-name" }, `工位 ${props.chIdx + 1}`),
            h("span", { class: "lgwt-thumb-info" },
              lv?.in_cycle ? `#${lv.cycle_number ?? "—"} · ${fmtSec(lv.cycle_elapsed)}`
                : (props.chData?.isDetecting ? "检测中" : "待机")),
          ]),
        ]);
      };
    },
  });
}

// ==================== 主看板 (monitor.layout.body 整页覆盖) ====================
function buildDashboard(host) {
  const { defineComponent, h, ref, computed, watch, onMounted, onBeforeUnmount } = host.vue;
  const apiBase = ((host.api && host.api.defaults && host.api.defaults.baseURL) || "/api/v1").replace(/\/$/, "");
  const LeanStack = buildLeanStackChart(host);
  const LeanDonut = buildLeanDonutChart(host);
  const Trend = buildTrendChart(host);
  const ChannelPanel = buildChannelPanel(host);
  const ChannelThumb = buildChannelThumb(host);

  return defineComponent({
    name: "LgWorktimeDashboard",
    props: {
      channelCount: { type: Number, default: 1 },
      multiChannelData: { type: Object, default: () => ({}) },
      selectedChannel: { type: Number, default: 0 },
      channelModelStats: { type: Object, default: () => ({}) },
      currentProject: { type: Object, default: null },
      actions: { type: Object, default: () => ({}) },
      streamUrlBuilder: { type: Function, default: null },
    },
    emits: ["update:selectedChannel"],
    setup(props, { emit }) {
      // channelFilter: null=全部工位 (领导默认看整机)
      const channelFilter = ref(null);
      const live = ref({ channels: {} });
      const summary = ref(null);          // KPI/占比环/趋势: 跟 channelFilter (全部工位=全机)
      const focusSummary = ref(null);     // 多工位时焦点工位的 summary (LEAN 堆叠图同口径基准)
      const stepAverages = ref({ steps: [] });   // 底部步骤表: 跟 channelFilter
      const stepStatsByCh = ref({});      // ch -> step-averages (面板流程带各取各的, 不串工位)
      const trend = ref({ days: [] });
      const clock = ref("");
      // v1.5.0 插件全局设置 (LG 口径全参数可配, 系统设置页维护):
      //   default_value_type 默认价值 / wait_value_type 等待归类 /
      //   lean_scope 统计范围 / trend_days 趋势天数
      const lgSettings = ref({ default_value_type: "VA", wait_value_type: "NVA",
                               lean_scope: "good_only", trend_days: 7 });

      // ---- 展会同款壳 (品牌顶栏 + 抽屉导航): 与其它 7 页 iframe 视觉完全连续 ----
      const navOpen = ref(false);
      const brandName = ref("天军机器人");
      const appTitle = ref("视觉AI行为分析防错检测系统");
      const deviceNo = ref("");           // 设备状态卡: 设备编号 (与展会同源 /system/display)
      // 设备状态卡真值 (v1.5.1): 整机开机时长 + 温度 (后端 /device-info, 无传感器为 null)
      const devInfo = ref({ uptime_seconds: null, temperature_c: null, temperature_source: null });
      let devFetchedAt = 0;               // uptime_seconds 的取数时刻, 本地续秒用
      const uptime = ref("—");
      const NAV_ITEMS = [
        { route: "/monitor", label: "实时监控", ic: "▦" },
        { route: "/project", label: "项目管理", ic: "▣" },
        { route: "/model", label: "模型仓库", ic: "◈" },
        { route: "/source", label: "输入源设置", ic: "▥" },
        { route: "/data", label: "数据中心", ic: "▤" },
        { route: "/mes", label: "MES 管理", ic: "≣" },
        { route: "/alarm", label: "报警设置", ic: "⚠" },
        { route: "/settings", label: "系统设置", ic: "⚙" },
      ];
      const navGo = (route) => {
        navOpen.value = false;
        if (route === "/monitor") return;
        try { host.router.push(route); } catch (e) { window.location.hash = `#${route}`; }
      };

      let tLive = null, tSummary = null, tSteps = null, tTrend = null, tClock = null;

      const chParam = () => {
        const params = {};
        if (channelFilter.value != null) {
          params.channel_id = channelFilter.value;
          // 筛具体工位时带上该工位项目, 底表价值徽章不串全局 active 项目
          const pid = ((props.multiChannelData || {})[channelFilter.value] || {}).project?.id;
          if (pid != null) params.project_id = pid;
        }
        // "全部工位"也按当前激活项目过滤: 领导看板口径是"本项目今日",
        // 不把同一天里别的项目 (如旧 demo) 的轮次/步骤混进 KPI 与底表
        if (params.project_id == null) {
          const pid = props.currentProject?.id;
          if (pid != null) params.project_id = pid;
        }
        return params;
      };

      const pollLive = async () => {
        try {
          const { data } = await host.api.get(`${BASE}/live`);
          if (data && data.channels) live.value = data;
        } catch (e) { /* 静默: 轮询失败保留旧数据 */ }
      };
      const pollSummary = async () => {
        try {
          const { data } = await host.api.get(`${BASE}/summary`, { params: chParam() });
          if (data) summary.value = data;
        } catch (e) { /* 静默 */ }
        // 多工位时 LEAN 堆叠图的"均值"必须与"当前轮(实时)"同工位, 否则口径错位
        try {
          if (visibleChannels.value.length > 1) {
            const { data } = await host.api.get(`${BASE}/summary`, {
              params: { channel_id: videoCh.value },
            });
            if (data) focusSummary.value = data;
          } else {
            focusSummary.value = null;
          }
        } catch (e) { /* 静默 */ }
      };
      const pollSteps = async () => {
        // 底部步骤表: 跟 channelFilter 口径 (全部工位=全机汇总)
        try {
          const { data } = await host.api.get(`${BASE}/step-averages`, { params: chParam() });
          if (data) stepAverages.value = data;
        } catch (e) { /* 静默 */ }
        // 面板流程带: 每个可见工位各查各的 (channel_id + 该工位项目 id),
        // 双/三工位跑不同项目时平均值/价值/兜底步骤序互不污染
        try {
          const chans = visibleChannels.value;
          const results = await Promise.all(chans.map(async (ch) => {
            const params = { channel_id: ch };
            const pid = ((props.multiChannelData || {})[ch] || {}).project?.id;
            if (pid != null) params.project_id = pid;
            try {
              const { data } = await host.api.get(`${BASE}/step-averages`, { params });
              return [ch, data || { steps: [] }];
            } catch (e) {
              return [ch, stepStatsByCh.value[ch] || { steps: [] }];  // 失败保留旧数据
            }
          }));
          stepStatsByCh.value = Object.fromEntries(results);
        } catch (e) { /* 静默 */ }
      };
      const pollSettings = async () => {
        try {
          const { data } = await host.api.get(`${BASE}/settings`);
          if (data?.settings) lgSettings.value = data.settings;
        } catch (e) { /* 静默: 保留出厂默认 */ }
      };
      const pollTrend = async () => {
        // 先刷设置再拉趋势: 系统设置页改了天数/口径, 下一个趋势周期即生效
        await pollSettings();
        try {
          const { data } = await host.api.get(`${BASE}/trend`,
            { params: { days: lgSettings.value.trend_days || 7, ...chParam() } });
          if (data) trend.value = data;
        } catch (e) { /* 静默 */ }
      };
      const pollDeviceInfo = async () => {
        try {
          const { data } = await host.api.get(`${BASE}/device-info`);
          if (data) { devInfo.value = data; devFetchedAt = Date.now(); }
        } catch (e) { /* 静默: 保留旧读数 */ }
      };
      const tickClock = () => {
        const d = new Date();
        const p = (n) => String(n).padStart(2, "0");
        clock.value = `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}  ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
        // 真·整机开机时长: 后端取数 + 本地续秒 (>24h 显示天数)
        const base = devInfo.value.uptime_seconds;
        if (base == null) {
          uptime.value = "—";
        } else {
          const t = Math.floor(base + (Date.now() - devFetchedAt) / 1000);
          const days = Math.floor(t / 86400);
          const hms = `${p(Math.floor((t % 86400) / 3600))}:${p(Math.floor((t % 3600) / 60))}:${p(t % 60)}`;
          uptime.value = days > 0 ? `${days}天 ${hms}` : hms;
        }
      };

      // ---- 操作日志 (对齐 LG 原版左下角面板): 事件 + 周期开始/结束 + 检测启停 ----
      const opLog = ref([]);   // [{no,t,ch,action,detail,cls}] 新在前
      let opLogNo = 0;
      const _logSeen = {};     // ch -> 已消费的 recentEvents 最大 seq
      const _logPrev = {};     // ch -> 上次扫描快照 (周期/检测状态边沿检测)
      const pushLog = (ch, action, detail, cls) => {
        opLog.value.unshift({
          no: ++opLogNo, t: fmtLogTime(Date.now()),
          ch, action, detail: detail || "", cls: cls || "",
        });
        if (opLog.value.length > 120) opLog.value.length = 120;
      };
      const scanLog = () => {
        const n = Math.max(1, props.channelCount || 1);
        for (let ch = 0; ch < n; ch++) {
          const cd = (props.multiChannelData || {})[ch] || {};
          // ① 检测事件 (OK/NG/自定义): 与 Toast 同源 recentEvents, seq 去重
          const evs = Array.isArray(cd.recentEvents) ? cd.recentEvents : [];
          if (!(ch in _logSeen)) {
            // 首扫只对齐水位, 不回放 30s 内的旧事件刷屏
            _logSeen[ch] = evs.length ? Math.max(...evs.map((e) => e.seq || 0)) : 0;
          } else {
            evs.filter((e) => (e.seq || 0) > _logSeen[ch]).forEach((e) => {
              _logSeen[ch] = Math.max(_logSeen[ch], e.seq || 0);
              const cls = e.event_id === 2 ? "is-ng" : e.event_id === 1 ? "is-ok" : "is-warn";
              pushLog(ch, e.event_name || "事件", e.reason || "", cls);
            });
          }
          // ② 周期开始/结束 + 检测启停 (边沿检测)
          const lv = (live.value.channels || {})[String(ch)] || {};
          const prev = _logPrev[ch] || {};
          const inCycle = !!lv.in_cycle;
          const cyc = lv.cycle_number;
          const det = !!cd.isDetecting;
          if (prev.init) {
            if (inCycle && !prev.inCycle) {
              pushLog(ch, "周期开始", cyc != null ? `第 ${cyc} 轮` : "");
            } else if (!inCycle && prev.inCycle) {
              const last = lv.last_cycle;
              pushLog(ch, "周期结束",
                last ? `${last.is_good ? "OK" : "NG"} · ${fmtSec(last.duration)}` : "",
                last ? (last.is_good ? "is-ok" : "is-ng") : "");
            } else if (inCycle && prev.inCycle && cyc != null
                       && prev.cycleNo != null && cyc !== prev.cycleNo) {
              // first_step 结算: 上一轮结束与新一轮开始同帧, in_cycle 不落沿
              pushLog(ch, "周期开始", `第 ${cyc} 轮`);
            }
            if (det !== prev.detecting) {
              pushLog(ch, det ? "开始检测" : "停止检测", "", det ? "is-ok" : "is-warn");
            }
          }
          _logPrev[ch] = { init: true, inCycle, cycleNo: cyc, detecting: det };
        }
      };

      let tLog = null, tDev = null;
      onMounted(() => {
        // 顶栏品牌/系统名与展会页同源 (/system/display), 保持八页文案一致
        host.api.get("/system/display").then(({ data }) => {
          if (data && data.brand_name) brandName.value = data.brand_name;
          if (data && data.app_name) appTitle.value = data.app_name;
          if (data && data.device_number) deviceNo.value = String(data.device_number);
        }).catch(() => {});
        pollLive(); pollSummary(); pollSteps(); pollTrend(); pollDeviceInfo(); tickClock();
        tLive = setInterval(pollLive, 1000);
        tSummary = setInterval(pollSummary, 5000);
        tSteps = setInterval(pollSteps, 15000);
        tTrend = setInterval(pollTrend, 60000);
        tClock = setInterval(tickClock, 1000);
        tLog = setInterval(scanLog, 500);
        tDev = setInterval(pollDeviceInfo, 30000);   // 温度 10s 后端缓存, 30s 刷足够
      });
      onBeforeUnmount(() => {
        [tLive, tSummary, tSteps, tTrend, tClock, tLog, tDev].forEach((t) => t && clearInterval(t));
      });

      // currentProject 是宿主异步灌入的: 首拉时常为 null → chParam 带不上
      // project_id, KPI/底表/趋势闪一帧"全项目混数"。项目就绪/切换即刻重拉。
      watch(() => props.currentProject?.id, (nv, ov) => {
        if (nv !== ov) { pollSummary(); pollSteps(); pollTrend(); }
      });

      const onChannelFilterChange = (ev) => {
        const v = ev?.target?.value;
        channelFilter.value = v === "" ? null : Number(v);
        if (channelFilter.value != null) emit("update:selectedChannel", channelFilter.value);
        pollSummary(); pollSteps(); pollTrend();
      };

      // 工位列表: 全部工位 → 全量; 选具体工位 → 该工位独占 (退化 single 模式)
      const visibleChannels = computed(() => {
        const n = Math.max(1, props.channelCount || 1);
        if (channelFilter.value != null && channelFilter.value < n) return [channelFilter.value];
        return Array.from({ length: n }, (_, i) => i);
      });

      // 三种工位数三种布局: 1=single(数据全量) / 2=dual(并排) / >=3=focus(缩略列+主画面)
      const layoutMode = computed(() => {
        const n = visibleChannels.value.length;
        return n === 1 ? "single" : n === 2 ? "dual" : "focus";
      });

      // focus 模式的焦点工位 (点缩略图切换)
      const focusedCh = ref(0);
      const setFocus = (ch) => {
        focusedCh.value = ch;
        emit("update:selectedChannel", ch);
        pollSummary();  // 切焦点立即刷新该工位的 LEAN 对比基准
        pollSteps();
      };

      // 主参考工位 (LEAN 实时对比 / 步骤表「本轮」列 / 当前周期卡)
      // dual/focus 都走 focusedCh; 点面板/缩略图可切换
      const videoCh = computed(() => {
        if (channelFilter.value != null) return channelFilter.value;
        if (layoutMode.value === "focus" || layoutMode.value === "dual") {
          const n = Math.max(1, props.channelCount || 1);
          return focusedCh.value < n ? focusedCh.value : 0;
        }
        return props.selectedChannel || 0;
      });

      // 当前工位 live 状态
      const chLive = computed(() => (live.value.channels || {})[String(videoCh.value)] || null);

      // 今日合格轮均值 (LEAN 堆叠图对比基准):
      // 单工位/已筛选 → summary 本身就是该工位; 多工位全部 → 焦点工位的 focusSummary (同口径)
      const leanAvg = computed(() => {
        const src = visibleChannels.value.length > 1 ? focusSummary.value : summary.value;
        return src?.lean?.avg_per_good || null;
      });
      // 当前轮实时 LEAN
      const leanLive = computed(() => chLive.value?.lean_live || null);

      // 面板流程带数据: 按工位取, 不串 (label -> {value_type, avg_duration, avg_wait, ...})
      const statsMapFor = (ch) => {
        const m = {};
        (((stepStatsByCh.value || {})[ch] || {}).steps || []).forEach((s) => { m[s.label] = s; });
        return m;
      };
      // 停机兜底步骤序: 同样按工位取 (宿主 chData.steps 空时流程带仍有内容)
      const fallbackFor = (ch) =>
        ((((stepStatsByCh.value || {})[ch] || {}).steps) || []).map((s) => ({
          label: s.label, name: s.label, status: "pending", screenshot: null,
        }));

      // 本轮各步骤耗时 map (步骤表「本轮」列)
      const liveStepDur = computed(() => {
        const m = {};
        const st = chLive.value;
        if (st) {
          (st.steps_done || []).forEach((s) => { m[s.label] = s.duration; });
          if (st.current_step) m[st.current_step.label] = st.current_step.elapsed;
        }
        return m;
      });

      const kpi = (label, value, cls, sub) =>
        h("div", { class: `lgwt-kpi ${cls || ""}` }, [
          h("div", { class: "lgwt-kpi-value" }, value),
          h("div", { class: "lgwt-kpi-label" }, label),
          sub ? h("div", { class: "lgwt-kpi-sub" }, sub) : null,
        ]);

      // 当前周期卡 (紧凑版): 单行数据条, 与右栏其它容器并列; 大块位置让给操作日志
      const renderLiveCard = () => {
        const cl = chLive.value;
        const cur = cl?.in_cycle ? cl.current_step : null;
        const last = cl?.last_cycle;
        const cell = (k, v, cls) => h("div", { class: "lgwt-livec-cell" }, [
          h("div", { class: "lgwt-livec-k" }, k),
          h("div", { class: `lgwt-livec-v ${cls || ""}` }, v),
        ]);
        return h("div", { class: "lgwt-card lgwt-card-live is-compact" }, [
          h("div", { class: "lgwt-card-title" }, [
            `当前周期 · 工位 ${videoCh.value + 1}`,
            cl?.in_cycle
              ? h("span", { class: "lgwt-livec-badge" }, `#${cl.cycle_number ?? "—"}`)
              : null,
          ]),
          cl && cl.in_cycle
            ? h("div", { class: "lgwt-livec-row" }, [
                cell("本轮用时", fmtSec(cl.cycle_elapsed), "is-hot"),
                cell("当前步骤", cur
                  ? [vtBadge(h, cur.value_type), " ", (cur.name || cur.label)]
                  : "步骤间等待…"),
                cell("步骤用时", cur ? fmtSec(cur.elapsed) : "—"),
                cell("已完成", String((cl.steps_done || []).length)),
              ])
            : h("div", { class: "lgwt-livec-row" }, [
                cell("状态", "等待新周期…"),
                cell("上一轮", last
                  ? [h("span", { class: last.is_good ? "lgwt-ok" : "lgwt-ng" },
                       last.is_good ? "OK" : "NG"),
                     ` · ${fmtSec(last.duration)}`]
                  : "—"),
                last?.lean
                  ? cell("上轮 LEAN",
                      `VA ${fmtSec(last.lean.va)} / NVA ${fmtSec((last.lean.nva || 0) + (last.lean.wait || 0))}`)
                  : null,
              ]),
        ]);
      };

      // 操作日志 (LG 原版左下角面板): 时间/工位/动作/详情, 新事件在顶
      const renderOpLog = (tall) =>
        h("div", { class: `lgwt-card lgwt-card-oplog${tall ? " is-tall" : ""}` }, [
          h("div", { class: "lgwt-card-title" }, "操作日志"),
          h("div", { class: "lgwt-oplog-head" }, [
            h("span", null, "时间"), h("span", null, "工位"),
            h("span", null, "动作"), h("span", null, "详情"),
          ]),
          h("div", { class: "lgwt-oplog-body" },
            opLog.value.length
              ? opLog.value.map((r) => h("div", { key: r.no, class: `lgwt-oplog-row ${r.cls}` }, [
                  h("span", { class: "lgwt-oplog-t" }, r.t),
                  h("span", { class: "lgwt-oplog-ch" }, String(r.ch + 1)),
                  h("span", { class: "lgwt-oplog-act" }, r.action),
                  h("span", { class: "lgwt-oplog-detail", title: r.detail }, r.detail || "—"),
                ]))
              : [h("div", { class: "lgwt-oplog-empty" }, "暂无日志 · 事件/周期/启停将实时记录")]),
        ]);

      // 设备状态卡 (展会同款: 3D 相机渲染 + 光环动画 + 数据列): 挂在操作日志列下方
      const renderDeviceCard = () => {
        const c = (props.multiChannelData || {})[videoCh.value] || {};
        const pill = (on, txtOn, txtOff) =>
          h("span", { class: `lgwt-pill ${on ? "on" : "off"}` }, on ? txtOn : txtOff);
        const drow = (k, v) => h("div", { class: "lgwt-drow" }, [
          h("span", { class: "lgwt-drow-k" }, k), v,
        ]);
        return h("div", { class: "lgwt-card lgwt-card-device" }, [
          h("div", { class: "lgwt-card-title" }, "设备状态"),
          h("div", { class: "lgwt-dev-body" }, [
            h("div", { class: "lgwt-dev-cam" }, [
              h("img", {
                src: `${apiBase}/plugins/active/assets/frontend/dist/showcase/tianjun_camera_dashboard_mount_alpha.png`,
                alt: "天军4K工业相机",
              }),
              h("span", { class: "lgwt-lens-glow" }),
              h("span", { class: "lgwt-scan-glint" }),
            ]),
            h("div", { class: "lgwt-dev-list" }, [
              drow("设备编号", h("span", { class: "lgwt-drow-v" }, deviceNo.value || "251011")),
              drow("运行状态", pill(!!c.isDetecting, "运行中", "待机")),
              // 连接状态 = 该工位视频源真实取流态 (isDetecting 蕴含 isRunning, 兜底老宿主字段缺失)
              drow("连接状态", pill(!!(c.isRunning || c.isDetecting), "正常", "未连接")),
              // 温度真值 (/device-info 探测链: GPU→CPU→电池 SMC); 无传感器诚实显示 —
              drow("温度", (() => {
                const t = devInfo.value.temperature_c;
                if (t == null) return h("span", { class: "lgwt-drow-v" }, "—");
                const pct = Math.max(6, Math.min(100, ((t - 15) / 75) * 100));  // 15~90℃ 映射条宽
                return h("span", { class: "lgwt-tempbar", title: `传感器: ${devInfo.value.temperature_source || "?"}` }, [
                  h("span", { class: "lgwt-tempbar-bar" }, h("i", { style: `width:${pct.toFixed(0)}%` })),
                  h("b", null, `${t}℃`),
                ]);
              })()),
              // 运行时长真值: 整机开机时长 (psutil.boot_time), 非页面挂载时长
              drow("运行时长", h("span", { class: "lgwt-drow-v" }, uptime.value)),
            ]),
          ]),
        ]);
      };

      const renderPanel = (ch, compact, flowSide) => h(ChannelPanel, {
        key: `st-${ch}`,
        chIdx: ch,
        chData: (props.multiChannelData || {})[ch] || null,
        liveCh: (live.value.channels || {})[String(ch)] || null,
        statsMap: statsMapFor(ch),
        stepsFallback: fallbackFor(ch),
        streamUrlBuilder: props.streamUrlBuilder,
        actions: props.actions,
        currentProject: props.currentProject,
        compact,
        flowSide: !!flowSide,
        focused: ch === videoCh.value && (layoutMode.value === "dual" || layoutMode.value === "focus"),
        onFocus: setFocus,
      });

      return () => {
        const S = summary.value || {};
        const L = S.lean || {};
        const vis = visibleChannels.value;
        const mode = layoutMode.value;

        // ---------- 展会同款壳: 品牌顶栏 + 抽屉导航 (标题/时钟上移, 八页视觉连续) ----------
        const detOn = Object.values(props.multiChannelData || {}).some((c) => c && c.isDetecting);
        const topbar = h("header", { class: "lgwt-topbar" }, [
          h("button", {
            class: "lgwt-nav-toggle", title: "导航菜单",
            onClick: () => { navOpen.value = !navOpen.value; },
          }, "☰"),
          h("div", { class: "lgwt-brand" }, [
            h("img", {
              class: "lgwt-brand-logo", alt: "TYENJUN",
              src: `${apiBase}/plugins/active/assets/frontend/dist/showcase/tyenjun-logo.png`,
            }),
            h("div", { class: "lgwt-brand-name" }, [
              brandName.value,
              h("small", null, "TYENJUN ROBOTICS"),
            ]),
          ]),
          h("span", { class: "lgwt-page-state" }, [
            h("span", { class: "lgwt-page-kv" }, ["当前页面", h("b", null, "实时监控")]),
            h("span", { class: `lgwt-page-run${detOn ? "" : " idle"}` }, detOn ? "运行中" : "待机"),
          ]),
          h("div", { class: "lgwt-apptitle" }, [
            h("h1", null, appTitle.value),
            h("div", { class: "lgwt-apptitle-sub" }, "AI 工时测量看板 · LEAN 价值分析 VA / BVA / NVA"),
          ]),
          h("div", { class: "lgwt-topbar-right" }, [
            h("span", { class: "lgwt-topclock" }, clock.value),
            h("span", { class: "lgwt-topuser" }, [
              h("span", { class: "lgwt-topuser-dot" }, "管"),
              "管理员",
            ]),
          ]),
        ]);
        const navMask = navOpen.value
          ? h("div", { class: "lgwt-nav-mask", onClick: () => { navOpen.value = false; } })
          : null;
        const navDrawer = h("nav", { class: `lgwt-nav${navOpen.value ? " open" : ""}` }, [
          ...NAV_ITEMS.map((it) => h("div", {
            key: it.route,
            class: `lgwt-nav-item${it.route === "/monitor" ? " active" : ""}`,
            onClick: () => navGo(it.route),
          }, [h("span", { class: "lgwt-nav-ic" }, it.ic), it.label])),
          h("div", { class: "lgwt-nav-slogan" }, [
            h("b", null, "Streamline Your AI Visual Applications"),
            "让视觉+AI落地工业生产，赋能全球企业智能化升级",
          ]),
        ]);

        return h("div", { class: "lgwt-shell" }, [
          topbar,
          navMask,
          navDrawer,
          h("div", { class: `lgwt-dashboard${mode === "single" ? " is-rich" : ""}` }, [
          // ---------- 看板工具行 (工位选择 + 项目; 标题/时钟已并入展会壳顶栏) ----------
          h("header", { class: "lgwt-header" }, [
            h("div", { class: "lgwt-header-mid" }, [
              h("label", { class: "lgwt-select-label" }, "工位"),
              h("select", {
                class: "lgwt-select", value: channelFilter.value == null ? "" : String(channelFilter.value),
                onChange: onChannelFilterChange,
              }, [
                h("option", { value: "" }, "全部工位"),
                ...Array.from({ length: props.channelCount }, (_, i) =>
                  h("option", { value: String(i) }, `工位 ${i + 1}`)),
              ]),
              props.currentProject?.name
                ? h("span", { class: "lgwt-proj" }, `项目: ${props.currentProject.name}`)
                : null,
            ]),
          ]),

          // ---------- KPI 行 ----------
          h("section", { class: "lgwt-kpi-row" }, [
            kpi("今日产量(轮)", String(S.total_cycles ?? "—"), "is-total"),
            kpi("合格", String(S.good_cycles ?? "—"), "is-ok"),
            kpi("NG", String(S.ng_cycles ?? "—"), "is-ng"),
            kpi("良率", fmtPct(S.yield_rate), (S.yield_rate ?? 100) >= 90 ? "is-ok" : "is-warn"),
            kpi("CT 均值", fmtSec(S.ct_avg), "is-ct",
              S.ct_min != null ? `最短 ${fmtSec(S.ct_min)} · 最长 ${fmtSec(S.ct_max)}` : null),
            kpi("CT 累计", fmtSec(S.ct_total), "is-ct"),
            kpi("等待累计", fmtSec(S.wait_total), "is-wait", "含全部轮 · 归 NVA"),
          ]),

          // ---------- 主区: 三种工位数三种布局 ----------
          h("section", { class: `lgwt-main mode-${mode}` }, [
            // focus 模式: 左侧缩略切换列 (对齐 LG 原版 SOURCE 列)
            mode === "focus"
              ? h("div", { class: "lgwt-thumbs" },
                  vis.map((ch) => h(ChannelThumb, {
                    key: `th-${ch}`,
                    chIdx: ch,
                    chData: (props.multiChannelData || {})[ch] || null,
                    liveCh: (live.value.channels || {})[String(ch)] || null,
                    focused: ch === videoCh.value,
                    streamUrlBuilder: props.streamUrlBuilder,
                    onFocus: setFocus,
                  })))
              : null,

            // 工位区: single/focus=单面板(数据全量/焦点); dual=两面板并排
            mode === "dual"
              ? h("div", { class: "lgwt-stations cols-2" },
                  vis.map((ch) => renderPanel(ch, true, false)))
              : h("div", { class: "lgwt-stations cols-1" },
                  [renderPanel(videoCh.value, false, mode === "focus")]),

            // single/focus 模式: 操作日志独立一列 + 下方展会同款设备状态卡
            // (LEAN 占比/工时分解移出后右栏更宽, 图表比例更舒展)
            (mode === "single" || mode === "focus")
              ? h("div", { class: "lgwt-live-col" }, [
                  renderOpLog(mode === "single"),
                  renderDeviceCard(),
                ])
              : null,

            // 右栏: [操作日志(dual)] + 当前周期(紧凑) + LEAN 占比环 + 工时分解
            h("aside", { class: "lgwt-rail" }, [
              mode === "dual" ? renderOpLog(false) : null,
              renderLiveCard(),
              h("div", { class: "lgwt-card lgwt-card-donut" }, [
                h("div", { class: "lgwt-card-title" },
                  `LEAN 占比 · ${channelFilter.value == null ? "全部工位" : `工位 ${channelFilter.value + 1}`}`
                  + ` · ${(L.scope || lgSettings.value.lean_scope) === "all" ? "今日累计" : "今日合格"} ${L.good_rounds ?? 0} 轮`),
                h(LeanDonut, { lean: L }),
                h("div", { class: "lgwt-ratio-row" }, [
                  h("span", { class: "lgwt-ratio is-va" }, `VA ${fmtPct(L.va_ratio)}`),
                  h("span", { class: "lgwt-ratio is-bva" }, `BVA ${fmtPct(L.bva_ratio)}`),
                  h("span", { class: "lgwt-ratio is-nva" }, `NVA ${fmtPct(L.nva_ratio)}`),
                ]),
              ]),
              h("div", { class: "lgwt-card lgwt-card-stack" }, [
                h("div", { class: "lgwt-card-title" },
                  `LEAN 工时分解 (秒)${vis.length > 1 ? ` · 工位 ${videoCh.value + 1}` : ""}`),
                h(LeanStack, {
                  live: leanLive.value, avg: leanAvg.value,
                  avgLabel: lgSettings.value.lean_scope === "all" ? "今日全轮均值" : "今日合格均值",
                  nvaLabel: lgSettings.value.wait_value_type === "NVA"
                    ? "NVA 非增值(含等待)" : "NVA 非增值",
                }),
              ]),
            ]),
          ]),

          // ---------- 底排 ----------
          h("section", { class: "lgwt-bottom-row" }, [
            h("div", { class: "lgwt-card lgwt-card-steps" }, [
              h("div", { class: "lgwt-card-title" },
                `步骤工时统计 (今日 · ${channelFilter.value == null ? "全部工位" : `工位 ${channelFilter.value + 1}`})`),
              h("div", { class: "lgwt-table-wrap" }, [
                h("table", { class: "lgwt-table" }, [
                  h("thead", null, h("tr", null, [
                    h("th", null, "步骤"),
                    h("th", null, "价值"),
                    h("th", null, vis.length > 1 ? `本轮·工位${videoCh.value + 1}` : "本轮"),
                    h("th", null, "次数"),
                    h("th", null, "平均"),
                    h("th", null, "最短"),
                    h("th", null, "最长"),
                    h("th", null, "平均等待"),
                  ])),
                  h("tbody", null,
                    (stepAverages.value.steps || []).length
                      ? (stepAverages.value.steps || []).map((s) =>
                          h("tr", { key: s.label }, [
                            h("td", { class: "lgwt-td-label" }, s.label),
                            h("td", null, vtBadge(h, s.value_type)),
                            h("td", { class: "lgwt-td-live" },
                              liveStepDur.value[s.label] != null
                                ? fmtSec(liveStepDur.value[s.label]) : "—"),
                            h("td", null, String(s.count ?? "—")),
                            h("td", null, fmtSec(s.avg_duration)),
                            h("td", null, fmtSec(s.min_duration)),
                            h("td", null, fmtSec(s.max_duration)),
                            h("td", { class: "lgwt-td-wait" }, fmtSec(s.avg_wait)),
                          ]))
                      : [h("tr", null, h("td", { colspan: 8, class: "lgwt-td-empty" }, "今日暂无步骤数据"))]
                  ),
                ]),
              ]),
            ]),
            h("div", { class: "lgwt-card lgwt-card-trend" }, [
              h("div", { class: "lgwt-card-title" },
                `近 ${trend.value.trend_days || lgSettings.value.trend_days || 7} 天趋势 · 产量 / CT / 良率`),
              h(Trend, { days: trend.value.days || [] }),
            ]),
          ]),
          ]),
        ]);
      };
    },
  });
}

// ==================== 步骤价值配置单元格 (project.step-cell.durations) ====================
// 存 steps_config[i].plugin_data["lg-worktime"].value_type, 经主程序
// PUT /projects/{id}/plugin-data (scope=steps_config, index=i) 精准写入 —
// 浅合并, 不碰其它插件/其它 key; 主程序保存项目时该子树原样保留。
function buildValueCell(host) {
  const { defineComponent, h, ref } = host.vue;
  return defineComponent({
    name: "LgwtStepValueCell",
    props: {
      step: { type: Object, default: () => ({}) },
      project: { type: Object, default: () => ({}) },
    },
    setup(props) {
      const saving = ref(false);
      const localVal = ref(null);  // 保存后本地回显 (project prop 不一定立即刷新)

      const currentVal = () => {
        if (localVal.value) return localVal.value;
        const pd = props.step?.plugin_data?.[CC];
        const raw = pd && pd.value_type;
        return (raw && ["VA", "BVA", "NVA"].includes(String(raw).toUpperCase()))
          ? String(raw).toUpperCase() : "VA";
      };

      const stepIndex = () => {
        const list = props.project?.steps_config;
        if (!Array.isArray(list)) return -1;
        return list.findIndex((s) => s && s.label === props.step?.label);
      };

      const onChange = async (ev) => {
        const vt = ev?.target?.value;
        const idx = stepIndex();
        const pid = props.project?.id;
        if (!vt || idx < 0 || !pid) {
          console.warn("[lg-worktime] 无法定位步骤或项目, 放弃保存", { idx, pid });
          return;
        }
        saving.value = true;
        try {
          await host.api.put(`/projects/${pid}/plugin-data`, {
            scope: "steps_config",
            index: idx,
            customer_code: CC,
            data: { value_type: vt },
          });
          localVal.value = vt;
          // 同步本地 prop 对象, 让同页其它逻辑立即看到 (prop 是引用)
          if (props.step) {
            props.step.plugin_data = props.step.plugin_data || {};
            props.step.plugin_data[CC] = Object.assign({}, props.step.plugin_data[CC], { value_type: vt });
          }
          console.log(`[lg-worktime] 步骤[${props.step?.label}] 动作价值已保存: ${vt}`);
        } catch (e) {
          console.warn("[lg-worktime] 保存动作价值失败:", e?.message);
        } finally {
          saving.value = false;
        }
      };

      return () => {
        if (!props.step?.label) {
          return h("span", { style: "font-size:10px;color:#64748b;" }, "--");
        }
        const vt = currentVal();
        const meta = VT_META[vt] || VT_META.VA;
        return h("div", { class: "lgwt-cell" }, [
          h("select", {
            class: "lgwt-cell-select",
            style: { borderColor: meta.color, color: meta.color },
            value: vt,
            disabled: saving.value,
            title: "LEAN 动作价值: VA 增值 / BVA 必要非增值 / NVA 非增值",
            onChange,
          }, [
            h("option", { value: "VA" }, "VA 增值"),
            h("option", { value: "BVA" }, "BVA 必要非增值"),
            h("option", { value: "NVA" }, "NVA 非增值"),
          ]),
        ]);
      };
    },
  });
}


// ==================== 展会其它页 (iframe 整页覆盖, 对齐 showcase 1.4.1) ====================
// 检测主页仍用 LG 领导看板; 项目/模型/源/数据/MES/报警/设置 走展会原型.
function buildShowcasePages(host) {
  const { defineComponent, h, ref, onMounted, onBeforeUnmount } = host.vue;
  const api = host.api;
  const router = host.router;
  const base = ((api && api.defaults && api.defaults.baseURL) || "/api/v1").replace(/\/$/, "");

  // 双向桥只装一次 (与纯展会插件同契约: __tjscAction / __tjscRes)
  if (api && !window.__lgwtScBridgeReady) {
    window.__lgwtScBridgeReady = true;
    window.addEventListener("message", async (ev) => {
      const d = ev && ev.data;
      if (!d || d.__tjscAction !== true) return;
      // 路由同步: 实时监控 → 宿主 /monitor (LG 看板); 其它 path 同步宿主以免刷新丢页
      if (d.action === "navigate") {
        const path = (d.payload && d.payload.path) || "/monitor";
        try {
          if (router && typeof router.push === "function") {
            const cur = (router.currentRoute && router.currentRoute.value && router.currentRoute.value.path) || "";
            if (cur !== path) router.push(path);
          }
        } catch (e) { /* ignore */ }
        return;
      }
      if (d.action === "scan-key") {
        try {
          const k = (d.payload || {}).key;
          if (k) window.dispatchEvent(new KeyboardEvent("keydown", { key: k, code: (d.payload || {}).code || "", bubbles: true }));
        } catch (e) { /* ignore */ }
        return;
      }
      if (d.action === "scan-keys") {
        try {
          const text = String((d.payload || {}).text || "");
          for (const ch of text) window.dispatchEvent(new KeyboardEvent("keydown", { key: ch, bubbles: true }));
          window.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
        } catch (e) { /* ignore */ }
        return;
      }
      if (d.action !== "api" && d.action !== "upload" && d.action !== "download") return;
      const id = d.id;
      const p = d.payload || {};
      const reply = (ok, data, error, status) => {
        try { ev.source && ev.source.postMessage({ __tjscRes: true, id, ok, data, error, status }, "*"); }
        catch (e) { /* ignore */ }
      };
      if (d.action === "upload") {
        try {
          const fd = new FormData();
          const fname = p.filename || (p.file && p.file.name) || "upload.bin";
          fd.append("file", p.file, fname);
          const fields = p.fields || {};
          Object.keys(fields).forEach((k) => {
            if (fields[k] != null && fields[k] !== "") fd.append(k, fields[k]);
          });
          const res = await api.post(p.url, fd, {
            params: p.params || undefined, timeout: 300000,
            headers: { "Content-Type": "multipart/form-data" },
          });
          reply(true, res && res.data, null, res && res.status);
        } catch (err) {
          const r = err && err.response;
          reply(false, r && r.data, (err && err.message) || "上传失败", r && r.status);
        }
        return;
      }
      if (d.action === "download") {
        try {
          const m = String(p.method || "get").toLowerCase();
          const cfg = { params: p.params || undefined, responseType: "blob", timeout: 300000 };
          const res = m === "get" ? await api.get(p.url, cfg) : await api[m](p.url, p.data || {}, cfg);
          let fname = p.filename || "download.bin";
          const cd = res.headers && (res.headers["content-disposition"] || res.headers["Content-Disposition"]);
          if (cd) {
            const mm = /filename\*?=(?:UTF-8'')?"?([^";]+)/i.exec(cd);
            if (mm) { try { fname = decodeURIComponent(mm[1]); } catch (e) { fname = mm[1]; } }
          }
          reply(true, { blob: res.data, filename: fname }, null, res && res.status);
        } catch (err) {
          const r = err && err.response;
          reply(false, null, (err && err.message) || "下载失败", r && r.status);
        }
        return;
      }
      const method = String(p.method || "get").toLowerCase();
      try {
        const cfg = { params: p.params || undefined, headers: p.headers || undefined };
        let res;
        if (method === "get") res = await api.get(p.url, cfg);
        else if (method === "delete") res = await api.delete(p.url, { ...cfg, data: p.data || undefined });
        else res = await api[method](p.url, p.data || {}, cfg);
        reply(true, res && res.data, null, res && res.status);
      } catch (err) {
        const r = err && err.response;
        reply(false, r && r.data, (err && err.message) || "请求失败", r && r.status);
      }
    });
  }

  return defineComponent({
    name: "LgwtShowcaseOverlay",
    props: {
      // 宿主当前路由, 用于首屏对齐 (project/model/...)
      initialRoute: { type: String, default: "" },
    },
    setup(props) {
      const frame = ref(null);
      let timer = null;
      let detTimer = null;
      const routeHint = props.initialRoute
        || (router && router.currentRoute && router.currentRoute.value && router.currentRoute.value.path)
        || "/project";
      const appUrl = base
        + "/plugins/active/assets/frontend/dist/showcase/showcase-app.html"
        + "?route=" + encodeURIComponent(routeHint)
        + "&v=" + Date.now();

      async function pump() {
        if (!api) return;
        const win = frame.value && frame.value.contentWindow;
        if (!win) return;
        const results = await Promise.allSettled([
          api.get("/source/detection/results", { params: { channel: 0 } }),
          api.get("/system/device-status"),
          api.get("/data/stats/behavior-score"),
          api.get("/data/stats/yield-trend"),
        ]);
        const pick = (i) => (results[i].status === "fulfilled" ? results[i].value.data : null);
        let displayCfg = null;
        try {
          const raw = localStorage.getItem("display_settings");
          if (raw) displayCfg = JSON.parse(raw);
        } catch (e) { /* ignore */ }
        try {
          win.postMessage({
            __tjsc: true,
            detection: pick(0), device: pick(1), score: pick(2), yield: pick(3),
            display: displayCfg,
          }, "*");
        } catch (e) { /* ignore */ }
      }
      async function pumpDetection() {
        if (!api) return;
        const win = frame.value && frame.value.contentWindow;
        if (!win) return;
        try {
          const r = await api.get("/source/detection/results", { params: { channel: 0 } });
          win.postMessage({ __tjsc: true, detection: r && r.data }, "*");
        } catch (e) { /* ignore */ }
      }

      onMounted(() => {
        timer = setInterval(pump, 1500);
        setTimeout(pump, 1200);
        detTimer = setInterval(pumpDetection, 400);
        setTimeout(pumpDetection, 700);
      });
      onBeforeUnmount(() => {
        if (timer) clearInterval(timer);
        if (detTimer) clearInterval(detTimer);
      });

      return () => h("iframe", {
        ref: frame,
        src: appUrl,
        title: "天军 AI 视觉行为分析防错检测系统",
        style: [
          "position:fixed", "inset:0", "width:100vw", "height:100vh",
          "border:0", "margin:0", "padding:0", "z-index:3000", "background:#04070f",
        ].join(";"),
        allow: "fullscreen; autoplay",
      });
    },
  });
}

// ==================== 入口 ====================
export default {
  async register({ host, registry }) {
    console.log(`[${CC}] register: v1.5.2 (echarts=${!!host.echarts}, showcase-pages=on, fullscreen-monitor=on, lg-params-configurable=on, device-info=real, mjpeg-no-kick=on)`);

    // 检测中心全屏化开关: theme.css 用 body.lgwt-app 把宿主 monitor 覆盖容器
    // 提升为 fixed 全屏 (盖掉宿主导航栏), 与其它 7 页展会 iframe 视觉连续
    try { document.body.classList.add("lgwt-app"); } catch (e) { /* SSR/测试环境忽略 */ }

    if (registry.slots && typeof registry.slots.register === "function") {
      // 检测主页 = LG 领导看板 (结构保留, 风格靠展会)
      registry.slots.register("monitor.layout.body", buildDashboard(host));
      _registeredSlots.push("monitor.layout.body");

      // 其它 7 页 = 展会全应用界面 (iframe 原型, 像素级一致)
      const Showcase = buildShowcasePages(host);
      const otherSlots = [
        "project.layout.body", "model.layout.body", "source.layout.body",
        "data.layout.body", "mes.layout.body", "alarm.layout.body",
        "settings.layout.body",
      ];
      otherSlots.forEach((name) => {
        registry.slots.register(name, Showcase);
        _registeredSlots.push(name);
      });
      console.log(`[${CC}] slots: monitor=LG看板, 其它 ${otherSlots.length} 页=展会界面`);

      // 项目步骤价值格: 宿主原项目页被展会覆盖时不可见; 仍注册以便将来嵌入/回退
      registry.slots.register("project.step-cell.durations", buildValueCell(host));
      _registeredSlots.push("project.step-cell.durations");
    } else {
      console.warn(`[${CC}] registry.slots 不可用 (主程序版本过旧?)`);
    }

    return { name: "lg-worktime-dashboard", version: "1.5.2" };
  },

  async unregister({ registry }) {
    _registeredSlots.forEach((name) => {
      try { registry?.slots?.unregister?.(name); } catch (e) { /* 忽略 */ }
    });
    _registeredSlots = [];
    try { document.body.classList.remove("lgwt-app"); } catch (e) { /* 忽略 */ }
    console.log(`[${CC}] unregister 完成`);
  },
};
