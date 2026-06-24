/**
 * Electron 加深就绪门槛 + 降级保护 状态机测试 (v3.23.x)
 *
 * 测对象: electron/backend-manager.js 的 checkHealth() 编排逻辑。
 * 手法: 实例化 BackendManager, 用桩替换底层 _probe(path), 模拟
 *   "深探(/system/startup-ready)" 与 "浅探(/source/status)" 各种组合,
 *   断言: 门槛关只浅探 / 冷启动不误降级 / DB正常立即就绪 /
 *         DB坏超阈值降级放行且只通知一次 / 启动期结束后不再降级。
 *
 * 纯 node 跑 (backend-manager.js 只依赖 node 内置模块, 无 electron require):
 *   node tests/electron/test_deep_gate_downgrade.cjs
 * exit 0 = 全过; exit 1 = 有失败。
 */
const assert = require('assert');
const path = require('path');
const BackendManager = require(path.join(__dirname, '..', '..', 'electron', 'backend-manager.js'));

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

let passed = 0;
let failed = 0;
async function test(name, fn) {
  try {
    await fn();
    passed += 1;
    console.log(`[OK] ${name}`);
  } catch (e) {
    failed += 1;
    console.log(`[!!] ${name}\n     ${e.message}`);
  }
}

// 造一个 BackendManager, 用 probeMap 决定每个路径返回 true/false, 并记录探测次数
function makeMgr({ deepReadyGate, probeMap, downgradeMs = 60 }) {
  const mgr = new BackendManager({ deepReadyGate });
  mgr._deepDowngradeMs = downgradeMs;
  mgr.probeCalls = [];
  mgr._probe = (p) => {
    mgr.probeCalls.push(p);
    const key = p.includes('startup-ready') ? 'deep' : 'shallow';
    return Promise.resolve(!!probeMap[key]);
  };
  return mgr;
}

const DEEP = '/api/v1/system/startup-ready';
const SHALLOW = '/api/v1/source/status';

(async () => {
  // 1) 门槛关: 只浅探, 不碰深探
  await test('门槛关时只浅探 /source/status, 不探深探针', async () => {
    const mgr = makeMgr({ deepReadyGate: false, probeMap: { shallow: true, deep: true } });
    const ok = await mgr.checkHealth();
    assert.strictEqual(ok, true, 'checkHealth 应 true');
    assert.ok(mgr.probeCalls.includes(SHALLOW), '应探浅探针');
    assert.ok(!mgr.probeCalls.includes(DEEP), '门槛关不应探深探针');
  });

  // 2) 门槛开 + 正常冷启动(uvicorn没起): 深×浅× → 不就绪, 不起降级时钟
  await test('冷启动 uvicorn 未起时不就绪且不误降级', async () => {
    const mgr = makeMgr({ deepReadyGate: true, probeMap: { shallow: false, deep: false } });
    let emitted = false;
    mgr.on('deep-gate-downgraded', () => { emitted = true; });
    const ok = await mgr.checkHealth();
    assert.strictEqual(ok, false, '都没起应 false');
    assert.strictEqual(mgr._shallowUpSince, null, '浅探未起不应起降级时钟');
    await sleep(80);
    const ok2 = await mgr.checkHealth();
    assert.strictEqual(ok2, false, '仍没起仍 false');
    assert.strictEqual(emitted, false, '冷启动期绝不应降级');
    assert.strictEqual(mgr.deepDowngraded, false);
  });

  // 3) 门槛开 + DB 正常: 深✓ → 立即就绪, 不降级
  await test('深探针通过时立即就绪且不降级', async () => {
    const mgr = makeMgr({ deepReadyGate: true, probeMap: { shallow: true, deep: true } });
    let emitted = false;
    mgr.on('deep-gate-downgraded', () => { emitted = true; });
    const ok = await mgr.checkHealth();
    assert.strictEqual(ok, true, '深探针过应就绪');
    assert.strictEqual(emitted, false, '正常就绪不应降级');
    assert.strictEqual(mgr.deepDowngraded, false);
  });

  // 4) 门槛开 + DB坏(浅✓深×): 未到阈值不降级, 起降级时钟
  await test('uvicorn起但DB未就绪, 阈值内不降级', async () => {
    const mgr = makeMgr({ deepReadyGate: true, probeMap: { shallow: true, deep: false }, downgradeMs: 5000 });
    let emitted = false;
    mgr.on('deep-gate-downgraded', () => { emitted = true; });
    const ok = await mgr.checkHealth();
    assert.strictEqual(ok, false, '阈值内应继续等, false');
    assert.ok(mgr._shallowUpSince !== null, '应起降级时钟');
    assert.strictEqual(emitted, false, '阈值内不应降级');
  });

  // 5) 门槛开 + DB坏 超阈值: 降级放行 + 只通知一次
  await test('DB持续不就绪超阈值降级放行且只通知一次', async () => {
    const mgr = makeMgr({ deepReadyGate: true, probeMap: { shallow: true, deep: false }, downgradeMs: 60 });
    let emitCount = 0;
    mgr.on('deep-gate-downgraded', () => { emitCount += 1; });
    const first = await mgr.checkHealth();
    assert.strictEqual(first, false, '首检起时钟, 还没到阈值');
    await sleep(90);
    const second = await mgr.checkHealth();
    assert.strictEqual(second, true, '超阈值应降级放行 true');
    assert.strictEqual(mgr.deepDowngraded, true, '降级标记应置位');
    assert.strictEqual(emitCount, 1, '应只通知一次');
    const third = await mgr.checkHealth();
    assert.strictEqual(third, true, '降级后仍放行');
    assert.strictEqual(emitCount, 1, '不应重复通知');
  });

  // 6) 启动期结束后(运行期监控): 即使深×浅✓ 也只浅探, 不降级
  await test('启动期结束后运行期不误降级', async () => {
    const mgr = makeMgr({ deepReadyGate: true, probeMap: { shallow: true, deep: false }, downgradeMs: 10 });
    mgr._startupResolved = true;  // 模拟启动就绪阶段已结束
    let emitted = false;
    mgr.on('deep-gate-downgraded', () => { emitted = true; });
    const ok = await mgr.checkHealth();
    assert.strictEqual(ok, true, '运行期只浅探, 浅探过即 true');
    assert.ok(!mgr.probeCalls.includes(DEEP), '运行期不应探深探针');
    await sleep(30);
    await mgr.checkHealth();
    assert.strictEqual(emitted, false, '运行期 DB 卡顿不应弹启动降级');
  });

  console.log(`\n==== deep-gate downgrade: ${passed} passed / ${failed} failed ====`);
  process.exit(failed === 0 ? 0 : 1);
})();
