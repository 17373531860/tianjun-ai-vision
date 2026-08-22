/**
 * useMultiStreams — 多通道视频取流域（巨石重构阶段1②，v3.54.1 逐行等价移植）
 *
 * 职责：多工位 MJPEG 长连接（multipart 解析→canvas 双缓冲绘制）、
 * >4 工位/多屏总览的快照轮询降级、WebKit 零帧断流兜底、按可见工位收放连接。
 *
 * ⚠ 不变量 7 相关域：本文件与单工位双缓冲共同构成 Monitor 视频通路，
 *   逻辑搬运不改语义；MAX_MJPEG_STREAMS/降级阈值/自适应取帧节奏勿随意调。
 *
 * ctx 依赖（全部由 index.vue 注入，本文件不 import store/不读全局）：
 *   channelCount, kioskMode, kioskChannel, zoomedChannel  — refs
 *   gridPageChannels, multiMonitorRuntime, effectiveLayoutBodyOverride — refs/computed
 *   multiFrameNaturalSize — 共享 plain object（overlay 点击换算也读它，属主留 index）
 *   bitmapDecodeEnabled() — 性能开关 getter（systemStore.performance.multiChannelBitmapDecode）
 */
import { getBackendHost } from '@/api/index';
import { createFramePump } from '../framePump';

