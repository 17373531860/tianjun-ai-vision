// ==================== D1: 多通道视频解码背压协调器 (纯逻辑, 无 DOM 依赖) ====================
//
// 问题: 多工位 MJPEG 每来一帧就 new Image() 异步解码; 解码跟不上到达速率时,
//   未完成的解码 + blob URL 越堆越多 (4 路高帧率叠加), 长时间运行内存/GC 压力升高。
//
// 解法: "每工位同时只解一帧, 跟不上就丢旧留最新"的背压:
//   - 某工位正在解码时, 新到的帧不排队, 只覆盖式记住"最新一帧";
//   - 当前帧解完后, 若有积压的最新帧, 接着解它 (永远只追最新, 中间帧直接丢)。
//   这样在途解码任务恒为 0~1/工位, 内存有界, 画面始终是最新可解出的那帧。
//
// 这里只放纯协调逻辑 (decodeFn 注入), 不碰 canvas/createImageBitmap, 便于独立单测。

export function createFramePump(decodeFn) {
  // decodeFn(ch, data) -> Promise|any   实际解码+绘制; 返回 Promise 时按其完成判定在途
  const inFlight = Object.create(null);   // ch -> true (该工位有解码在途)
  const pending = Object.create(null);    // ch -> 最新积压帧 (覆盖式, 只留最后一个)

  function push(ch, data) {
    if (inFlight[ch]) {
      pending[ch] = data;   // 覆盖: 只保留最新, 丢弃中间帧
      return;
    }
    _run(ch, data);
  }

  function _run(ch, data) {
    inFlight[ch] = true;
    let p;
    try {
      p = Promise.resolve(decodeFn(ch, data));
    } catch (e) {
      p = Promise.reject(e);
    }
    p.catch(() => {}).then(() => {
      inFlight[ch] = false;
      if (pending[ch] !== undefined) {
        const next = pending[ch];
        delete pending[ch];
        _run(ch, next);   // 追最新积压帧
      }
    });
  }

  function reset() {
    for (const k in inFlight) delete inFlight[k];
    for (const k in pending) delete pending[k];
  }

  // 仅供测试观察内部状态
  function _state() {
    return {
      inFlight: { ...inFlight },
      pending: { ...pending },
    };
  }

  return { push, reset, _state };
}
