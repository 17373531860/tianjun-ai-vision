/**
 * useSingleStream — 单工位 MJPEG 双缓冲流域（巨石重构阶段1③，v3.54.1 逐行等价移植）
 *
 * ⚠ 不变量 7 核心区：双 <img> 交替（释放 Chromium 原生解码器内存）+
 *   watchdog 强制重连（++streamKey 重建 DOM 破 Chrome socket pool 复用）。
 *   定期换流的节拍 STREAM_SWAP_INTERVAL=600 在 index.vue 轮询里驱动（勿删勿大改），
 *   本文件只提供 swapStream 动作本体。
 *
 * ctx 依赖（index.vue 注入）：
 *   isMounted()               — monitorMounted 惰性读取
 *   effectiveLayoutBodyOverride — ref（layout.body 插件独占流时宿主必须让位）
 *   selectedChannel           — ref（调试日志用）
 *   onFirstFrame()            — 首帧到达回调（index 里做 resizeCanvas）
 */
import { ref, nextTick } from 'vue';
import { getBackendHost } from '@/api/index';
import { dbg } from '@/utils/debug';

export function useSingleStream(ctx) {
  const { isMounted, effectiveLayoutBodyOverride, selectedChannel, onFirstFrame } = ctx;

  const isStreaming = ref(false);

  // Double-buffered MJPEG stream: two <img> elements alternate to release
  // Chromium's native decoder memory without any visible flicker.
  const activeStream = ref(0);           // which img is currently visible (0 or 1)
  const streamSrc0 = ref('');
  const streamSrc1 = ref('');
  // v3.7.x: <img> 元素的 key, watchdog 触发"强制重连"时 ++,
  // Vue 会销毁旧 <img> DOM 节点 + 创建新节点, 这样 Chrome 必断
  // keep-alive socket pool 里那条卡死的旧连接, 这是单纯改 src 做不到的。
  const streamKey = ref(0);
  let streamErrorCount = 0;
  let streamReconnectTimer = null;

  // v3.7.x MJPEG watchdog: "改完参数回 Monitor 偶尔黑屏"的根因是
  // Chrome 对 multipart/x-mixed-replace 长连接的 socket 可能在 keep-alive
  // pool 里复用到一条已经卡死的旧连接, <img @error> 不会触发, 导致
  // 永远收不到新帧, 也没有任何兜底自愈机制。watchdog 在 connectStream 后
  // 启动 N 秒首帧检测, 期间没收到首帧 -> 强制 disconnect+ ++streamKey 重建
  // <img> DOM + 用新 url 重连, 这是破 Chrome socket pool 复用的唯一方式。
  //
  // 注: multipart/x-mixed-replace 流的 <img> 只在【首帧】触发 @load 事件,
  // 后续帧通过 image decoder 直接推到 GPU, 不触发 onload。所以心跳式 watchdog
  // (每帧 onload 重置 timer) 不可行 — 会误触发把好流当死流重连。中途流卡死
  // 的检测改用"后端轮询 fps > 0 但前端 isStreaming=false" 来判定
  // (trackBackendFpsMismatch, 由 polling 每拍调用)。
  let streamWatchdogTimer = null;
  let streamConnectAttempts = 0;
  const STREAM_FIRST_FRAME_TIMEOUT_MS = 5000;
  const STREAM_MAX_WATCHDOG_RETRIES = 10;
  // 后端报 fps > 0 但前端 isStreaming 假持续超过这个秒数 -> 判定真黑屏
  const STREAM_BACKEND_FPS_MISMATCH_THRESHOLD_MS = 4000;
  let streamBackendMismatchSince = 0;

  const buildStreamUrl = () => `${getBackendHost()}/video_feed?t=${Date.now()}`;

  // 启动一个 watchdog timer。timeoutMs 内若没有被 onStreamReady 重新
  // armStreamWatchdog 重置, 触发强制重连: ++ streamKey 重建 <img> DOM,
  // nextTick 后设新 url。这是破 Chrome socket pool 复用的唯一可靠方式。
  const armStreamWatchdog = (timeoutMs) => {
    if (streamWatchdogTimer) {
      clearTimeout(streamWatchdogTimer);
      streamWatchdogTimer = null;
    }
    streamWatchdogTimer = setTimeout(() => {
      streamWatchdogTimer = null;
      if (!isMounted()) return;
      if (streamConnectAttempts >= STREAM_MAX_WATCHDOG_RETRIES) {
        console.warn('[MJPEG] watchdog 重试已达上限, 放弃自动重连');
        return;
      }
      streamConnectAttempts++;
      console.warn(`[MJPEG] watchdog 第 ${streamConnectAttempts} 次强制重连: ${timeoutMs}ms 内未收到帧, 重建 <img> DOM`);
      dbg('monitor.video', `watchdog 强制重连 (第${streamConnectAttempts}次)`, `${timeoutMs}ms 内未收到帧`);
      isStreaming.value = false;
      streamSrc0.value = '';
      streamSrc1.value = '';
      streamKey.value++;
      nextTick(() => {
        if (!isMounted()) return;
        streamSrc0.value = buildStreamUrl();
        activeStream.value = 0;
        armStreamWatchdog(STREAM_FIRST_FRAME_TIMEOUT_MS);
      });
    }, timeoutMs);
  };

  const connectStream = () => {
    // layout.body 插件自己用 <img> 吃 /video_feed; 宿主再连会踢断插件流 → 画面冻帧。
    if (effectiveLayoutBodyOverride.value) {
      disconnectStream();
      return;
    }
    // 先清两个 img 的 src + 重建 DOM, 让浏览器关掉潜在旧 socket;
    // nextTick 后再设新 url, 配合 watchdog 形成完整的"破 socket 复用"信号。
    dbg('monitor.video', '连接视频流', `channel=${selectedChannel.value || 0}`);
    streamSrc0.value = '';
    streamSrc1.value = '';
    isStreaming.value = false;
    streamErrorCount = 0;
    streamConnectAttempts = 0;
    streamKey.value++;
    nextTick(() => {
      if (!isMounted()) return;
      if (effectiveLayoutBodyOverride.value) return;
      streamSrc0.value = buildStreamUrl();
      activeStream.value = 0;
      armStreamWatchdog(STREAM_FIRST_FRAME_TIMEOUT_MS);
    });
  };

  const disconnectStream = () => {
    dbg('monitor.video', '断开视频流');
    if (streamWatchdogTimer) {
      clearTimeout(streamWatchdogTimer);
      streamWatchdogTimer = null;
    }
    streamConnectAttempts = 0;
    isStreaming.value = false;
    streamSrc0.value = '';
    streamSrc1.value = '';
  };

  const swapStream = () => {
    const bg = activeStream.value === 0 ? 1 : 0;
    const bgSrcRef = bg === 0 ? streamSrc0 : streamSrc1;
    bgSrcRef.value = buildStreamUrl();
    // onStreamReady(bg) will do the actual swap when the first frame arrives.
    //
    // 历史踩坑: 这里曾经加过 swap watchdog (3s 内 bg 接不到首帧就强制 connectStream),
    // 但健康流的 swap 也会偶发 onload 延迟超过 3s, watchdog 误判 → 整体重连 → 闪烁。
    // (实测: uvicorn 日志每 9s 一组 3 次 /video_feed 长连接.)
    // 现在 swap 失败保持 active 不切, 等下次 swap (默认 1 分钟) 再尝试, 不再做激进探活。
  };

  // Kept as alias so existing call-sites (start/stop/resume) still work
  const forceReconnectStream = () => connectStream();

  const onStreamReady = (idx) => {
    streamErrorCount = 0;
    streamConnectAttempts = 0;
    isStreaming.value = true;
    streamBackendMismatchSince = 0;
    dbg('monitor.video', '视频流首帧到达', `img=${idx} channel=${selectedChannel.value || 0} (接管为active并重置画布)`);
    // 首帧成功 -> 取消首帧 watchdog。multipart/x-mixed-replace 后续帧不
    // 触发 onload, 不能用心跳 watchdog 重置, 中途卡死改由 polling 路径检测。
    if (streamWatchdogTimer) {
      clearTimeout(streamWatchdogTimer);
      streamWatchdogTimer = null;
    }
    onFirstFrame();
    if (idx !== activeStream.value) {
      const oldIdx = activeStream.value;
      activeStream.value = idx;
      // Release the old img's decoder memory
      if (oldIdx === 0) streamSrc0.value = '';
      else streamSrc1.value = '';
    }
  };

  const onStreamError = (idx) => {
    if (!isMounted()) return;
    // 只 active img 的 error 触发重连; bg img 接不到首帧改由 swap watchdog 兜底。
    // 之前尝试取消这个守门让 bg error 也走重连, 结果 connectStream 清空 src='' 又
    // 触发新一轮 error → onStreamError → connectStream 的死循环, 画面 100ms 一次闪烁。
    // (uvicorn 日志同一秒内建 3 次 /video_feed 长连接 → 闭环已确认.)
    if (idx !== activeStream.value) return;
    streamErrorCount++;
    dbg('monitor.video', `视频流错误 (第${streamErrorCount}次)`, `img=${idx}`);
    if (streamErrorCount > 50) return;
    const delay = Math.min(streamErrorCount * 300, 3000);
    if (streamReconnectTimer) {
      clearTimeout(streamReconnectTimer);
      streamReconnectTimer = null;
    }
    streamReconnectTimer = setTimeout(() => {
      streamReconnectTimer = null;
      if (!isMounted()) return;
      connectStream();
    }, delay);
  };

  // 后端推理中 (is_running && fps>0) 但前端 <img> 一直没帧 → 判定真黑屏强制重连。
  // 由单工位 polling 每拍调用, 语义与内联版逐行一致。
  const trackBackendFpsMismatch = (backendActive, nowTs) => {
    if (backendActive && !isStreaming.value) {
      if (streamBackendMismatchSince === 0) {
        streamBackendMismatchSince = nowTs;
      } else if (nowTs - streamBackendMismatchSince > STREAM_BACKEND_FPS_MISMATCH_THRESHOLD_MS) {
        console.warn(`[MJPEG] 后端推理中但前端 <img> 未收到帧持续 ${(nowTs - streamBackendMismatchSince) / 1000}s, 强制重连`);
        streamBackendMismatchSince = 0;
        connectStream();
      }
    } else if (isStreaming.value) {
      streamBackendMismatchSince = 0;
    }
  };

  // 供 index 的 clearMonitorPendingTimers 统一清理挂起 timer
  const clearStreamTimers = () => {
    if (streamReconnectTimer) { clearTimeout(streamReconnectTimer); streamReconnectTimer = null; }
    if (streamWatchdogTimer) { clearTimeout(streamWatchdogTimer); streamWatchdogTimer = null; }
  };

  const resetStreamErrorCount = () => { streamErrorCount = 0; };

  return {
    isStreaming, activeStream, streamSrc0, streamSrc1, streamKey,
    connectStream, disconnectStream, swapStream, forceReconnectStream,
    onStreamReady, onStreamError,
    trackBackendFpsMismatch, clearStreamTimers, resetStreamErrorCount,
  };
}
