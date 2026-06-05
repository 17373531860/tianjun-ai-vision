/**
 * 天军 展会 全应用定制界面 — 前端入口 (ESM, 手写无构建)
 *
 * 设计取舍 (v1.1 重做):
 *   之前用 h() 把原型重写了一遍 → 出来是"简洁版原程序", 完全丢了原型 ai-behavior-monitor.html
 *   的 HUD 仪表盘质感。3276 行的原型有自带外壳 / 客户端 8 页路由 / ECharts / 模式可见性 /
 *   环境填充动画, 用 h() 等比例还原成本高且必然失真。
 *
 *   改为: 把原型 HTML 本身作为插件资源 (showcase-app.html), 用一个全屏 iframe 直接加载它。
 *   这样展示出来的"就是"那张原型 → 像素级一致, CSS/JS 完全隔离, 零污染主程序。
 *   iframe 通过 RFC12 的 *.layout.body slot 挂上, position:fixed 覆盖整屏 (含主程序导航),
 *   原型自带的左侧导航负责 8 页切换 → 一个 iframe 自洽承载整套应用。
 *
 *   真实数据: 父窗口轮询主程序 REST (检测结果 / 设备状态 / 行为评分), 通过 postMessage
 *   下发给 iframe; 原型内监听 message 把真值写进对应面板 (见 showcase-app.html 末尾的
 *   __tjsc 接收器)。接口未就绪/无数据时原型回退自带演示数据, 不崩。
 */

export function register(ctx) {
  const host = (ctx && ctx.host) || {};
  const registry = (ctx && ctx.registry) || {};
  const vue = host.vue;
  if (!vue || !registry || !registry.slots) {
    // 缺核心能力直接放弃, 不影响主程序
    return { ok: false, reason: "missing host.vue/registry" };
  }
  const { h, defineComponent, ref, onMounted, onBeforeUnmount } = vue;
  const api = host.api;
  const base = ((api && api.defaults && api.defaults.baseURL) || "/api/v1").replace(/\/$/, "");
  const appUrl = base + "/plugins/active/assets/frontend/dist/showcase-app.html";

  // ==================== 双向桥: 通用 REST 代理 ====================
  // iframe 内通过 postMessage 发 {__tjscAction:true, id, action:'api', payload:{method,url,params,data}}
  // 父层用已鉴权的 host.api 调真后端, 把结果 / 错误回填给发起方 iframe (ev.source)。
  // 一次注册, 复用同一个 host.api (带 token / baseURL)。展会插件场景, 不做额外白名单。
  if (api && !window.__tjscBridgeReady) {
    window.__tjscBridgeReady = true;
    window.addEventListener("message", async (ev) => {
      const d = ev && ev.data;
      if (!d || d.__tjscAction !== true || d.action !== "api") return;
      const id = d.id;
      const p = d.payload || {};
      const method = String(p.method || "get").toLowerCase();
      const reply = (ok, data, error, status) => {
        try {
          ev.source && ev.source.postMessage(
            { __tjscRes: true, id, ok, data, error, status }, "*"
          );
        } catch (e) { /* iframe 已卸载等情况静默 */ }
      };
      try {
        let res;
        if (method === "get" || method === "delete") {
          res = await api[method](p.url, { params: p.params || undefined });
        } else {
          res = await api[method](p.url, p.data || {}, { params: p.params || undefined });
        }
        reply(true, res && res.data, null, res && res.status);
      } catch (err) {
        const r = err && err.response;
        reply(false, r && r.data, (err && err.message) || "请求失败", r && r.status);
      }
    });
  }

  const Overlay = defineComponent({
    name: "TjShowcaseOverlay",
    setup() {
      const frame = ref(null);
      let timer = null;

      // ==================== 真实数据泵 ====================
      // 轮询关键接口, 把真值 postMessage 给 iframe。iframe 内 __tjsc 接收器消费。
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
        // 显示设置: 同源读主程序 useSystemStore 落盘的 display_settings, 让面板显隐跟设置走
        let displayCfg = null;
        try {
          const raw = localStorage.getItem("display_settings");
          if (raw) displayCfg = JSON.parse(raw);
        } catch (e) { /* 无配置 → iframe 默认全显示 */ }
        try {
          win.postMessage({
            __tjsc: true,
            detection: pick(0),
            device: pick(1),
            score: pick(2),
            yield: pick(3),
            display: displayCfg,
          }, "*");
        } catch (e) { /* 跨域/未就绪时静默 */ }
      }

      onMounted(() => {
        // 首拍延迟到 iframe 内脚本就绪后再发
        timer = setInterval(pump, 1500);
        setTimeout(pump, 1200);
      });
      onBeforeUnmount(() => { if (timer) clearInterval(timer); });

      return () => h("iframe", {
        ref: frame,
        src: appUrl,
        title: "天军 AI 视觉行为分析防错检测系统",
        style: [
          "position:fixed", "inset:0",
          "width:100vw", "height:100vh",
          "border:0", "margin:0", "padding:0",
          "z-index:3000", "background:#04070f",
        ].join(";"),
        allow: "fullscreen; autoplay",
      });
    },
  });

  // 8 页全部挂同一覆盖层: 无论主路由停在哪一页, 整屏都是展会界面 (原型内部自管 8 页切换)。
  // 同一时刻只有当前路由的 slot 渲染, 不会叠多个 iframe。
  const slots = [
    "monitor.layout.body",
    "project.layout.body",
    "model.layout.body",
    "source.layout.body",
    "data.layout.body",
    "mes.layout.body",
    "alarm.layout.body",
    "settings.layout.body",
  ];
  for (const name of slots) {
    registry.slots.register(name, Overlay);
  }

  return { ok: true, mode: "iframe-overlay", slots: slots.length };
}

export default { register };
