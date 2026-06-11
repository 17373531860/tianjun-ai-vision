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
  // 带时间戳防 HTTP 缓存: 浏览器/Electron 会缓存旧版 HTML, 升级插件后按钮绑定停留在旧代码
  const appUrl = base + "/plugins/active/assets/frontend/dist/showcase-app.html?v=" + Date.now();

  // ==================== 双向桥: 通用 REST 代理 ====================
  // iframe 内通过 postMessage 发 {__tjscAction:true, id, action:'api', payload:{method,url,params,data}}
  // 父层用已鉴权的 host.api 调真后端, 把结果 / 错误回填给发起方 iframe (ev.source)。
  // 一次注册, 复用同一个 host.api (带 token / baseURL)。展会插件场景, 不做额外白名单。
  if (api && !window.__tjscBridgeReady) {
    window.__tjscBridgeReady = true;
    window.addEventListener("message", async (ev) => {
      const d = ev && ev.data;
      if (!d || d.__tjscAction !== true) return;
      if (d.action !== "api" && d.action !== "upload" && d.action !== "download") return;
      const id = d.id;
      const p = d.payload || {};
      const reply = (ok, data, error, status) => {
        try {
          ev.source && ev.source.postMessage(
            { __tjscRes: true, id, ok, data, error, status }, "*"
          );
        } catch (e) { /* iframe 已卸载等情况静默 */ }
      };
      // ==================== 文件上传分支 ====================
      // iframe 把 File 对象 (postMessage 结构化克隆可传) 发上来, 父层包成
      // FormData 走 multipart; JSON 桥传不了文件, 故单列。超时放大到 5 分钟。
      if (d.action === "upload") {
        try {
          const fd = new FormData();
          const fname = p.filename || (p.file && p.file.name) || "upload.bin";
          fd.append("file", p.file, fname);
          const fields = p.fields || {};
          Object.keys(fields).forEach((k) => {
            if (fields[k] != null && fields[k] !== "") fd.append(k, fields[k]);
          });
          // host.api 实例默认 Content-Type 是 application/json, 不覆盖的话
          // FormData 体会顶着 JSON 头发出去, 后端表单解析失败 422 (file/name 全丢)。
          const res = await api.post(p.url, fd, {
            params: p.params || undefined,
            timeout: 300000,
            headers: { "Content-Type": "multipart/form-data" },
          });
          reply(true, res && res.data, null, res && res.status);
        } catch (err) {
          const r = err && err.response;
          reply(false, r && r.data, (err && err.message) || "上传失败", r && r.status);
        }
        return;
      }
      // ==================== 二进制下载分支 ====================
      // CSV 导出 / 自定义渲染 / 数据库备份等返回文件流, JSON 桥拿不到,
      // 父层以 blob 取回 (Blob 可结构化克隆) 连同文件名回传 iframe 触发保存。
      if (d.action === "download") {
        try {
          const m = String(p.method || "get").toLowerCase();
          const cfg = { params: p.params || undefined, responseType: "blob", timeout: 300000 };
          const res = m === "get"
            ? await api.get(p.url, cfg)
            : await api[m](p.url, p.data || {}, cfg);
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
        let res;
        // headers 透传: 插件侧登录后可带自己的 Authorization (宿主无 token 时生效)
        const cfg = { params: p.params || undefined, headers: p.headers || undefined };
        if (method === "get") {
          res = await api.get(p.url, cfg);
        } else if (method === "delete") {
          // axios delete 的 body 走 config.data (后端部分 DELETE 端点要请求体)
          res = await api.delete(p.url, { ...cfg, data: p.data || undefined });
        } else {
          res = await api[method](p.url, p.data || {}, cfg);
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
      let detTimer = null;

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

      // 检测结果单独高频轮询: 视频 MJPEG 实时, 检测框/OK-NG 提示靠这条数据驱动,
      // 跟 1.5s 的设备/评分轮询分开, 否则框一跳一跳很卡
      async function pumpDetection() {
        if (!api) return;
        const win = frame.value && frame.value.contentWindow;
        if (!win) return;
        try {
          const r = await api.get("/source/detection/results", { params: { channel: 0 } });
          win.postMessage({ __tjsc: true, detection: r && r.data }, "*");
        } catch (e) { /* 未就绪静默 */ }
      }

      onMounted(() => {
        // 首拍延迟到 iframe 内脚本就绪后再发
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