export function useMultiStreams(ctx) {
  const {
    channelCount, kioskMode, kioskChannel, zoomedChannel,
    gridPageChannels, multiMonitorRuntime, effectiveLayoutBodyOverride,
    multiFrameNaturalSize, bitmapDecodeEnabled,
  } = ctx;

  const multiVideoCanvasRefs = {};
  let multiStreamRunning = false;
  const multiStreamAborts = {};

  // 与单工位 buildStreamUrl 同源: 走 getBackendHost() (开发 .env → 8004 等; 桌面壳默认主机; 浏览器空 host 走 Vite 代理)
  const streamHost = () => getBackendHost();
  const BOUNDARY = '--frame';
  const HEADER_END = '\r\n\r\n';

  // v3.47 多工位重构: MJPEG 流按「可见工位」收放。
  // - 双/三工位: 全部常显, 照旧全拉
  // - 4+ 工位总览网格: 只拉当前页的工位
  // - 放大详情: 只拉放大的那一路
  // 数据轮询 (startMultiPolling) 始终覆盖全部工位 — 计数/Toast/语音/MES 不因翻页丢失,
  // 省的只是不可见通道的 MJPEG 带宽与 JPEG 解码开销。
  const visibleStreamChannels = () => {
    const n = channelCount.value || 1;
    if (kioskMode.value) return [kioskChannel.value];
    if (zoomedChannel.value !== null) return [zoomedChannel.value];
    if (n <= 1) return [];
    if (n <= 3) return Array.from({ length: n }, (_, i) => i);
    return gridPageChannels.value;
  };

  // 浏览器对同一 host 的 HTTP/1.1 并发连接上限是 6 (Chrome/Safari 硬限制)。
  // 每路 MJPEG 是一条永久占用的连接, 3x3 九工位 = 9 条流 + 150ms 数据轮询全挤同一个
  // 后端 host → 流被饿死, 前端 1s 重连 + 后端"新连接上位"互踢, 画面永远加载不出来。
  // 修复一: 可见工位 > 4 时放弃 MJPEG 长连接, 改为 /snapshot 单帧轮询 (短请求, keep-alive
  // 复用 socket, 与数据轮询共存); ≤4 工位(双/三/2x2页/放大单路)保持原 MJPEG 行为不变。
  // 修复二 (Safari/WebKit): WebKit 的 fetch() 读不了 multipart/x-mixed-replace 流
  // (立刻 "Load failed"), canvas 永远黑屏。某工位的 MJPEG 流连续 2 次一帧未出就断
  // → 该工位自动降级为快照轮询兜底 (Playwright webkit 内核实测复现+验证)。
  const MAX_MJPEG_STREAMS = 4;
  const MJPEG_FALLBACK_FAILS = 2;
  const SNAPSHOT_TICK_MS = 40;          // 定时器基础节拍; 实际取帧节奏按工位数自适应
  let snapshotPollTimer = null;
  const snapshotInFlight = {};
  const snapshotLastStart = {};         // ch -> 上次取帧起始时刻 (节奏控制)
  let snapshotChannels = new Set();     // 当前走快照轮询的工位 (定时器常驻读取)
  const mjpegZeroFrameFails = {};       // ch -> 连续"零帧断流"次数, 出过帧即归零

  // 快照取帧间隔按并发工位数自适应: 放大单路 ~12fps, 3x3 九宫格 5fps。
  // 不能一味调快: 每张快照是一次完整 JPEG 编码+HTTP 往返, 工位越多请求越挤
  // (浏览器同 host 只有 6 条连接, 还要让位给 150ms 数据轮询)。
  const _snapshotIntervalMs = () => {
    const n = snapshotChannels.size || 1;
    if (n <= 2) return 80;
    if (n <= 4) return 120;
    if (n <= 9) return 200;
    return 300;
  };

  const startSnapshotPolling = (channels) => {
    snapshotChannels = new Set(channels);
    if (!snapshotChannels.size) {
      stopSnapshotPolling();
      return;
    }
    if (snapshotPollTimer) return;   // 定时器复用, 每 tick 读最新 snapshotChannels
    snapshotPollTimer = setInterval(() => {
      if (!multiStreamRunning) return;
      const interval = _snapshotIntervalMs();
      const now = Date.now();
      snapshotChannels.forEach((ch) => {
        if (snapshotInFlight[ch]) return;   // 上一帧还没取完/没解完, 跳过本 tick (背压)
        if (now - (snapshotLastStart[ch] || 0) < interval) return;
        snapshotInFlight[ch] = true;
        snapshotLastStart[ch] = now;
        fetch(`${streamHost()}/snapshot?channel=${ch}`, { cache: 'no-store' })
          .then((res) => (res.ok ? res.arrayBuffer() : null))
          .then((buf) => {
            if (buf && multiStreamRunning) drawFrameToCanvas(ch, new Uint8Array(buf));
          })
          .catch(() => {})
          .finally(() => { snapshotInFlight[ch] = false; });
      });
    }, SNAPSHOT_TICK_MS);
  };

  const stopSnapshotPolling = () => {
    if (snapshotPollTimer) { clearInterval(snapshotPollTimer); snapshotPollTimer = null; }
    snapshotChannels = new Set();
    Object.keys(snapshotInFlight).forEach((k) => delete snapshotInFlight[k]);
    Object.keys(snapshotLastStart).forEach((k) => delete snapshotLastStart[k]);
  };

  const syncMultiStreams = () => {
    if (!multiStreamRunning) return;
    const visible = visibleStreamChannels();
    const useSnapshotAll = visible.length > MAX_MJPEG_STREAMS
      || (multiMonitorRuntime.value.enabled && !kioskMode.value && zoomedChannel.value === null);
    const snapWant = visible.filter(
      (ch) => useSnapshotAll || (mjpegZeroFrameFails[ch] || 0) >= MJPEG_FALLBACK_FAILS
    );
    const mjpegWant = new Set(visible.filter((ch) => !snapWant.includes(ch)));

    Object.keys(multiStreamAborts).forEach((k) => {
      if (!mjpegWant.has(Number(k))) {
        try { multiStreamAborts[k].abort(); } catch {}
        delete multiStreamAborts[k];
      }
    });
    mjpegWant.forEach((ch) => {
      if (!(ch in multiStreamAborts)) connectMjpegStream(ch);
    });
    startSnapshotPolling(snapWant);
  };

  // MJPEG 流死亡登记: 一帧未出就断 = 疑似环境不支持 (WebKit) 或被同通道新连接踢掉。
  // 连续 MJPEG_FALLBACK_FAILS 次 → 该工位转快照轮询, 返回 true = 调用方不要再排 MJPEG 重连。
  const _registerMjpegDeath = (ch, gotFrame) => {
    if (gotFrame) return false;
    mjpegZeroFrameFails[ch] = (mjpegZeroFrameFails[ch] || 0) + 1;
    if (mjpegZeroFrameFails[ch] >= MJPEG_FALLBACK_FAILS) {
      console.warn(`[MJPEGStream] ch${ch} 连续 ${mjpegZeroFrameFails[ch]} 次零帧断流, 降级为快照轮询`);
      delete multiStreamAborts[ch];
      syncMultiStreams();
      return true;
    }
    return false;
  };

  const startMultiStreams = () => {
    // layout.body 插件独占 MJPEG: 任何误调都直接拒绝, 防竞态漏网
    if (effectiveLayoutBodyOverride.value) {
      stopMultiStreams();
      return;
    }
    stopMultiStreams();
    multiStreamRunning = true;
    syncMultiStreams();
  };

  const connectMjpegStream = async (ch) => {
    if (!multiStreamRunning) return;
    const abort = new AbortController();
    multiStreamAborts[ch] = abort;
    let gotFrame = false;   // 本条连接是否出过至少一帧 (零帧断流 → WebKit 兜底计数)
    try {
      const res = await fetch(`${streamHost()}/video_feed?channel=${ch}`, { signal: abort.signal });
      const reader = res.body.getReader();
      const INIT_BUF_SIZE = 512 * 1024;
      let buf = new Uint8Array(INIT_BUF_SIZE);
      let bufLen = 0;

      while (multiStreamRunning) {
        const { done, value } = await reader.read();
        if (done) break;

        const needed = bufLen + value.length;
        if (needed > buf.length) {
          const newSize = Math.max(buf.length * 2, needed);
          const grown = new Uint8Array(newSize);
          grown.set(buf.subarray(0, bufLen));
          buf = grown;
        }
        buf.set(value, bufLen);
        bufLen += value.length;

        let startIdx = 0;
        const view = buf.subarray(0, bufLen);
        while (true) {
          const boundaryIdx = findBytes(view, BOUNDARY, startIdx);
          if (boundaryIdx === -1) break;
          const headerEndIdx = findBytes(view, HEADER_END, boundaryIdx);
          if (headerEndIdx === -1) break;
          const jpegStart = headerEndIdx + HEADER_END.length;
          const nextBoundary = findBytes(view, BOUNDARY, jpegStart);
          if (nextBoundary === -1) break;

          const jpegEnd = nextBoundary - 2;
          if (jpegEnd > jpegStart) {
            const jpegData = view.slice(jpegStart, jpegEnd);
            if (!gotFrame) { gotFrame = true; mjpegZeroFrameFails[ch] = 0; }
            drawFrameToCanvas(ch, jpegData);
          }
          startIdx = nextBoundary;
        }
        if (startIdx > 0) {
          const remaining = bufLen - startIdx;
          buf.copyWithin(0, startIdx, bufLen);
          bufLen = remaining;
        }
        if (bufLen > 2 * 1024 * 1024) {
          const keep = 512 * 1024;
          buf.copyWithin(0, bufLen - keep, bufLen);
          bufLen = keep;
        }
      }
      // v3.47: 服务端正常关流 (done, 非异常) 也要重连 —— 例如后端重启/换源关旧流,
      // 否则该工位画面从此定格; 与 catch 分支同样按"仍可见"守门
      // (该工位已转快照轮询时 snapshotChannels 含 ch, 禁止 MJPEG 复活抢连接)
      if (multiStreamRunning) {
        if (_registerMjpegDeath(ch, gotFrame)) return;
        setTimeout(() => {
          if (multiStreamRunning && !snapshotChannels.has(ch) && visibleStreamChannels().includes(ch)) connectMjpegStream(ch);
        }, 1000);
      }
    } catch (e) {
      if (e.name !== 'AbortError' && multiStreamRunning) {
        if (_registerMjpegDeath(ch, gotFrame)) return;
        console.warn(`[MJPEGStream] ch${ch} disconnected, reconnecting...`);
        // v3.47: 重连前确认该工位仍可见 (翻页/退出放大后不再为隐藏通道续命)
        setTimeout(() => {
          if (multiStreamRunning && !snapshotChannels.has(ch) && visibleStreamChannels().includes(ch)) connectMjpegStream(ch);
        }, 2000);
      }
    }
  };

  const findBytes = (buf, str, offset = 0) => {
    const target = typeof str === 'string' ? new TextEncoder().encode(str) : str;
    outer: for (let i = offset; i <= buf.length - target.length; i++) {
      for (let j = 0; j < target.length; j++) {
        if (buf[i + j] !== target[j]) continue outer;
      }
      return i;
    }
    return -1;
  };

  // 把一张解出的位图 (HTMLImageElement 或 ImageBitmap) 等比居中绘到工位画布
  const paintToCanvas = (ch, src, natW, natH) => {
    const canvas = multiVideoCanvasRefs[ch];
    if (!canvas || !canvas.isConnected) return;  // v3.47: 脱离 DOM 的旧画布不画
    const parent = canvas.parentElement;
    if (parent) {
      canvas.width = parent.clientWidth;
      canvas.height = parent.clientHeight;
    }
    multiFrameNaturalSize[ch] = { w: natW, h: natH };
    const ctx2 = canvas.getContext('2d');
    const cw = canvas.width, ch2 = canvas.height;
    const scale = Math.min(cw / natW, ch2 / natH);
    const dw = natW * scale;
    const dh = natH * scale;
    const dx = (cw - dw) / 2;
    const dy = (ch2 - dh) / 2;
    ctx2.fillStyle = '#000';
    ctx2.fillRect(0, 0, cw, ch2);
    ctx2.drawImage(src, dx, dy, dw, dh);
  };

  // 旧路径 (默认): new Image() 逐帧解码, 行为与历史字节级一致
  const drawFrameLegacy = (ch, jpegData) => {
    const canvas = multiVideoCanvasRefs[ch];
    if (!canvas) return;
    const blob = new Blob([jpegData], { type: 'image/jpeg' });
    const url = URL.createObjectURL(blob);
    const img = new Image();
    img.onload = () => {
      paintToCanvas(ch, img, img.naturalWidth, img.naturalHeight);
      URL.revokeObjectURL(url);
    };
    img.onerror = () => { try { URL.revokeObjectURL(url); } catch {} };
    img.src = url;
  };

  // D1 新路径 (开关开): createImageBitmap + 背压. 返回 Promise 供 framePump 判定在途。
  const decodeFrameBitmap = (ch, jpegData) => {
    if (!multiVideoCanvasRefs[ch]) return Promise.resolve();
    const blob = new Blob([jpegData], { type: 'image/jpeg' });
    return createImageBitmap(blob)
      .then((bitmap) => {
        try {
          if (multiStreamRunning) paintToCanvas(ch, bitmap, bitmap.width, bitmap.height);
        } finally {
          bitmap.close();   // 立即释放解码像素, 不等 GC
        }
      });
  };

  const multiFramePump = createFramePump(decodeFrameBitmap);

  const drawFrameToCanvas = (ch, jpegData) => {
    if (bitmapDecodeEnabled()) {
      multiFramePump.push(ch, jpegData);   // 背压: 每工位只解最新一帧
    } else {
      drawFrameLegacy(ch, jpegData);
    }
  };

  const stopMultiStreams = () => {
    multiStreamRunning = false;
    stopSnapshotPolling();
    Object.keys(mjpegZeroFrameFails).forEach(k => delete mjpegZeroFrameFails[k]);
    Object.values(multiStreamAborts).forEach(a => { try { a.abort(); } catch {} });
    Object.keys(multiStreamAborts).forEach(k => delete multiStreamAborts[k]);
    multiFramePump.reset();
  };

  // v3.51.5: 工位源"从停到跑"的瞬间强制重连该路视频流（供 processChannelResult 调用）。
  // 语义与内联版逐行一致: 清零帧计数 → 掐掉旧连接 → 按可见性重排。
  const reconnectChannelStream = (ch) => {
    mjpegZeroFrameFails[ch] = 0;
    if (multiStreamAborts[ch]) {
      try { multiStreamAborts[ch].abort(); } catch {}
      delete multiStreamAborts[ch];
    }
    syncMultiStreams();
  };

  const isMultiStreamRunning = () => multiStreamRunning;

  return {
    multiVideoCanvasRefs,
    startMultiStreams,
    stopMultiStreams,
    syncMultiStreams,
    reconnectChannelStream,
    isMultiStreamRunning,
  };
}
