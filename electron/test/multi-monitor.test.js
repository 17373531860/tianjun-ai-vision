'use strict';

const assert = require('node:assert/strict');
const test = require('node:test');
const {
  advanceCrashWindow,
  buildKioskHash,
  enumerateDisplaysForApply,
  isMainRenderer,
  isReservedMainDisplayTarget,
  normalizeMultiMonitorConfig,
  resolveDisplayTarget,
  toDisplayDto,
} = require('../multi-monitor');

const displays = [
  {
    id: 'primary',
    label: '主显示器',
    bounds: { x: 0, y: 0, width: 1920, height: 1080 },
    workArea: { x: 0, y: 0, width: 1920, height: 1040 },
  },
  {
    id: 'wavlink-1',
    label: 'WAVLINK',
    bounds: { x: 1920, y: 0, width: 1920, height: 1080 },
    workArea: { x: 1920, y: 0, width: 1920, height: 1040 },
  },
];

test('默认关闭且只读，非法映射会被过滤', () => {
  const { config, warnings } = normalizeMultiMonitorConfig({
    mapping: {
      '-1': { display_id: 'bad' },
      '0': { display_id: ' 100 ' },
      '1': { bounds: { x: 1920, y: 0, width: 1920, height: 1080 } },
      '2': { display_id: '', bounds: { x: 0, y: 0, width: 0.5, height: 1080 } },
      nope: { display_id: 'bad' },
    },
  });

  assert.equal(config.enabled, false);
  assert.equal(config.readonly, true);
  assert.deepEqual(config.mapping, {
    '0': { display_id: '100', role: 'monitor' },
    '1': { display_id: '', role: 'monitor', bounds: { x: 1920, y: 0, width: 1920, height: 1080 } },
  });
  assert.equal(warnings.length, 3);
});

test('窗口角色 projection 保留、非法值归一 monitor、缺省 monitor', () => {
  const { config } = normalizeMultiMonitorConfig({
    mapping: {
      '0': { display_id: 'a', role: 'projection' },
      '1': { display_id: 'b', role: 'hologram' },
      '2': { display_id: 'c' },
    },
  });

  assert.equal(config.mapping['0'].role, 'projection');
  assert.equal(config.mapping['1'].role, 'monitor');
  assert.equal(config.mapping['2'].role, 'monitor');
});

test('优先按 display_id 定位并使用当前 bounds', () => {
  const target = resolveDisplayTarget({
    display_id: 'wavlink-1',
    bounds: { x: -9999, y: 0, width: 800, height: 600 },
  }, displays);

  assert.equal(target.source, 'display_id');
  assert.deepEqual(target.bounds, displays[1].bounds);
  assert.equal(target.warning, null);
});

test('显示器 ID 漂移时按记忆 bounds 匹配当前显示器', () => {
  const target = resolveDisplayTarget({
    display_id: 'old-wavlink-id',
    bounds: { x: 1920, y: 0, width: 1920, height: 1080 },
  }, displays);

  assert.equal(target.source, 'remembered_bounds');
  assert.equal(target.display.id, 'wavlink-1');
  assert.match(target.warning, /记忆坐标/);
});

test('枚举异常时保留手工 bounds 作为降级位置', () => {
  const displayResult = enumerateDisplaysForApply(() => {
    throw new Error('DisplayLink temporary failure');
  });
  const target = resolveDisplayTarget({
    display_id: 'missing',
    bounds: { x: 3840, y: 0, width: 1280, height: 720 },
  }, displayResult.displays);

  assert.deepEqual(displayResult.displays, []);
  assert.match(displayResult.warnings.join('\n'), /枚举失败.*bounds 降级/);
  assert.equal(target.source, 'manual_bounds');
  assert.deepEqual(target.bounds, { x: 3840, y: 0, width: 1280, height: 720 });
  assert.match(target.warning, /降级/);
});

test('kiosk hash 固定同一路由并显式携带 readonly', () => {
  assert.equal(
    buildKioskHash(3, true),
    '/monitor?channel=3&kiosk=1&readonly=1&multi_monitor=1',
  );
  assert.equal(
    buildKioskHash(3, false),
    '/monitor?channel=3&kiosk=1&readonly=0&multi_monitor=1',
  );
});

test('projection 角色路由到投影引导画布', () => {
  assert.equal(
    buildKioskHash(2, true, 'projection'),
    '/projection?channel=2&kiosk=1&multi_monitor=1',
  );
  // 未显式传 role 时保持一期 monitor 行为
  assert.equal(
    buildKioskHash(2, true),
    '/monitor?channel=2&kiosk=1&readonly=1&multi_monitor=1',
  );
});

test('显示器 DTO 标识 OS 主屏与主窗口当前所在屏', () => {
  assert.deepEqual(toDisplayDto(displays[0], 'primary', 'primary'), {
    id: 'primary',
    label: '主显示器',
    bounds: { x: 0, y: 0, width: 1920, height: 1080 },
    workArea: { x: 0, y: 0, width: 1920, height: 1040 },
    isPrimary: true,
    isMainWindowDisplay: true,
  });
});

test('主窗口当前所在屏不得作为工位窗口目标', () => {
  const mainDisplay = toDisplayDto(displays[0], 'primary', 'primary');

  assert.equal(isReservedMainDisplayTarget({
    display: displays[0],
    bounds: displays[0].bounds,
  }, mainDisplay), true);
  assert.equal(isReservedMainDisplayTarget({
    display: null,
    bounds: { x: 0, y: 0, width: 1920, height: 1080 },
  }, mainDisplay), true);
  assert.equal(isReservedMainDisplayTarget({
    display: displays[1],
    bounds: displays[1].bounds,
  }, mainDisplay), false);
});

test('renderer crash 在 60 秒窗口内第 3 次停止恢复计数', () => {
  const first = advanceCrashWindow({}, 1000);
  const second = advanceCrashWindow(first, 30000);
  const third = advanceCrashWindow(second, 59000);

  assert.equal(first.reloadAttempts, 1);
  assert.equal(second.reloadAttempts, 2);
  assert.equal(third.reloadAttempts, 3);
});

test('renderer crash 超过 60 秒后重开计数窗口', () => {
  const previous = { reloadAttempts: 2, lastCrashAt: 1000 };

  assert.deepEqual(advanceCrashWindow(previous, 61002), {
    reloadAttempts: 1,
    lastCrashAt: 61002,
  });
});

test('只有主窗口 renderer sender 通过布局应用授权', () => {
  const mainWebContents = {};
  const kioskWebContents = {};

  assert.equal(isMainRenderer(mainWebContents, mainWebContents), true);
  assert.equal(isMainRenderer(kioskWebContents, mainWebContents), false);
  assert.equal(isMainRenderer(null, mainWebContents), false);
});
