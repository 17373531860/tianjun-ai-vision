// D1 背压协调器单测 (node 直跑: node frontend/tests/framePump.test.mjs)
// 验证: 每工位同时只解一帧 / 跟不上时丢中间帧只留最新 / 跨工位互不阻塞。
import assert from 'node:assert';
import { createFramePump } from '../src/views/Monitor/framePump.js';

function deferred() {
  let resolve;
  const p = new Promise((res) => { resolve = res; });
  return { p, resolve };
}

async function flush(n = 4) {
  for (let i = 0; i < n; i++) await Promise.resolve();
}

async function main() {
  // ============ 背压: 只解最新, 中间帧丢弃, 同时只解一帧 ============
  const decoded = [];
  const gate = {};
  let concurrent = 0, maxConcurrent = 0;
  const pump = createFramePump((ch, data) => {
    concurrent++; maxConcurrent = Math.max(maxConcurrent, concurrent);
    decoded.push(data);
    const d = deferred();
    gate[data] = () => { concurrent--; d.resolve(); };
    return d.p;
  });

  pump.push(0, 'f0');   // 立即开解 f0
  pump.push(0, 'f1');   // f0 在途 → pending=f1
  pump.push(0, 'f2');   // 覆盖 pending=f2
  pump.push(0, 'f3');   // pending=f3
  pump.push(0, 'f4');   // pending=f4 (最新)

  assert.deepEqual(decoded, ['f0'], '在途时不应并发解第二帧');
  assert.deepEqual(pump._state().pending, { 0: 'f4' }, 'pending 必须只留最新一帧 f4');

  gate['f0']();
  await flush();
  assert.deepEqual(decoded, ['f0', 'f4'], 'f0 解完应接着解最新 f4, 丢弃 f1~f3');

  gate['f4']();
  await flush();
  assert.equal(maxConcurrent, 1, '每工位同时只能有一帧在解');
  assert.deepEqual(pump._state().pending, {}, '排空后 pending 应为空');

  // ============ 跨工位互不阻塞 ============
  const seen = [];
  const g2 = {};
  const pump2 = createFramePump((ch, data) => {
    seen.push(`${ch}:${data}`);
    const d = deferred();
    g2[data] = d.resolve;
    return d.p;
  });
  pump2.push(0, 'a');   // ch0 卡住
  pump2.push(1, 'b');   // ch1 应立即也开始 (不被 ch0 阻塞)
  assert.deepEqual(seen.sort(), ['0:a', '1:b'], '不同工位应各自独立解码');

  console.log('framePump 背压测试全过 ✓ (decoded =', decoded, ')');
}

main().then(() => process.exit(0)).catch((e) => {
  console.error('FAIL:', e.message);
  process.exit(1);
});
